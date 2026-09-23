# -*- coding: utf-8 -*-
"""
Carga la FINANCIACION DEL SINDICATO (UTHGRA) de sept-2026: la deuda acumulada
hasta el periodo 07/2026, refinanciada en 8 eCheqs de pago diferido.

POR QUE UN SCRIPT Y NO LA PANTALLA WEB:
el formulario de planes genera las cuotas con un "dia del mes" fijo, y estos
cheques NO caen todos el mismo dia: el 6to vence el 06-mar-2027 y el 7mo el
31-mar-2027 (febrero no tiene dia 30, asi que el banco corrio esa cuota a
marzo y ese mes termina con dos cheques). Cargarlo desde la web dejaria dos
fechas equivocadas. Ademas asi cada cuota queda con su numero de eCheq anotado.

Datos tomados del listado de eCheqs del banco (captura del 23-sep-2026):
beneficiario CUIT 34-53133865-2 UNION DE TRABAJADORES HOTELEROS GASTRONOMICOS,
los 8 en estado "Liberada".

OJO PARA EL FUTURO: si alguien edita este plan desde la web y toca el monto,
la cantidad de cuotas, el dia del mes o la fecha de la primera, el dashboard
REHACE las cuotas pendientes usando dia 30 y se pierden las fechas reales del
06-mar-2027 y el 31-mar-2027. Si eso pasa, volver a correr este script (borra
y recrea) o corregir esas dos fechas a mano.

Es idempotente: si el plan ya existe (mismo nombre), no lo duplica.

Uso:
    python importar_financiacion_sindicato_2026.py --simular   # muestra que haria
    python importar_financiacion_sindicato_2026.py             # carga de verdad
"""
import os
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.models import Plan, Vencimiento  # noqa: E402


NOMBRE_PLAN = "PLAN SINDICATO 2 (deuda hasta jul-2026)"

# Los 8 eCheqs, tal cual figuran en el listado del banco.
# (numero de eCheq, fecha de pago, importe)
CHEQUES = [
    ("13738969", date(2026,  9, 30), Decimal("1125350.00")),
    ("13738970", date(2026, 10, 30), Decimal("1125350.00")),
    ("13738971", date(2026, 11, 30), Decimal("1125350.00")),
    ("13738972", date(2026, 12, 30), Decimal("1125350.00")),
    ("13738973", date(2027,  1, 30), Decimal("1125350.00")),
    ("13738974", date(2027,  3,  6), Decimal("1125350.00")),   # la de febrero, corrida a marzo
    ("13738975", date(2027,  3, 31), Decimal("1125350.00")),
    ("13738976", date(2027,  4, 30), Decimal("1125350.00")),
]

TOTAL = sum(m for _, _, m in CHEQUES)


def _normalizar(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _url_base() -> str:
    """Busca la llave de la base. Primero en el entorno; si no, en el .env maestro."""
    url = os.environ.get("VENCIMIENTOS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url.strip()
    maestro = os.path.join(os.path.expanduser("~"), ".claude", "secrets", "piccolina_bases.env")
    if os.path.exists(maestro):
        with open(maestro, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith("VENCIMIENTOS_DATABASE_URL="):
                    return linea.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("Falta VENCIMIENTOS_DATABASE_URL (entorno o ~/.claude/secrets/piccolina_bases.env).")


def _plata(m: Decimal) -> str:
    return f"${m:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def main(simular: bool = False):
    engine = create_engine(_normalizar(_url_base()), pool_pre_ping=True)
    db = sessionmaker(bind=engine)()

    try:
        if db.query(Plan).filter(Plan.nombre == NOMBRE_PLAN).first() is not None:
            print(f"«{NOMBRE_PLAN}» ya estaba cargado. No se toca nada.")
            return

        notas = (
            "Refinanciacion de la deuda con el sindicato (UTHGRA) acumulada hasta el "
            "periodo 07/2026, pagada con 8 eCheqs de pago diferido.\n"
            f"Total financiado: {_plata(TOTAL)} en 8 cuotas iguales de "
            f"{_plata(CHEQUES[0][2])}.\n"
            "Beneficiario: CUIT 34-53133865-2 - UNION DE TRABAJADORES HOTELEROS "
            "GASTRONOMICOS. Los 8 cheques figuran en estado 'Liberada'.\n"
            "El numero de eCheq de cada cuota esta anotado en la cuota misma.\n\n"
            "IMPORTANTE - las fechas NO son todas el mismo dia del mes: la cuota 6 "
            "vence el 06-mar-2027 (febrero no tiene dia 30, el banco la corrio) y la 7 "
            "el 31-mar-2027, asi que marzo 2027 tiene dos cheques. Si se edita el plan "
            "desde la web, el sistema rehace las cuotas pendientes con dia 30 y pisa "
            "esas dos fechas: hay que volver a corregirlas."
        )

        plan = Plan(
            nombre=NOMBRE_PLAN,
            fuente="otro",                       # es el sindicato, no AFIP
            monto_cuota=CHEQUES[0][2],
            cuotas_totales=len(CHEQUES),
            dia_del_mes=30,                      # el dia dominante de los cheques
            fecha_primera_cuota=CHEQUES[0][1],
            obligaciones_cubiertas=(
                "Deuda con el sindicato (UTHGRA) hasta el periodo 07/2026, "
                "financiada en 8 eCheqs."
            ),
            estado="activo",
            notas=notas,
        )

        print(f"  + {NOMBRE_PLAN}")
        print(f"      {len(CHEQUES)} cuotas de {_plata(CHEQUES[0][2])} — total {_plata(TOTAL)}")

        if not simular:
            db.add(plan)
            db.flush()

        for nro, (echeq, fecha, monto) in enumerate(CHEQUES, start=1):
            print(f"      cuota {nro}/{len(CHEQUES)}  {fecha.strftime('%d-%m-%Y')}  "
                  f"{_plata(monto)}  eCheq {echeq}")
            if simular:
                continue
            db.add(Vencimiento(
                categoria="financiaciones",
                tipo=NOMBRE_PLAN[:80],
                concepto=f"{NOMBRE_PLAN} - cuota {nro}/{len(CHEQUES)}"[:200],
                monto=monto,
                fecha_vencimiento=fecha,
                pagado=False,
                # Las cuotas de un plan NO se replican: ya estan todas creadas.
                # Si quedaran como recurrentes, el generador mensual las duplicaria.
                es_recurrente=False,
                plan_id=plan.id,
                plan_cuota_nro=nro,
                notas=f"eCheq N° {echeq} — pago diferido, liberado. Beneficiario UTHGRA (CUIT 34-53133865-2).",
            ))

        if simular:
            print("\n(simulacion: no se guardo nada)")
            db.rollback()
        else:
            db.commit()
            print(f"\nListo: plan cargado con {len(CHEQUES)} cuotas.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main(simular="--simular" in sys.argv)
