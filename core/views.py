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
    Si el usuario es Coordinador, es redirigido a la vista exclusiva para responder solicitudes.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)

    # Redirección automática si el usuario es Coordinador
    if profile.es_admin_area():
        return redirect('core:solicitudes_recibidas')

    # Sincronización automática de datos con Intranet
    if not request.user.first_name or not profile.cargo or request.user.first_name == request.user.username:
        fetch_and_sync_sisma_user(request.user.username)
        request.user.refresh_from_db()
        profile.refresh_from_db()

    # Contadores para el resumen
    is_gh = (request.user.username == '1102830559' or request.user.is_superuser)
    if is_gh:
        recibidas_pendientes = SolicitudCambio.objects.filter(Q(estado='PENDIENTE_GH') | Q(coordinador=request.user, estado='PENDIENTE_COORDINADOR') | Q(reemplazo=request.user, estado='PENDIENTE_REEMPLAZO')).distinct().count()
    elif request.user.profile.es_admin_area():
        recibidas_pendientes = SolicitudCambio.objects.filter(
            Q(coordinador=request.user, estado='PENDIENTE_COORDINADOR') | Q(reemplazo=request.user, estado='PENDIENTE_REEMPLAZO') | Q(reemplazo_2=request.user, estado='PENDIENTE_REEMPLAZO')
        ).distinct().count()
    else:
        recibidas_pendientes = SolicitudCambio.objects.filter(
            Q(reemplazo=request.user) | Q(reemplazo_2=request.user), 
            estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO']
        ).distinct().count()
    enviadas_pendientes = SolicitudCambio.objects.filter(solicitante=request.user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO', 'PENDIENTE_COORDINADOR', 'PENDIENTE_GH']).count()

    # Lista de coordinadores disponibles
    coordinadores = User.objects.filter(
        Q(areas_administradas__isnull=False) | Q(profile__cargo__icontains='coordinador')
    ).select_related('profile').distinct().order_by('first_name')
    if not coordinadores.exists():
        coordinadores = User.objects.all().select_related('profile').order_by('first_name')

    # Sincronización de cargos para coordinadores si falta en el perfil
    for c in coordinadores:
        if not getattr(c, 'profile', None) or not c.profile.cargo:
            fetch_and_sync_sisma_user(c.username)

    context = {
        'user': request.user,
        'profile': profile,
        'recibidas_pendientes': recibidas_pendientes,
        'enviadas_pendientes': enviadas_pendientes,
        'coordinadores': coordinadores,
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
    Página dedicada para ver y gestionar las Solicitudes Recibidas según la instancia correspondiente.
    """
    is_gh = (request.user.username == '1102830559' or request.user.is_superuser)

    if is_gh:
        solicitudes_recibidas = SolicitudCambio.objects.all().select_related(
            'solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile', 'coordinador'
        ).distinct()
        recibidas_pendientes = solicitudes_recibidas.filter(estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO', 'PENDIENTE_COORDINADOR', 'PENDIENTE_GH']).count()
    elif hasattr(request.user, 'profile') and request.user.profile.es_admin_area():
        solicitudes_recibidas = SolicitudCambio.objects.filter(
            Q(coordinador=request.user) | Q(reemplazo=request.user) | Q(reemplazo_2=request.user) | Q(solicitante__profile__area__administradores=request.user)
        ).select_related('solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile', 'coordinador').distinct()
        recibidas_pendientes = solicitudes_recibidas.filter(
            Q(coordinador=request.user, estado='PENDIENTE_COORDINADOR') | Q(solicitante__profile__area__administradores=request.user, estado='PENDIENTE_COORDINADOR') | Q(reemplazo=request.user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO']) | Q(reemplazo_2=request.user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO'])
        ).distinct().count()
    else:
        solicitudes_recibidas = SolicitudCambio.objects.filter(
            Q(reemplazo=request.user) | Q(reemplazo_2=request.user) | Q(coordinador=request.user)
        ).select_related('solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile', 'coordinador').distinct()
        recibidas_pendientes = solicitudes_recibidas.filter(
            Q(coordinador=request.user, estado='PENDIENTE_COORDINADOR') | Q(reemplazo=request.user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO']) | Q(reemplazo_2=request.user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO'])
        ).distinct().count()

    context = {
        'solicitudes_recibidas': solicitudes_recibidas,
        'recibidas_pendientes': recibidas_pendientes,
        'is_gh': is_gh,
    }
    return render(request, 'solicitudes_recibidas.html', context)


@login_required(login_url='core:login')
def solicitudes_enviadas_view(request):
    """
    Página dedicada para ver y gestionar las Solicitudes Enviadas.
    """
    solicitudes_enviadas = SolicitudCambio.objects.filter(solicitante=request.user).select_related(
        'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'reemplazo_2__profile', 'coordinador'
    )
    enviadas_pendientes = solicitudes_enviadas.filter(estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO', 'PENDIENTE_COORDINADOR', 'PENDIENTE_GH']).count()

    context = {
        'solicitudes_enviadas': solicitudes_enviadas,
        'enviadas_pendientes': enviadas_pendientes,
    }
    return render(request, 'solicitudes_enviadas.html', context)


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def crear_solicitud(request):
    """
    Crea una nueva solicitud de cambio de turno enviada en primera instancia al reemplazo.
    """
    reemplazo_val = (request.POST.get('reemplazo_cedula') or request.POST.get('reemplazo_id') or '').strip()
    reemplazo_val_2 = (request.POST.get('reemplazo_cedula_2') or request.POST.get('reemplazo_id_2') or '').strip()
    coordinador_val = (request.POST.get('coordinador_cedula') or request.POST.get('coordinador_id') or '').strip()

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

    # Validar que la fecha de devolución sea posterior a la fecha del turno a relevar
    try:
        dt_turno = datetime.strptime(fecha_turno, '%Y-%m-%d').date()
        dt_devolucion = datetime.strptime(fecha_devolucion, '%Y-%m-%d').date()
        if dt_devolucion <= dt_turno:
            messages.error(request, 'La fecha de devolución del turno debe ser posterior a la fecha del turno a relevar.')
            return redirect('core:home')

        if fecha_devolucion_2:
            dt_devolucion_2 = datetime.strptime(fecha_devolucion_2, '%Y-%m-%d').date()
            if dt_devolucion_2 <= dt_turno:
                messages.error(request, 'La fecha de la segunda devolución debe ser posterior a la fecha del turno a relevar.')
                return redirect('core:home')
    except (ValueError, TypeError):
        messages.error(request, 'Formato de fecha inválido.')
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

    # Buscar Coordinador
    coordinador = None
    if coordinador_val:
        if coordinador_val.isdigit():
            coordinador = User.objects.filter(pk=coordinador_val).first()
        if not coordinador:
            coordinador = User.objects.filter(username=coordinador_val).first()

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
        coordinador=coordinador,
        fecha_turno=fecha_turno,
        jornada=jornada,
        fecha_devolucion=fecha_devolucion,
        jornada_devolucion=jornada_devolucion,
        fecha_devolucion_2=fecha_devolucion_2,
        jornada_devolucion_2=jornada_devolucion_2,
        motivo=motivo,
        estado='PENDIENTE_REEMPLAZO'
    )

    reemplazo_nombre = reemplazo.first_name or reemplazo.username
    if reemplazo_2 and reemplazo_2 != reemplazo:
        r2_nombre = reemplazo_2.first_name or reemplazo_2.username
        msg_str = f'Solicitud enviada a {reemplazo_nombre} y {r2_nombre}. Queda en espera de su aceptación.'
    else:
        msg_str = f'Solicitud enviada a {reemplazo_nombre}. Queda en espera de su aceptación.'

    messages.success(request, msg_str)
    return redirect('core:solicitudes_enviadas')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def responder_solicitud(request, pk):
    """
    Maneja la aprobación secuencial en 3 instancias:
    1. PENDIENTE_REEMPLAZO: Aceptado por el reemplazo -> Pasa a PENDIENTE_COORDINADOR.
    2. PENDIENTE_COORDINADOR: Aprobado por el Coordinador de área -> Pasa a PENDIENTE_GH.
    3. PENDIENTE_GH: Aprobado por Gestión Humana (1102830559) -> Pasa a ACEPTADA.
    """
    solicitud = get_object_or_404(SolicitudCambio, pk=pk)
    is_gh = (request.user.username == '1102830559' or request.user.is_superuser)
    accion = request.POST.get('accion')

    if solicitud.estado in ['ACEPTADA', 'RECHAZADA', 'CANCELADA']:
        messages.warning(request, 'Esta solicitud ya ha sido procesada previamente.')
        return redirect('core:solicitudes_recibidas')

    solicitante_nombre = solicitud.solicitante.first_name or solicitud.solicitante.username
    coord_nombre = solicitud.coordinador.first_name or solicitud.coordinador.username if solicitud.coordinador else "el Coordinador de Área"

    if accion == 'aceptar':
        # Instancia 1: Aceptación por el Compañero Reemplazo
        if solicitud.estado in ['PENDIENTE', 'PENDIENTE_REEMPLAZO']:
            if not is_gh and request.user != solicitud.reemplazo and request.user != solicitud.reemplazo_2:
                messages.error(request, 'Únicamente el compañero asignado como reemplazo puede aceptar esta primera instancia.')
                return redirect('core:solicitudes_recibidas')

            solicitud.estado = 'PENDIENTE_COORDINADOR'
            solicitud.save()
            messages.success(
                request, 
                f'Has ACEPTADO el reemplazo de turno de {solicitante_nombre}. '
                f'La solicitud ha sido enviada a {coord_nombre} para su autorización.'
            )

        # Instancia 2: Aprobación por el Coordinador de Área
        elif solicitud.estado == 'PENDIENTE_COORDINADOR':
            is_coord = (
                is_gh or 
                request.user == solicitud.coordinador or 
                (hasattr(request.user, 'profile') and request.user.profile.es_admin_area())
            )
            if not is_coord:
                messages.error(request, 'Esta instancia requiere la autorización del Coordinador de Área asignado.')
                return redirect('core:solicitudes_recibidas')

            solicitud.estado = 'PENDIENTE_GH'
            solicitud.save()
            messages.success(
                request, 
                f'Has APROBADO la solicitud de {solicitante_nombre} en segunda instancia. '
                f'Ha sido enviada al Coordinador de Gestión Humana (Gustavo Salgado - 1102830559) para la aprobación final.'
            )

        # Instancia 3: Aprobación Definitiva por Gestión Humana
        elif solicitud.estado == 'PENDIENTE_GH':
            if not is_gh:
                messages.error(request, 'Esta solicitud requiere la aprobación final del Coordinador de Gestión Humana.')
                return redirect('core:solicitudes_recibidas')

            solicitud.estado = 'ACEPTADA'
            solicitud.save()
            messages.success(
                request, 
                f'Has APROBADO DEFINITIVAMENTE el cambio de turno de {solicitante_nombre}. '
                f'En esta instancia el cambio de turno se encuentra totalmente autorizado.'
            )

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
    Permite al solicitante cancelar una petición enviada que aún no haya sido finalizada.
    """
    solicitud = get_object_or_404(SolicitudCambio, pk=pk, solicitante=request.user)

    if solicitud.estado in ['PENDIENTE', 'PENDIENTE_REEMPLAZO', 'PENDIENTE_COORDINADOR', 'PENDIENTE_GH']:
        solicitud.estado = 'CANCELADA'
        solicitud.save()
        messages.info(request, 'La solicitud de cambio de turno ha sido cancelada.')
    else:
        messages.warning(request, 'No se puede cancelar una solicitud que ya fue procesada definitivamente.')

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


from .models import Profile, SolicitudCambio, Area, TurnoArea, Jornada

def ensure_default_jornadas():
    if not Jornada.objects.exists():
        default_list = [
            ('N', 'DE 06:00 P.M A 06:00 A.M (NOCHE 12H)', 'NOCHE', 12),
            ('C', 'DE 06:00 A.M A 06:00 P.M (CORRIDO 11 HRS)', 'CORRIDO', 11),
            ('C1', 'DE 08:00 A.M A 06:00 P.M (CORRIDO 10 HRS)', 'CORRIDO', 10),
            ('H', 'DE 08:00 A.M A 12:00 M Y DE 01:00 P.M A 05:00 P.M (ADMINISTRATIVO)', 'ADMINISTRATIVO', 8),
            ('N1', 'DE 06:00 P.M A 12:00 P.M Y DE 01:00 A.M A 06:00 A.M (NOCHE 11H)', 'NOCHE', 11),
            ('M4', 'DE 08:00 A.M A 12:00 P.M (4 HORAS MAÑANA)', 'MAÑANA', 4),
            ('LM', 'LICENCIA MATERNIDAD', 'LICENCIA', 0),
            ('D', 'DESCANSO', 'DESCANSO', 0),
            ('L', 'LIBRE', 'LIBRE', 0),
            ('LC', 'LIBRE CUMPLEAÑOS', 'PERMISO', 8),
            ('ME', 'MAÑANA EDUCATIVA', 'EDUCATIVA', 6),
            ('TE', 'TARDE EDUCATIVA', 'EDUCATIVA', 6),
            ('V', 'VACACIONES', 'VACACIONES', 0),
        ]
        for codigo, desc, tipo, horas in default_list:
            Jornada.objects.create(codigo=codigo, descripcion=desc, tipo=tipo, horas=horas)


@login_required(login_url='core:login')
def gestion_area_view(request):
    """
    Vista dedicada para la administración de jornadas y aprobación/rechazo de cambios de jornadas del área.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos para acceder a la gestión de áreas.')
        return redirect('core:home')

    ensure_default_jornadas()

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

    # Jornadas registradas
    jornadas = Jornada.objects.all().order_by('codigo')

    is_gh = (request.user.username == '1102830559' or request.user.is_superuser)
    if is_gh:
        solicitudes_recibidas = SolicitudCambio.objects.filter(
            Q(estado='PENDIENTE_GH') | Q(coordinador=request.user) | Q(solicitante__profile__area=area_activa) | Q(reemplazo__profile__area=area_activa)
        ).select_related('solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'coordinador').distinct()
        recibidas_pendientes = solicitudes_recibidas.filter(Q(estado='PENDIENTE_GH') | Q(estado='PENDIENTE')).count()
    else:
        solicitudes_recibidas = SolicitudCambio.objects.filter(
            Q(coordinador=request.user) | Q(solicitante__profile__area=area_activa) | Q(reemplazo__profile__area=area_activa)
        ).select_related('solicitante', 'solicitante__profile', 'reemplazo', 'reemplazo__profile', 'reemplazo_2', 'coordinador').distinct()
        recibidas_pendientes = solicitudes_recibidas.filter(estado='PENDIENTE').count()

    context = {
        'area_activa': area_activa,
        'areas_administradas': areas_administradas,
        'jornadas': jornadas,
        'solicitudes_recibidas': solicitudes_recibidas,
        'recibidas_pendientes': recibidas_pendientes,
        'jornada_choices': SolicitudCambio.JORNADA_CHOICES,
    }
    return render(request, 'gestion_area.html', context)


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def crear_jornada(request):
    """
    Permite registrar o actualizar un tipo de jornada (turno).
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos para crear jornadas.')
        return redirect('core:gestion_area')

    codigo = request.POST.get('codigo', '').strip().upper()
    descripcion = request.POST.get('descripcion', '').strip()
    tipo = request.POST.get('tipo', '').strip()
    horas_str = request.POST.get('horas', '0').strip()

    if not codigo or not descripcion:
        messages.error(request, 'El código y la descripción de la jornada son obligatorios.')
        return redirect('core:gestion_area')

    try:
        horas = int(horas_str)
    except ValueError:
        horas = 0

    jornada_obj, created = Jornada.objects.get_or_create(
        codigo=codigo,
        defaults={'descripcion': descripcion, 'tipo': tipo, 'horas': horas}
    )

    if created:
        messages.success(request, f'Jornada "{codigo}" creada correctamente.')
    else:
        jornada_obj.descripcion = descripcion
        jornada_obj.tipo = tipo
        jornada_obj.horas = horas
        jornada_obj.save()
        messages.info(request, f'Jornada "{codigo}" actualizada correctamente.')

    return redirect('core:gestion_area')


@login_required(login_url='core:login')
@require_http_methods(["POST"])
def eliminar_jornada(request, pk):
    """
    Permite eliminar un tipo de jornada existente del catálogo.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if not profile.es_admin_area():
        messages.error(request, 'No tienes permisos para eliminar jornadas.')
        return redirect('core:gestion_area')

    jornada_obj = get_object_or_404(Jornada, pk=pk)
    codigo = jornada_obj.codigo
    jornada_obj.delete()
    messages.info(request, f'Jornada "{codigo}" eliminada correctamente.')
    return redirect('core:gestion_area')


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

