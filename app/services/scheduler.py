"""
Scheduler de avisos Telegram + generador mensual.

Jobs:
1. Día 1 de cada mes a las 00:01 → generador.replicar_recurrentes()
2. Lunes a las 10:00 → resumen semanal por Telegram
3. Diario a las 09:00 → checkear vtos a 3 días + del día
4. Cada 5 minutos → enviar pendientes de avisos de actividad (Agus cargó X)

Usa APScheduler con BackgroundScheduler en proceso (no requiere worker separado).
"""
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from extensions import AR_TZ


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

    # 2. Resumen semanal: lunes a las 10
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
    print(f"[scheduler] Iniciado. Resumen lun {h_resumen}:00, Avisos {h_aviso_3d}:00.", flush=True)


# ─── Jobs ─────────────────────────────────────────────────────────────────────

def _job_resumen_lunes():
    """Lunes 10:00 — manda a Facu el resumen semanal."""
    try:
        from services.telegram_bot import enviar_resumen_semanal_facu
        enviar_resumen_semanal_facu()
    except Exception as e:
        print(f"[scheduler] Error en resumen_lunes: {e}", flush=True)


def _job_avisos_vencimientos():
    """Diario 9:00 — chequea vtos a 3 días y vtos del día."""
    try:
        from services.telegram_bot import enviar_avisos_vencimientos
        enviar_avisos_vencimientos()
    except Exception as e:
        print(f"[scheduler] Error en avisos_vencimientos: {e}", flush=True)


def _job_avisos_actividad():
    """Cada 5 min — manda los avisos pendientes (LogActividad sin notificar)."""
    try:
        from services.telegram_bot import enviar_avisos_actividad_pendientes
        enviar_avisos_actividad_pendientes()
    except Exception as e:
        print(f"[scheduler] Error en avisos_actividad: {e}", flush=True)
