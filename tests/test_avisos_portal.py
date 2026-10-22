"""
Avisos push por el Portal (oct-2026) — interruptor AVISOS_POR_PORTAL.

Qué se prueba:
1. Interruptor APAGADO → todo sale por Telegram como siempre (resumen de
   los lunes, aviso de hoy + "en 3 días", actividad de la contadora).
2. Interruptor PRENDIDO (="1") → push por el Portal (se reemplaza `avisar`
   por un simulador, no sale nada de verdad):
   - lo que vence hoy, uno o varios; si no vence nada, no avisa;
   - "en 3 días" y el resumen de los lunes NO avisan;
   - la actividad de la contadora va en UN aviso por tanda y se marca.

Usa una base SQLite temporal (nunca la de producción).

Correr:
    .venv/Scripts/python -m pytest tests/test_avisos_portal.py -q
"""
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(RAIZ, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Si extensions todavía no se importó, que arranque con una base temporal
# (así nunca toca otra base al importarse).
_TMP_DIR = tempfile.mkdtemp(prefix="vtos_test_")
if "extensions" not in sys.modules:
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP_DIR, "arranque.db").replace("\\", "/")

from models import get_engine, init_db, Vencimiento, LogActividad, User, Plan  # noqa: E402
from extensions import today_ar  # noqa: E402
from services import avisos_portal, scheduler, telegram_bot  # noqa: E402


class BaseAvisos(unittest.TestCase):
    """Base SQLite nueva por prueba, enchufada a los dos módulos de avisos."""

    def setUp(self):
        ruta = os.path.join(_TMP_DIR, f"{self._testMethodName}.db").replace("\\", "/")
        if os.path.exists(ruta):
            os.remove(ruta)
        self.engine = get_engine(f"sqlite:///{ruta}")
        self.Session = init_db(self.engine)
        for modulo in (avisos_portal, telegram_bot):
            p = mock.patch.object(modulo, "SessionFactory", self.Session)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.engine.dispose)
        self.hoy = today_ar()

    # ─── Helpers para cargar datos ─────────────────────────────────────────
    def vto(self, concepto, monto, dias=0, pagado=False, estimado=False, **extra):
        db = self.Session()
        v = Vencimiento(categoria="impuestos", tipo="IVA", concepto=concepto,
                        monto=Decimal(str(monto)) if monto is not None else None,
                        monto_estimado=estimado, pagado=pagado,
                        fecha_vencimiento=self.hoy + timedelta(days=dias), **extra)
        db.add(v)
        db.commit()
        db.close()

    def log(self, descripcion, nombre="Agustina Pérez"):
        db = self.Session()
        u = db.query(User).filter_by(name=nombre).first()
        if not u:
            u = User(username=nombre.split()[0].lower(), email=f"{nombre.split()[0].lower()}@x.com",
                     name=nombre, password_hash="x", rol="contadoras")
            db.add(u)
            db.flush()
        db.add(LogActividad(user_id=u.id, accion="crear", descripcion=descripcion))
        db.commit()
        db.close()

    def pendientes(self):
        db = self.Session()
        n = db.query(LogActividad).filter(LogActividad.notificado_telegram.is_(False)).count()
        db.close()
        return n


def _env(prendido):
    """Prende o apaga el interruptor (y deja un chat de Telegram de prueba)."""
    valores = {"TELEGRAM_CHAT_ID_FACU": "123"}
    if prendido:
        valores["AVISOS_POR_PORTAL"] = "1"
    entorno = {k: v for k, v in os.environ.items() if k != "AVISOS_POR_PORTAL"}
    entorno.update(valores)
    return mock.patch.dict(os.environ, entorno, clear=True)


# ═══ 1. Interruptor apagado = Telegram como antes ═════════════════════════════

class TestInterruptorApagado(BaseAvisos):

    def test_diario_manda_hoy_y_3_dias_por_telegram(self):
        self.vto("IVA oct", 1000)
        self.vto("IIBB oct", 500, dias=3)
        with _env(False), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar") as portal:
            scheduler._job_avisos_vencimientos()
        textos = [c.args[1] for c in tg.call_args_list]
        self.assertEqual(len(textos), 2)
        self.assertIn("Vence HOY", textos[0])
        self.assertIn("En 3 días vence", textos[1])
        portal.assert_not_called()

    def test_resumen_lunes_por_telegram(self):
        with _env(False), \
                mock.patch.object(telegram_bot, "enviar_resumen_semanal_facu") as resumen:
            scheduler._job_resumen_lunes()
        resumen.assert_called_once()

    def test_actividad_por_telegram_uno_por_log(self):
        self.log("Cargó IVA")
        self.log("Pagó IIBB")
        with _env(False), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar") as portal:
            scheduler._job_avisos_actividad()
        self.assertEqual(tg.call_count, 2)
        self.assertEqual(self.pendientes(), 0)
        portal.assert_not_called()

    def test_scheduler_registra_el_resumen_de_los_lunes(self):
        ids = _ids_registrados(prendido=False)
        self.assertIn("resumen_lunes", ids)
        self.assertIn("avisos_diarios", ids)
        self.assertIn("avisos_actividad", ids)


# ═══ 2. Interruptor prendido = push por el Portal ═════════════════════════════

class TestVenceHoyPortal(BaseAvisos):

    def correr(self):
        with _env(True), \
                mock.patch.object(avisos_portal, "avisar") as portal, \
                mock.patch.object(telegram_bot, "_enviar_mensaje") as tg:
            scheduler._job_avisos_vencimientos()
        tg.assert_not_called()  # nada por Telegram
        return portal

    def test_uno_solo(self):
        self.vto("IVA oct", "1234567.60")
        portal = self.correr()
        portal.assert_called_once()
        a, k = portal.call_args
        self.assertEqual(a[0], ["facu"])
        self.assertEqual(a[1], "⏰ Vence hoy: IVA oct — $1.234.568")
        self.assertEqual(k["ruta"], f"/vencimientos/?dia={self.hoy.isoformat()}")
        self.assertEqual(k["categoria"], "vencimientos.hoy")
        self.assertEqual(k["clave"], f"vence-hoy-{self.hoy.isoformat()}")

    def test_varios_con_total_y_lista(self):
        self.vto("IVA oct", 1000)
        self.vto("Edenor", 500)
        self.vto("Ya pagado", 9999, pagado=True)   # no cuenta
        self.vto("Mañana", 7777, dias=1)           # no cuenta
        portal = self.correr()
        a, _ = portal.call_args
        self.assertEqual(a[1], "⏰ Hoy vencen 2 cosas · $1.500")
        self.assertIn("IVA oct — $1.000", a[2])
        self.assertIn("Edenor — $500", a[2])
        self.assertNotIn("Ya pagado", a[2])
        self.assertNotIn("Mañana", a[2])

    def test_lista_larga_se_corta(self):
        for i in range(7):
            self.vto(f"Cosa {i}", 100)
        a, _ = self.correr().call_args
        self.assertIn("Hoy vencen 7 cosas · $700", a[1])
        self.assertIn("… y 2 más", a[2])

    def test_cubierto_por_plan_no_cuenta(self):
        db = self.Session()
        plan = Plan(nombre="Plan AFIP", monto_cuota=Decimal("100"), cuotas_totales=6,
                    dia_del_mes=16, fecha_primera_cuota=self.hoy)
        db.add(plan)
        db.commit()
        pid = plan.id
        db.close()
        self.vto("IVA en plan", 1000, plan_id=pid)  # cubierto por el plan (sin nro de cuota)
        self.correr().assert_not_called()

    def test_nada_que_vence_hoy_no_avisa(self):
        self.vto("Ayer", 100, dias=-1)
        self.correr().assert_not_called()

    def test_en_3_dias_no_avisa(self):
        self.vto("IIBB", 500, dias=3)
        self.correr().assert_not_called()


class TestLunesPortal(BaseAvisos):

    def test_resumen_lunes_no_avisa(self):
        self.vto("IVA", 1000, dias=2)
        with _env(True), \
                mock.patch.object(telegram_bot, "_enviar_mensaje") as tg, \
                mock.patch.object(avisos_portal, "avisar") as portal:
            scheduler._job_resumen_lunes()
        tg.assert_not_called()
        portal.assert_not_called()

    def test_scheduler_no_registra_el_resumen_de_los_lunes(self):
        ids = _ids_registrados(prendido=True)
        self.assertNotIn("resumen_lunes", ids)
        self.assertIn("avisos_diarios", ids)
        self.assertIn("avisos_actividad", ids)


class TestActividadPortal(BaseAvisos):

    def correr(self, respuesta={"ok": True}):
        with _env(True), \
                mock.patch.object(avisos_portal, "avisar", return_value=respuesta) as portal, \
                mock.patch.object(telegram_bot, "_enviar_mensaje") as tg:
            scheduler._job_avisos_actividad()
        tg.assert_not_called()
        return portal

    def test_una_accion(self):
        self.log("Cargó IVA oct $1.000")
        portal = self.correr()
        a, k = portal.call_args
        self.assertEqual(a[0], ["facu"])
        self.assertEqual(a[1], "👤 Agustina")
        self.assertEqual(a[2], "Cargó IVA oct $1.000")
        self.assertEqual(k["ruta"], "/vencimientos/")
        self.assertTrue(k["esperar"])
        self.assertEqual(self.pendientes(), 0)

    def test_varias_acciones_un_solo_aviso(self):
        self.log("Cargó IVA")
        self.log("Pagó IIBB")
        self.log("Editó Edenor")
        portal = self.correr()
        portal.assert_called_once()
        a, k = portal.call_args
        self.assertEqual(a[1], "👤 Agustina: 3 movimientos en Vencimientos")
        for texto in ("Cargó IVA", "Pagó IIBB", "Editó Edenor"):
            self.assertIn(texto, a[2])
        self.assertTrue(k["clave"].startswith("vtos-actividad-"))
        self.assertEqual(self.pendientes(), 0)

    def test_si_el_portal_no_contesta_quedan_pendientes(self):
        self.log("Cargó IVA")
        self.correr(respuesta=None)
        self.assertEqual(self.pendientes(), 1)

    def test_sin_actividad_no_avisa(self):
        self.correr().assert_not_called()


def _ids_registrados(prendido):
    """Qué tareas programa el scheduler (sin arrancarlo de verdad)."""
    falso = mock.MagicMock()
    with _env(prendido), \
            mock.patch.object(scheduler, "BackgroundScheduler", return_value=falso), \
            mock.patch.object(scheduler, "_scheduler", None):
        scheduler.start_scheduler()
    return [c.kwargs["id"] for c in falso.add_job.call_args_list]


if __name__ == "__main__":
    unittest.main()
