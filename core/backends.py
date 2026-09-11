import os
import pyodbc
import json
import redis
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from .models import Profile

NIVELES_DICT = {
    0: 'TODOS LOS PRIVILEGIOS', 1: 'CONTRATACION', -1: 'NINGUNO', 10: 'COORDINADOR CONTABILIDAD',
    11: 'AUX. FARMACIA', 12: 'AUDITOR DE CUENTAS MEDICAS', 13: 'AUDITOR CONCURRENTE',
    14: 'AUDITOR EXTERNO', 15: 'ARCHIVO', 16: 'CITAS', 17: 'COORDINADOR CONSULTA EXTERNA',
    18: 'PROGRAMACION DE CIRUGIA', 19: 'INSTRUMENTADORAS', 2: 'COORD.FACTURACION',
    20: 'RADICACION', 21: 'CAJA', 22: 'CARTERA', 23: 'ESTADISTICA', 24: 'RECEPCION',
    25: 'SEGURIDAD DEL PACIENTE', 26: 'Creación de Canastas', 27: 'AUXILIAR DE ENFERMERIA CIRUGIA',
    28: 'ENFERMERA TRIAGE', 29: 'AUX ENFERMERIA CITAS', 3: 'AUXILIAR DE FACTURACION',
    30: 'Solicitud Consumo Directo', 31: 'SALUD PUBLICA', 36: 'AUX.FACTURACION Y AUDITOR ',
    38: 'AUXILIAR ENFERMERIA GASTRO', 39: 'CONTABILIDAD', 4: 'MEDICO ESPECIALISTA',
    40: 'TESORERIA', 41: 'CITAS TALENTO HUMANO', 42: 'DIRECTORA FINANCIERA',
    44: 'MEDICOS ESP NO COPIA NI PEGA', 45: 'AUX. DE ADMISIONES', 48: 'CORDINADOR URGENCIAS',
    5: 'MEDICO GENERAL', 50: 'ACLARAR HISTORIAS CLINICAS', 51: 'DIRECCION MEDICA',
    52: 'SOPORTE FARMACIA', 53: 'LABORATORIO', 54: 'PROGRAMACION DE CIRUGIA 2',
    55: 'ENFERMERA LÍDER PROA', 56: 'AUDITOR Y MEDICO GENERAL', 57: 'AUXILIAR DE PATOLOGÍA',
    58: 'CITAS Y TERAPEUTA', 6: 'AUXILIAR DE ENFERMERIA', 60: 'TODOS LOS PRIVILEGIOS EDITADO',
    61: 'AUXILIAR DE SISTEMAS', 62: 'ADMISIONES CONSULTA EXTERNA', 63: 'LIDER DE ADMISION Y AUTORIZACION',
    7: 'ENFERMERIA', 70: 'rx', 71: 'LIDER DE IMAGENOLOGIA', 72: 'ADMINISTRADOR SISTEMAS',
    73: 'CONFIERMA CITAS Y QX', 74: 'AUXILIAR ENFERMERIA EN PEDIATRIA', 75: 'AUXILIAR DE TRANSCRIPCION',
    76: 'SERVCIO VARIOS ', 77: 'CALIDAD', 78: 'CITAS IMAGENOLOGIA', 79: 'SEGURIDAD DEL PACIENTE',
    8: 'TERAPEUTA', 80: 'COORDINACION ENFERMERIA', 81: 'COORDINACIÓN FONOAUDIOLOGIA',
    82: 'FISIOTERAUPEUTA ', 83: 'MARIA', 84: 'nivel de prueba', 85: 'SEGURIDAD PRIVADA',
    86: 'FISIOTERAPEUTA HOSPITALIZACION', 87: 'PSICÓLOGA CLÍNICA', 88: 'AUXILIAR DE ENFERMERIA ADMINISTRATIVA',
    89: 'NUTRICIONISTA', 9: 'SUPER FACTURADOR', 90: 'Farmacovigilancia', 91: 'ESPECIALISTAS IMAGENEOLOGIA',
    92: 'ENFERMERIA UCIN', 93: 'AUDITOR DE CUENTAS', 94: 'MANTENIMIENTO Y BIOMEDICOS',
    95: 'AUDITORIA PGP', 96: 'AUXILIAR ADMINISTRATIVO'
}

def fetch_and_sync_sisma_user(username, password=None):
    """
    Sincroniza los datos del usuario (Nombre Completo y Cargo) desde Redis e Intranet / SismaSalud SQL Server.
    """
    cedula = str(username).strip()
    nombre = ""
    cargo = ""
    email = ""

    # 1. Consultar Redis (patrones sisma:contrato:{cedula} y user_sisma:{cedula})
    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", 6379))
        r = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True, socket_connect_timeout=0.2)

        # 1a. Datos de contrato en Redis
        val_contrato = r.get(f"sisma:contrato:{cedula}")
        if val_contrato:
            data_c = json.loads(val_contrato)
            nombre = data_c.get("nombre_completo", "")
            cargo = data_c.get("cargo_actual", "")

        # 1b. Datos de usuario en Redis
        val_user = r.get(f"user_sisma:{cedula}")
        if val_user:
            data_u = json.loads(val_user)
            if not nombre:
                nombre = data_u.get("nombre", "")
            if not email:
                email = data_u.get("email", "")
            nivel = data_u.get("nivel")
            if not cargo and nivel is not None:
                try:
                    cargo = NIVELES_DICT.get(int(nivel), f"Rol {nivel}")
                except (ValueError, TypeError):
                    cargo = f"Rol {nivel}"
    except Exception as e:
        print(f"Error consultando Redis para {cedula}: {e}")

    # 2. Consultar SQL Server SismaSalud si falta información
    if not nombre or not cargo:
        server = os.getenv("DB_SERVER", "GLSALUD")
        database = os.getenv("DB_DATABASE", "SismaSalud")
        db_user = os.getenv("DB_USER", "ADMIN")
        db_password = os.getenv("DB_PASSWORD", "123")
        try:
            conn = pyodbc.connect(
                f'DRIVER={{ODBC Driver 17 for SQL Server}};'
                f'SERVER={server};'
                f'DATABASE={database};'
                f'UID={db_user};'
                f'PWD={db_password};'
                'TrustServerCertificate=yes;',
                timeout=2
            )
            cursor = conn.cursor()
            sql = """
            SELECT TOP 1 cedula, nombre, email, status, nivel
            FROM [SismaSalud].[dbo].[usuario] 
            WHERE cedula = ?
            """
            cursor.execute(sql, (cedula,))
            user_row = cursor.fetchone()
            conn.close()

            if user_row:
                if not nombre:
                    nombre = user_row.nombre or ""
                if not email:
                    email = user_row.email or ""
                if not cargo and user_row.nivel is not None:
                    try:
                        cargo = NIVELES_DICT.get(int(user_row.nivel), f"Rol {user_row.nivel}")
                    except (ValueError, TypeError):
                        cargo = f"Rol {user_row.nivel}"
        except Exception as e:
            print(f"Error consultando SQL Server para {cedula}: {e}")

    # 3. Actualizar o crear usuario y perfil en Django
    user, created = User.objects.get_or_create(username=cedula)
    if password:
        user.set_password(password)

    if nombre and user.first_name != nombre[:150]:
        user.first_name = nombre[:150]
    if email and user.email != email:
        user.email = email
    user.save()

    profile, _ = Profile.objects.get_or_create(user=user)
    if cargo and profile.cargo != cargo:
        profile.cargo = cargo
        profile.save()

    return user, profile


class SismaAuthBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or username != password:
            return None

        user, profile = fetch_and_sync_sisma_user(username, password)
        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
