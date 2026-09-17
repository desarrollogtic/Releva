import logging
from datetime import datetime, date
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

def _get_user_display_name(user):
    if not user:
        return ""
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name if full_name else user.username

def _fmt_date(val):
    """
    Convierte seguro cualquier valor de fecha (date, datetime, str) a formato DD/MM/YYYY.
    """
    if not val:
        return ""
    if isinstance(val, (datetime, date)):
        return val.strftime('%d/%m/%Y')
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return ""
        try:
            dt = datetime.strptime(val_str[:10], '%Y-%m-%d')
            return dt.strftime('%d/%m/%Y')
        except ValueError:
            pass
        try:
            dt = datetime.strptime(val_str[:10], '%d/%m/%Y')
            return dt.strftime('%d/%m/%Y')
        except ValueError:
            pass
        return val_str
    return str(val)

def _enviar_correo(destinatarios, asunto, mensaje_texto, mensaje_html=None):
    """
    Función interna para estructurar y enviar correos de forma segura.
    Filtra los destinatarios que tengan correo configurado.
    """
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
    
    # Filtrar direcciones no nulas o vacías
    emails_validos = [e.strip() for e in destinatarios if e and isinstance(e, str) and '@' in e]
    if not emails_validos:
        return False

    try:
        msg = EmailMultiAlternatives(
            subject=asunto,
            body=mensaje_texto,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'desarrollo.gtic@clinicasaludsocial.com'),
            to=emails_validos
        )
        if mensaje_html:
            msg.attach_alternative(mensaje_html, "text/html")
        
        msg.send(fail_silently=True)
        return True
    except Exception as e:
        logger.error(f"Error al enviar correo electrónico: {e}")
        return False


def _generar_plantilla_html(titulo, nombre_destinatario, cuerpo_mensaje, detalles_dict=None):
    """
    Genera un HTML estilizado profesional para las notificaciones por correo de Releva.
    """
    detalles_html = ""
    if detalles_dict:
        rows = ""
        for clave, valor in detalles_dict.items():
            rows += f"""
            <tr>
                <td style="padding: 8px 12px; font-weight: bold; color: #475569; border-bottom: 1px solid #e2e8f0; width: 40%;">{clave}</td>
                <td style="padding: 8px 12px; color: #0f172a; border-bottom: 1px solid #e2e8f0;">{valor}</td>
            </tr>
            """
        detalles_html = f"""
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0; background-color: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0;">
            {rows}
        </table>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 10px; padding: 24px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; }}
            .header {{ background-color: #2563eb; color: #ffffff; padding: 16px 24px; border-radius: 8px 8px 0 0; text-align: center; font-size: 20px; font-weight: bold; }}
            .content {{ padding: 20px 0; color: #334155; line-height: 1.6; font-size: 15px; }}
            .footer {{ text-align: center; padding-top: 20px; font-size: 12px; color: #94a3b8; border-top: 1px solid #e2e8f0; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                Releva - Sistema de Cambio de Turnos
            </div>
            <div class="content">
                <h2 style="color: #0f172a; margin-top: 0;">{titulo}</h2>
                <p>Hola <strong>{nombre_destinatario}</strong>,</p>
                <p>{cuerpo_mensaje}</p>
                {detalles_html}
                <p style="margin-top: 20px;">Puedes gestionar esta solicitud ingresando a la plataforma <strong>Releva</strong>.</p>
            </div>
            <div class="footer">
                <p>Este es un correo automático generado por el sistema <strong>Releva</strong>. Por favor no responder a esta dirección.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def notificar_nueva_solicitud(solicitud):
    """
    Notifica al reemplazo (y reemplazo_2 si aplica) de una nueva solicitud creada,
    y envía confirmación al solicitante.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    reemp_nombre = _get_user_display_name(solicitud.reemplazo)
    
    detalles = {
        "Solicitante": solic_nombre,
        "Reemplazo": reemp_nombre,
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Jornada a Relevar": solicitud.get_jornada_display(),
        "Fecha de Devolución": _fmt_date(solicitud.fecha_devolucion),
        "Jornada Devolución": solicitud.get_jornada_devolucion_display(),
        "Estado": "Pendiente de Aceptación por Reemplazo"
    }
    if solicitud.motivo:
        detalles["Motivo / Observaciones"] = solicitud.motivo

    # 1. Notificar al Reemplazo 1
    if solicitud.reemplazo and solicitud.reemplazo.email:
        titulo = "Nueva Solicitud de Cambio de Turno Recibida"
        cuerpo = f"<strong>{solic_nombre}</strong> ha registrado una solicitud de cambio de turno solicitando tu apoyo como reemplazo."
        html = _generar_plantilla_html(titulo, reemp_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo.email, f"[Releva] Nueva solicitud de cambio de turno de {solic_nombre}", cuerpo, html)

    # 2. Notificar al Reemplazo 2 (si existe)
    if solicitud.reemplazo_2 and solicitud.reemplazo_2.email and solicitud.reemplazo_2 != solicitud.reemplazo:
        r2_nombre = _get_user_display_name(solicitud.reemplazo_2)
        titulo = "Nueva Solicitud de Cambio de Turno Recibida (Devolución 2)"
        cuerpo = f"<strong>{solic_nombre}</strong> ha registrado una solicitud de cambio de turno incluyéndote en la devolución de turno."
        html = _generar_plantilla_html(titulo, r2_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo_2.email, f"[Releva] Nueva solicitud de cambio de turno de {solic_nombre}", cuerpo, html)

    # 3. Confirmación al Solicitante
    if solicitud.solicitante and solicitud.solicitante.email:
        titulo = "Confirmación de Solicitud Creada"
        cuerpo = f"Tu solicitud de cambio de turno enviada a <strong>{reemp_nombre}</strong> se ha registrado correctamente y queda a la espera de su aceptación."
        html = _generar_plantilla_html(titulo, solic_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.solicitante.email, "[Releva] Confirmación de solicitud de cambio de turno enviada", cuerpo, html)


def notificar_aprobacion_reemplazo(solicitud):
    """
    Instancia 1: El reemplazo aceptó la solicitud. Notifica al solicitante y al coordinador.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    reemp_nombre = _get_user_display_name(solicitud.reemplazo)
    coord_nombre = _get_user_display_name(solicitud.coordinador) if solicitud.coordinador else "Coordinador de Área"

    detalles = {
        "Solicitante": solic_nombre,
        "Reemplazo": reemp_nombre,
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Jornada": solicitud.get_jornada_display(),
        "Nuevo Estado": "Pendiente por Autorización de Coordinador"
    }

    # 1. Notificar al Solicitante
    if solicitud.solicitante and solicitud.solicitante.email:
        titulo = "Solicitud Aceptada por el Reemplazo"
        cuerpo = f"<strong>{reemp_nombre}</strong> ha ACEPTADO tu solicitud de cambio de turno para el {_fmt_date(solicitud.fecha_turno)}. La solicitud ahora pasa a revisión y aprobación del Coordinador de Área."
        html = _generar_plantilla_html(titulo, solic_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.solicitante.email, f"[Releva] Tu solicitud fue aceptada por {reemp_nombre}", cuerpo, html)

    # 2. Notificar al Coordinador
    if solicitud.coordinador and solicitud.coordinador.email:
        titulo = "Solicitud Pendiente de tu Aprobación (Coordinador)"
        cuerpo = f"La solicitud de cambio de turno entre <strong>{solic_nombre}</strong> y <strong>{reemp_nombre}</strong> ha sido aceptada por el reemplazo y requiere tu autorización."
        html = _generar_plantilla_html(titulo, coord_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.coordinador.email, f"[Releva] Solicitud de cambio de turno requiere tu aprobación - {solic_nombre}", cuerpo, html)
    elif hasattr(solicitud.solicitante, 'profile') and solicitud.solicitante.profile.area:
        for admin in solicitud.solicitante.profile.area.administradores.filter(email__isnull=False).exclude(email=''):
            titulo = "Solicitud Pendiente de tu Aprobación (Coordinador)"
            cuerpo = f"La solicitud de cambio de turno de <strong>{solic_nombre}</strong> en el área <strong>{solicitud.solicitante.profile.area.nombre}</strong> requiere tu autorización."
            html = _generar_plantilla_html(titulo, _get_user_display_name(admin), cuerpo, detalles)
            _enviar_correo(admin.email, f"[Releva] Solicitud de cambio de turno requiere autorización - {solic_nombre}", cuerpo, html)


def notificar_aprobacion_coordinador(solicitud):
    """
    Instancia 2: El coordinador aprobó la solicitud. Notifica al solicitante, al reemplazo y a Gestión Humana.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    reemp_nombre = _get_user_display_name(solicitud.reemplazo)
    coord_nombre = _get_user_display_name(solicitud.coordinador) if solicitud.coordinador else "el Coordinador de Área"

    detalles = {
        "Solicitante": solic_nombre,
        "Reemplazo": reemp_nombre,
        "Aprobado por": coord_nombre,
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Nuevo Estado": "Pendiente por Aprobación Final de Gestión Humana"
    }

    # 1. Notificar al Solicitante
    if solicitud.solicitante and solicitud.solicitante.email:
        titulo = "Solicitud Aprobada por Coordinador"
        cuerpo = f"<strong>{coord_nombre}</strong> ha APROBADO tu solicitud de cambio de turno. Ha sido enviada a Gestión Humana para la aprobación final."
        html = _generar_plantilla_html(titulo, solic_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.solicitante.email, "[Releva] Solicitud Aprobada por Coordinador", cuerpo, html)

    # 2. Notificar al Reemplazo
    if solicitud.reemplazo and solicitud.reemplazo.email:
        titulo = "Solicitud Aprobada por Coordinador"
        cuerpo = f"El coordinador <strong>{coord_nombre}</strong> ha autorizado el cambio de turno con <strong>{solic_nombre}</strong>."
        html = _generar_plantilla_html(titulo, reemp_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo.email, f"[Releva] Cambio de turno con {solic_nombre} aprobado por Coordinador", cuerpo, html)

    # 3. Notificar a Gestión Humana
    gh_users = User.objects.filter(username='1102830559', email__isnull=False).exclude(email='')
    if not gh_users.exists():
        gh_users = User.objects.filter(is_superuser=True, email__isnull=False).exclude(email='')

    for gh in gh_users:
        gh_nombre = _get_user_display_name(gh)
        titulo = "Solicitud Pendiente de Aprobación Final (Gestión Humana)"
        cuerpo = f"La solicitud de cambio de turno de <strong>{solic_nombre}</strong> (Reemplazo: <strong>{reemp_nombre}</strong>) ha sido aprobada por el coordinador y está lista para tu Vo.Bo. definitivo."
        html = _generar_plantilla_html(titulo, gh_nombre, cuerpo, detalles)
        _enviar_correo(gh.email, f"[Releva] Solicitud pendiente de Aprobación Final GH - {solic_nombre}", cuerpo, html)


def notificar_aprobacion_gh(solicitud):
    """
    Instancia 3: Gestión Humana aprobó definitivamente la solicitud. Notifica a todas las partes.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    reemp_nombre = _get_user_display_name(solicitud.reemplazo)

    detalles = {
        "Solicitante": solic_nombre,
        "Reemplazo": reemp_nombre,
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Jornada": solicitud.get_jornada_display(),
        "Fecha Devolución": _fmt_date(solicitud.fecha_devolucion),
        "Jornada Devolución": solicitud.get_jornada_devolucion_display(),
        "Estado Definitivo": "APROBADA Y AUTORIZADA"
    }

    # 1. Notificar al Solicitante
    if solicitud.solicitante and solicitud.solicitante.email:
        titulo = "¡Solicitud de Cambio de Turno Aprobada Definitivamente!"
        cuerpo = f"¡Buenas noticias! Tu cambio de turno para la fecha <strong>{_fmt_date(solicitud.fecha_turno)}</strong> ha sido <strong>APROBADO DEFINITIVAMENTE por Gestión Humana</strong>."
        html = _generar_plantilla_html(titulo, solic_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.solicitante.email, "[Releva] ¡Cambio de turno Aprobado Definitivamente!", cuerpo, html)

    # 2. Notificar al Reemplazo
    if solicitud.reemplazo and solicitud.reemplazo.email:
        titulo = "Cambio de Turno Autorizado Definitivamente"
        cuerpo = f"El cambio de turno con <strong>{solic_nombre}</strong> para la fecha <strong>{_fmt_date(solicitud.fecha_turno)}</strong> fue <strong>APROBADO DEFINITIVAMENTE por Gestión Humana</strong>."
        html = _generar_plantilla_html(titulo, reemp_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo.email, f"[Releva] Cambio de turno con {solic_nombre} Aprobado Definitivamente", cuerpo, html)

    # 3. Notificar al Reemplazo 2 si aplica
    if solicitud.reemplazo_2 and solicitud.reemplazo_2.email and solicitud.reemplazo_2 != solicitud.reemplazo:
        r2_nombre = _get_user_display_name(solicitud.reemplazo_2)
        titulo = "Cambio de Turno Autorizado Definitivamente"
        cuerpo = f"El cambio de turno con <strong>{solic_nombre}</strong> (Devolución 2) ha sido <strong>APROBADO DEFINITIVAMENTE por Gestión Humana</strong>."
        html = _generar_plantilla_html(titulo, r2_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo_2.email, f"[Releva] Cambio de turno con {solic_nombre} Aprobado Definitivamente", cuerpo, html)

    # 4. Notificar al Coordinador
    if solicitud.coordinador and solicitud.coordinador.email:
        coord_nombre = _get_user_display_name(solicitud.coordinador)
        titulo = "Cambio de Turno Autorizado Definitivamente"
        cuerpo = f"Te informamos que el cambio de turno entre <strong>{solic_nombre}</strong> y <strong>{reemp_nombre}</strong> recibió la aprobación final de Gestión Humana."
        html = _generar_plantilla_html(titulo, coord_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.coordinador.email, f"[Releva] Solicitud Autorizada Definitivamente - {solic_nombre}", cuerpo, html)


def notificar_rechazo(solicitud, actor):
    """
    Notifica cuando una solicitud de cambio de turno es rechazada.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    actor_nombre = _get_user_display_name(actor) if actor else "Un usuario autorizado"

    detalles = {
        "Solicitante": solic_nombre,
        "Reemplazo": _get_user_display_name(solicitud.reemplazo),
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Estado": "RECHAZADA"
    }

    # 1. Notificar al Solicitante
    if solicitud.solicitante and solicitud.solicitante.email:
        titulo = "Solicitud de Cambio de Turno Rechazada"
        cuerpo = f"La solicitud de cambio de turno para la fecha <strong>{_fmt_date(solicitud.fecha_turno)}</strong> ha sido <strong>RECHAZADA</strong> por <strong>{actor_nombre}</strong>."
        html = _generar_plantilla_html(titulo, solic_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.solicitante.email, f"[Releva] Solicitud de cambio de turno Rechazada por {actor_nombre}", cuerpo, html)


def notificar_cancelacion(solicitud):
    """
    Notifica al reemplazo y coordinador cuando el solicitante cancela la petición.
    """
    solic_nombre = _get_user_display_name(solicitud.solicitante)
    detalles = {
        "Solicitante": solic_nombre,
        "Fecha de Turno": _fmt_date(solicitud.fecha_turno),
        "Estado": "CANCELADA"
    }

    # 1. Notificar al Reemplazo
    if solicitud.reemplazo and solicitud.reemplazo.email:
        reemp_nombre = _get_user_display_name(solicitud.reemplazo)
        titulo = "Solicitud de Cambio de Turno Cancelada"
        cuerpo = f"<strong>{solic_nombre}</strong> ha cancelado la solicitud de cambio de turno para el {_fmt_date(solicitud.fecha_turno)}."
        html = _generar_plantilla_html(titulo, reemp_nombre, cuerpo, detalles)
        _enviar_correo(solicitud.reemplazo.email, f"[Releva] Solicitud de cambio de turno cancelada por {solic_nombre}", cuerpo, html)
