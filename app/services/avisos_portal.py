"""
Avisos push por el Portal Piccolina (reemplazan a los de Telegram).

Interruptor: variable AVISOS_POR_PORTAL.
- "1"      → SOLO estos avisos push (Telegram no manda recordatorios).
- "ambos"  → los DOS canales (transición hasta noviembre 2026, decisión de
             Facu): Telegram exactamente como antes (hoy + "en 3 días",
             resumen de los lunes, actividad) + estos push.
- sin valor (u otro) → todo sigue exactamente como antes, por el bot de
             Telegram (services/telegram_bot.py).
Las dos preguntas que se hacen son `push_activo()` y `telegram_activo()`.

Con el interruptor prendido quedan DOS avisos, los dos solo para Facu:
1. Diario 09:00 → lo que vence HOY y no está pagado (si no hay nada, no avisa).
2. Cada 5 min  → lo que cargó/editó/pagó la contadora (LogActividad),
   un solo aviso por tanda aunque sean varias acciones.

Con "1" se eliminan el aviso "en 3 días vence" y el resumen de los lunes
(con "ambos" siguen saliendo por Telegram).
Los comandos del bot (/que_vence, etc.) no se tocan.
"""
import os
from decimal import Decimal

from extensions import SessionFactory, today_ar
from models import Vencimiento, LogActividad
# Pasa por la bandeja (campanita): guarda una copia y manda el push.
from bandeja_avisos import avisar


# Usuarios del PORTAL que reciben los avisos de Vencimientos.
DESTINATARIOS = ["facu"]

# Cuántas líneas entran en el cuerpo del aviso antes de cortar con "y N más".
MAX_LINEAS = 5


def _valor_interruptor() -> str:
    """Valor de AVISOS_POR_PORTAL, sin espacios y en minúscula."""
    return os.getenv("AVISOS_POR_PORTAL", "").strip().lower()


def push_activo() -> bool:
    """¿Salen los avisos push por el Portal? Con "1" o con "ambos"."""
    return _valor_interruptor() in ("1", "ambos")


def telegram_activo() -> bool:
    """¿Salen los recordatorios por Telegram como siempre? Sí, salvo con "1"."""
    return _valor_interruptor() != "1"


def avisos_por_portal() -> bool:
    """
    ¿Está en modo SOLO Portal ("1")? Nombre viejo, se deja para no romper
    llamadores. Para decidir qué canal manda, usar push_activo()/telegram_activo().
    """
    return _valor_interruptor() == "1"


# ─── 1. Lo que vence hoy ──────────────────────────────────────────────────────

def avisar_vence_hoy():
    """
    Manda a Facu un push con lo que vence hoy y no está pagado.
    Mismo criterio que el aviso de Telegram de siempre: deja afuera los
    vencimientos "cubiertos" por un plan (esos se pagan por sus cuotas).
    Devuelve cuántos vencimientos entraron en el aviso (0 = no se mandó).
    """
    db = SessionFactory()
    try:
        hoy = today_ar()
        vtos = db.query(Vencimiento).filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.fecha_vencimiento == hoy,
        ).order_by(Vencimiento.monto.desc().nullslast(), Vencimiento.id.asc()).all()

        if not vtos:
            return 0  # nada que vence hoy → no se molesta a Facu

        if len(vtos) == 1:
            v = vtos[0]
            titulo = f"⏰ Vence hoy: {v.concepto} — {_fmt_pesos(v.monto)}"
            cuerpo = "Monto estimado. " if v.monto_estimado else ""
            cuerpo += "Tocá para verlo y pagarlo."
        else:
            total = sum((v.monto or Decimal("0")) for v in vtos)
            titulo = f"⏰ Hoy vencen {len(vtos)} cosas · {_fmt_pesos(total)}"
            lineas = [_linea_vto(v) for v in vtos[:MAX_LINEAS]]
            if len(vtos) > MAX_LINEAS:
                lineas.append(f"… y {len(vtos) - MAX_LINEAS} más")
            cuerpo = "\n".join(lineas)

        # La ruta abre el listado filtrado por el día de hoy (pendientes),
        # que es donde se ven y se pagan.
        avisar(
            DESTINATARIOS, titulo, cuerpo,
            ruta=f"/vencimientos/?dia={hoy.isoformat()}",
            categoria="vencimientos.hoy",
            clave=f"vence-hoy-{hoy.isoformat()}",
        )
        return len(vtos)
    finally:
        db.close()


# ─── 2. Actividad de la contadora ─────────────────────────────────────────────

def avisar_actividad_pendiente(marcar_telegram: bool = True):
    """
    Junta los LogActividad todavía sin avisar por el Portal y manda UN push
    a Facu. Si el Portal confirma que lo recibió, los marca como avisados
    (`notificado_portal`). Si el Portal no contesta, quedan pendientes y se
    reintenta en la próxima tanda.

    `marcar_telegram`: con "1" (solo push) se marca también la de Telegram,
    como hasta ahora. Con "ambos" va False: Telegram lleva su propia cuenta.
    Devuelve cuántos se marcaron como avisados.
    """
    db = SessionFactory()
    try:
        logs = db.query(LogActividad).filter(
            LogActividad.notificado_portal.is_(False)
        ).order_by(LogActividad.creado_en.asc()).limit(20).all()
        if not logs:
            return 0

        quien = _nombre_contadora(logs)
        if len(logs) == 1:
            titulo = f"👤 {quien}"
            cuerpo = logs[0].descripcion
        else:
            titulo = f"👤 {quien}: {len(logs)} movimientos en Vencimientos"
            lineas = [f"• {log.descripcion}" for log in logs[:MAX_LINEAS]]
            if len(logs) > MAX_LINEAS:
                lineas.append(f"… y {len(logs) - MAX_LINEAS} más")
            cuerpo = "\n".join(lineas)

        # esperar=True: hace falta saber si llegó para marcarlos.
        # La clave evita un aviso repetido si se mandó pero no se pudo marcar.
        resp = avisar(
            DESTINATARIOS, titulo, cuerpo,
            ruta="/vencimientos/",
            categoria="vencimientos.actividad",
            clave=f"vtos-actividad-{logs[0].id}-{logs[-1].id}",
            esperar=True,
        )
        if not (resp and resp.get("ok")):
            return 0
        for log in logs:
            log.notificado_portal = True
            if marcar_telegram:
                log.notificado_telegram = True
        db.commit()
        return len(logs)
    finally:
        db.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _nombre_contadora(logs) -> str:
    """Nombre de pila de quien cargó; si fueron varias personas, 'Contadoras'."""
    nombres = set()
    for log in logs:
        nombre = (log.user.name if log.user and log.user.name else "Agus").split()[0]
        nombres.add(nombre)
    return nombres.pop() if len(nombres) == 1 else "Contadoras"


def _linea_vto(v) -> str:
    est = " (estimado)" if v.monto_estimado else ""
    return f"• {v.concepto} — {_fmt_pesos(v.monto)}{est}"


def _fmt_pesos(value) -> str:
    """$1.234.567 (pesos enteros, punto de miles). Sin monto → '$?'."""
    if value is None:
        return "$?"
    try:
        n = int(Decimal(str(value)).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
    except Exception:
        return f"${value}"
    signo = "-" if n < 0 else ""
    return f"{signo}${abs(n):,}".replace(",", ".")
