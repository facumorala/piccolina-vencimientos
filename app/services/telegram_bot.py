"""
Bot de Telegram para Vencimientos.

Funcionalidades:
1. Recordatorios automáticos (disparados por scheduler):
   - Lunes 10 AM: resumen semanal agrupado por día.
   - 3 días antes y día del vto: aviso individual.
   - Avisos de actividad de Agus (cada vez que carga/edita/paga algo).
2. Comandos consultables por Facu:
   - /que_vence
   - /total_mes
   - /vencidos

Implementación: skeleton inicial. La conexión asyncio + polling se hace cuando
TELEGRAM_BOT_TOKEN está seteado.
"""
import os
import asyncio
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func

from extensions import SessionFactory, today_ar
from models import Vencimiento, LogActividad


MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _get_facu_chat_id() -> str | None:
    return os.getenv("TELEGRAM_CHAT_ID_FACU") or None


def _get_token() -> str | None:
    return os.getenv("TELEGRAM_BOT_TOKEN") or None


# ─── Envío de mensajes ────────────────────────────────────────────────────────

def _enviar_mensaje(chat_id: str, texto: str) -> bool:
    """Envía un mensaje a un chat por la API HTTP de Telegram. Sync (sin asyncio)."""
    token = _get_token()
    if not token or not chat_id:
        print(f"[telegram] Token o chat_id faltante, no se envió.", flush=True)
        return False
    try:
        import urllib.request
        import urllib.parse
        import json

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": texto,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = json.loads(resp.read().decode())
            return j.get("ok", False)
    except Exception as e:
        print(f"[telegram] Error enviando: {e}", flush=True)
        return False


# ─── Resumen semanal (lunes 10 AM) ────────────────────────────────────────────

def enviar_resumen_semanal_facu():
    """Manda a Facu los vencimientos de la semana en curso, agrupados por día."""
    chat_id = _get_facu_chat_id()
    if not chat_id:
        return

    db = SessionFactory()
    try:
        hoy = today_ar()
        fin = hoy + timedelta(days=7)

        vtos = db.query(Vencimiento).filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.fecha_vencimiento >= hoy,
            Vencimiento.fecha_vencimiento <= fin,
        ).order_by(Vencimiento.fecha_vencimiento.asc()).all()

        if not vtos:
            _enviar_mensaje(chat_id, "📍 <b>Semana tranquila</b>\nNo hay vencimientos en los próximos 7 días.")
            return

        # Agrupar por día
        por_dia = {}
        total = Decimal("0")
        for v in vtos:
            por_dia.setdefault(v.fecha_vencimiento, []).append(v)
            if v.monto:
                total += v.monto

        lineas = ["📍 <b>Esta semana vence:</b>", ""]
        for fecha in sorted(por_dia):
            dia_str = f"{DIAS_ES[fecha.weekday()]} {fecha.day:02d}/{fecha.month:02d}"
            for v in por_dia[fecha]:
                est = " (estimado)" if v.monto_estimado else ""
                monto_str = _fmt_money(v.monto) if v.monto else "?"
                lineas.append(f"{dia_str} — {v.tipo} {monto_str}{est}")
        lineas.append("─" * 20)
        lineas.append(f"<b>Total: {_fmt_money(total)}</b>")
        _enviar_mensaje(chat_id, "\n".join(lineas))
    finally:
        db.close()


# ─── Avisos individuales (3 días antes + día del vto) ─────────────────────────

def enviar_avisos_vencimientos():
    """Chequea vtos a 3 días y vtos del día. Manda aviso individual a Facu."""
    chat_id = _get_facu_chat_id()
    if not chat_id:
        return

    db = SessionFactory()
    try:
        hoy = today_ar()
        en_3 = hoy + timedelta(days=3)

        # Hoy
        hoy_vtos = db.query(Vencimiento).filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.fecha_vencimiento == hoy,
        ).all()
        for v in hoy_vtos:
            est = " (estimado)" if v.monto_estimado else ""
            _enviar_mensaje(chat_id, f"⏰ <b>Vence HOY:</b> {v.concepto} — {_fmt_money(v.monto)}{est}")

        # En 3 días
        tres_vtos = db.query(Vencimiento).filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.fecha_vencimiento == en_3,
        ).all()
        for v in tres_vtos:
            est = " (estimado)" if v.monto_estimado else ""
            _enviar_mensaje(chat_id, f"📅 <b>En 3 días vence:</b> {v.concepto} — {_fmt_money(v.monto)}{est}")
    finally:
        db.close()


# ─── Avisos de actividad de Agus ──────────────────────────────────────────────

def enviar_avisos_actividad_pendientes():
    """Manda a Facu los LogActividad sin notificar."""
    chat_id = _get_facu_chat_id()
    if not chat_id:
        return

    db = SessionFactory()
    try:
        logs = db.query(LogActividad).filter(
            LogActividad.notificado_telegram.is_(False)
        ).order_by(LogActividad.creado_en.asc()).limit(20).all()
        for log in logs:
            ok = _enviar_mensaje(chat_id, f"👤 Agus: {log.descripcion}")
            if ok:
                log.notificado_telegram = True
        db.commit()
    finally:
        db.close()


# ─── Bot polling (comandos) ───────────────────────────────────────────────────

def run_bot_polling():
    """Arranca el bot en modo polling para responder a comandos."""
    token = _get_token()
    if not token:
        return
    try:
        from telegram.ext import ApplicationBuilder, CommandHandler
        from telegram import Update

        async def cmd_que_vence(update: Update, context):
            db = SessionFactory()
            try:
                hoy = today_ar()
                fin = hoy + timedelta(days=7)
                vtos = db.query(Vencimiento).filter(
                    Vencimiento.pagado.is_(False),
                    Vencimiento.fecha_vencimiento >= hoy,
                    Vencimiento.fecha_vencimiento <= fin,
                ).order_by(Vencimiento.fecha_vencimiento.asc()).all()
                if not vtos:
                    await update.message.reply_text("Esta semana no vence nada.")
                    return
                lineas = ["📍 Próximos 7 días:"]
                for v in vtos:
                    f = v.fecha_vencimiento
                    lineas.append(f"{f.day:02d}/{f.month:02d} — {v.tipo} {_fmt_money(v.monto)}")
                await update.message.reply_text("\n".join(lineas))
            finally:
                db.close()

        async def cmd_total_mes(update: Update, context):
            db = SessionFactory()
            try:
                hoy = today_ar()
                primer = hoy.replace(day=1)
                if primer.month == 12:
                    sig = primer.replace(year=primer.year + 1, month=1)
                else:
                    sig = primer.replace(month=primer.month + 1)
                pendiente = db.query(func.coalesce(func.sum(Vencimiento.monto), 0)).filter(
                    Vencimiento.pagado.is_(False),
                    Vencimiento.fecha_vencimiento >= primer,
                    Vencimiento.fecha_vencimiento < sig,
                ).scalar() or 0
                pagado = db.query(func.coalesce(func.sum(Vencimiento.monto), 0)).filter(
                    Vencimiento.pagado.is_(True),
                    Vencimiento.fecha_pago >= primer,
                    Vencimiento.fecha_pago < sig,
                ).scalar() or 0
                mes_str = f"{MESES_ES[hoy.month].capitalize()}"
                await update.message.reply_text(
                    f"📊 {mes_str}:\nPendiente: {_fmt_money(Decimal(str(pendiente)))}\n"
                    f"Pagado: {_fmt_money(Decimal(str(pagado)))}"
                )
            finally:
                db.close()

        async def cmd_vencidos(update: Update, context):
            db = SessionFactory()
            try:
                hoy = today_ar()
                vtos = db.query(Vencimiento).filter(
                    Vencimiento.pagado.is_(False),
                    Vencimiento.fecha_vencimiento < hoy,
                ).order_by(Vencimiento.fecha_vencimiento.asc()).limit(20).all()
                if not vtos:
                    await update.message.reply_text("Ningún vencimiento atrasado. ✅")
                    return
                lineas = ["🔴 Vencidos:"]
                for v in vtos:
                    dias = (hoy - v.fecha_vencimiento).days
                    lineas.append(f"-{dias}d — {v.concepto} {_fmt_money(v.monto)}")
                await update.message.reply_text("\n".join(lineas))
            finally:
                db.close()

        app = ApplicationBuilder().token(token).build()
        app.add_handler(CommandHandler("que_vence", cmd_que_vence))
        app.add_handler(CommandHandler("total_mes", cmd_total_mes))
        app.add_handler(CommandHandler("vencidos", cmd_vencidos))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        print(f"[bot] Error en polling: {e}", flush=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_money(value) -> str:
    if value is None:
        return "?"
    try:
        n = Decimal(str(value))
    except Exception:
        return str(value)
    entero, _, dec = f"{abs(n):.2f}".partition(".")
    grupos = [entero[::-1][i:i+3] for i in range(0, len(entero), 3)]
    return f"${' ' if n >= 0 else '-'}{'.'.join(grupos)[::-1]},{dec}"
