"""
Home / Dashboard principal.

Muestra:
- Total del mes en curso (total bruto / pendiente) + referencia mes anterior.
- Pendiente / Vencido / Esta semana.
- 3 más vencidos (más viejos primero).
- Financiaciones con progreso.
- Datos por completar (estimados / faltantes).

Acepta query param `?mes=YYYY-MM` (28-may-2026): si está, el "mes ref"
para los totales mensuales pasa a ser ese mes (no el actual). Los KPIs
de "ahora" (pendiente / vencido / esta semana / mas vencidos / datos
faltantes) NO cambian — siguen siendo el estado actual. Esto permite que
el dashboard Financiero linkee al home con un mes preseleccionado para
auditar.
"""
from datetime import datetime, timedelta, date as _date
from decimal import Decimal

from flask import Blueprint, render_template, redirect, request, url_for
from sqlalchemy import func

from extensions import get_db, today_ar, AR_TZ
from models import Vencimiento, Plan
from auth_helpers import login_required

bp = Blueprint("home", __name__)


def _parsear_mes(mes_str, hoy):
    """Parsea ?mes=YYYY-MM al primer día de ese mes. Si está vacío o
    inválido devuelve el primer día del mes actual."""
    if mes_str:
        try:
            anio_s, mes_s = mes_str.strip().split("-", 1)
            anio_i = int(anio_s)
            mes_i = int(mes_s)
            if 2000 <= anio_i <= 2100 and 1 <= mes_i <= 12:
                return _date(anio_i, mes_i, 1)
        except (ValueError, AttributeError):
            pass
    return hoy.replace(day=1)


@bp.route("/")
def root():
    return redirect(url_for("home.dashboard"))


@bp.route("/home")
@login_required
def dashboard():
    db = get_db()
    hoy = today_ar()
    hora_actual = datetime.now(AR_TZ).hour

    # Mes de referencia (querystring o el actual). Solo afecta los totales
    # mensuales — los KPIs de "ahora" siguen usando hoy.
    primer_dia_mes = _parsear_mes(request.args.get("mes"), hoy)
    if primer_dia_mes.month == 12:
        primer_dia_mes_sig = primer_dia_mes.replace(year=primer_dia_mes.year + 1, month=1)
    else:
        primer_dia_mes_sig = primer_dia_mes.replace(month=primer_dia_mes.month + 1)
    ultimo_dia_mes = primer_dia_mes_sig - timedelta(days=1)

    es_mes_actual_view = (primer_dia_mes.year == hoy.year
                          and primer_dia_mes.month == hoy.month)

    # Rango del mes anterior al de referencia
    if primer_dia_mes.month == 1:
        primer_dia_mes_ant = primer_dia_mes.replace(year=primer_dia_mes.year - 1, month=12)
    else:
        primer_dia_mes_ant = primer_dia_mes.replace(month=primer_dia_mes.month - 1)
    ultimo_dia_mes_ant = primer_dia_mes - timedelta(days=1)

    # Strings YYYY-MM para los botones "anterior / siguiente" del header.
    prev_mes_str = primer_dia_mes_ant.strftime("%Y-%m")
    if primer_dia_mes.month == 12:
        next_mes_dt = primer_dia_mes.replace(year=primer_dia_mes.year + 1, month=1)
    else:
        next_mes_dt = primer_dia_mes.replace(month=primer_dia_mes.month + 1)
    next_mes_str = next_mes_dt.strftime("%Y-%m")

    # Próximos 7 días (rolling)
    fin_semana_rolling = hoy + timedelta(days=7)

    # Query base: NO vencimientos cubiertos por plan (no aparecen en pendientes)
    q_base = db.query(Vencimiento).filter(Vencimiento.plan_id.is_(None))

    # ── TOTAL DEL MES (en curso) ─────────────────────────────────────────────
    total_mes_bruto = _sum_monto(
        q_base.filter(
            Vencimiento.fecha_vencimiento >= primer_dia_mes,
            Vencimiento.fecha_vencimiento <= ultimo_dia_mes,
        )
    )
    total_mes_pendiente = _sum_monto(
        q_base.filter(
            Vencimiento.fecha_vencimiento >= primer_dia_mes,
            Vencimiento.fecha_vencimiento <= ultimo_dia_mes,
            Vencimiento.pagado.is_(False),
        )
    )

    # ── TOTAL MES ANTERIOR (lo que se debía pagar, comparable) ───────────────
    total_mes_anterior = _sum_monto(
        q_base.filter(
            Vencimiento.fecha_vencimiento >= primer_dia_mes_ant,
            Vencimiento.fecha_vencimiento <= ultimo_dia_mes_ant,
        )
    )

    # ── PENDIENTE / VENCIDO / ESTA SEMANA ────────────────────────────────────
    pendiente_total = _sum_monto(q_base.filter(Vencimiento.pagado.is_(False)))
    vencido_total = _sum_monto(
        q_base.filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.fecha_vencimiento < hoy,
        )
    )

    # ── INTERESES POR PAGAR FUERA DE TÉRMINO (KPI dedicado) ──────────────────
    intereses_pendientes = Decimal(str(
        q_base.filter(Vencimiento.pagado.is_(False))
            .with_entities(func.coalesce(func.sum(Vencimiento.monto_intereses), 0))
            .scalar() or 0
    ))
    vencimientos_con_intereses = (
        q_base.filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.monto_intereses.isnot(None),
            Vencimiento.monto_intereses > 0,
        ).all()
    )
    esta_semana = _sum_monto(
        q_base.filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.fecha_vencimiento >= hoy,
            Vencimiento.fecha_vencimiento <= fin_semana_rolling,
        )
    )

    # ── 3 MÁS VENCIDOS (por días de atraso, más viejos primero) ──────────────
    mas_vencidos = (
        q_base.filter(
            Vencimiento.pagado.is_(False),
            Vencimiento.fecha_vencimiento.isnot(None),
            Vencimiento.fecha_vencimiento < hoy,
        )
        .order_by(Vencimiento.fecha_vencimiento.asc())
        .limit(3)
        .all()
    )

    # ── FINANCIACIONES (planes activos con progreso) ─────────────────────────
    planes_activos = db.query(Plan).filter_by(estado="activo").all()
    planes_data = []
    for p in planes_activos:
        cuotas_pagas = (
            db.query(func.count(Vencimiento.id))
            .filter(Vencimiento.plan_id == p.id, Vencimiento.plan_cuota_nro.isnot(None), Vencimiento.pagado.is_(True))
            .scalar() or 0
        )
        cuotas_impagas_vencidas = (
            db.query(func.count(Vencimiento.id))
            .filter(
                Vencimiento.plan_id == p.id,
                Vencimiento.plan_cuota_nro.isnot(None),
                Vencimiento.pagado.is_(False),
                Vencimiento.fecha_vencimiento < hoy,
            )
            .scalar() or 0
        )
        planes_data.append({
            "plan": p,
            "cuotas_pagas": cuotas_pagas,
            "cuotas_totales": p.cuotas_totales,
            "cuotas_impagas_vencidas": cuotas_impagas_vencidas,
            "monto_restante": (p.cuotas_totales - cuotas_pagas) * p.monto_cuota,
        })

    # ── DATOS POR COMPLETAR (estimados + faltantes) ──────────────────────────
    datos_faltantes = (
        q_base.filter(
            Vencimiento.pagado.is_(False),
            (Vencimiento.monto_estimado.is_(True)) |
            (Vencimiento.fecha_estimada.is_(True)) |
            (Vencimiento.monto.is_(None)) |
            (Vencimiento.fecha_vencimiento.is_(None))
        )
        .order_by(Vencimiento.creado_en.desc())
        .limit(10)
        .all()
    )
    datos_faltantes_total = (
        q_base.filter(
            Vencimiento.pagado.is_(False),
            (Vencimiento.monto_estimado.is_(True)) |
            (Vencimiento.fecha_estimada.is_(True)) |
            (Vencimiento.monto.is_(None)) |
            (Vencimiento.fecha_vencimiento.is_(None))
        )
        .count()
    )

    return render_template(
        "home.html",
        hoy=hoy,
        hora_actual=hora_actual,
        # Mes de referencia (el que se muestra en el kicker / totales).
        mes_ref=primer_dia_mes,
        es_mes_actual_view=es_mes_actual_view,
        prev_mes_str=prev_mes_str,
        next_mes_str=next_mes_str,
        # Totales del mes ref.
        total_mes_bruto=total_mes_bruto,
        total_mes_pendiente=total_mes_pendiente,
        total_mes_anterior=total_mes_anterior,
        # KPIs siempre "ahora".
        pendiente_total=pendiente_total,
        vencido_total=vencido_total,
        esta_semana=esta_semana,
        intereses_pendientes=intereses_pendientes,
        vencimientos_con_intereses=vencimientos_con_intereses,
        mas_vencidos=mas_vencidos,
        planes_data=planes_data,
        datos_faltantes=datos_faltantes,
        datos_faltantes_total=datos_faltantes_total,
    )


def _sum_monto(query) -> Decimal:
    """Suma de la columna monto, ignorando NULLs."""
    val = query.with_entities(func.coalesce(func.sum(Vencimiento.monto), 0)).scalar()
    return Decimal(str(val or 0))
