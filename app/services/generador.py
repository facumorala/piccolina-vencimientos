"""
Generador mensual de vencimientos recurrentes.

Lógica:
- Toma todos los vencimientos del mes ANTERIOR marcados `es_recurrente=True` y
  `pausado=False`.
- Para cada uno, crea un vencimiento equivalente en el MES EN CURSO:
  - Misma categoría, tipo, concepto (con periodo nuevo).
  - Para variables (sin monto_fijo), copia el monto del mes anterior y lo
    marca como `monto_estimado=True`.
  - Misma fecha de vencimiento pero un mes después.
  - Si no se sabe la fecha exacta (era estimada), la nueva también lo es.

Se ejecuta:
- Automático: el día 1 de cada mes a las 00:01 (via scheduler).
- Manual: botón "generar mes próximo" en la UI.

Idempotente: si ya hay un vencimiento del mismo tipo en el mes destino, no lo duplica.
"""
from calendar import monthrange
from datetime import date

from extensions import SessionFactory, today_ar
from models import Vencimiento


MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def generar_recurrentes_para_mes(mes_destino: date | None = None) -> int:
    """
    Genera los vencimientos recurrentes para `mes_destino` (default: mes en curso)
    a partir de los del mes anterior.

    Devuelve la cantidad de vencimientos creados.
    """
    db = SessionFactory()
    try:
        if mes_destino is None:
            mes_destino = today_ar().replace(day=1)
        else:
            mes_destino = mes_destino.replace(day=1)

        # Mes anterior
        if mes_destino.month == 1:
            mes_anterior = mes_destino.replace(year=mes_destino.year - 1, month=12)
        else:
            mes_anterior = mes_destino.replace(month=mes_destino.month - 1)

        if mes_destino.month == 12:
            siguiente = mes_destino.replace(year=mes_destino.year + 1, month=1)
        else:
            siguiente = mes_destino.replace(month=mes_destino.month + 1)

        # Recurrentes del mes anterior (sin plan, no pausados)
        fuente = db.query(Vencimiento).filter(
            Vencimiento.es_recurrente.is_(True),
            Vencimiento.pausado.is_(False),
            Vencimiento.plan_id.is_(None),
            Vencimiento.fecha_vencimiento >= mes_anterior,
            Vencimiento.fecha_vencimiento < mes_destino,
        ).all()

        creados = 0
        for v in fuente:
            # Evitar duplicado: si ya existe uno del mismo tipo en mes_destino, saltar
            ya = db.query(Vencimiento).filter(
                Vencimiento.categoria == v.categoria,
                Vencimiento.tipo == v.tipo,
                Vencimiento.plan_id.is_(None),
                Vencimiento.fecha_vencimiento >= mes_destino,
                Vencimiento.fecha_vencimiento < siguiente,
            ).first()
            if ya:
                continue

            # Fecha del nuevo: mismo día del mes (ajustado si el mes destino es más corto)
            ultimo = monthrange(mes_destino.year, mes_destino.month)[1]
            nueva_fecha = mes_destino.replace(day=min(v.fecha_vencimiento.day if v.fecha_vencimiento else 1, ultimo))

            # Nuevo periodo en texto
            nuevo_periodo = f"{MESES_ES[mes_destino.month]} {mes_destino.year}"

            # Concepto: reemplazar referencia al mes anterior si es claro, sino agregar mes
            nuevo_concepto = f"{v.tipo} {nuevo_periodo}"

            nuevo = Vencimiento(
                categoria=v.categoria,
                tipo=v.tipo,
                concepto=nuevo_concepto,
                periodo_facturado=nuevo_periodo,
                monto=v.monto,
                monto_estimado=True,  # siempre estimado al replicar
                estimado_monto_de=v.periodo_facturado or "mes anterior",
                fecha_vencimiento=nueva_fecha,
                fecha_estimada=v.fecha_estimada,
                es_recurrente=True,
                pausado=False,
                notas=v.notas,
            )
            db.add(nuevo)
            creados += 1

        db.commit()
        print(f"[generador] Creados {creados} vencimientos recurrentes para {MESES_ES[mes_destino.month]} {mes_destino.year}.", flush=True)
        return creados
    except Exception as e:
        db.rollback()
        print(f"[generador] Error: {e}", flush=True)
        raise
    finally:
        db.close()
