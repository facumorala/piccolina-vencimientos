"""
Planes / Financiaciones: listar, crear con generación automática de cuotas,
ver detalle, vincular vencimientos cubiertos.
Esqueleto inicial — flujos completos en próxima iteración.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from calendar import monthrange

from flask import Blueprint, render_template, request, flash, redirect, url_for
from sqlalchemy import func

from extensions import get_db, today_ar
from models import Plan, Vencimiento, LogActividad, FUENTES_PLAN, ESTADOS_PLAN
from auth_helpers import login_required, current_user
from nav_helpers import redirect_back

bp = Blueprint("planes", __name__)


@bp.route("/")
@login_required
def list_view():
    db = get_db()
    planes = db.query(Plan).order_by(Plan.estado.asc(), Plan.creado_en.desc()).all()

    data = []
    for p in planes:
        cuotas_pagas = db.query(func.count(Vencimiento.id)).filter(
            Vencimiento.plan_id == p.id,
            Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.pagado.is_(True),
        ).scalar() or 0
        cuotas_impagas_vencidas = db.query(func.count(Vencimiento.id)).filter(
            Vencimiento.plan_id == p.id,
            Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.pagado.is_(False),
            Vencimiento.fecha_vencimiento < today_ar(),
        ).scalar() or 0
        data.append({
            "plan": p,
            "cuotas_pagas": cuotas_pagas,
            "cuotas_impagas_vencidas": cuotas_impagas_vencidas,
            "total_calculado": p.monto_cuota * p.cuotas_totales,
        })

    # Vencimientos disponibles para tildar al crear un plan (no pagados, sin plan)
    disponibles = db.query(Vencimiento).filter(
        Vencimiento.plan_id.is_(None),
        Vencimiento.pagado.is_(False),
    ).order_by(Vencimiento.categoria.asc(), Vencimiento.fecha_vencimiento.asc()).all()

    return render_template(
        "planes/list.html",
        data=data,
        disponibles=disponibles,
        fuentes=FUENTES_PLAN,
        estados=ESTADOS_PLAN,
    )


@bp.route("/<int:pid>")
@login_required
def detalle(pid):
    db = get_db()
    p = db.get(Plan, pid)
    if not p:
        flash("Plan no encontrado.", "error")
        return redirect(url_for("planes.list_view"))
    cuotas = db.query(Vencimiento).filter(
        Vencimiento.plan_id == pid,
        Vencimiento.plan_cuota_nro.isnot(None),
    ).order_by(Vencimiento.plan_cuota_nro.asc()).all()
    cubiertos = db.query(Vencimiento).filter(
        Vencimiento.plan_id == pid,
        Vencimiento.plan_cuota_nro.is_(None),
    ).order_by(Vencimiento.fecha_vencimiento.asc()).all()
    # Vencimientos disponibles para sumar a este plan (sin plan asignado, no pagados)
    disponibles = db.query(Vencimiento).filter(
        Vencimiento.plan_id.is_(None),
        Vencimiento.pagado.is_(False),
    ).order_by(Vencimiento.fecha_vencimiento.asc()).all()
    return render_template(
        "planes/detalle.html",
        plan=p,
        cuotas=cuotas,
        cubiertos=cubiertos,
        disponibles=disponibles,
    )


@bp.route("/nuevo", methods=["POST"])
@login_required
def nuevo():
    db = get_db()
    nombre = request.form.get("nombre", "").strip()
    fuente = request.form.get("fuente", "AFIP")
    monto_cuota = _parse_decimal(request.form.get("monto_cuota"))
    cuotas_totales = _parse_int(request.form.get("cuotas_totales"))
    dia_del_mes = _parse_int(request.form.get("dia_del_mes"))
    fecha_primera = _parse_date(request.form.get("fecha_primera_cuota"))
    obligaciones = (request.form.get("obligaciones_cubiertas") or "").strip() or None
    notas = (request.form.get("notas") or "").strip() or None

    if not all([nombre, monto_cuota, cuotas_totales, dia_del_mes, fecha_primera]):
        flash("Faltan campos obligatorios del plan.", "error")
        return redirect_back("planes.list_view")

    p = Plan(
        nombre=nombre,
        fuente=fuente,
        monto_cuota=monto_cuota,
        cuotas_totales=cuotas_totales,
        dia_del_mes=dia_del_mes,
        fecha_primera_cuota=fecha_primera,
        obligaciones_cubiertas=obligaciones,
        notas=notas,
    )
    db.add(p)
    db.flush()  # para tener p.id antes del commit

    # Generar las N cuotas automáticamente
    _generar_cuotas_plan(db, p)

    # Vincular vencimientos tildados (los que este plan cubre)
    ids_cubiertos = request.form.getlist("vencimiento_cubierto", type=int)
    n_cubiertos = 0
    for vid in ids_cubiertos:
        v = db.get(Vencimiento, vid)
        if v and v.plan_id is None:
            v.plan_id = p.id
            v.plan_cuota_nro = None
            n_cubiertos += 1

    db.commit()
    _log_actividad(db, "crear_plan", None, p.id,
                   f"Creó plan: {nombre} ({cuotas_totales} cuotas, {n_cubiertos} vtos cubiertos)")
    extra = f" + {n_cubiertos} vencimientos vinculados" if n_cubiertos else ""
    flash(f"Plan '{nombre}' creado con {cuotas_totales} cuotas{extra}.", "success")
    return redirect(url_for("planes.detalle", pid=p.id))


@bp.route("/<int:pid>/vincular", methods=["POST"])
@login_required
def vincular_vencimientos(pid):
    """Marca vencimientos como cubiertos por este plan (multi-select)."""
    db = get_db()
    p = db.get(Plan, pid)
    if not p:
        flash("Plan no encontrado.", "error")
        return redirect_back("planes.list_view")
    ids = request.form.getlist("vencimiento_id", type=int)
    n = 0
    for vid in ids:
        v = db.get(Vencimiento, vid)
        if v and v.plan_id is None:
            v.plan_id = p.id
            v.plan_cuota_nro = None  # los cubiertos no son cuotas
            n += 1
    db.commit()
    _log_actividad(db, "vincular_plan", None, p.id, f"Sumó {n} vencimientos al plan {p.nombre}")
    flash(f"{n} vencimientos vinculados al plan.", "success")
    return redirect(url_for("planes.detalle", pid=pid))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generar_cuotas_plan(db, p: Plan):
    """Crea las N cuotas como Vencimientos en categoría 'financiaciones'."""
    f = p.fecha_primera_cuota
    for nro in range(1, p.cuotas_totales + 1):
        # Sumar (nro-1) meses a la fecha de la primera cuota
        fecha_cuota = _sumar_meses(f, nro - 1)
        # Ajustar al día del mes especificado, respetando finales de mes
        ultimo_dia = monthrange(fecha_cuota.year, fecha_cuota.month)[1]
        dia = min(p.dia_del_mes, ultimo_dia)
        fecha_cuota = fecha_cuota.replace(day=dia)
        v = Vencimiento(
            categoria="financiaciones",
            tipo=p.nombre,
            concepto=f"{p.nombre} - cuota {nro}/{p.cuotas_totales}",
            periodo_facturado=None,
            monto=p.monto_cuota,
            fecha_vencimiento=fecha_cuota,
            es_recurrente=False,
            plan_id=p.id,
            plan_cuota_nro=nro,
        )
        db.add(v)


def _sumar_meses(d: date, n: int) -> date:
    """Suma n meses a la fecha, conservando el día (si el mes destino es más corto, ajusta)."""
    mes_total = d.month - 1 + n
    año = d.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo_dia = monthrange(año, mes)[1]
    return d.replace(year=año, month=mes, day=min(d.day, ultimo_dia))


def _parse_decimal(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return Decimal(str(s).replace(",", ".").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _log_actividad(db, accion, vencimiento_id, plan_id, descripcion):
    u = current_user()
    if not u or u.rol == "facu":
        return
    log = LogActividad(
        user_id=u.id,
        accion=accion,
        vencimiento_id=vencimiento_id,
        plan_id=plan_id,
        descripcion=descripcion,
    )
    db.add(log)
    db.commit()
