"""
Home / Dashboard principal.

Muestra:
- Total del mes en curso (total bruto / pendiente) + referencia mes anterior.
- Pendiente / Vencido / Esta semana.
- 3 más vencidos (más viejos primero).
- Financiaciones con progreso.
- Datos por completar (estimados / faltantes).
"""
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, redirect, url_for
from sqlalchemy import func

from extensions import get_db, today_ar, AR_TZ
from models import Vencimiento, Plan
from auth_helpers import login_required

bp = Blueprint("home", __name__)


@bp.route("/")
def root():
    return redirect(url_for("home.dashboard"))


@bp.route("/home")
@login_required
def dashboard():
    db = get_db()
    hoy = today_ar()
    hora_actual = datetime.now(AR_TZ).hour

    # Rango del mes en curso
    primer_dia_mes = hoy.replace(day=1)
    if primer_dia_mes.month == 12:
        primer_dia_mes_sig = primer_dia_mes.replace(year=primer_dia_mes.year + 1, month=1)
    else:
        primer_dia_mes_sig = primer_dia_mes.replace(month=primer_dia_mes.month + 1)
    ultimo_dia_mes = primer_dia_mes_sig - timedelta(days=1)

    # Rango del mes anterior
    if primer_dia_mes.month == 1:
        primer_dia_mes_ant = primer_dia_mes.replace(year=primer_dia_mes.year - 1, month=12)
    else:
        primer_dia_mes_ant = primer_dia_mes.replace(month=primer_dia_mes.month - 1)
    ultimo_dia_mes_ant = primer_dia_mes - timedelta(days=1)

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
        total_mes_bruto=total_mes_bruto,
        total_mes_pendiente=total_mes_pendiente,
        total_mes_anterior=total_mes_anterior,
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
