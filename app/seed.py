"""
Seed inicial del dashboard Vencimientos.

Carga (idempotente):
- Usuario 'facu' (admin) y 'agus' (contadora compartida).
- 5 planes AFIP activos identificados de la Sheet vieja.
- ~17 reglas recurrentes (Autónomos, F.931, Sindicato, IVA, IIBB,
  Honorarios + servicios Piccolina) cargados como vencimientos del mes
  en curso. El generador mensual va a replicar al mes siguiente.

Las contadoras pueden revisar y corregir todo desde la UI después.
"""
import os
import bcrypt
from datetime import date
from decimal import Decimal
from calendar import monthrange

from extensions import SessionFactory, today_ar
from models import User, Vencimiento, Plan


# ─── Planes AFIP activos (de la Sheet vieja, abril 2026) ──────────────────────

PLANES_AFIP = [
    {
        "nombre": "Plan A (IVA mar-abr 2025 + Aportes may-jun)",
        "fuente": "AFIP",
        "monto_cuota": Decimal("1540715.69"),
        "cuotas_totales": 8,
        "cuotas_ya_pagas": 7,  # va por la 7/8 (próxima = 8/8)
        "dia_del_mes": 16,
        "obligaciones": "IVA marzo y abril 2025 + Aportes y Contribuciones SS mayo y junio (cuotas 4 y 7 quedaron impagas)",
    },
    {
        "nombre": "Plan B (Aportes SS julio 2025)",
        "fuente": "AFIP",
        "monto_cuota": Decimal("317243.64"),
        "cuotas_totales": 12,
        "cuotas_ya_pagas": 6,
        "dia_del_mes": 16,
        "obligaciones": "Aportes y Contribuciones SS julio 2025",
    },
    {
        "nombre": "Plan C (Ganancias 2024 + Aportes sep-nov)",
        "fuente": "AFIP",
        "monto_cuota": Decimal("1324658.50"),
        "cuotas_totales": 12,
        "cuotas_ya_pagas": 2,
        "dia_del_mes": 16,
        "obligaciones": "Ganancias 2024 + Aportes y Contribuciones SS septiembre y noviembre 2025",
    },
    {
        "nombre": "Plan D (IVA ago-sep-oct 2025)",
        "fuente": "AFIP",
        "monto_cuota": Decimal("2215911.78"),
        "cuotas_totales": 8,
        "cuotas_ya_pagas": 1,
        "dia_del_mes": 16,
        "obligaciones": "IVA agosto, septiembre y octubre 2025",
    },
    {
        "nombre": "Plan E (IVA nov-dic-feb + Aportes ene-feb)",
        "fuente": "AFIP",
        "monto_cuota": Decimal("2961723.17"),
        "cuotas_totales": 8,
        "cuotas_ya_pagas": 1,
        "dia_del_mes": 16,
        "obligaciones": "IVA noviembre, diciembre, febrero + Aportes y Contribuciones SS enero y febrero",
    },
]


# ─── Reglas recurrentes mensuales (vencimientos del mes en curso) ────────────

# Para los variables, el monto inicial es 0/None — las contadoras lo completan.
# Para los fijos, se carga el monto.
RECURRENTES = [
    # Categoría, Tipo, día del mes, monto fijo (None si variable), Notas
    ("impuestos",       "Autónomos",       7,  Decimal("90342.38"), "VEP - Banco. Día 7 de cada mes."),
    ("cargas_sociales", "F.931 AFIP",      10, None,                "Vence día 10 del mes siguiente."),
    ("cargas_sociales", "Sindicato",       13, None,                "Vence día 13 del mes siguiente."),
    ("impuestos",       "Ingresos Brutos", 15, None,                "Vence aprox día 15."),
    ("impuestos",       "IVA",             18, None,                "Vence aprox día 18 del 2do mes posterior."),
    ("honorarios",      "Honorarios Contadoras", 1, None,           "Primeros días del mes siguiente."),
    # Servicios Piccolina
    ("servicios", "Edenor 1",            10, None, "1 de los 2 medidores. Mensual."),
    ("servicios", "Edenor 2",            10, None, "2 de los 2 medidores. Mensual."),
    ("servicios", "Alquiler Piccolina",  10, Decimal("1720000.00"), "Pago a Luis."),
    ("servicios", "Expensas Piccolina",  10, None, "COMPLETAR día y monto típico."),
    ("servicios", "Picco celu",          10, None, "COMPLETAR día y monto típico."),
    ("servicios", "Internet Piccolina",  10, None, "COMPLETAR día y monto típico."),
    ("servicios", "Sistema de Facturación", 10, None, "COMPLETAR día y monto típico."),
    ("servicios", "Alarma",              10, None, "COMPLETAR día y monto típico."),
    ("servicios", "Seguro del local",    10, None, "COMPLETAR día y monto típico."),
]


def seed():
    db = SessionFactory()

    try:
        _seed_usuarios(db)
        _seed_planes_afip(db)
        _seed_recurrentes_mes_actual(db)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[seed] Error: {e}", flush=True)
        raise
    finally:
        db.close()


def _seed_usuarios(db):
    """Crea/actualiza los 2 usuarios: facu y agus."""
    facu_pwd = os.getenv("ADMIN_PASSWORD") or "facu2026"
    agus_pwd = os.getenv("CONTADORAS_PASSWORD") or "agus2026"

    for username, name, email, pwd, rol in [
        ("facu", "Facu Morala", "facu@piccolina.com", facu_pwd, "facu"),
        ("agus", "Agus (contadora)", "agus@piccolina.com", agus_pwd, "contadoras"),
    ]:
        u = db.query(User).filter_by(username=username).first()
        hash_ = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
        if u:
            u.password_hash = hash_
            u.active = True
            print(f"[seed] Updated user '{username}' (rol={rol}).", flush=True)
        else:
            db.add(User(username=username, name=name, email=email, password_hash=hash_, rol=rol, active=True))
            print(f"[seed] Created user '{username}' (rol={rol}, pwd='{pwd}').", flush=True)


def _seed_planes_afip(db):
    """Crea los 5 planes AFIP activos si no existen, con sus cuotas."""
    hoy = today_ar()

    for plan_data in PLANES_AFIP:
        existente = db.query(Plan).filter_by(nombre=plan_data["nombre"]).first()
        if existente:
            print(f"[seed] Plan ya existe: {plan_data['nombre']}.", flush=True)
            continue

        # Calcular fecha de la primera cuota tal que la cuota actual caiga en este mes
        cuota_actual = plan_data["cuotas_ya_pagas"] + 1  # la próxima
        fecha_primera = _restar_meses(hoy.replace(day=plan_data["dia_del_mes"]), cuota_actual - 1)

        p = Plan(
            nombre=plan_data["nombre"],
            fuente=plan_data["fuente"],
            monto_cuota=plan_data["monto_cuota"],
            cuotas_totales=plan_data["cuotas_totales"],
            dia_del_mes=plan_data["dia_del_mes"],
            fecha_primera_cuota=fecha_primera,
            obligaciones_cubiertas=plan_data["obligaciones"],
            estado="activo",
        )
        db.add(p)
        db.flush()

        # Generar las N cuotas y marcar las ya pagas
        for nro in range(1, plan_data["cuotas_totales"] + 1):
            fecha_cuota = _sumar_meses(fecha_primera, nro - 1)
            ultimo = monthrange(fecha_cuota.year, fecha_cuota.month)[1]
            fecha_cuota = fecha_cuota.replace(day=min(plan_data["dia_del_mes"], ultimo))

            ya_paga = nro <= plan_data["cuotas_ya_pagas"]
            v = Vencimiento(
                categoria="financiaciones",
                tipo=plan_data["nombre"],
                concepto=f"{plan_data['nombre']} - cuota {nro}/{plan_data['cuotas_totales']}",
                monto=plan_data["monto_cuota"],
                fecha_vencimiento=fecha_cuota,
                pagado=ya_paga,
                fecha_pago=fecha_cuota if ya_paga else None,
                metodo_pago="debito_automatico" if ya_paga else None,
                es_recurrente=False,
                plan_id=p.id,
                plan_cuota_nro=nro,
            )
            db.add(v)
        print(f"[seed] Plan creado: {plan_data['nombre']} ({plan_data['cuotas_totales']} cuotas).", flush=True)


def _seed_recurrentes_mes_actual(db):
    """Crea un vencimiento del mes en curso por cada regla recurrente."""
    hoy = today_ar()
    for categoria, tipo, dia, monto_fijo, notas in RECURRENTES:
        # Buscar si ya hay un vencimiento de este tipo en el mes en curso
        primer_dia_mes = hoy.replace(day=1)
        if primer_dia_mes.month == 12:
            fin_mes = primer_dia_mes.replace(year=primer_dia_mes.year + 1, month=1)
        else:
            fin_mes = primer_dia_mes.replace(month=primer_dia_mes.month + 1)

        existente = db.query(Vencimiento).filter(
            Vencimiento.categoria == categoria,
            Vencimiento.tipo == tipo,
            Vencimiento.fecha_vencimiento >= primer_dia_mes,
            Vencimiento.fecha_vencimiento < fin_mes,
        ).first()
        if existente:
            continue

        ultimo = monthrange(hoy.year, hoy.month)[1]
        fecha = hoy.replace(day=min(dia, ultimo))

        # Mes en string (ej: "mayo 2026")
        meses_es = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        periodo = f"{meses_es[hoy.month]} {hoy.year}"

        v = Vencimiento(
            categoria=categoria,
            tipo=tipo,
            concepto=f"{tipo} {periodo}",
            periodo_facturado=periodo,
            monto=monto_fijo,
            monto_estimado=False if monto_fijo else False,
            fecha_vencimiento=fecha,
            es_recurrente=True,
            notas=notas,
        )
        db.add(v)
    print(f"[seed] Vencimientos recurrentes del mes en curso creados.", flush=True)


def _sumar_meses(d: date, n: int) -> date:
    mes_total = d.month - 1 + n
    año = d.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo = monthrange(año, mes)[1]
    return d.replace(year=año, month=mes, day=min(d.day, ultimo))


def _restar_meses(d: date, n: int) -> date:
    return _sumar_meses(d, -n)


if __name__ == "__main__":
    seed()
