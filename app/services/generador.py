"""
Generador de vencimientos recurrentes con horizonte rodante CORTO (1 mes).

Lógica (rediseño 18-jun-2026 — "presente prolijo"):
- Cada vencimiento recurrente solo se anticipa UN mes: el próximo.
- Cuando Facu crea un vencimiento nuevo recurrente, se dispara solo la copia
  del mes siguiente, con monto_estimado=True y fecha_estimada=True, para que
  después la confirme. Esa copia vive en la solapa "Estimaciones".
- El job mensual del día 1 corre `asegurar_horizonte_completo()` para
  asegurar que existan el mes actual y el próximo (salvavidas).

Por qué tan corto: el horizonte largo (12 meses) inflaba la "deuda pendiente"
y multiplicaba x12 cada cosa cargada. Si en el futuro se quiere proyección
anual para análisis de negocio, basta con subir `HORIZONTE_MESES`.

Reglas:
- No duplica: si ya hay un vencimiento del mismo categoria+tipo (sin plan) en
  el mes destino, no crea otro.
- No replica los `pausado=True` ni los que están adentro de un plan (`plan_id`).
- Hijos arrancan estimados; al editarlos Facu puede confirmar fecha y monto.

Ver `routes/vencimientos.py::nuevo()` para el disparo al crear y
`services/scheduler.py` para el salvavidas mensual.
"""
import re
from calendar import monthrange
from datetime import date

from extensions import SessionFactory, today_ar
from models import Vencimiento
from services.periodo import primer_dia, ultimo_dia


# Horizonte rodante: cuántos meses (contando el actual) mantenemos cargados.
# 2 = mes actual + mes próximo. El "mes próximo" es lo que se ve en Estimaciones.
HORIZONTE_MESES = 2

MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Para parsear períodos como "marzo 2026", "MARZO 2026", "marzo de 2026".
_MES_A_NUM = {nombre: i for i, nombre in enumerate(MESES_ES) if nombre}
_RE_PERIODO_SIMPLE = re.compile(
    r"^\s*(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)"
    r"(?:\s+de)?\s+(\d{4})\s*$",
    re.IGNORECASE,
)


def _parsear_periodo_simple(texto: str | None) -> date | None:
    """
    Devuelve `date(año, mes, 1)` si el texto es un período simple tipo
    "marzo 2026", "MARZO 2026" o "marzo de 2026". Devuelve None si es
    bimestral ("mar-abr 2026"), anual ("2025") o cualquier otra cosa.
    """
    if not texto:
        return None
    m = _RE_PERIODO_SIMPLE.match(texto)
    if not m:
        return None
    mes = _MES_A_NUM[m.group(1).lower()]
    anio = int(m.group(2))
    return date(anio, mes, 1)


def _sumar_meses(d: date, n: int) -> date:
    """Devuelve `d` + n meses, ajustando el día si el mes destino es más corto."""
    total_mes = d.month - 1 + n
    nuevo_anio = d.year + total_mes // 12
    nuevo_mes = total_mes % 12 + 1
    ultimo_dia = monthrange(nuevo_anio, nuevo_mes)[1]
    return date(nuevo_anio, nuevo_mes, min(d.day, ultimo_dia))


def _primer_dia_mes(d: date) -> date:
    return d.replace(day=1)


def _siguiente_primer_dia(d: date) -> date:
    """Primer día del mes siguiente a `d`."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _existe_en_mes(db, categoria: str, tipo: str, mes: date) -> bool:
    """¿Ya hay un vencimiento del mismo categoria+tipo (sin plan) en ese mes?"""
    inicio = _primer_dia_mes(mes)
    fin = _siguiente_primer_dia(inicio)
    ya = db.query(Vencimiento).filter(
        Vencimiento.categoria == categoria,
        Vencimiento.tipo == tipo,
        Vencimiento.plan_id.is_(None),
        Vencimiento.fecha_vencimiento >= inicio,
        Vencimiento.fecha_vencimiento < fin,
    ).first()
    return ya is not None


def _calcular_periodo_copia(origen: Vencimiento, nueva_fecha: date) -> str | None:
    """
    Calcula el período facturado de una copia respetando el offset entre
    `periodo_facturado` y `fecha_vencimiento` del origen.

    Ejemplo EDENOR: origen período "marzo 2026" + vto 24/04/2026 →
    offset = -1 mes. Copia con vto 24/06/2026 → período "mayo 2026".

    Si el período del origen no es parseable (bimestral, anual, libre) o no
    se puede calcular el offset, devuelve el mes del vencimiento como
    fallback ("junio 2026").
    """
    fallback = f"{MESES_ES[nueva_fecha.month]} {nueva_fecha.year}"

    if not origen.fecha_vencimiento:
        return fallback

    periodo_origen = _parsear_periodo_simple(origen.periodo_facturado)
    if not periodo_origen:
        return fallback

    # Offset en meses entre periodo_origen y fecha_vencimiento del origen
    vto_o = origen.fecha_vencimiento
    offset = (periodo_origen.year - vto_o.year) * 12 + (periodo_origen.month - vto_o.month)

    # Aplicar offset a la nueva_fecha
    total_meses = nueva_fecha.month - 1 + offset
    anio = nueva_fecha.year + total_meses // 12
    mes = total_meses % 12 + 1
    return f"{MESES_ES[mes]} {anio}"


def _crear_copia(origen: Vencimiento, nueva_fecha: date) -> Vencimiento:
    """Crea una copia estimada de `origen` con la fecha indicada."""
    nuevo_periodo = _calcular_periodo_copia(origen, nueva_fecha)
    # periodo_desde/hasta de la copia: si el período es parseable como
    # "mes-año" simple, lo usamos. Si no (bimestrales, anuales, libre),
    # caemos al mes del vencimiento como mejor aproximación. La fila queda
    # marcada como estimada, así que Facu lo va a revisar al editarla igual.
    nuevo_periodo_simple = _parsear_periodo_simple(nuevo_periodo)
    if nuevo_periodo_simple:
        p_desde = primer_dia(nuevo_periodo_simple.year, nuevo_periodo_simple.month)
        p_hasta = ultimo_dia(nuevo_periodo_simple.year, nuevo_periodo_simple.month)
    else:
        p_desde = primer_dia(nueva_fecha.year, nueva_fecha.month)
        p_hasta = ultimo_dia(nueva_fecha.year, nueva_fecha.month)
    return Vencimiento(
        categoria=origen.categoria,
        tipo=origen.tipo,
        concepto=origen.tipo,
        periodo_facturado=nuevo_periodo,
        periodo_desde=p_desde,
        periodo_hasta=p_hasta,
        monto=origen.monto,
        monto_estimado=True,
        estimado_monto_de=origen.periodo_facturado or "mes anterior",
        fecha_vencimiento=nueva_fecha,
        fecha_estimada=True,
        es_recurrente=True,
        pausado=False,
        notas=origen.notas,
    )


# ─── API pública ─────────────────────────────────────────────────────────────

def generar_horizonte_desde(vencimiento_id: int, meses_adelante: int = HORIZONTE_MESES - 1) -> int:
    """
    Dispara la creación de copias estimadas para los próximos `meses_adelante`
    meses a partir del vencimiento `vencimiento_id` (el origen NO se cuenta).
    Con el horizonte corto (HORIZONTE_MESES=2) esto es una sola copia: el mes
    siguiente.

    Se llama desde `routes/vencimientos.py::nuevo()` cuando Facu carga un
    vencimiento recurrente nuevo.

    Devuelve cuántas copias se crearon.
    """
    db = SessionFactory()
    try:
        origen = db.get(Vencimiento, vencimiento_id)
        if not origen or not origen.es_recurrente or origen.pausado:
            return 0
        if origen.plan_id is not None:
            return 0
        if not origen.fecha_vencimiento:
            return 0

        creados = 0
        for i in range(1, meses_adelante + 1):
            nueva_fecha = _sumar_meses(origen.fecha_vencimiento, i)
            if _existe_en_mes(db, origen.categoria, origen.tipo, nueva_fecha):
                continue
            db.add(_crear_copia(origen, nueva_fecha))
            creados += 1

        db.commit()
        print(f"[generador] {creados} copias estimadas creadas para {origen.tipo} ({origen.categoria}).", flush=True)
        return creados
    except Exception as e:
        db.rollback()
        print(f"[generador] Error en generar_horizonte_desde({vencimiento_id}): {e}", flush=True)
        raise
    finally:
        db.close()


def asegurar_horizonte_completo(meses: int = HORIZONTE_MESES, hoy: date | None = None) -> int:
    """
    Salvavidas: revisa todos los tipos+categorías recurrentes y se asegura que
    haya un vencimiento cargado en cada uno de los próximos `meses` meses.

    Para cada tipo+categoria distinto que tenga al menos un recurrente
    no-pausado (ignorando los pagados/atrasados anteriores a hoy), busca el
    vencimiento más reciente como "modelo" y rellena los meses faltantes con
    copias estimadas.

    Devuelve la cantidad total de vencimientos creados.
    """
    db = SessionFactory()
    try:
        if hoy is None:
            hoy = today_ar()

        # Tipos recurrentes activos: distintos (categoria, tipo) con al menos un
        # vencimiento recurrente no-pausado, sin plan.
        pares = (
            db.query(Vencimiento.categoria, Vencimiento.tipo)
            .filter(
                Vencimiento.es_recurrente.is_(True),
                Vencimiento.pausado.is_(False),
                Vencimiento.plan_id.is_(None),
            )
            .distinct()
            .all()
        )

        total_creados = 0
        mes_actual = _primer_dia_mes(hoy)

        for categoria, tipo in pares:
            # Modelo = el vencimiento más reciente (último mes cargado) del mismo
            # tipo+categoria, recurrente, no pausado, sin plan.
            modelo = (
                db.query(Vencimiento)
                .filter(
                    Vencimiento.categoria == categoria,
                    Vencimiento.tipo == tipo,
                    Vencimiento.es_recurrente.is_(True),
                    Vencimiento.pausado.is_(False),
                    Vencimiento.plan_id.is_(None),
                    Vencimiento.fecha_vencimiento.isnot(None),
                )
                .order_by(Vencimiento.fecha_vencimiento.desc())
                .first()
            )
            if not modelo or not modelo.fecha_vencimiento:
                continue

            # Rellenar cada uno de los próximos `meses` meses desde el mes actual.
            for i in range(meses):
                # mes objetivo = mes_actual + i
                mes_objetivo = _sumar_meses(mes_actual, i)
                # Mismo día del mes que el modelo, ajustado al largo del mes
                ultimo = monthrange(mes_objetivo.year, mes_objetivo.month)[1]
                nueva_fecha = mes_objetivo.replace(day=min(modelo.fecha_vencimiento.day, ultimo))

                if _existe_en_mes(db, categoria, tipo, nueva_fecha):
                    continue
                # No retroceder: si el modelo es de un mes futuro al objetivo,
                # no crear "para atrás" del modelo.
                if nueva_fecha <= modelo.fecha_vencimiento:
                    continue
                db.add(_crear_copia(modelo, nueva_fecha))
                total_creados += 1

        db.commit()
        print(f"[generador] asegurar_horizonte_completo: {total_creados} copias creadas (horizonte {meses} meses).", flush=True)
        return total_creados
    except Exception as e:
        db.rollback()
        print(f"[generador] Error en asegurar_horizonte_completo: {e}", flush=True)
        raise
    finally:
        db.close()


