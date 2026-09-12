import os
import json
import sys
import time
import django
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.middleware.csrf import get_token
from django.contrib import messages
from .models import Profile, SolicitudCambio, Area, TurnoArea
from .backends import fetch_and_sync_sisma_user

_CONTRATOS_CACHE = []
_CONTRATOS_CACHE_TIME = 0

def get_all_sisma_contratos():
    global _CONTRATOS_CACHE, _CONTRATOS_CACHE_TIME
    now = time.time()
    if _CONTRATOS_CACHE and (now - _CONTRATOS_CACHE_TIME < 300):
        return _CONTRATOS_CACHE

    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", 6379))
        import redis
        r = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True, socket_connect_timeout=1.0)
        keys = r.keys("sisma:contrato:*")
        if keys:
            vals = r.mget(keys)
            contratos = []
            for v in vals:
                if v:
                    try:
                        c = json.loads(v)
                        contratos.append({
                            'cedula': str(c.get('codigo', '')).strip(),
                            'nombre': str(c.get('nombre_completo', '')).strip(),
                            'cargo': str(c.get('cargo_actual', '')).strip()
                        })
                    except Exception:
                        pass
            if contratos:
                _CONTRATOS_CACHE = contratos
                _CONTRATOS_CACHE_TIME = now
                return _CONTRATOS_CACHE
    except Exception as e:
        print(f"Error cargando contratos desde Redis: {e}")

    return _CONTRATOS_CACHE


@login_required(login_url='core:login')
def home(request):
    """
    Dashboard principal de Releva:
    Muestra la información del usuario y el formulario para crear nuevas solicitudes.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)

    # Sincronización automática de datos con Intranet
    if not request.user.first_name or not profile.cargo or request.user.first_name == request.user.username:
        fetch_and_sync_sisma_user(request.user.username)
        request.user.refresh_from_db()
        profile.refresh_from_db()

    # Contadores para el resumen
    recibidas_pendientes = SolicitudCambio.objects.filter(reemplazo=request.user, estado='PENDIENTE').count()
    enviadas_pendientes = SolicitudCambio.objects.filter(solicitante=request.user, estado='PENDIENTE').count()

    context = {
        'user': request.user,
        'profile': profile,
        'recibidas_pendientes': recibidas_pendientes,
        'enviadas_pendientes': enviadas_pendientes,
        'jornada_choices': SolicitudCambio.JORNADA_CHOICES,
        'server_time': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
    }
    return render(request, 'home.html', context)


@login_required(login_url='core:login')
def api_buscar_usuarios(request):
    """
    Endpoint para buscar usuarios/empleados por Nombre, Cédula o Cargo
    utilizando los contratos en Redis y los usuarios locales.
    Únicamente realiza la búsqueda cuando se ingresan al menos 2 caracteres.
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'usuarios': []})

    dict_usuarios = {}

    # 1. Cargar contratos de Redis
    contratos = get_all_sisma_contratos()
    for c in contratos:
        ced = c['cedula']
        if ced and ced != request.user.username:
            dict_usuarios[ced] = {
                'cedula': ced,
                'nombre': c['nombre'],
                'cargo': c['cargo']
            }

    # 2. Cargar usuarios locales de Django
    local_users = User.objects.exclude(pk=request.user.pk).select_related('profile')
    for u in local_users:
        ced = u.username
        if ced != request.user.username:
            if ced not in dict_usuarios or not dict_usuarios[ced]['nombre']:
                dict_usuarios[ced] = {
                    'cedula': ced,
                    'nombre': u.first_name or u.username,
                    'cargo': getattr(u, 'profile', None) and u.profile.cargo or ''
                }

    lista = list(dict_usuarios.values())
    terms = q.lower().split()
    resultados = [
        u for u in lista 
        if all(t in f"{u['nombre']} {u['cedula']} {u['cargo']}".lower() for t in terms)
    ]

    # Ordenar por nombre alfabéticamente
    resultados.sort(key=lambda x: x['nombre'])

    return JsonResponse({'usuarios': resultados[:40]})


from django.db.models import Q

@login_required(login_url='core:login')
def solicitudes_recibidas_view(request):
    """
    Página dedicada para ver y gestionar las Solicitudes Recibidas.
    """
    solicitudes_recibidas = SolicitudCambio.objects.filter(
        Q(reemplazo=request.user) | Q(reemplazo_2=request.user)
    ).select_related('solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile').distinct()
    
    recibidas_pendientes = solicitudes_recibidas.filter(estado='PENDIENTE').count()

    context = {
        'solicitudes_recibidas': solicitudes_recibidas,
        'recibidas_pendientes': recibidas_pendientes,
    }
    return render(request, 'solicitudes_recibidas.html', context)


@login_required(login_url='core:login')
def solicitudes_enviadas_view(request):
    """
    Página dedicada para ver y gestionar las Solicitudes Enviadas.
    """
    solicitudes_enviadas = SolicitudCambio.objects.filter(solicitante=request.user).select_related(
        'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile'
    )
    enviadas_pendientes = solicitudes_enviadas.filter(estado='PENDIENTE').count()

    context = {
        'solicitudes_enviadas': solicitudes_enviadas,
        'enviadas_pendientes': enviadas_pendientes,
    }
    return render(request, 'solicitudes_enviadas.html', context)


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def crear_solicitud(request):
    """
    Crea una nueva solicitud de cambio de turno hacia uno o dos reemplazos.
    """
    reemplazo_val = (request.POST.get('reemplazo_cedula') or request.POST.get('reemplazo_id') or '').strip()
    reemplazo_val_2 = (request.POST.get('reemplazo_cedula_2') or request.POST.get('reemplazo_id_2') or '').strip()

    fecha_turno = request.POST.get('fecha_turno')
    jornada = request.POST.get('jornada')
    
    fecha_devolucion = request.POST.get('fecha_devolucion')
    jornada_devolucion = request.POST.get('jornada_devolucion')
    
    fecha_devolucion_2 = request.POST.get('fecha_devolucion_2') or None
    jornada_devolucion_2 = request.POST.get('jornada_devolucion_2') or None
    
    motivo = request.POST.get('motivo', '').strip()

    if not reemplazo_val or not fecha_turno or not fecha_devolucion:
        messages.error(request, 'Por favor completa todos los campos requeridos para enviar la solicitud.')
        return redirect('core:home')

    reemplazo = None
    if reemplazo_val.isdigit():
        reemplazo = User.objects.filter(pk=reemplazo_val).first()
    if not reemplazo:
        reemplazo = User.objects.filter(username=reemplazo_val).first()

    # Si el usuario no existe localmente, se sincroniza desde Sisma/Redis
    if not reemplazo:
        reemplazo, _ = fetch_and_sync_sisma_user(reemplazo_val)

    if not reemplazo or not reemplazo.username:
        messages.error(request, 'El reemplazo seleccionado no existe.')
        return redirect('core:home')

    if reemplazo == request.user:
        messages.error(request, 'No puedes enviarte una solicitud a ti mismo.')
        return redirect('core:home')

    # Reemplazo 2 (Opcional para Devolución 2)
    reemplazo_2 = None
    if fecha_devolucion_2 and reemplazo_val_2:
        if reemplazo_val_2.isdigit():
            reemplazo_2 = User.objects.filter(pk=reemplazo_val_2).first()
        if not reemplazo_2:
            reemplazo_2 = User.objects.filter(username=reemplazo_val_2).first()
        if not reemplazo_2:
            reemplazo_2, _ = fetch_and_sync_sisma_user(reemplazo_val_2)

        if reemplazo_2 == request.user:
            messages.error(request, 'No puedes asignarte a ti mismo como Reemplazo 2.')
            return redirect('core:home')

    # Validación de equivalencia de tiempo entre jornadas (Turno vs Suma de Devoluciones)
    horas_turno = SolicitudCambio.JORNADA_HORAS.get(jornada, 0)
    h1 = SolicitudCambio.JORNADA_HORAS.get(jornada_devolucion, 0)
    h2 = SolicitudCambio.JORNADA_HORAS.get(jornada_devolucion_2, 0) if jornada_devolucion_2 else 0
    total_devolucion = h1 + h2

    if horas_turno != total_devolucion:
        messages.error(
            request, 
            f'Los cambios de turno deben ser equivalentes en tiempo. La jornada a relevar ({horas_turno} HRS) '
            f'no equivale a las jornadas de devolución seleccionadas ({total_devolucion} HRS totales).'
        )
        return redirect('core:home')

    SolicitudCambio.objects.create(
        solicitante=request.user,
        reemplazo=reemplazo,
        reemplazo_2=reemplazo_2,
        fecha_turno=fecha_turno,
        jornada=jornada,
        fecha_devolucion=fecha_devolucion,
        jornada_devolucion=jornada_devolucion,
        fecha_devolucion_2=fecha_devolucion_2,
        jornada_devolucion_2=jornada_devolucion_2,
        motivo=motivo,
        estado='PENDIENTE'
    )

    reemplazo_nombre = reemplazo.first_name or reemplazo.username
    if reemplazo_2 and reemplazo_2 != reemplazo:
        r2_nombre = reemplazo_2.first_name or reemplazo_2.username
        msg_str = f'Solicitud enviada correctamente a {reemplazo_nombre} y {r2_nombre}. Ver en la pestaña Solicitudes Enviadas.'
    else:
        msg_str = f'Solicitud enviada correctamente a {reemplazo_nombre}. Ver en la pestaña Solicitudes Enviadas.'

    messages.success(request, msg_str)
    return redirect('core:solicitudes_enviadas')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def responder_solicitud(request, pk):
    """
    Permite al reemplazo aceptar o rechazar una solicitud de cambio recibida.
    """
    solicitud = get_object_or_404(SolicitudCambio, Q(reemplazo=request.user) | Q(reemplazo_2=request.user), pk=pk)
    accion = request.POST.get('accion')

    if solicitud.estado != 'PENDIENTE':
        messages.warning(request, 'Esta solicitud ya ha sido procesada previamente.')
        return redirect('core:solicitudes_recibidas')

    solicitante_nombre = solicitud.solicitante.first_name or solicitud.solicitante.username

    if accion == 'aceptar':
        solicitud.estado = 'ACEPTADA'
        solicitud.save()
        messages.success(request, f'Has ACEPTADO el cambio de turno con {solicitante_nombre}.')
    elif accion == 'rechazar':
        solicitud.estado = 'RECHAZADA'
        solicitud.save()
        messages.info(request, f'Has RECHAZADO la solicitud de cambio de turno de {solicitante_nombre}.')
    else:
        messages.error(request, 'Acción no válida.')

    return redirect('core:solicitudes_recibidas')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def cancelar_solicitud(request, pk):
    """
    Permite al solicitante cancelar una petición enviada que aún se encuentre pendiente.
    """
    solicitud = get_object_or_404(SolicitudCambio, pk=pk, solicitante=request.user)

    if solicitud.estado == 'PENDIENTE':
        solicitud.estado = 'CANCELADA'
        solicitud.save()
        messages.info(request, 'La solicitud de cambio de turno ha sido cancelada.')
    else:
        messages.warning(request, 'No se puede cancelar una solicitud que ya fue procesada.')

    return redirect('core:solicitudes_enviadas')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:home')

    areas = Area.objects.all()

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        area_id = request.POST.get('area_id', '').strip()

        if not username:
            messages.error(request, 'El usuario es obligatorio.')
        elif not password:
            messages.error(request, 'La contraseña es obligatoria.')
        elif username != password:
            messages.error(request, 'Usuario o contraseña incorrectos.')
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                user, profile = fetch_and_sync_sisma_user(username, password)

            if user is not None:
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                if area_id and area_id.isdigit():
                    area_obj = Area.objects.filter(pk=area_id).first()
                    if area_obj:
                        profile, _ = Profile.objects.get_or_create(user=user)
                        profile.area = area_obj
                        profile.save()

                next_url = request.GET.get('next') or 'core:home'
                return redirect(next_url)
            else:
                messages.error(request, 'Usuario o contraseña incorrectos.')

    return render(request, 'login.html', {'areas': areas})


@login_required(login_url='core:login')
def gestion_area_view(request):
    """
    Vista dedicada para la administración de turnos del área.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos para acceder a la gestión de áreas.')
        return redirect('core:home')

    if request.user.is_superuser:
        areas_administradas = Area.objects.all()
    else:
        areas_administradas = request.user.areas_administradas.all()

    if not areas_administradas.exists():
        messages.warning(request, 'No tienes áreas asignadas bajo tu administración.')
        return redirect('core:home')

    area_id = request.GET.get('area_id')
    area_activa = None
    if area_id and area_id.isdigit():
        area_activa = areas_administradas.filter(pk=area_id).first()
    if not area_activa:
        area_activa = areas_administradas.first()

    empleados = User.objects.filter(profile__area=area_activa).select_related('profile').order_by('first_name')
    turnos_programados = TurnoArea.objects.filter(area=area_activa).select_related('usuario', 'usuario__profile').order_by('fecha', 'usuario__first_name')
    todos_los_usuarios = User.objects.all().select_related('profile').order_by('first_name')[:80]

    context = {
        'area_activa': area_activa,
        'areas_administradas': areas_administradas,
        'empleados': empleados,
        'turnos_programados': turnos_programados,
        'todos_los_usuarios': todos_los_usuarios,
        'jornada_choices': SolicitudCambio.JORNADA_CHOICES,
    }
    return render(request, 'gestion_area.html', context)


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def asignar_turno_area(request):
    """
    Asigna un turno programado a un empleado dentro de un área.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos para asignar turnos de área.')
        return redirect('core:home')

    area_id = request.POST.get('area_id')
    empleado_val = (request.POST.get('empleado_cedula') or '').strip()
    fecha_turno = request.POST.get('fecha_turno')
    jornada = request.POST.get('jornada')

    area = get_object_or_404(Area, pk=area_id)
    if not request.user.is_superuser and area not in request.user.areas_administradas.all():
        messages.error(request, 'No tienes administración sobre esta área.')
        return redirect('core:gestion_area')

    usuario = User.objects.filter(username=empleado_val).first()
    if not usuario and empleado_val.isdigit():
        usuario = User.objects.filter(pk=empleado_val).first()

    if not usuario:
        usuario, _ = fetch_and_sync_sisma_user(empleado_val)

    if not usuario:
        messages.error(request, 'El empleado seleccionado no existe.')
        return redirect(f'/gestion-area/?area_id={area.id}')

    # Vincular empleado al área si no tiene una
    prof_emp, _ = Profile.objects.get_or_create(user=usuario)
    if not prof_emp.area:
        prof_emp.area = area
        prof_emp.save()

    TurnoArea.objects.create(
        area=area,
        usuario=usuario,
        fecha=fecha_turno,
        jornada=jornada,
        creado_por=request.user
    )

    emp_nombre = usuario.first_name or usuario.username
    messages.success(request, f'Turno programado correctamente para {emp_nombre} en {area.nombre}.')
    return redirect(f'/gestion-area/?area_id={area.id}')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def eliminar_turno_area(request, pk):
    """
    Elimina un turno programado del área.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos.')
        return redirect('core:home')

    turno = get_object_or_404(TurnoArea, pk=pk)
    area_id = turno.area.id

    if not request.user.is_superuser and turno.area not in request.user.areas_administradas.all():
        messages.error(request, 'No tienes administración sobre esta área.')
        return redirect('core:gestion_area')

    turno.delete()
    messages.info(request, 'El turno programado ha sido eliminado.')
    return redirect(f'/gestion-area/?area_id={area_id}')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def crear_area(request):
    """
    Permite al administrador principal crear una nueva área.
    """
    if not request.user.is_superuser:
        messages.error(request, 'Únicamente el administrador principal puede crear áreas.')
        return redirect('core:gestion_area')

    nombre = request.POST.get('nombre', '').strip()
    descripcion = request.POST.get('descripcion', '').strip()

    if not nombre:
        messages.error(request, 'El nombre del área es obligatorio.')
        return redirect('core:gestion_area')

    area, created = Area.objects.get_or_create(nombre=nombre, defaults={'descripcion': descripcion})
    if created:
        messages.success(request, f'Área "{nombre}" creada correctamente.')
    else:
        messages.warning(request, f'El área "{nombre}" ya existía.')

    return redirect(f'/gestion-area/?area_id={area.id}')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def eliminar_area(request, pk):
    """
    Permite al administrador principal eliminar un área existente.
    """
    if not request.user.is_superuser:
        messages.error(request, 'Únicamente el administrador principal puede eliminar áreas.')
        return redirect('core:gestion_area')

    area = get_object_or_404(Area, pk=pk)
    nombre_area = area.nombre
    area.delete()
    messages.info(request, f'Área "{nombre_area}" eliminada correctamente.')
    return redirect('core:gestion_area')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def agregar_coordinador_area(request):
    """
    Asigna un usuario como coordinador/administrador de un área.
    """
    if not request.user.is_superuser and not request.user.profile.es_admin_area():
        messages.error(request, 'No tienes permisos para agregar coordinadores.')
        return redirect('core:gestion_area')

    area_id = request.POST.get('area_id')
    coordinador_val = (request.POST.get('coordinador_cedula') or '').strip()

    area = get_object_or_404(Area, pk=area_id)
    if not request.user.is_superuser and area not in request.user.areas_administradas.all():
        messages.error(request, 'No tienes permisos de administración en esta área.')
        return redirect('core:gestion_area')

    usuario = User.objects.filter(username=coordinador_val).first()
    if not usuario and coordinador_val.isdigit():
        usuario = User.objects.filter(pk=coordinador_val).first()

    if not usuario:
        usuario, _ = fetch_and_sync_sisma_user(coordinador_val)

    if not usuario:
        messages.error(request, 'El usuario seleccionado no existe.')
        return redirect(f'/gestion-area/?area_id={area.id}')

    area.administradores.add(usuario)
    prof, _ = Profile.objects.get_or_create(user=usuario)
    if not prof.area:
        prof.area = area
        prof.save()

    coord_nombre = usuario.first_name or usuario.username
    messages.success(request, f'{coord_nombre} ha sido asignado(a) como coordinador(a) de {area.nombre}.')
    return redirect(f'/gestion-area/?area_id={area.id}')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def quitar_coordinador_area(request, area_pk, user_pk):
    """
    Remueve un coordinador/administrador de un área.
    """
    if not request.user.is_superuser and not request.user.profile.es_admin_area():
        messages.error(request, 'No tienes permisos para remover coordinadores.')
        return redirect('core:gestion_area')

    area = get_object_or_404(Area, pk=area_pk)
    if not request.user.is_superuser and area not in request.user.areas_administradas.all():
        messages.error(request, 'No tienes permisos de administración en esta área.')
        return redirect('core:gestion_area')

    usuario = get_object_or_404(User, pk=user_pk)
    area.administradores.remove(usuario)
    coord_nombre = usuario.first_name or usuario.username
    messages.info(request, f'{coord_nombre} ya no es coordinador(a) de {area.nombre}.')
    return redirect(f'/gestion-area/?area_id={area.id}')


def logout_view(request):
    logout(request)
    return redirect('core:login')


@csrf_exempt
@require_http_methods(["POST"])
def api_login(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Solicitud inválida.'}, status=400)

    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()

    if not username or not password:
        return JsonResponse({'error': 'Todos los campos son obligatorios.'}, status=400)

    user = authenticate(request, username=username, password=password)
    if user is None and username == password:
        user, profile = fetch_and_sync_sisma_user(username, password)

    if user is not None:
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        profile, _ = Profile.objects.get_or_create(user=user)
        return JsonResponse({
            'ok': True,
            'username': user.username,
            'nombre': user.first_name or user.username,
            'cargo': profile.cargo or ''
        })

    return JsonResponse({'error': 'Usuario o contraseña incorrectos.'}, status=401)


@csrf_exempt
@require_http_methods(["POST"])
def api_logout(request):
    logout(request)
    return JsonResponse({'ok': True})


@ensure_csrf_cookie
def api_me(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'No autenticado.'}, status=401)

    profile, _ = Profile.objects.get_or_create(user=request.user)

    return JsonResponse({
        'username': request.user.username,
        'nombre': request.user.first_name or request.user.username,
        'cargo': profile.cargo or '',
        'email': request.user.email,
        'is_superuser': request.user.is_superuser,
        'is_staff': request.user.is_staff,
    })


@ensure_csrf_cookie
def api_csrf(request):
    return JsonResponse({'csrfToken': get_token(request)})


def api_status(request):
    return JsonResponse({
        'status': 'online',
        'app': 'Releva Core',
        'version': '1.0.0',
        'django': django.get_version(),
        'timestamp': datetime.now().isoformat()
    })

