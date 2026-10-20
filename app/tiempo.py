"""
Helpers de fecha y hora en zona horaria Argentina (Buenos Aires, UTC-3).

El servidor de Railway (y su Postgres) corren en UTC, 3 horas adelante.
Si se usa la hora "cruda" del servidor, desde las 21 h de Buenos Aires
todo queda anotado con la fecha del día siguiente. Por eso TODA fecha u
hora "de ahora" sale de acá.

Uso:
    from tiempo import ahora_ar, hoy_ar
    user.last_login = ahora_ar()
    hoy = hoy_ar()

Lo vigila tests/test_zona_horaria.py.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Necesita el paquete `tzdata` (en requirements.txt): en Windows y en
# algunas imágenes de Railway no viene la base de zonas horarias.
ZONA_AR = ZoneInfo("America/Argentina/Buenos_Aires")


def ahora_ar() -> datetime:
    """
    Fecha y hora actual de Buenos Aires, sin tzinfo (naive).

    Naive porque las columnas DateTime de la base no guardan zona y
    mezclar naive con aware rompe las comparaciones de SQLAlchemy.
    """
    return datetime.now(ZONA_AR).replace(tzinfo=None)


def hoy_ar() -> date:
    """Fecha de hoy según el reloj de Buenos Aires."""
    return ahora_ar().date()
