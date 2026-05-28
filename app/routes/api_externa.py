"""
Endpoints de API para integraciones externas (otros dashboards de Piccolina).

Protegidos con un token compartido (header `X-API-Token`) que se setea
en Railway tanto en COMPRAS como acá. Sin sesión Flask, sin login web.

Hoy el único caller es el OCR de mail de COMPRAS: cuando llega una factura
de un proveedor de servicios en la whitelist (Verisure, Bistrosoft, etc.),
en vez de crear borrador de compra la deriva acá.
"""
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from extensions import get_db
from models import Vencimiento, CATEGORIAS

bp = Blueprint("api_externa", __name__)


def _token_configurado() -> str:
    return (os.getenv("API_TOKEN_DESDE_COMPRAS") or "").strip()


def _autorizar() -> bool:
    """Compara el header X-API-Token contra la env var. Si la env var no
    está configurada, deja todo bloqueado (NO entra nada)."""
    esperado = _token_configurado()
    if not esperado:
        return False
    recibido = (request.headers.get("X-API-Token") or "").strip()
    return bool(recibido) and recibido == esperado


def _parse_decimal(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return Decimal(str(s).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except ValueError:
        return None


@bp.route("/vencimiento-desde-compras", methods=["POST"])
def vencimiento_desde_compras():
    """
    Crea un vencimiento a partir de una factura detectada por el OCR de COMPRAS.

    Headers:
        X-API-Token: token compartido (env API_TOKEN_DESDE_COMPRAS)

    Body JSON esperado:
        {
            "cuit_emisor": "33-71630774-9",
            "numero_comprobante": "A000101311061",
            "proveedor_nombre": "VERISURE ARGENTINA",
            "monto": 97418.96,
            "fecha_vencimiento": "2026-05-14",   // YYYY-MM-DD
            "categoria": "servicios",            // opcional, default "servicios"
            "tipo": "Servicios",                 // opcional, default "Servicios"
            "concepto": "...",                   // opcional, autogenerado si falta
            "periodo_facturado": "mayo 2026",    // opcional
            "marcar_estimado": true              // si true, entra con flags estimado
        }

    Respuestas:
        201 → vencimiento creado. Body: {"status": "creado", "id": 123}
        200 → ya existía (dedup por cuit+numero). Body: {"status": "duplicado", "id": 123}
        400 → datos inválidos.
        401 → token faltante o incorrecto.
    """
    if not _autorizar():
        return jsonify({"error": "no autorizado"}), 401

    data = request.get_json(silent=True) or {}

    cuit = (data.get("cuit_emisor") or "").strip()
    numero = (data.get("numero_comprobante") or "").strip()
    if not cuit or not numero:
        return jsonify({"error": "cuit_emisor y numero_comprobante son obligatorios"}), 400

    monto = _parse_decimal(data.get("monto"))
    fecha_vto = _parse_date(data.get("fecha_vencimiento"))
    if monto is None or fecha_vto is None:
        return jsonify({"error": "monto y fecha_vencimiento son obligatorios"}), 400

    categoria = (data.get("categoria") or "servicios").strip()
    if categoria not in CATEGORIAS:
        return jsonify({"error": f"categoria '{categoria}' no es válida"}), 400

    db = get_db()

    # Dedup por (cuit_emisor, numero_comprobante).
    existente = db.query(Vencimiento).filter(
        Vencimiento.cuit_emisor == cuit,
        Vencimiento.numero_comprobante == numero,
    ).first()
    if existente:
        return jsonify({"status": "duplicado", "id": existente.id}), 200

    proveedor_nombre = (data.get("proveedor_nombre") or "Servicio").strip()[:80]
    tipo = (data.get("tipo") or "Servicios").strip()[:80]
    concepto_default = f"{proveedor_nombre} — {numero}"
    concepto = (data.get("concepto") or concepto_default).strip()[:200]
    periodo = (data.get("periodo_facturado") or "").strip() or None
    marcar_estimado = bool(data.get("marcar_estimado", False))

    # Intentamos derivar periodo_desde/hasta del string. Si no se puede,
    # caemos al mes de la fecha de vencimiento como fallback (igual queda
    # marcado como estimado, para que Facu lo revise al editar).
    from services.periodo import parsear_legacy, primer_dia, ultimo_dia
    p_desde, p_hasta = parsear_legacy(periodo)
    if (not p_desde or not p_hasta) and fecha_vto is not None:
        p_desde = primer_dia(fecha_vto.year, fecha_vto.month)
        p_hasta = ultimo_dia(fecha_vto.year, fecha_vto.month)

    v = Vencimiento(
        categoria=categoria,
        tipo=tipo,
        concepto=concepto,
        periodo_facturado=periodo,
        periodo_desde=p_desde,
        periodo_hasta=p_hasta,
        monto=monto,
        monto_estimado=marcar_estimado,
        estimado_monto_de="OCR — revisar" if marcar_estimado else None,
        fecha_vencimiento=fecha_vto,
        fecha_estimada=marcar_estimado,
        # NO es_recurrente: cada mes llega una factura nueva por mail. Si fuera
        # recurrente, el generador de horizonte crearía 12 copias estimadas
        # que se pisarían con las reales mes a mes.
        es_recurrente=False,
        cuit_emisor=cuit[:20],
        numero_comprobante=numero[:40],
        notas=(
            "Cargado automáticamente desde el OCR de COMPRAS "
            f"(mail de {proveedor_nombre})."
        ),
    )
    db.add(v)
    db.commit()

    return jsonify({"status": "creado", "id": v.id}), 201
