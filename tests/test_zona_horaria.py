"""
Guardia de la zona horaria (oct-2026). Facu: "siempre hora de Argentina,
Buenos Aires". Railway y su Postgres corren en UTC (3 h más), así que
cualquier hora "cruda" del servidor corre los vencimientos al día
siguiente después de las 21 h ("vence hoy" que figura como "vence mañana",
fecha de pago con el día siguiente, etc.).

Correr:
    .venv/Scripts/python.exe -m unittest tests.test_zona_horaria
    (o .venv/Scripts/python.exe tests/test_zona_horaria.py)

1. ahora_ar()/hoy_ar() dan la hora de Buenos Aires (UTC-3, sin horario
   de verano desde 2009), sin importar la zona de la máquina.
2. Nadie volvió a usar horas "crudas" del servidor (datetime.now() sin
   zona, utcnow, date.today, NOW()/CURRENT_DATE en SQL...) ni del
   navegador (toISOString().slice(0,10), toLocaleDateString sin
   timeZone, new Date().getMonth()...).

Si una línea es legítima (por ejemplo, un epoch para firmar tokens), se
le agrega al final el marcador  hora-ok: motivo  (como comentario).
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(RAIZ, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from tiempo import ahora_ar, hoy_ar  # noqa: E402

# Patrón -> por qué está prohibido.
PROHIBIDO = [
    (r"datetime\.now\(\s*\)", "datetime.now() sin zona → usar ahora_ar()"),
    (r"\.utcnow\(|\butcnow\b", "utcnow → usar ahora_ar()"),
    (r"datetime\.today\(", "datetime.today() → usar ahora_ar()"),
    (r"\bdate\.today\b", "date.today → usar hoy_ar()"),
    (r"time\.localtime\(", "time.localtime → usar ahora_ar()"),
    (r"func\.now\(|server_default", "func.now()/server_default usan el reloj UTC de Postgres"),
    (r"\bNOW\(\)|CURRENT_DATE|CURRENT_TIMESTAMP|\bLOCALTIMESTAMP\b",
     "SQL con la hora de Postgres (UTC) → pasar hoy_ar() como parámetro"),
    (r"toISOString\(\)\s*\.slice\(\s*0\s*,\s*10\s*\)", "toISOString().slice(0,10) da la fecha de Londres"),
    (r"toLocaleDateString\((?![^)]*timeZone)", "toLocaleDateString sin timeZone"),
    (r"new Date\(\)\s*\.\s*get(Date|Day|Month|FullYear|Hours)\(",
     "new Date().getX() usa el reloj del navegador → Intl con timeZone de Buenos Aires"),
]
EXTENSIONES = (".py", ".html", ".js", ".sql")
CARPETAS_IGNORADAS = {".venv", "venv", "node_modules", "__pycache__", ".git", "tests", "_compartido"}
MARCADOR = "hora-ok:"


def _archivos():
    """Todos los .py/.html/.js/.sql del repo (sin entornos ni tests)."""
    for carpeta, subcarpetas, archivos in os.walk(RAIZ):
        subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_IGNORADAS]
        for nombre in archivos:
            if nombre.endswith(EXTENSIONES):
                yield os.path.join(carpeta, nombre)


class ZonaHorariaTest(unittest.TestCase):
    def test_helper_es_utc_menos_3(self):
        esperado = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        self.assertLess(abs((ahora_ar() - esperado).total_seconds()), 60,
                        "ahora_ar() no es UTC-3")
        self.assertIsNone(ahora_ar().tzinfo, "ahora_ar() tiene que ser naive")
        self.assertEqual(hoy_ar(), esperado.date(), "hoy_ar() no es la fecha de Buenos Aires")

    def test_today_ar_usa_el_helper(self):
        from extensions import today_ar
        self.assertEqual(today_ar(), hoy_ar())

    def test_sin_horas_crudas(self):
        fallos = []
        for ruta in _archivos():
            with open(ruta, encoding="utf-8", errors="ignore") as fh:
                for n, linea in enumerate(fh, 1):
                    if MARCADOR in linea:
                        continue
                    for patron, motivo in PROHIBIDO:
                        if re.search(patron, linea):
                            fallos.append(f"{os.path.relpath(ruta, RAIZ)}:{n} {motivo}")
        self.assertFalse(fallos, "Horas crudas del servidor/navegador:\n  " + "\n  ".join(fallos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
