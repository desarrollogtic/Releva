from django.db.models import Q
from .models import SolicitudCambio

def solicitudes_context(request):
    """
    Procesador de contexto global para detectar si existen solicitudes pendientes de aprobación o rechazo
    y activar el punto rojo brillante en la barra de navegación.
    """
    if not request.user.is_authenticated:
        return {'navbar_recibidas_pendientes': 0}

    user = request.user
    is_gh = (user.username == '1102830559' or user.is_superuser)

    if is_gh:
        # Gestión Humana ve el indicador si hay solicitudes pendientes de gestión o en cualquier etapa pendiente
        count = SolicitudCambio.objects.filter(
            estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO', 'PENDIENTE_COORDINADOR', 'PENDIENTE_GH']
        ).distinct().count()
    else:
        # Reemplazo o Coordinador ven el indicador si tienen alguna solicitud pendiente de su acción
        filtros = (
            Q(reemplazo=user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO']) |
            Q(reemplazo_2=user, estado__in=['PENDIENTE', 'PENDIENTE_REEMPLAZO']) |
            Q(coordinador=user, estado='PENDIENTE_COORDINADOR')
        )
        if hasattr(user, 'profile') and user.profile.es_admin_area():
            filtros |= Q(solicitante__profile__area__administradores=user, estado='PENDIENTE_COORDINADOR')

        count = SolicitudCambio.objects.filter(filtros).distinct().count()

    return {'navbar_recibidas_pendientes': count}
