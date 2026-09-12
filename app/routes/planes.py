"""
Planes / Financiaciones: listar, crear con generación automática de cuotas,
ver detalle, vincular vencimientos cubiertos, editar y eliminar.

Al editar, los campos de texto son libres; si cambian los datos que definen
el calendario (monto, cantidad de cuotas, día del mes, fecha de la primera)
se rehacen las cuotas pendientes y se dejan intactas las ya pagadas.
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


# ─── Editar / Eliminar ────────────────────────────────────────────────────────

@bp.route("/<int:pid>/editar", methods=["GET", "POST"])
@login_required
def editar(pid):
    """
    Edita un plan ya creado.

    Los datos "de texto" (nombre, fuente, obligaciones, notas, estado) se cambian
    sin consecuencias. Los datos "de plata" (monto de cuota, cantidad de cuotas,
    día del mes, fecha de la primera) obligan a rehacer el calendario: las cuotas
    YA PAGADAS quedan intactas — esa plata ya se movió — y las pendientes se
    regeneran con los valores nuevos.
    """
    db = get_db()
    p = db.get(Plan, pid)
    if not p:
        flash("Plan no encontrado.", "error")
        return redirect(url_for("planes.list_view"))

    cuotas_pagadas = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.pagado.is_(True),
    ).order_by(Vencimiento.plan_cuota_nro.asc()).all()

    if request.method == "GET":
        n_pendientes = db.query(func.count(Vencimiento.id)).filter(
            Vencimiento.plan_id == p.id,
            Vencimiento.plan_cuota_nro.isnot(None),
            Vencimiento.pagado.is_(False),
        ).scalar() or 0
        n_cubiertos = db.query(func.count(Vencimiento.id)).filter(
            Vencimiento.plan_id == p.id,
            Vencimiento.plan_cuota_nro.is_(None),
        ).scalar() or 0
        return render_template(
            "planes/form.html",
            plan=p,
            fuentes=FUENTES_PLAN,
            estados=ESTADOS_PLAN,
            cuotas_pagadas=cuotas_pagadas,
            n_pendientes=n_pendientes,
            n_cubiertos=n_cubiertos,
            importes_distintos=_cuotas_con_importes_distintos(db, p),
        )

    # ── POST: guardar cambios ────────────────────────────────────────────────
    nombre = request.form.get("nombre", "").strip()
    fuente = request.form.get("fuente") or p.fuente
    estado = request.form.get("estado") or p.estado
    monto_cuota = _parse_decimal(request.form.get("monto_cuota"))
    cuotas_totales = _parse_int(request.form.get("cuotas_totales"))
    dia_del_mes = _parse_int(request.form.get("dia_del_mes"))
    fecha_primera = _parse_date(request.form.get("fecha_primera_cuota"))
    obligaciones = (request.form.get("obligaciones_cubiertas") or "").strip() or None
    notas = (request.form.get("notas") or "").strip() or None

    if not all([nombre, monto_cuota, cuotas_totales, dia_del_mes, fecha_primera]):
        flash("Faltan campos obligatorios del plan.", "error")
        return redirect(url_for("planes.editar", pid=p.id))
    if fuente not in FUENTES_PLAN:
        fuente = p.fuente
    if estado not in ESTADOS_PLAN:
        estado = p.estado
    if cuotas_totales < 1:
        flash("La cantidad de cuotas tiene que ser 1 o más.", "error")
        return redirect(url_for("planes.editar", pid=p.id))
    if not 1 <= dia_del_mes <= 31:
        flash("El día del mes tiene que estar entre 1 y 31.", "error")
        return redirect(url_for("planes.editar", pid=p.id))
    if monto_cuota <= 0:
        flash("El monto de la cuota tiene que ser mayor a cero.", "error")
        return redirect(url_for("planes.editar", pid=p.id))

    # Candado: no se puede achicar el plan por debajo de una cuota ya pagada,
    # porque esa cuota quedaría fuera del plan con la plata ya gastada.
    nros_pagados = [c.plan_cuota_nro for c in cuotas_pagadas]
    if nros_pagados and max(nros_pagados) > cuotas_totales:
        flash(
            f"No se puede bajar a {cuotas_totales} cuotas: la cuota "
            f"{max(nros_pagados)} ya figura pagada. Deshacé ese pago primero.",
            "error",
        )
        return redirect(url_for("planes.editar", pid=p.id))

    # ¿Cambió algo que obligue a rehacer las cuotas del calendario? Se separa el
    # monto del resto: si el usuario NO tocó el importe, las cuotas pendientes
    # conservan el suyo (clave en los créditos bancarios, donde cada cuota tiene
    # un importe propio y el del plan es solo de referencia).
    cambio_monto = monto_cuota != p.monto_cuota
    cambio_fechas = (
        cuotas_totales != p.cuotas_totales
        or dia_del_mes != p.dia_del_mes
        or fecha_primera != p.fecha_primera_cuota
    )
    cambio_calendario = cambio_monto or cambio_fechas
    cambio_nombre = nombre != p.nombre

    p.nombre = nombre
    p.fuente = fuente
    p.estado = estado
    p.monto_cuota = monto_cuota
    p.cuotas_totales = cuotas_totales
    p.dia_del_mes = dia_del_mes
    p.fecha_primera_cuota = fecha_primera
    p.obligaciones_cubiertas = obligaciones
    p.notas = notas

    n_regeneradas = 0
    if cambio_calendario:
        n_regeneradas = _regenerar_cuotas_pendientes(db, p, aplicar_monto=cambio_monto)
    if cambio_calendario or cambio_nombre:
        _sincronizar_textos_cuotas(db, p)

    db.commit()

    detalle_log = f"Editó el plan {nombre}"
    if cambio_calendario:
        detalle_log += f" (rehizo {n_regeneradas} cuotas pendientes"
        detalle_log += ", con el monto nuevo)" if cambio_monto else ", conservando importes)"
    _log_actividad(db, "editar_plan", None, p.id, detalle_log)

    if cambio_calendario:
        n_intactas = len(nros_pagados)
        partes = [
            f"Plan '{nombre}' actualizado. Se rehicieron {n_regeneradas} cuotas "
            "pendientes con los datos nuevos."
        ]
        if not cambio_monto:
            partes.append("Cada una conservó su importe.")
        if n_intactas:
            partes.append(f"Quedaron intactas las {n_intactas} ya pagadas.")
        flash(" ".join(partes), "success")
    else:
        flash(f"Plan '{nombre}' actualizado.", "success")
    return redirect(url_for("planes.detalle", pid=p.id))


@bp.route("/<int:pid>/eliminar", methods=["POST"])
@login_required
def eliminar(pid):
    """
    Borra un plan entero junto con sus cuotas del calendario.

    Dos recaudos:
    - Se bloquea si alguna cuota ya está pagada: esa plata ya se movió y borrarla
      dejaría el historial mintiendo (para un plan terminado o caído está el
      campo `estado`).
    - Los vencimientos que el plan cubría NO se borran: se desvinculan y vuelven
      a aparecer como pendientes en el calendario.
    """
    db = get_db()
    p = db.get(Plan, pid)
    if not p:
        flash("Plan no encontrado.", "error")
        return redirect(url_for("planes.list_view"))

    n_pagadas = db.query(func.count(Vencimiento.id)).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.pagado.is_(True),
    ).scalar() or 0
    if n_pagadas:
        flash(
            f"No se puede eliminar '{p.nombre}': tiene {n_pagadas} cuota(s) ya "
            "pagada(s). Si el plan se terminó o se cayó, cambiale el estado a "
            "'terminado' o 'incumplido' en vez de borrarlo.",
            "error",
        )
        return redirect(url_for("planes.editar", pid=p.id))

    nombre = p.nombre
    cuotas = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
    ).all()
    n_cuotas = len(cuotas)
    for c in cuotas:
        db.delete(c)

    cubiertos = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.is_(None),
    ).all()
    n_cubiertos = len(cubiertos)
    for v in cubiertos:
        v.plan_id = None

    db.flush()
    db.delete(p)
    db.commit()

    _log_actividad(
        db, "eliminar_plan", None, None,
        f"Eliminó el plan {nombre} ({n_cuotas} cuotas borradas, "
        f"{n_cubiertos} vencimientos liberados)",
    )
    extra = f" {n_cubiertos} vencimiento(s) volvieron a pendientes." if n_cubiertos else ""
    flash(f"Plan '{nombre}' eliminado: se borraron {n_cuotas} cuotas.{extra}", "success")
    return redirect(url_for("planes.list_view"))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _armar_cuota(p: Plan, nro: int) -> Vencimiento:
    """
    Construye (sin guardarla) la cuota número `nro` de un plan, calculando su
    fecha: se suman (nro-1) meses a la fecha de la primera cuota y se ajusta al
    día del mes elegido, recortando si ese mes es más corto (un plan con día 31
    cae el 28/29 en febrero).
    """
    fecha_cuota = _sumar_meses(p.fecha_primera_cuota, nro - 1)
    ultimo_dia = monthrange(fecha_cuota.year, fecha_cuota.month)[1]
    fecha_cuota = fecha_cuota.replace(day=min(p.dia_del_mes, ultimo_dia))
    return Vencimiento(
        categoria="financiaciones",
        tipo=p.nombre[:80],
        concepto=f"{p.nombre} - cuota {nro}/{p.cuotas_totales}"[:200],
        periodo_facturado=None,
        monto=p.monto_cuota,
        fecha_vencimiento=fecha_cuota,
        es_recurrente=False,
        plan_id=p.id,
        plan_cuota_nro=nro,
    )


def _generar_cuotas_plan(db, p: Plan):
    """Crea las N cuotas como Vencimientos en categoría 'financiaciones'."""
    for nro in range(1, p.cuotas_totales + 1):
        db.add(_armar_cuota(p, nro))


def _regenerar_cuotas_pendientes(db, p: Plan, aplicar_monto: bool = True) -> int:
    """
    Rehace las cuotas NO pagadas del plan con los valores actuales (cantidad,
    día del mes y fecha de la primera). Devuelve cuántas creó.

    Las cuotas ya pagadas no se tocan y los números que ellas ocupan se respetan:
    se generan solamente los números libres. Así un hueco en el medio (por
    ejemplo, la 3 pagada pero la 2 no) se vuelve a crear correctamente.

    `aplicar_monto=False` conserva el importe que cada cuota pendiente ya tenía y
    usa `p.monto_cuota` solo para las cuotas que antes no existían. Es lo que
    salva a los créditos bancarios: ahí cada cuota tiene su propio importe (el
    interés baja mes a mes) y el monto del plan es solo de referencia, así que
    mover la fecha no puede aplanar los importes reales del extracto del banco.
    Se pasa `True` únicamente cuando el usuario cambió a propósito el monto.
    """
    pagadas = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.pagado.is_(True),
    ).all()
    nros_ocupados = {c.plan_cuota_nro for c in pagadas}

    pendientes = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.pagado.is_(False),
    ).all()
    # Guardar el importe de cada cuota antes de borrarla, por si hay que devolverlo
    montos_previos = {c.plan_cuota_nro: c.monto for c in pendientes}
    for c in pendientes:
        db.delete(c)
    db.flush()  # que el borrado pegue antes de insertar las nuevas

    n = 0
    for nro in range(1, p.cuotas_totales + 1):
        if nro in nros_ocupados:
            continue
        cuota = _armar_cuota(p, nro)
        if not aplicar_monto and montos_previos.get(nro) is not None:
            cuota.monto = montos_previos[nro]
        db.add(cuota)
        n += 1
    return n


def _cuotas_con_importes_distintos(db, p: Plan) -> bool:
    """
    True si las cuotas del plan no tienen todas el mismo importe. Es la firma de
    un crédito bancario (cuota con interés decreciente) frente a un plan de AFIP
    (todas iguales). Sirve para avisarle al usuario antes de que toque el monto.
    """
    montos = db.query(Vencimiento.monto).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.monto.isnot(None),
    ).distinct().all()
    return len(montos) > 1


def _sincronizar_textos_cuotas(db, p: Plan):
    """
    Pone al día el texto de las cuotas (tipo y concepto) con el nombre y el total
    actuales del plan. Solo texto: a una cuota ya pagada no le toca ni el monto
    ni la fecha.
    """
    cuotas = db.query(Vencimiento).filter(
        Vencimiento.plan_id == p.id,
        Vencimiento.plan_cuota_nro.isnot(None),
    ).all()
    for c in cuotas:
        c.tipo = p.nombre[:80]
        c.concepto = f"{p.nombre} - cuota {c.plan_cuota_nro}/{p.cuotas_totales}"[:200]


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
