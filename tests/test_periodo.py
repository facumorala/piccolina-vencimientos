"""
Tests del módulo `services.periodo`.

Cubren los formatos canónicos esperados de cada tipo (mensual / bimestral /
trimestral / semestral / anual / personalizado), las fechas desde/hasta
calculadas, la validación de datos faltantes y el parser legacy de strings
viejos.

Correr:
    cd VENCIMIENTOS/sistema-vencimientos
    python -m unittest tests.test_periodo
"""
import os
import sys
import unittest
from datetime import date

# Hace importable el paquete `app/` igual que en producción.
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from services.periodo import construir_periodo, parsear_legacy  # noqa: E402


class TestConstruirPeriodoMensual(unittest.TestCase):
    def test_formato_canonico(self):
        canon, d, h = construir_periodo("mensual", anio=2026, mes=5)
        self.assertEqual(canon, "mayo 2026")
        self.assertEqual(d, date(2026, 5, 1))
        self.assertEqual(h, date(2026, 5, 31))

    def test_febrero_dias_correctos(self):
        canon, d, h = construir_periodo("mensual", anio=2026, mes=2)
        self.assertEqual(canon, "febrero 2026")
        self.assertEqual(h, date(2026, 2, 28))

    def test_falta_mes_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo("mensual", anio=2026)


class TestConstruirPeriodoBimestral(unittest.TestCase):
    def test_marzo_abril(self):
        canon, d, h = construir_periodo("bimestral", anio=2026, mes=3)
        self.assertEqual(canon, "mar-abr 2026")
        self.assertEqual(d, date(2026, 3, 1))
        self.assertEqual(h, date(2026, 4, 30))

    def test_bimestre_cruza_ano(self):
        # diciembre 2025 + enero 2026
        canon, d, h = construir_periodo("bimestral", anio=2025, mes=12)
        self.assertEqual(d, date(2025, 12, 1))
        self.assertEqual(h, date(2026, 1, 31))
        # El canónico debe dejar claro que cruza años
        self.assertIn("2025", canon)
        self.assertIn("2026", canon)


class TestConstruirPeriodoTrimestral(unittest.TestCase):
    def test_enero_marzo(self):
        canon, d, h = construir_periodo("trimestral", anio=2026, mes=1)
        self.assertEqual(canon, "ene-feb-mar 2026")
        self.assertEqual(d, date(2026, 1, 1))
        self.assertEqual(h, date(2026, 3, 31))

    def test_octubre_diciembre(self):
        canon, d, h = construir_periodo("trimestral", anio=2026, mes=10)
        self.assertEqual(canon, "oct-nov-dic 2026")
        self.assertEqual(h, date(2026, 12, 31))


class TestConstruirPeriodoSemestral(unittest.TestCase):
    def test_primer_semestre(self):
        canon, d, h = construir_periodo("semestral", anio=2025, semestre=1)
        self.assertEqual(canon, "ene-jun 2025")
        self.assertEqual(d, date(2025, 1, 1))
        self.assertEqual(h, date(2025, 6, 30))

    def test_segundo_semestre(self):
        canon, d, h = construir_periodo("semestral", anio=2025, semestre=2)
        self.assertEqual(canon, "jul-dic 2025")
        self.assertEqual(d, date(2025, 7, 1))
        self.assertEqual(h, date(2025, 12, 31))

    def test_semestre_invalido_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo("semestral", anio=2025, semestre=3)


class TestConstruirPeriodoAnual(unittest.TestCase):
    def test_2025(self):
        canon, d, h = construir_periodo("anual", anio=2025)
        self.assertEqual(canon, "2025")
        self.assertEqual(d, date(2025, 1, 1))
        self.assertEqual(h, date(2025, 12, 31))

    def test_falta_anio_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo("anual")


class TestConstruirPeriodoPersonalizado(unittest.TestCase):
    def test_rango_mismo_ano(self):
        canon, d, h = construir_periodo(
            "personalizado",
            desde_anio=2026, desde_mes=3,
            hasta_anio=2026, hasta_mes=7,
        )
        self.assertEqual(d, date(2026, 3, 1))
        self.assertEqual(h, date(2026, 7, 31))
        self.assertIn("mar", canon)
        self.assertIn("jul", canon)
        self.assertIn("2026", canon)

    def test_rango_cruza_ano(self):
        canon, d, h = construir_periodo(
            "personalizado",
            desde_anio=2025, desde_mes=11,
            hasta_anio=2026, hasta_mes=2,
        )
        self.assertEqual(d, date(2025, 11, 1))
        self.assertEqual(h, date(2026, 2, 28))
        self.assertIn("2025", canon)
        self.assertIn("2026", canon)

    def test_desde_mayor_que_hasta_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo(
                "personalizado",
                desde_anio=2026, desde_mes=7,
                hasta_anio=2026, hasta_mes=3,
            )

    def test_un_solo_mes_normaliza_a_mensual(self):
        canon, d, h = construir_periodo(
            "personalizado",
            desde_anio=2026, desde_mes=5,
            hasta_anio=2026, hasta_mes=5,
        )
        self.assertEqual(canon, "mayo 2026")
        self.assertEqual(d, date(2026, 5, 1))
        self.assertEqual(h, date(2026, 5, 31))


class TestTipoInvalido(unittest.TestCase):
    def test_tipo_inventado_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo("quincenal", anio=2026, mes=5)

    def test_tipo_vacio_lanza(self):
        with self.assertRaises(ValueError):
            construir_periodo("", anio=2026, mes=5)


class TestParsearLegacy(unittest.TestCase):
    def test_mensual_completo(self):
        d, h = parsear_legacy("mayo 2026")
        self.assertEqual(d, date(2026, 5, 1))
        self.assertEqual(h, date(2026, 5, 31))

    def test_mensual_con_de(self):
        d, h = parsear_legacy("mayo de 2026")
        self.assertEqual(d, date(2026, 5, 1))

    def test_mensual_mayusculas(self):
        d, h = parsear_legacy("MAYO 2026")
        self.assertEqual(d, date(2026, 5, 1))

    def test_mensual_abreviado(self):
        d, h = parsear_legacy("may 2026")
        self.assertEqual(d, date(2026, 5, 1))
        self.assertEqual(h, date(2026, 5, 31))

    def test_anio_corto(self):
        d, h = parsear_legacy("mar 25")
        self.assertEqual(d, date(2025, 3, 1))

    def test_anual(self):
        d, h = parsear_legacy("2025")
        self.assertEqual(d, date(2025, 1, 1))
        self.assertEqual(h, date(2025, 12, 31))

    def test_bimestral(self):
        d, h = parsear_legacy("mar-abr 2026")
        self.assertEqual(d, date(2026, 3, 1))
        self.assertEqual(h, date(2026, 4, 30))

    def test_bimestral_con_slash(self):
        d, h = parsear_legacy("ene/feb 2026")
        self.assertEqual(d, date(2026, 1, 1))
        self.assertEqual(h, date(2026, 2, 28))

    def test_trimestral(self):
        d, h = parsear_legacy("ene-feb-mar 2026")
        self.assertEqual(d, date(2026, 1, 1))
        self.assertEqual(h, date(2026, 3, 31))

    def test_basura_devuelve_none(self):
        d, h = parsear_legacy("cualquier cosa")
        self.assertIsNone(d)
        self.assertIsNone(h)

    def test_vacio_devuelve_none(self):
        self.assertEqual(parsear_legacy(None), (None, None))
        self.assertEqual(parsear_legacy(""), (None, None))


if __name__ == "__main__":
    unittest.main()
