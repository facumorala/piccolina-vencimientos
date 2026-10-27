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


# ═══ 3. Modo "ambos" = Telegram como siempre + push (hasta nov-2026) ═══════════

def _env_valor(valor):
    """Deja AVISOS_POR_PORTAL en `valor` (None = sin la variable)."""
    entorno = {k: v for k, v in os.environ.items() if k != "AVISOS_POR_PORTAL"}
    entorno["TELEGRAM_CHAT_ID_FACU"] = "123"
    if valor is not None:
        entorno["AVISOS_POR_PORTAL"] = valor
    return mock.patch.dict(os.environ, entorno, clear=True)


def _ids_con_valor(valor):
    """Qué tareas programa el scheduler con ese valor del interruptor."""
    falso = mock.MagicMock()
    with _env_valor(valor), \
            mock.patch.object(scheduler, "BackgroundScheduler", return_value=falso), \
            mock.patch.object(scheduler, "_scheduler", None):
        scheduler.start_scheduler()
    return [c.kwargs["id"] for c in falso.add_job.call_args_list]


class TestInterruptorDosCanales(unittest.TestCase):

    def test_valores(self):
        # (valor, push, telegram, solo_portal)
        casos = [
            (None, False, True, False), ("", False, True, False), ("0", False, True, False),
            ("1", True, False, True), ("ambos", True, True, False), (" AMBOS ", True, True, False),
        ]
        for valor, push, tg, solo in casos:
            with self.subTest(valor=valor), _env_valor(valor):
                self.assertIs(avisos_portal.push_activo(), push)
                self.assertIs(avisos_portal.telegram_activo(), tg)
                self.assertIs(avisos_portal.avisos_por_portal(), solo)


class TestAmbos(BaseAvisos):

    def pendientes_de(self, columna):
        db = self.Session()
        n = db.query(LogActividad).filter(getattr(LogActividad, columna).is_(False)).count()
        db.close()
        return n

    def test_diario_manda_telegram_hoy_y_3_dias_y_push_de_hoy(self):
        self.vto("IVA oct", 1000)
        self.vto("IIBB oct", 500, dias=3)
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar") as portal:
            scheduler._job_avisos_vencimientos()
        textos = [c.args[1] for c in tg.call_args_list]
        self.assertEqual(len(textos), 2)
        self.assertIn("Vence HOY", textos[0])
        self.assertIn("En 3 días vence", textos[1])
        portal.assert_called_once()
        self.assertEqual(portal.call_args.args[1], "⏰ Vence hoy: IVA oct — $1.000")

    def test_diario_si_falla_el_portal_igual_sale_telegram(self):
        self.vto("IVA oct", 1000)
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", side_effect=RuntimeError("caído")):
            scheduler._job_avisos_vencimientos()
        self.assertEqual(tg.call_count, 1)

    def test_resumen_lunes_sale_por_telegram(self):
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "enviar_resumen_semanal_facu") as resumen:
            scheduler._job_resumen_lunes()
        resumen.assert_called_once()

    def test_scheduler_mismos_jobs_que_siempre_sin_duplicar(self):
        ids = _ids_con_valor("ambos")
        self.assertEqual(sorted(ids), ["avisos_actividad", "avisos_diarios", "resumen_lunes"])

    def test_scheduler_1_y_vacio_no_cambian(self):
        self.assertEqual(sorted(_ids_con_valor("1")), ["avisos_actividad", "avisos_diarios"])
        self.assertEqual(sorted(_ids_con_valor(None)),
                         ["avisos_actividad", "avisos_diarios", "resumen_lunes"])

    def test_actividad_por_los_dos_y_cada_uno_marca_lo_suyo(self):
        self.log("Cargó IVA")
        self.log("Pagó IIBB")
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}) as portal:
            scheduler._job_avisos_actividad()
            # Segunda tanda: no se repite nada por ningún lado
            scheduler._job_avisos_actividad()
        self.assertEqual(tg.call_count, 2)       # uno por log, como siempre
        portal.assert_called_once()              # un push por tanda
        self.assertEqual(self.pendientes_de("notificado_telegram"), 0)
        self.assertEqual(self.pendientes_de("notificado_portal"), 0)

    def test_portal_caido_no_repite_telegram_y_reintenta_push(self):
        self.log("Cargó IVA")
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value=None):
            scheduler._job_avisos_actividad()
        self.assertEqual(tg.call_count, 1)
        self.assertEqual(self.pendientes_de("notificado_telegram"), 0)
        self.assertEqual(self.pendientes_de("notificado_portal"), 1)
        # Vuelve el Portal: sale el push, Telegram no repite
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg2, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}) as portal2:
            scheduler._job_avisos_actividad()
        tg2.assert_not_called()
        portal2.assert_called_once()
        self.assertEqual(self.pendientes_de("notificado_portal"), 0)

    def test_telegram_caido_no_repite_push_y_reintenta_telegram(self):
        self.log("Cargó IVA")
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=False), \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}):
            scheduler._job_avisos_actividad()
        self.assertEqual(self.pendientes_de("notificado_telegram"), 1)
        self.assertEqual(self.pendientes_de("notificado_portal"), 0)
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}) as portal:
            scheduler._job_avisos_actividad()
        tg.assert_called_once()
        portal.assert_not_called()

    def test_lo_avisado_sin_interruptor_no_se_repite_al_pasar_a_ambos(self):
        self.log("Cargó IVA")
        with _env_valor(None), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True), \
                mock.patch.object(avisos_portal, "avisar") as portal:
            scheduler._job_avisos_actividad()
        portal.assert_not_called()
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}) as portal:
            scheduler._job_avisos_actividad()
        tg.assert_not_called()
        portal.assert_not_called()

    def test_lo_avisado_con_1_no_se_repite_al_pasar_a_ambos(self):
        self.log("Cargó IVA")
        with _env_valor("1"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje") as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}):
            scheduler._job_avisos_actividad()
        tg.assert_not_called()
        with _env_valor("ambos"), \
                mock.patch.object(telegram_bot, "_enviar_mensaje", return_value=True) as tg, \
                mock.patch.object(avisos_portal, "avisar", return_value={"ok": True}) as portal:
            scheduler._job_avisos_actividad()
        tg.assert_not_called()
        portal.assert_not_called()


class TestMigracionMarcaPortal(unittest.TestCase):
    """La columna nueva arranca copiando la marca de Telegram (base vieja)."""

    def test_backfill(self):
        from sqlalchemy import text
        ruta = os.path.join(_TMP_DIR, "migracion_marca.db").replace("\\", "/")
        if os.path.exists(ruta):
            os.remove(ruta)
        engine = get_engine(f"sqlite:///{ruta}")
        # Base "vieja": log_actividad sin la columna notificado_portal
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE log_actividad (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                "accion VARCHAR(40) NOT NULL, vencimiento_id INTEGER, plan_id INTEGER, "
                "descripcion VARCHAR(400) NOT NULL, notificado_telegram BOOLEAN NOT NULL, "
                "creado_en DATETIME NOT NULL)"))
            conn.execute(text(
                "INSERT INTO log_actividad (id, user_id, accion, descripcion, notificado_telegram, creado_en) "
                "VALUES (1, 1, 'crear', 'viejo avisado', 1, '2026-10-01'), "
                "(2, 1, 'crear', 'viejo pendiente', 0, '2026-10-01')"))
        init_db(engine)
        with engine.connect() as conn:
            filas = dict(conn.execute(text(
                "SELECT id, notificado_portal FROM log_actividad ORDER BY id")).fetchall())
        engine.dispose()
        self.assertTrue(bool(filas[1]))
        self.assertFalse(bool(filas[2]))


if __name__ == "__main__":
    unittest.main()
