"""
Vencimientos: listado con filtros, agrupado por categoría/tipo,
popovers PAGAR/EDITAR, modal de carga/edición.

Rediseño 19-jun-2026 (pedido de Facu — "el objetivo es el historial"):
- Formulario simplificado: un solo campo "Nombre" (antes Tipo + Concepto).
  Se sacaron Importe estimado, Fecha estimada, Esporádico y Pausado.
- Se eliminó toda la UX de estimaciones (solapas, banner, etiquetas) y la
  generación automática de horizonte. El modelo conserva los campos
  monto_estimado/fecha_estimada por compatibilidad (los usa la API de COMPRAS),
  pero no se tocan desde la carga manual.
- Botón "Repetir mes anterior": duplica los vencimientos del mes pasado al mes
  actual (importe vacío, fecha corrida un mes), marcados con es_repeticion=True
  (color distinto) hasta que alguien los edite o pague.
- Adjuntar comprobante de pago: foto o PDF guardado EN LA BASE (perdura entre
  deploys de Railway).
"""
from calendar import monthrange
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, flash, redirect, url_for, Response

from extensions import get_db, today_ar
from models import Vencimiento, LogActividad, Ficha, CATEGORIAS
from auth_helpers import login_required, current_user
from nav_helpers import redirect_back
from services.periodo import (
    construir_periodo, TIPOS_PERIODO, primer_dia, ultimo_dia,
    sumar_meses, MESES_ES, MESES_ES_ABR,
)

bp = Blueprint("vencimientos", __name__)


_NOMBRE_MES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Comprobantes de pago: límite y tipos aceptados.
MAX_COMPROBANTE = 15 * 1024 * 1024  # 15 MB


# ─── Listado ──────────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_view():
    db = get_db()

    # Filtros desde query string
    f_categoria = request.args.getlist("categoria")
    f_estado = request.args.get("estado", "no_pagado")  # 'no_pagado' | 'pagado' | 'en_plan' | 'todos'
    f_tipo = request.args.get("tipo", "")
    f_fecha_tipo = request.args.get("fecha_tipo", "vencimiento")  # 'vencimiento' | 'pago' | 'periodo'
    f_fecha_desde = _parse_date(request.args.get("fecha_desde"))
    f_fecha_hasta = _parse_date(request.args.get("fecha_hasta"))
    f_periodo_texto = (request.args.get("periodo_texto") or "").strip()

    rango_activo = (f_fecha_desde or f_fecha_hasta) and f_fecha_tipo in ("vencimiento", "pago")
    periodo_activo = f_fecha_tipo == "periodo" and f_periodo_texto

    q = db.query(Vencimiento)

    if f_categoria:
        q = q.filter(Vencimiento.categoria.in_(f_categoria))
    if f_tipo:
        q = q.filter(Vencimiento.tipo == f_tipo)

    # Filtros de fecha (rango por vencimiento / pago / texto de período)
    if f_fecha_tipo == "vencimiento":
        if f_fecha_desde:
            q = q.filter(Vencimiento.fecha_vencimiento >= f_fecha_desde)
        if f_fecha_hasta:
            q = q.filter(Vencimiento.fecha_vencimiento <= f_fecha_hasta)
    elif f_fecha_tipo == "pago":
        if f_fecha_desde:
            q = q.filter(Vencimiento.fecha_pago >= f_fecha_desde)
        if f_fecha_hasta:
            q = q.filter(Vencimiento.fecha_pago <= f_fecha_hasta)
    elif f_fecha_tipo == "periodo" and f_periodo_texto:
        q = q.filter(Vencimiento.periodo_facturado.ilike(f"%{f_periodo_texto}%"))

    # Estado
    if f_estado == "pagado":
        q = q.filter(Vencimiento.pagado.is_(True))
    elif f_estado == "en_plan":
        q = q.filter(Vencimiento.plan_id.isnot(None), Vencimiento.plan_cuota_nro.is_(None))
    elif f_estado == "no_pagado":
        # Todo lo no pagado (atrasado + actual + lo que se haya cargado a futuro).
        # Excluye los "cubiertos" por un plan (esos viven en Financiaciones).
        q = q.filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
        )
    # 'todos' = sin filtro de estado

    # Ordenar: más vencidos primero (fechas viejas), luego nulls al final
    q = q.order_by(Vencimiento.fecha_vencimiento.asc().nullslast(), Vencimiento.id.desc())

    vencimientos = q.all()

    # Agrupar en Python por categoría / tipo (más simple que GROUP_BY de SQL para esto)
    agrupados = {}
    for v in vencimientos:
        agrupados.setdefault(v.categoria, {}).setdefault(v.tipo, []).append(v)

    # Subtotales por categoría + totales globales del listado filtrado.
    # Incluye intereses por pago fuera de término dentro del total (pagados y pendientes).
    subtotales = {}
    g_total = Decimal("0")
    g_pagado = Decimal("0")
    g_no_pagado = Decimal("0")
    g_int_pagados = Decimal("0")
    g_int_pendientes = Decimal("0")
    g_cantidad = 0

    for cat, tipos in agrupados.items():
        total = Decimal("0")
        pagado = Decimal("0")
        no_pagado = Decimal("0")
        int_pagados = Decimal("0")
        int_pendientes = Decimal("0")
        for tipo_lista in tipos.values():
            for v in tipo_lista:
                monto_v = v.monto or Decimal("0")
                int_v = v.monto_intereses or Decimal("0")
                total += monto_v + int_v
                if v.pagado:
                    pagado += monto_v + int_v
                    int_pagados += int_v
                else:
                    no_pagado += monto_v + int_v
                    int_pendientes += int_v
                g_cantidad += 1
        subtotales[cat] = {
            "total": total,
            "pagado": pagado,
            "no_pagado": no_pagado,
            "intereses_pagados": int_pagados,
            "intereses_pendientes": int_pendientes,
        }
        g_total += total
        g_pagado += pagado
        g_no_pagado += no_pagado
        g_int_pagados += int_pagados
        g_int_pendientes += int_pendientes

    globales = {
        "total": g_total,
        "pagado": g_pagado,
        "no_pagado": g_no_pagado,
        "intereses_pagados": g_int_pagados,
        "intereses_pendientes": g_int_pendientes,
        "cantidad": g_cantidad,
    }

    # Set de tipos que ya tienen ficha cargada — sirve para que el ícono 📖
    # del listado distinga visualmente "tiene instructivo" vs "vacío / crear".
    tipos_con_ficha = {row[0] for row in db.query(Ficha.tipo).all()}

    hay_filtro_fecha = bool(rango_activo or periodo_activo)

    return render_template(
        "vencimientos/list.html",
        agrupados=agrupados,
        subtotales=subtotales,
        globales=globales,
        f_categoria=f_categoria,
        f_estado=f_estado,
        f_tipo=f_tipo,
        f_fecha_tipo=f_fecha_tipo,
        f_fecha_desde=f_fecha_desde,
        f_fecha_hasta=f_fecha_hasta,
        f_periodo_texto=f_periodo_texto,
        hay_filtro_fecha=hay_filtro_fecha,
        categorias=CATEGORIAS,
        tipos_con_ficha=tipos_con_ficha,
    )


# ─── Repetir mes anterior ────────────────────────────────────────────────────

@bp.route("/repetir-mes-anterior", methods=["POST"])
@login_required
def repetir_mes_anterior():
    """
    Duplica al mes en curso todos los vencimientos sueltos del mes pasado.
    Cada copia: fecha de vencimiento corrida +1 mes, período corrido +1 mes,
    importe VACÍO (Facu carga el real) y marcada con es_repeticion=True para
    que se vea de un color distinto hasta que alguien la edite o pague.
    No duplica las cuotas de financiaciones ni pisa lo que ya exista en el mes.
    """
    db = get_db()
    hoy = today_ar()

    primer_mes_actual = hoy.replace(day=1)
    if primer_mes_actual.month == 1:
        primer_mes_ant = primer_mes_actual.replace(year=primer_mes_actual.year - 1, month=12)
    else:
        primer_mes_ant = primer_mes_actual.replace(month=primer_mes_actual.month - 1)

    origenes = db.query(Vencimiento).filter(
        Vencimiento.plan_id.is_(None),
        Vencimiento.fecha_vencimiento >= primer_mes_ant,
        Vencimiento.fecha_vencimiento < primer_mes_actual,
    ).all()

    creados = 0
    salteados = 0
    for o in origenes:
        nueva_fecha = _sumar_un_mes(o.fecha_vencimiento) if o.fecha_vencimiento else None

        # Período corrido +1 mes (preserva el largo: mensual, bimestral, etc.)
        p_desde = p_hasta = None
        periodo_str = None
        if o.periodo_desde and o.periodo_hasta:
            d_anio, d_mes = sumar_meses(o.periodo_desde.year, o.periodo_desde.month, 1)
            h_anio, h_mes = sumar_meses(o.periodo_hasta.year, o.periodo_hasta.month, 1)
            p_desde = primer_dia(d_anio, d_mes)
            p_hasta = ultimo_dia(h_anio, h_mes)
            periodo_str = _periodo_string(p_desde, p_hasta)

        # Dedup: si ya hay uno de mismo tipo+categoría+período en el destino, saltear.
        if p_desde and p_hasta and _buscar_duplicado_periodo(db, o.categoria, o.tipo, p_desde, p_hasta):
            salteados += 1
            continue

        copia = Vencimiento(
            categoria=o.categoria,
            tipo=o.tipo,
            concepto=o.tipo,
            periodo_facturado=periodo_str,
            periodo_desde=p_desde,
            periodo_hasta=p_hasta,
            monto=None,                 # importe vacío (decisión de Facu)
            fecha_vencimiento=nueva_fecha,
            es_recurrente=True,
            es_repeticion=True,
            notas=o.notas,
        )
        db.add(copia)
        creados += 1

    db.commit()
    _log_actividad(db, "repetir", None, None, f"Repitió {creados} vencimientos del mes anterior")

    if creados:
        msg = f"Listo: repetí {creados} vencimiento{'s' if creados != 1 else ''} del mes pasado. "
        msg += "Quedaron marcados en color para que cargues el importe de cada uno."
        if salteados:
            msg += f" ({salteados} ya estaban cargados este mes, no se duplicaron.)"
        flash(msg, "success")
    elif salteados:
        flash("Los vencimientos del mes pasado ya estaban todos cargados este mes.", "success")
    else:
        flash("No encontré vencimientos el mes pasado para repetir.", "error")
    return redirect_back("vencimientos.list_view")


# ─── Crear / editar (modal) ───────────────────────────────────────────────────

@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
def nuevo():
    if request.method == "POST":
        db = get_db()
        categoria = request.form.get("categoria")
        nombre = request.form.get("nombre", "").strip()

        try:
            canon, p_desde, p_hasta = _periodo_desde_form(request.form)
        except ValueError as e:
            flash(f"Período inválido: {e}", "error")
            return redirect_back("vencimientos.list_view")

        dup = _buscar_duplicado_periodo(db, categoria, nombre, p_desde, p_hasta)
        if dup is not None:
            flash(
                f"Ya hay un vencimiento de «{nombre}» ({categoria}) cargado para el período "
                f"{canon} (id #{dup.id}). Si querés reemplazarlo, editá el existente.",
                "error",
            )
            return redirect_back("vencimientos.list_view")

        v = Vencimiento(
            categoria=categoria,
            tipo=nombre,
            concepto=nombre,
            periodo_facturado=canon,
            periodo_desde=p_desde,
            periodo_hasta=p_hasta,
            monto=_parse_decimal(request.form.get("monto")),
            fecha_vencimiento=_parse_date(request.form.get("fecha_vencimiento")),
            es_recurrente=True,
            notas=(request.form.get("notas") or "").strip() or None,
        )
        db.add(v)
        db.commit()
        _log_actividad(db, "crear", v.id, None, f"Cargó: {v.concepto}")
        flash("Vencimiento creado.", "success")
        return redirect_back("vencimientos.list_view")
    # GET no se usa habitualmente (el form vive como modal en el listado)
    return render_template("vencimientos/form.html", v=None, categorias=CATEGORIAS)


@bp.route("/<int:vid>/editar", methods=["POST"])
@login_required
def editar(vid):
    """Edita un vencimiento. Al tocarlo a mano deja de ser 'repetición sin revisar'."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect(url_for("vencimientos.list_view"))

    nueva_categoria = request.form.get("categoria", v.categoria)
    nuevo_nombre = request.form.get("nombre", v.tipo).strip()

    try:
        canon, p_desde, p_hasta = _periodo_desde_form(request.form)
    except ValueError as e:
        flash(f"Período inválido: {e}", "error")
        return redirect_back("vencimientos.list_view")

    dup = _buscar_duplicado_periodo(db, nueva_categoria, nuevo_nombre, p_desde, p_hasta, excluir_id=v.id)
    if dup is not None:
        flash(
            f"Ya hay otro vencimiento de «{nuevo_nombre}» ({nueva_categoria}) para el período "
            f"{canon} (id #{dup.id}). No se guardaron los cambios.",
            "error",
        )
        return redirect_back("vencimientos.list_view")

    v.categoria = nueva_categoria
    v.tipo = nuevo_nombre
    v.concepto = nuevo_nombre
    v.periodo_facturado = canon
    v.periodo_desde = p_desde
    v.periodo_hasta = p_hasta
    v.monto = _parse_decimal(request.form.get("monto"))
    v.fecha_vencimiento = _parse_date(request.form.get("fecha_vencimiento"))
    v.notas = (request.form.get("notas") or "").strip() or None
    # Lo tocaron a mano → ya no es una repetición pendiente de revisar.
    v.es_repeticion = False

    db.commit()
    _log_actividad(db, "editar", v.id, None, f"Editó: {v.concepto}")
    flash("Vencimiento actualizado.", "success")
    return redirect_back("vencimientos.list_view")


@bp.route("/<int:vid>/eliminar", methods=["POST"])
@login_required
def eliminar(vid):
    """Elimina un vencimiento."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")

    concepto = v.concepto
    db.delete(v)
    db.commit()
    _log_actividad(db, "eliminar", None, None, f"Eliminó: {concepto}")
    flash("Vencimiento eliminado.", "success")
    return redirect_back("vencimientos.list_view")


# ─── Marcar pagado / Editar pago / Eliminar pago ──────────────────────────────

@bp.route("/<int:vid>/pagar", methods=["POST"])
@login_required
def pagar(vid):
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")

    # Comprobante opcional (foto/PDF). Si falla la validación, no registra el pago.
    ok, msg = _guardar_comprobante(v, request.files.get("comprobante"))
    if not ok:
        flash(msg, "error")
        return redirect_back("vencimientos.list_view")

    v.pagado = True
    v.fecha_pago = _parse_date(request.form.get("fecha_pago")) or today_ar()
    v.metodo_pago = request.form.get("metodo_pago") or None
    # Pagarlo también lo confirma como cargado a mano.
    v.es_repeticion = False
    db.commit()
    _log_actividad(db, "pagar", v.id, None, f"Marcó pagado: {v.concepto} (${v.monto or '?'})")
    flash("Pago registrado." + (" Comprobante adjuntado." if v.comprobante_datos else ""), "success")
    return redirect_back("vencimientos.list_view")


@bp.route("/<int:vid>/comprobante", methods=["POST"])
@login_required
def adjuntar_comprobante(vid):
    """Adjunta, reemplaza o quita el comprobante de un vencimiento ya existente."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")

    if request.form.get("quitar") == "1":
        v.comprobante_datos = None
        v.comprobante_nombre = None
        v.comprobante_tipo = None
        db.commit()
        _log_actividad(db, "comprobante", v.id, None, f"Quitó comprobante de: {v.concepto}")
        flash("Comprobante quitado.", "success")
        return redirect_back("vencimientos.list_view")

    archivo = request.files.get("comprobante")
    if not archivo or not archivo.filename:
        flash("No elegiste ningún archivo.", "error")
        return redirect_back("vencimientos.list_view")

    ok, msg = _guardar_comprobante(v, archivo)
    if not ok:
        flash(msg, "error")
        return redirect_back("vencimientos.list_view")
    db.commit()
    _log_actividad(db, "comprobante", v.id, None, f"Adjuntó comprobante a: {v.concepto}")
    flash("Comprobante guardado.", "success")
    return redirect_back("vencimientos.list_view")


@bp.route("/<int:vid>/comprobante", methods=["GET"])
@login_required
def ver_comprobante(vid):
    """Devuelve el comprobante para verlo/descargarlo en el navegador."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v or not v.comprobante_datos:
        flash("Ese vencimiento no tiene comprobante.", "error")
        return redirect_back("vencimientos.list_view")

    tipo = v.comprobante_tipo or "application/octet-stream"
    nombre = v.comprobante_nombre or "comprobante"
    # inline para que las imágenes/PDFs se abran en el navegador.
    return Response(
        v.comprobante_datos,
        mimetype=tipo,
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


@bp.route("/<int:vid>/intereses", methods=["POST"])
@login_required
def cargar_intereses(vid):
    """
    Carga / actualiza / borra los intereses por pago fuera de término.
    Si monto está vacío o en 0, se borra (set NULL).
    """
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")

    monto_str = request.form.get("monto_intereses", "").strip()
    link = (request.form.get("link_intereses") or "").strip() or None
    monto = _parse_decimal(monto_str)

    if monto is None or monto == 0:
        v.monto_intereses = None
        v.link_intereses = None
        accion_msg = f"Quitó intereses de: {v.concepto}"
    else:
        v.monto_intereses = monto
        v.link_intereses = link
        accion_msg = f"Cargó intereses ${monto} en: {v.concepto}"

    db.commit()
    _log_actividad(db, "intereses", v.id, None, accion_msg)
    flash("Intereses actualizados.", "success")
    return redirect_back("vencimientos.list_view")


@bp.route("/<int:vid>/eliminar-pago", methods=["POST"])
@login_required
def eliminar_pago(vid):
    db = get_db()
    v = db.get(Vencimiento, vid)
    if v:
        v.pagado = False
        v.fecha_pago = None
        v.metodo_pago = None
        db.commit()
        _log_actividad(db, "eliminar_pago", v.id, None, f"Desmarcó pago: {v.concepto}")
        flash("Pago eliminado.", "success")
    return redirect_back("vencimientos.list_view")


# ─── Helpers internos ─────────────────────────────────────────────────────────

def _guardar_comprobante(v, archivo):
    """
    Valida y guarda el comprobante (foto/PDF) en el vencimiento `v`.
    Devuelve (ok, mensaje_de_error). Si no se adjuntó nada, devuelve (True, None)
    sin tocar nada (el comprobante es siempre opcional).
    """
    if not archivo or not archivo.filename:
        return True, None
    tipo = (archivo.mimetype or "").lower()
    if not (tipo.startswith("image/") or tipo == "application/pdf"):
        return False, "El comprobante tiene que ser una foto o un PDF."
    datos = archivo.read()
    if len(datos) > MAX_COMPROBANTE:
        return False, "El comprobante es muy pesado (máximo 15 MB). Probá con una foto más liviana."
    if not datos:
        return True, None
    v.comprobante_datos = datos
    v.comprobante_nombre = (archivo.filename or "comprobante")[:255]
    v.comprobante_tipo = tipo[:100]
    return True, None


def _sumar_un_mes(d):
    """Devuelve `d` corrido un mes adelante, ajustando el día si el mes es más corto."""
    if d is None:
        return None
    total_mes = d.month  # 0-based +1 = d.month
    nuevo_anio = d.year + total_mes // 12
    nuevo_mes = total_mes % 12 + 1
    ultimo = monthrange(nuevo_anio, nuevo_mes)[1]
    return d.replace(year=nuevo_anio, month=nuevo_mes, day=min(d.day, ultimo))


def _periodo_string(desde, hasta):
    """Arma el string del período a partir de las fechas desde/hasta."""
    if not desde or not hasta:
        return None
    if desde.year == hasta.year and desde.month == hasta.month:
        return f"{MESES_ES[desde.month]} {desde.year}"
    if desde.year == hasta.year:
        return f"{MESES_ES_ABR[desde.month]}-{MESES_ES_ABR[hasta.month]} {desde.year}"
    return f"{MESES_ES_ABR[desde.month]} {desde.year}-{MESES_ES_ABR[hasta.month]} {hasta.year}"


def _periodo_desde_form(form):
    """
    Lee del form los campos del selector estructurado de período y devuelve
    `(canonico, periodo_desde, periodo_hasta)`. Lanza ValueError si falta
    el tipo o los datos del tipo elegido.
    """
    tipo = (form.get("periodo_tipo") or "").strip().lower()
    if not tipo:
        raise ValueError("Tenés que elegir el tipo de período (mensual, bimestral, etc.).")
    if tipo not in TIPOS_PERIODO:
        raise ValueError(f"Tipo de período no reconocido: {tipo}.")

    def _entero(name):
        val = (form.get(name) or "").strip()
        if not val:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    return construir_periodo(
        tipo,
        anio=_entero("periodo_anio"),
        mes=_entero("periodo_mes"),
        semestre=_entero("periodo_semestre"),
        desde_anio=_entero("periodo_desde_anio"),
        desde_mes=_entero("periodo_desde_mes"),
        hasta_anio=_entero("periodo_hasta_anio"),
        hasta_mes=_entero("periodo_hasta_mes"),
    )


def _buscar_duplicado_periodo(db, categoria, tipo, p_desde, p_hasta, excluir_id=None):
    """
    Devuelve el vencimiento existente que ya cubre EXACTAMENTE el mismo
    período (mismo desde/hasta) y mismo tipo+categoría, o None.

    Sirve para impedir que se cargue dos veces el mismo servicio/impuesto/
    honorario para el mismo mes/bimestre/etc. Los vencimientos que son
    cuotas de un plan (plan_cuota_nro IS NOT NULL) no se consideran
    duplicados — cuotas iguales son válidas dentro de una financiación.
    """
    if not p_desde or not p_hasta:
        return None
    q = db.query(Vencimiento).filter(
        Vencimiento.categoria == categoria,
        Vencimiento.tipo == tipo,
        Vencimiento.periodo_desde == p_desde,
        Vencimiento.periodo_hasta == p_hasta,
        Vencimiento.plan_cuota_nro.is_(None),
    )
    if excluir_id is not None:
        q = q.filter(Vencimiento.id != excluir_id)
    return q.first()


def _parse_decimal(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return Decimal(str(s).replace(",", ".").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _log_actividad(db, accion, vencimiento_id, plan_id, descripcion):
    """Solo registra si el actor NO es Facu (los avisos Telegram son para Facu)."""
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
