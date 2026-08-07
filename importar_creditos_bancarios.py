"""
Importa los CREDITOS BANCARIOS al dashboard de Vencimientos.

POR QUE: hasta ago-2026 los 4 creditos vigentes de Piccolina (2 de ICBC y 2
del Banco Nacion) vivian en el dashboard FINANCIERO, que se dio de baja porque
no se usaba. Una cuota de credito es exactamente lo que Vencimientos ya sabe
manejar: un plan de pago con N cuotas mensuales. Asi que se mudan aca, donde
ademas aparecen en el calendario y las contadoras las ven.

Cada credito se convierte en un `Plan` (fuente='otro') y cada cuota en un
`Vencimiento` de categoria 'financiaciones', igual que los planes de AFIP que
ya estaban cargados.

QUE NO SE IMPORTA:
  * El credito de Mercado Pago ($25M): se cancelo en jul-2026 refinanciandolo
    con el prestamo del Banco Nacion de $20M. Traerlo contaria la misma deuda
    dos veces. Su historia queda en `finanzas/creditos/Creditos_Piccolina.xlsx`.
  * Las cuotas anuladas de ese credito, por lo mismo.

Es idempotente: si el plan ya existe (mismo nombre), no lo vuelve a crear ni
duplica sus cuotas.

Uso:
    python importar_creditos_bancarios.py            # importa
    python importar_creditos_bancarios.py --simular  # muestra que haria
"""
import os
import sys
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.models import Plan, Vencimiento  # noqa: E402


# Nombre con el que se ve cada credito en el dashboard. Corto, porque entra en
# el `tipo` (80 caracteres) y se repite en el concepto de cada cuota.
def _nombre_plan(banco: str, capital: Decimal) -> str:
    banco = {"Nacion": "Banco Nación", "ICBC": "ICBC"}.get(banco, banco)
    return f"Crédito {banco} ${capital / 1_000_000:.0f}M"


def _normalizar(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _leer_creditos_del_financiero():
    """Trae los creditos ACTIVOS y sus cuotas no anuladas."""
    url = os.environ.get("FINANCIERO_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "Falta FINANCIERO_DATABASE_URL. Es la base vieja de donde se leen "
            "los creditos (solo lectura)."
        )

    engine = create_engine(_normalizar(url), pool_pre_ping=True)
    with engine.connect() as cx:
        creditos = cx.execute(text(
            "SELECT id, banco, capital, plazo_meses, sistema_amortizacion, "
            "       fecha_primera_cuota, notas "
            "FROM financiero.credito WHERE estado = 'activo' "
            "ORDER BY banco, id"
        )).all()

        cuotas = {}
        for c in creditos:
            cuotas[c.id] = cx.execute(text(
                "SELECT numero, fecha, monto_total, capital, interes, estado, "
                "       fecha_pago_real, debito_automatico "
                "FROM financiero.cuota_credito "
                "WHERE credito_id = :cid AND estado <> 'anulada' "
                "ORDER BY numero"
            ), {"cid": c.id}).all()
    return creditos, cuotas


def main(simular: bool = False):
    creditos, cuotas_por_credito = _leer_creditos_del_financiero()
    if not creditos:
        print("No hay creditos activos para importar.")
        return

    url_venc = os.environ.get("VENCIMIENTOS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url_venc:
        raise SystemExit("Falta VENCIMIENTOS_DATABASE_URL (o DATABASE_URL).")

    engine = create_engine(_normalizar(url_venc), pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    creados = 0
    try:
        for c in creditos:
            cuotas = cuotas_por_credito.get(c.id, [])
            if not cuotas:
                print(f"  - {c.banco}: sin cuotas. Se saltea.")
                continue

            nombre = _nombre_plan(c.banco, Decimal(str(c.capital)))

            if db.query(Plan).filter(Plan.nombre == nombre).first() is not None:
                print(f"  - «{nombre}» ya estaba cargado. No se toca.")
                continue

            pendientes = [q for q in cuotas if q.estado == "pendiente"]
            pagadas = [q for q in cuotas if q.estado == "pagada"]

            # `monto_cuota` del Plan es un valor de referencia: en un credito
            # bancario cada cuota tiene su propio importe (el interes baja mes a
            # mes), y ese importe real va en cada Vencimiento. Ponemos el de la
            # proxima cuota a pagar, que es el numero que Facu quiere ver.
            monto_referencia = (Decimal(str(pendientes[0].monto_total))
                                if pendientes else
                                Decimal(str(cuotas[-1].monto_total)))

            total_pendiente = sum(
                (Decimal(str(q.monto_total)) for q in pendientes), Decimal("0"),
            )

            notas = (
                f"Importado del dashboard Financiero (dado de baja en ago-2026).\n"
                f"Capital original: ${Decimal(str(c.capital)):,.0f}. "
                f"Sistema {c.sistema_amortizacion}, {c.plazo_meses} cuotas.\n"
                f"⚠️ Cada cuota tiene su propio importe (el interés baja mes a "
                f"mes): el monto del plan es solo de referencia.\n\n"
                f"{(c.notas or '').strip()}"
            ).replace(",", ".")

            print(f"\n  + {nombre}")
            print(f"      {len(cuotas)} cuotas ({len(pagadas)} pagadas, "
                  f"{len(pendientes)} por pagar)")
            print(f"      falta pagar: ${total_pendiente:,.0f}".replace(",", "."))
            if pendientes:
                print(f"      próxima: {pendientes[0].fecha} por "
                      f"${Decimal(str(pendientes[0].monto_total)):,.0f}".replace(",", "."))

            if simular:
                continue

            plan = Plan(
                nombre=nombre,
                fuente="otro",
                monto_cuota=monto_referencia,
                cuotas_totales=c.plazo_meses,
                dia_del_mes=c.fecha_primera_cuota.day,
                fecha_primera_cuota=c.fecha_primera_cuota,
                obligaciones_cubiertas=f"Préstamo bancario de ${Decimal(str(c.capital)):,.0f}".replace(",", "."),
                estado="activo" if pendientes else "terminado",
                notas=notas,
            )
            db.add(plan)
            db.flush()

            for q in cuotas:
                pagada = q.estado == "pagada"
                db.add(Vencimiento(
                    categoria="financiaciones",
                    tipo=nombre,
                    concepto=f"{nombre} - cuota {q.numero}/{c.plazo_meses}",
                    monto=Decimal(str(q.monto_total)),
                    fecha_vencimiento=q.fecha,
                    pagado=pagada,
                    fecha_pago=q.fecha_pago_real or (q.fecha if pagada else None),
                    metodo_pago="debito_automatico" if q.debito_automatico else None,
                    # Las cuotas de un plan NO se replican: ya están todas
                    # creadas. Si quedaran como recurrentes, el generador
                    # mensual las duplicaría todos los meses.
                    es_recurrente=False,
                    plan_id=plan.id,
                    plan_cuota_nro=q.numero,
                    notas=(f"Capital ${Decimal(str(q.capital)):,.0f} + "
                           f"interés ${Decimal(str(q.interes)):,.0f}").replace(",", "."),
                ))
            creados += 1

        if simular:
            print("\n(simulación: no se guardó nada)")
            db.rollback()
        else:
            db.commit()
            print(f"\nListo: {creados} créditos importados como planes de pago.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main(simular="--simular" in sys.argv)
