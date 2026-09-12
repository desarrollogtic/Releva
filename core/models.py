from django.db import models
from django.contrib.auth.models import User

class Area(models.Model):
    nombre = models.CharField(max_length=150, unique=True, verbose_name="Nombre del Área")
    descripcion = models.TextField(blank=True, null=True, verbose_name="Descripción")
    administradores = models.ManyToManyField(User, related_name='areas_administradas', blank=True, verbose_name="Administradores del Área")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")

    class Meta:
        ordering = ['nombre']
        verbose_name = "Área"
        verbose_name_plural = "Áreas"

    def __str__(self):
        return self.nombre


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    cargo = models.CharField(max_length=150, blank=True, null=True, verbose_name="Cargo")
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, related_name='empleados', verbose_name="Área")

    def __str__(self):
        return f"Perfil de {self.user.first_name or self.user.username}"

    def es_admin_area(self):
        if self.user.is_superuser:
            return True
        return self.user.areas_administradas.exists()


class SolicitudCambio(models.Model):
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('ACEPTADA', 'Aceptada'),
        ('RECHAZADA', 'Rechazada'),
        ('CANCELADA', 'Cancelada'),
    ]

    JORNADA_CHOICES = [
        ('N', 'N - DE 07:00 P.M A 07:00 A.M (NOCHE - 12 HRS)'),
        ('C', 'C - DE 07:00 A.M A 07:00 P.M (CORRIDO 12 HRS)'),
        ('C1', 'C1 - DE 07:00 A.M A 12:00 M Y DE 01:00 P.M A 07:00 P.M (CORRIDO 11 HRS)'),
        ('C2', 'C2 - DE 07:00 A.M A 01:00 P.M Y DE 02:00 P.M A 07:00 P.M (CORRIDO 11 HRS)'),
        ('M', 'M - DE 07:00 A.M A 01:00 P.M (MAÑANA - 6 HRS)'),
        ('T', 'T - DE 01:00 P.M A 07:00 P.M (TARDE - 6 HRS)'),
        ('T9', 'T9 - DE 01:00 P.M A 09:00 P.M (TARDE - 8 HRS)'),
        ('H', 'H - DE 07:00 A.M A 12:00 M Y DE 01:00 P.M A 04:00 P.M (ADMINISTRATIVO - 8 HRS)'),
        ('D', 'D - DESCANSO'),
        ('L', 'L - LIBRE'),
        ('LC', 'LC - LIBRE CUMPLEAÑOS (8 HORAS)'),
        ('ME', 'ME - MAÑANA EDUCATIVA (6 HRS)'),
        ('TE', 'TE - TARDE EDUCATIVA (6 HRS)'),
    ]

    JORNADA_HORAS = {
        'N': 12,
        'C': 12,
        'C1': 11,
        'C2': 11,
        'M': 6,
        'T': 6,
        'T9': 8,
        'H': 8,
        'D': 0,
        'L': 0,
        'LC': 8,
        'ME': 6,
        'TE': 6,
    }

    JORNADA_DICT = dict(JORNADA_CHOICES)

    # Datos Principales
    solicitante = models.ForeignKey(User, on_delete=models.CASCADE, related_name='solicitudes_enviadas', verbose_name="Nombre del Solicitante")
    reemplazo = models.ForeignKey(User, on_delete=models.CASCADE, related_name='solicitudes_recibidas', verbose_name="Nombre del Reemplazo 1")
    reemplazo_2 = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='solicitudes_recibidas_2', blank=True, null=True, verbose_name="Nombre del Reemplazo 2 (Devolución 2)")

    # Turno a Relevar
    fecha_turno = models.DateField(verbose_name="Fecha de Turno")
    jornada = models.CharField(max_length=50, choices=JORNADA_CHOICES, default='M', verbose_name="Jornada")

    # Devolución 1
    fecha_devolucion = models.DateField(verbose_name="Fecha de Devolución 1")
    jornada_devolucion = models.CharField(max_length=50, choices=JORNADA_CHOICES, default='M', verbose_name="Jornada de Devolución 1")

    # Devolución 2 (Opcional: para devolver 12 horas en 2 turnos de 6 horas)
    fecha_devolucion_2 = models.DateField(blank=True, null=True, verbose_name="Fecha de Devolución 2")
    jornada_devolucion_2 = models.CharField(max_length=50, choices=JORNADA_CHOICES, blank=True, null=True, verbose_name="Jornada de Devolución 2")

    # Información Adicional
    motivo = models.TextField(blank=True, null=True, verbose_name="Motivo u Observaciones")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE', verbose_name="Estado")

    fecha_solicitud = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = "Solicitud de Cambio de Turno"
        verbose_name_plural = "Solicitudes de Cambio de Turnos"

    def __str__(self):
        return f"Solicitud de {self.solicitante.first_name or self.solicitante.username} a {self.reemplazo.first_name or self.reemplazo.username} ({self.estado})"

    def get_horas_turno(self):
        return self.JORNADA_HORAS.get(self.jornada, 0)

    def get_horas_devolucion(self):
        h1 = self.JORNADA_HORAS.get(self.jornada_devolucion, 0)
        h2 = self.JORNADA_HORAS.get(self.jornada_devolucion_2, 0) if self.jornada_devolucion_2 else 0
        return h1 + h2

    def es_equivalente_tiempo(self):
        return self.get_horas_turno() == self.get_horas_devolucion()


class TurnoArea(models.Model):
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='turnos_programados', verbose_name="Área")
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='turnos_asignados', verbose_name="Empleado")
    fecha = models.DateField(verbose_name="Fecha del Turno")
    jornada = models.CharField(max_length=50, choices=SolicitudCambio.JORNADA_CHOICES, verbose_name="Jornada")
    creado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='turnos_creados', verbose_name="Programado Por")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")

    class Meta:
        ordering = ['fecha', 'usuario']
        verbose_name = "Turno de Área"
        verbose_name_plural = "Turnos de Área"

    def __str__(self):
        return f"{self.usuario.first_name or self.usuario.username} - {self.area.nombre} ({self.fecha} [{self.jornada}])"
