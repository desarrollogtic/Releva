from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('cambiar-password/', views.cambiar_password, name='cambiar_password'),
    path('notificaciones/', views.notificaciones_view, name='notificaciones'),
    path('recuperar-password/', views.recuperar_password_view, name='recuperar_password'),
    path('restablecer-password/<uidb64>/<token>/', views.restablecer_password_view, name='restablecer_password'),


    
    # Solicitudes de Cambio de Turno
    path('solicitudes/recibidas/', views.solicitudes_recibidas_view, name='solicitudes_recibidas'),
    path('solicitudes/enviadas/', views.solicitudes_enviadas_view, name='solicitudes_enviadas'),
    path('solicitudes/crear/', views.crear_solicitud, name='crear_solicitud'),
    path('solicitudes/<int:pk>/responder/', views.responder_solicitud, name='responder_solicitud'),
    path('solicitudes/<int:pk>/cancelar/', views.cancelar_solicitud, name='cancelar_solicitud'),

    # Gestión de Área y Jornadas/Turnos
    path('gestion-area/', views.gestion_area_view, name='gestion_area'),
    path('gestion-area/area/crear/', views.crear_area, name='crear_area'),
    path('gestion-area/area/<int:pk>/eliminar/', views.eliminar_area, name='eliminar_area'),
    path('gestion-area/coordinador/agregar/', views.agregar_coordinador_area, name='agregar_coordinador_area'),
    path('gestion-area/coordinador/<int:area_pk>/quitar/<int:user_pk>/', views.quitar_coordinador_area, name='quitar_coordinador_area'),
    path('gestion-area/asignar-turno/', views.asignar_turno_area, name='asignar_turno_area'),
    path('gestion-area/turno/<int:pk>/eliminar/', views.eliminar_turno_area, name='eliminar_turno_area'),
    path('gestion-area/jornada/crear/', views.crear_jornada, name='crear_jornada'),
    path('gestion-area/jornada/<int:pk>/eliminar/', views.eliminar_jornada, name='eliminar_jornada'),

    # Endpoints API
    path('api/usuarios/buscar/', views.api_buscar_usuarios, name='api_buscar_usuarios'),
    path('api/login/', views.api_login, name='api_login'),
    path('api/logout/', views.api_logout, name='api_logout'),
    path('api/me/', views.api_me, name='api_me'),
    path('api/csrf/', views.api_csrf, name='api_csrf'),
    path('api/status/', views.api_status, name='api_status'),
]
