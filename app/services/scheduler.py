"""
Scheduler de avisos Telegram + generador mensual.

Jobs:
1. Día 1 de cada mes a las 00:01 → generador.replicar_recurrentes()
2. Lunes a las 10:00 → resumen semanal por Telegram
3. Diario a las 09:00 → checkear vtos a 3 días + del día
4. Cada 5 minutos → enviar pendientes de avisos de actividad (Agus cargó X)

Usa APScheduler con BackgroundScheduler en proceso (no requiere worker separado).

Interruptor AVISOS_POR_PORTAL="1" (oct-2026): los avisos salen como push por
el Portal Piccolina (services/avisos_portal.py) en vez de Telegram. Con el
interruptor prendido: el diario de las 9 avisa solo lo que vence HOY, la
actividad de la contadora va por push, y NO hay resumen de los lunes ni
aviso de "en 3 días". Sin el interruptor, todo igual que siempre.

AVISOS_POR_PORTAL="ambos" (transición hasta noviembre 2026): los DOS canales.
Telegram exactamente como antes (resumen de los lunes, hoy + "en 3 días",
actividad) + los push del Portal (vence hoy, actividad). Los jobs son los
mismos (mismo id, no se duplican): cada job llama a los dos canales.
La actividad lleva una marca por canal (notificado_telegram /
notificado_portal) para que ninguno repita ni le coma avisos al otro.
"""
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from extensions import AR_TZ
# Interruptor AVISOS_POR_PORTAL (vive en avisos_portal.py, fuente única).
from services.avisos_portal import push_activo, telegram_activo


_scheduler: BackgroundScheduler | None = None


def start_scheduler():
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler(timezone=AR_TZ)

    # Hora para cada trigger (configurable por env vars)
    h_resumen = int(os.getenv("RESUMEN_LUNES_HORA", "10"))
    h_aviso_3d = int(os.getenv("AVISO_3D_HORA", "9"))
    h_aviso_dia = int(os.getenv("AVISO_DIA_HORA", "9"))

    # (El generador mensual automático se quitó el 19-jun-2026: ya no se crean
    # estimaciones a futuro. Ahora Facu trae el mes nuevo con "Repetir mes
    # anterior" desde el listado, cuando quiere.)

    # 2. Resumen semanal: lunes a las 10 (solo con Telegram activo: sin
    #    interruptor o "ambos"; con "1" se eliminó)
    if telegram_activo():
        _scheduler.add_job(
            _job_resumen_lunes,
            trigger=CronTrigger(day_of_week="mon", hour=h_resumen, minute=0),
            id="resumen_lunes",
            replace_existing=True,
        )

    # 3. Avisos individuales: todos los días a las 9
    _scheduler.add_job(
        _job_avisos_vencimientos,
        trigger=CronTrigger(hour=h_aviso_3d, minute=0),
        id="avisos_diarios",
        replace_existing=True,
    )

    # 4. Avisos de actividad de Agus: cada 5 minutos
    _scheduler.add_job(
        _job_avisos_actividad,
        trigger=CronTrigger(minute="*/5"),
        id="avisos_actividad",
        replace_existing=True,
    )

    _scheduler.start()
    if push_activo() and telegram_activo():
        print(f"[scheduler] Iniciado (avisos por Telegram Y por el Portal). "
              f"Resumen lun {h_resumen}:00, Avisos {h_aviso_3d}:00.", flush=True)
    elif push_activo():
        print(f"[scheduler] Iniciado (avisos por el Portal). Vence hoy {h_aviso_3d}:00.", flush=True)
    else:
        print(f"[scheduler] Iniciado. Resumen lun {h_resumen}:00, Avisos {h_aviso_3d}:00.", flush=True)


# ─── Jobs ─────────────────────────────────────────────────────────────────────

def _job_resumen_lunes():
    """Lunes 10:00 — manda a Facu el resumen semanal (solo con Telegram)."""
    if not telegram_activo():
        return  # con "1" (solo Portal) el resumen de los lunes se eliminó
    try:
        from services.telegram_bot import enviar_resumen_semanal_facu
        enviar_resumen_semanal_facu()
    except Exception as e:
        print(f"[scheduler] Error en resumen_lunes: {e}", flush=True)


def _job_avisos_vencimientos():
    """Diario 9:00 — Portal: push con lo que vence hoy. Telegram: hoy + a 3 días.
    Con "ambos" salen los dos (cada canal en su try: si uno falla, el otro sale)."""
    if push_activo():
        try:
            from services.avisos_portal import avisar_vence_hoy
            avisar_vence_hoy()
        except Exception as e:
            print(f"[scheduler] Error en avisos_vencimientos (Portal): {e}", flush=True)
    if telegram_activo():
        try:
            from services.telegram_bot import enviar_avisos_vencimientos
            enviar_avisos_vencimientos()
        except Exception as e:
            print(f"[scheduler] Error en avisos_vencimientos: {e}", flush=True)


def _job_avisos_actividad():
    """Cada 5 min — manda los avisos pendientes (LogActividad sin notificar).
    Con "ambos" sale por los dos, y cada canal marca SOLO su propia marca;
    con un solo canal, el que avisa marca las dos (como hasta ahora)."""
    push = push_activo()
    telegram = telegram_activo()
    if push:
        try:
            from services.avisos_portal import avisar_actividad_pendiente
            avisar_actividad_pendiente(marcar_telegram=not telegram)
        except Exception as e:
            print(f"[scheduler] Error en avisos_actividad (Portal): {e}", flush=True)
    if telegram:
        try:
            from services.telegram_bot import enviar_avisos_actividad_pendientes
            enviar_avisos_actividad_pendientes(marcar_portal=not push)
        except Exception as e:
            print(f"[scheduler] Error en avisos_actividad: {e}", flush=True)
