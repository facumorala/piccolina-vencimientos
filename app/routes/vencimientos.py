"""
Vencimientos: listado con filtros, agrupado por categoría/tipo,
popovers PAGAR/EDITAR, modal de carga/edición.
"""
from calendar import monthrange
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, flash, redirect, url_for

from extensions import get_db, today_ar
from models import Vencimiento, LogActividad, Ficha, CATEGORIAS
from auth_helpers import login_required, current_user
from nav_helpers import redirect_back
from services.periodo import construir_periodo, TIPOS_PERIODO

bp = Blueprint("vencimientos", __name__)


# ─── Listado ──────────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def list_view():
    db = get_db()
    hoy = today_ar()

    # Filtros desde query string
    f_categoria = request.args.getlist("categoria")
    f_estado = request.args.get("estado", "no_pagado")  # 'no_pagado' | 'pagado' | 'en_plan' | 'todos'
    f_tipo = request.args.get("tipo", "")
    f_estimado = request.args.get("estimado", "")  # '' | 'si' | 'no'
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
    if f_estimado == "si":
        q = q.filter((Vencimiento.monto_estimado.is_(True)) | (Vencimiento.fecha_estimada.is_(True)))
    elif f_estimado == "no":
        q = q.filter(Vencimiento.monto_estimado.is_(False), Vencimiento.fecha_estimada.is_(False))

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
        if rango_activo or periodo_activo:
            # Hay filtro de fecha activo → respetar el rango, solo filtrar por no pagado.
            q = q.filter(
                Vencimiento.pagado.is_(False),
                Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
            )
        else:
            # Default sin filtro de fecha: lo no pagado del mes en curso + atrasado.
            primer_dia_mes = hoy.replace(day=1)
            if primer_dia_mes.month == 12:
                fin_mes = primer_dia_mes.replace(year=primer_dia_mes.year + 1, month=1)
            else:
                fin_mes = primer_dia_mes.replace(month=primer_dia_mes.month + 1)
            q = q.filter(
                Vencimiento.pagado.is_(False),
                Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
                (Vencimiento.fecha_vencimiento < fin_mes) | (Vencimiento.fecha_vencimiento.is_(None)),
            )

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
    fechas_estimadas_por_cat = {}  # cat -> [Vencimiento, ...]
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
        estimadas = []
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
                if v.fecha_estimada and not v.pagado:
                    estimadas.append(v)
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
        if estimadas:
            fechas_estimadas_por_cat[cat] = estimadas

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

    # Banner global del MES EN CURSO: vencimientos no pagados con fecha o monto
    # estimado. Independiente de los filtros del listado — siempre mira al mes
    # actual. Sirve para que Facu el día 1 confirme los estimados del mes.
    primer_dia = hoy.replace(day=1)
    if primer_dia.month == 12:
        fin_mes = primer_dia.replace(year=primer_dia.year + 1, month=1)
    else:
        fin_mes = primer_dia.replace(month=primer_dia.month + 1)
    estimados_mes = db.query(Vencimiento).filter(
        Vencimiento.pagado.is_(False),
        Vencimiento.plan_id.is_(None) | Vencimiento.plan_cuota_nro.isnot(None),
        Vencimiento.fecha_vencimiento >= primer_dia,
        Vencimiento.fecha_vencimiento < fin_mes,
        (Vencimiento.monto_estimado.is_(True)) | (Vencimiento.fecha_estimada.is_(True)),
    ).order_by(Vencimiento.fecha_vencimiento.asc()).all()

    return render_template(
        "vencimientos/list.html",
        agrupados=agrupados,
        subtotales=subtotales,
        fechas_estimadas_por_cat=fechas_estimadas_por_cat,
        globales=globales,
        f_categoria=f_categoria,
        f_estado=f_estado,
        f_tipo=f_tipo,
        f_estimado=f_estimado,
        f_fecha_tipo=f_fecha_tipo,
        f_fecha_desde=f_fecha_desde,
        f_fecha_hasta=f_fecha_hasta,
        f_periodo_texto=f_periodo_texto,
        hay_filtro_fecha=hay_filtro_fecha,
        categorias=CATEGORIAS,
        tipos_con_ficha=tipos_con_ficha,
        estimados_mes=estimados_mes,
        nombre_mes_actual=_NOMBRE_MES_ES[hoy.month],
        anio_mes_actual=hoy.year,
    )


_NOMBRE_MES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ─── Generador manual: crear los recurrentes del mes próximo ─────────────────

@bp.route("/generar-mes-proximo", methods=["POST"])
@login_required
def generar_mes_proximo():
    """Completa el horizonte rodante de 12 meses (botón manual)."""
    from services.generador import asegurar_horizonte_completo
    try:
        n = asegurar_horizonte_completo()
        if n:
            flash(f"Se crearon {n} vencimientos estimados para completar 12 meses adelante.", "success")
        else:
            flash("Todos los recurrentes ya tienen sus 12 meses cargados.", "success")
    except Exception as e:
        flash(f"Error generando: {e}", "error")
    return redirect_back("vencimientos.list_view")


# ─── Confirmar fechas estimadas en lote (por categoría) ──────────────────────

@bp.route("/confirmar-fechas", methods=["POST"])
@login_required
def confirmar_fechas():
    """Recibe id_<n>=YYYY-MM-DD por cada vencimiento de la categoría y los confirma."""
    db = get_db()
    n = 0
    for key, val in request.form.items():
        if not key.startswith("fecha_") or not val:
            continue
        try:
            vid = int(key.replace("fecha_", ""))
        except ValueError:
            continue
        nueva = _parse_date(val)
        if not nueva:
            continue
        v = db.get(Vencimiento, vid)
        if v:
            v.fecha_vencimiento = nueva
            v.fecha_estimada = False
            n += 1
    db.commit()
    _log_actividad(db, "confirmar_fechas", None, None, f"Confirmó {n} fechas estimadas")
    flash(f"Se confirmaron {n} fechas.", "success")
    return redirect_back("vencimientos.list_view")


# ─── Crear / editar (modal) ───────────────────────────────────────────────────

@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
def nuevo():
    if request.method == "POST":
        db = get_db()
        categoria = request.form.get("categoria")
        tipo = request.form.get("tipo", "").strip()

        try:
            canon, p_desde, p_hasta = _periodo_desde_form(request.form)
        except ValueError as e:
            flash(f"Período inválido: {e}", "error")
            return redirect_back("vencimientos.list_view")

        dup = _buscar_duplicado_periodo(db, categoria, tipo, p_desde, p_hasta)
        if dup is not None:
            flash(
                f"Ya hay un vencimiento de {tipo} ({categoria}) cargado para el período "
                f"{canon} (id #{dup.id}, concepto «{dup.concepto}»). Si querés reemplazarlo, "
                "editá el existente.",
                "error",
            )
            return redirect_back("vencimientos.list_view")

        v = Vencimiento(
            categoria=categoria,
            tipo=tipo,
            concepto=request.form.get("concepto", "").strip(),
            periodo_facturado=canon,
            periodo_desde=p_desde,
            periodo_hasta=p_hasta,
            monto=_parse_decimal(request.form.get("monto")),
            monto_estimado=request.form.get("monto_estimado") == "on",
            estimado_monto_de=(request.form.get("estimado_monto_de") or "").strip() or None,
            fecha_vencimiento=_parse_date(request.form.get("fecha_vencimiento")),
            fecha_estimada=request.form.get("fecha_estimada") == "on",
            es_recurrente=not (request.form.get("esporadico") == "on"),
            notas=(request.form.get("notas") or "").strip() or None,
        )
        db.add(v)
        db.commit()
        _log_actividad(db, "crear", v.id, None, f"Cargó: {v.concepto}")

        # Si es recurrente y tiene fecha, generar los 11 meses siguientes (estimados).
        n_horizonte = 0
        if v.es_recurrente and not v.pausado and v.fecha_vencimiento:
            from services.generador import generar_horizonte_desde
            try:
                n_horizonte = generar_horizonte_desde(v.id)
            except Exception as e:
                print(f"[nuevo] No se pudo generar horizonte para {v.id}: {e}", flush=True)

        if n_horizonte:
            flash(f"Vencimiento creado + {n_horizonte} meses estimados cargados a futuro.", "success")
        else:
            flash("Vencimiento creado.", "success")
        return redirect_back("vencimientos.list_view")
    # GET no se usa habitualmente (el form vive como modal en el listado)
    return render_template("vencimientos/form.html", v=None, categorias=CATEGORIAS)


@bp.route("/<int:vid>/editar", methods=["POST"])
@login_required
def editar(vid):
    """Edita un vencimiento. Si es recurrente, opcionalmente propaga cambios a futuros."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect(url_for("vencimientos.list_view"))

    alcance = request.form.get("alcance", "solo")  # 'solo' | 'futuros' | 'todos'

    # Estado previo para detectar transición "estimado → confirmado"
    monto_estimado_antes = v.monto_estimado
    monto_antes = v.monto

    # Aplicar al original
    nueva_categoria = request.form.get("categoria", v.categoria)
    nuevo_tipo = request.form.get("tipo", v.tipo).strip()

    try:
        canon, p_desde, p_hasta = _periodo_desde_form(request.form)
    except ValueError as e:
        flash(f"Período inválido: {e}", "error")
        return redirect_back("vencimientos.list_view")

    dup = _buscar_duplicado_periodo(db, nueva_categoria, nuevo_tipo, p_desde, p_hasta, excluir_id=v.id)
    if dup is not None:
        flash(
            f"Ya hay otro vencimiento de {nuevo_tipo} ({nueva_categoria}) para el período "
            f"{canon} (id #{dup.id}). No se guardaron los cambios.",
            "error",
        )
        return redirect_back("vencimientos.list_view")

    v.categoria = nueva_categoria
    v.tipo = nuevo_tipo
    v.concepto = request.form.get("concepto", v.concepto).strip()
    v.periodo_facturado = canon
    v.periodo_desde = p_desde
    v.periodo_hasta = p_hasta
    v.monto = _parse_decimal(request.form.get("monto"))
    v.monto_estimado = request.form.get("monto_estimado") == "on"
    v.estimado_monto_de = (request.form.get("estimado_monto_de") or "").strip() or None
    nueva_fecha = _parse_date(request.form.get("fecha_vencimiento"))
    v.fecha_vencimiento = nueva_fecha
    v.fecha_estimada = request.form.get("fecha_estimada") == "on"
    v.es_recurrente = not (request.form.get("esporadico") == "on")
    v.pausado = request.form.get("pausado") == "on"
    v.notas = (request.form.get("notas") or "").strip() or None

    # AUTO: si Facu acaba de confirmar el monto real (destildó "Importe estimado"
    # o cambió el monto teniéndolo confirmado), propagar el monto nuevo a las
    # copias futuras del mismo tipo+categoria que sigan marcadas como estimadas.
    # No pisa copias que Facu ya editó manualmente (monto_estimado=False).
    n_montos_propagados = 0
    confirmo_monto = (monto_estimado_antes and not v.monto_estimado) or (
        not v.monto_estimado and v.monto is not None and v.monto != monto_antes
    )
    if confirmo_monto and v.es_recurrente and v.monto is not None and nueva_fecha:
        futuros_estimados = db.query(Vencimiento).filter(
            Vencimiento.id != v.id,
            Vencimiento.tipo == v.tipo,
            Vencimiento.categoria == v.categoria,
            Vencimiento.es_recurrente.is_(True),
            Vencimiento.plan_id.is_(None),
            Vencimiento.pagado.is_(False),
            Vencimiento.monto_estimado.is_(True),
            Vencimiento.fecha_vencimiento > nueva_fecha,
        ).all()
        for f in futuros_estimados:
            f.monto = v.monto
            f.estimado_monto_de = v.periodo_facturado or "mes anterior"
            n_montos_propagados += 1

    # Propagar a recurrentes futuros/pendientes
    n_propagados = 0
    if alcance != "solo" and v.es_recurrente and nueva_fecha:
        relacionados = _buscar_recurrentes_relacionados(db, v, alcance)
        for r in relacionados:
            r.categoria = v.categoria
            r.tipo = v.tipo
            r.es_recurrente = v.es_recurrente
            r.pausado = v.pausado
            r.notas = v.notas
            # Día del mes: mantener mes/año del relacionado, cambiar solo el día
            if r.fecha_vencimiento:
                ultimo = monthrange(r.fecha_vencimiento.year, r.fecha_vencimiento.month)[1]
                r.fecha_vencimiento = r.fecha_vencimiento.replace(day=min(nueva_fecha.day, ultimo))
            n_propagados += 1

    db.commit()
    _log_actividad(db, "editar", v.id, None, f"Editó: {v.concepto}")
    mensajes = ["Vencimiento actualizado."]
    if n_propagados:
        mensajes.append(f"{n_propagados} recurrentes propagados.")
    if n_montos_propagados:
        mensajes.append(f"{n_montos_propagados} montos estimados a futuro actualizados al nuevo valor.")
    flash(" ".join(mensajes), "success")
    return redirect_back("vencimientos.list_view")


@bp.route("/<int:vid>/eliminar", methods=["POST"])
@login_required
def eliminar(vid):
    """Elimina un vencimiento. Si es recurrente, opcionalmente borra futuros también."""
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")

    concepto = v.concepto
    alcance = request.form.get("alcance", "solo")

    n_extras = 0
    if alcance != "solo" and v.es_recurrente:
        for r in _buscar_recurrentes_relacionados(db, v, alcance):
            db.delete(r)
            n_extras += 1

    db.delete(v)
    db.commit()
    _log_actividad(db, "eliminar", None, None, f"Eliminó: {concepto} (+ {n_extras} relacionados)")
    flash(f"Vencimiento eliminado{'' if not n_extras else f' + {n_extras} recurrentes'}.", "success")
    return redirect_back("vencimientos.list_view")


def _buscar_recurrentes_relacionados(db, original: Vencimiento, alcance: str):
    """
    Devuelve los vencimientos del mismo tipo+categoría que están relacionados
    con `original` según el alcance:
      - 'futuros' → no pagados con fecha > original.fecha (o sin fecha)
      - 'todos'   → todos los no pagados (excepto el original)
    """
    q = db.query(Vencimiento).filter(
        Vencimiento.id != original.id,
        Vencimiento.tipo == original.tipo,
        Vencimiento.categoria == original.categoria,
        Vencimiento.es_recurrente.is_(True),
        Vencimiento.plan_id.is_(None),
        Vencimiento.pagado.is_(False),
    )
    if alcance == "futuros" and original.fecha_vencimiento:
        q = q.filter(
            (Vencimiento.fecha_vencimiento > original.fecha_vencimiento) |
            (Vencimiento.fecha_vencimiento.is_(None))
        )
    return q.all()


# ─── Marcar pagado / Editar pago / Eliminar pago ──────────────────────────────

@bp.route("/<int:vid>/pagar", methods=["POST"])
@login_required
def pagar(vid):
    db = get_db()
    v = db.get(Vencimiento, vid)
    if not v:
        flash("Vencimiento no encontrado.", "error")
        return redirect_back("vencimientos.list_view")
    v.pagado = True
    v.fecha_pago = _parse_date(request.form.get("fecha_pago")) or today_ar()
    v.metodo_pago = request.form.get("metodo_pago") or None
    db.commit()
    _log_actividad(db, "pagar", v.id, None, f"Marcó pagado: {v.concepto} (${v.monto or '?'})")
    flash("Pago registrado.", "success")
    return redirect_back("vencimientos.list_view")


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
