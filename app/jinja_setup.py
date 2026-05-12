"""
Filtros y globals de Jinja2 para los templates.
"""
from datetime import date, datetime
from decimal import Decimal


def fmt_money(value) -> str:
    """Formato AR de monto: $ 1.234.567,89 o '-' si vacío."""
    if value is None or value == "":
        return "-"
    try:
        n = Decimal(str(value))
    except Exception:
        return str(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    entero, _, dec = f"{n:.2f}".partition(".")
    # Insertar puntos como separador de miles
    entero_rev = entero[::-1]
    grupos = [entero_rev[i:i+3] for i in range(0, len(entero_rev), 3)]
    entero_fmt = ".".join(grupos)[::-1]
    return f"{sign}$ {entero_fmt},{dec}"


def fmt_date(value) -> str:
    """Formato DD/MM/YYYY o '—' si vacío."""
    if not value:
        return "—"
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    return str(value)


def dias_diferencia(fecha, hoy=None) -> int:
    """Días entre `fecha` y hoy. Negativo = atrasado, positivo = futuro, 0 = hoy."""
    if not fecha:
        return 0
    if not hoy:
        from extensions import today_ar
        hoy = today_ar()
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    return (fecha - hoy).days


def estado_semaforo(fecha, pagado=False, en_plan=False) -> str:
    """
    Devuelve clave del semáforo para usar en CSS:
    - 'pagado'    → gris claro
    - 'en_plan'   → azul claro
    - 'rojo'      → vencido (días < 0 o 0)
    - 'amarillo'  → 0 < días <= 7
    - 'verde'     → > 7 días
    - 'sin_fecha' → sin fecha cargada
    """
    if pagado:
        return "pagado"
    if en_plan:
        return "en_plan"
    if not fecha:
        return "sin_fecha"
    d = dias_diferencia(fecha)
    if d < 0:
        return "rojo"
    if d <= 7:
        return "amarillo"
    return "verde"


def label_estado(fecha, pagado=False, en_plan=False) -> str:
    """Texto del estado: 'vence en 5 días', '12 días atraso', 'Pagado', 'EN PLAN'."""
    if pagado:
        return "Pagado"
    if en_plan:
        return "EN PLAN"
    if not fecha:
        return "Sin fecha"
    d = dias_diferencia(fecha)
    if d < 0:
        return f"{-d} días atraso"
    if d == 0:
        return "vence hoy"
    if d == 1:
        return "vence mañana"
    return f"vence en {d} días"


def label_categoria(categoria: str) -> str:
    """Convierte la clave de categoría en label humano."""
    return {
        "impuestos": "Impuestos",
        "cargas_sociales": "Cargas Sociales",
        "financiaciones": "Financiaciones",
        "honorarios": "Honorarios",
        "servicios": "Servicios Piccolina",
    }.get(categoria, categoria.capitalize())


def register_jinja(app):
    """Registra todos los filtros y globals en la app Flask."""
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["fecha"] = fmt_date
    app.jinja_env.filters["dias_dif"] = dias_diferencia
    app.jinja_env.filters["semaforo"] = estado_semaforo
    app.jinja_env.filters["label_estado"] = label_estado
    app.jinja_env.filters["label_categoria"] = label_categoria

    # Disponibles como globals dentro de cualquier template
    from extensions import today_ar
    app.jinja_env.globals["today_ar"] = today_ar
