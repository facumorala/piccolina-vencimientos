"""
Helpers para el período facturado de un Vencimiento.

Un período representa A QUÉ MESES corresponde el consumo / la obligación que
estamos pagando, independiente de cuándo se vence el pago. Puede ser:

- Mensual    → 1 mes calendario   (ej "mayo 2026")
- Bimestral  → 2 meses corridos   (ej "mar-abr 2026")
- Trimestral → 3 meses corridos   (ej "ene-feb-mar 2026")
- Semestral  → 6 meses corridos   (ej "ene-jun 2026" / "jul-dic 2026")
- Anual      → 12 meses           (ej "2025")
- Personalizado → rango libre     (ej "mar-2026 a jul-2026")

El dashboard Financiero usa `periodo_desde` y `periodo_hasta` (date)
para atribuir el costo a cada mes calendario que toca el período.
El string canónico (`periodo_facturado`) lo seguimos guardando para
mostrar en la UI y mantener compatibilidad con datos viejos.

Funciones principales:
- `construir_periodo(tipo, ...)` → (canonico_str, desde_date, hasta_date)
- `parsear_legacy(texto)`        → (desde_date, hasta_date) o (None, None)
                                   intento "best effort" sobre strings viejos
                                   tipo "mayo 2026", "mar-abr 26", "2025".
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from typing import Optional


TIPOS_PERIODO = ["mensual", "bimestral", "trimestral", "semestral", "anual", "personalizado"]

MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Abreviatura de 3 letras estándar usada en los strings canónicos.
MESES_ES_ABR = ["", "ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic"]

# Tabla de aliases para parseo legacy (acepta typos comunes y formatos sueltos).
_ALIASES_MES = {
    "ene": 1, "enero": 1, "en": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "set": 9, "septiembre": 9, "setiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}


# ─── Helpers de fechas ───────────────────────────────────────────────────────

def primer_dia(anio: int, mes: int) -> date:
    """Devuelve el primer día del mes (date(año, mes, 1))."""
    return date(anio, mes, 1)


def ultimo_dia(anio: int, mes: int) -> date:
    """Devuelve el último día del mes (28, 30 o 31 según corresponda)."""
    return date(anio, mes, monthrange(anio, mes)[1])


def sumar_meses(anio: int, mes: int, n: int) -> tuple[int, int]:
    """Suma n meses al par (año, mes) y devuelve el nuevo par."""
    total = (anio * 12 + (mes - 1)) + n
    return total // 12, (total % 12) + 1


# ─── Construcción del período canónico ───────────────────────────────────────

def construir_periodo(
    tipo: str,
    *,
    anio: Optional[int] = None,
    mes: Optional[int] = None,
    semestre: Optional[int] = None,
    desde_anio: Optional[int] = None,
    desde_mes: Optional[int] = None,
    hasta_anio: Optional[int] = None,
    hasta_mes: Optional[int] = None,
) -> tuple[str, date, date]:
    """
    Construye el período facturado a partir de los datos del form.

    Devuelve `(canonico, periodo_desde, periodo_hasta)`. Lanza `ValueError`
    si faltan datos o el rango es inválido.

    Tipos esperados y sus argumentos:
        mensual       → anio, mes
        bimestral     → anio, mes (mes inicial; cubre mes y mes+1)
        trimestral    → anio, mes (mes inicial; cubre mes, mes+1, mes+2)
        semestral     → anio, semestre (1 = ene-jun, 2 = jul-dic)
        anual         → anio
        personalizado → desde_anio, desde_mes, hasta_anio, hasta_mes
    """
    tipo = (tipo or "").strip().lower()
    if tipo not in TIPOS_PERIODO:
        raise ValueError(f"Tipo de período inválido: {tipo!r}")

    if tipo == "mensual":
        _exigir_mes_anio(mes, anio)
        d = primer_dia(anio, mes)
        h = ultimo_dia(anio, mes)
        canon = f"{MESES_ES[mes]} {anio}"
        return canon, d, h

    if tipo == "bimestral":
        _exigir_mes_anio(mes, anio)
        fin_anio, fin_mes = sumar_meses(anio, mes, 1)
        d = primer_dia(anio, mes)
        h = ultimo_dia(fin_anio, fin_mes)
        canon = f"{MESES_ES_ABR[mes]}-{MESES_ES_ABR[fin_mes]} {fin_anio}"
        # Si el bimestre cae a caballo de dos años (ej dic 2025 + ene 2026),
        # el año del canónico es el del último mes — que coincide con el
        # convenio histórico ("nov-dic 2025", "dic-ene 2026").
        if anio != fin_anio:
            canon = f"{MESES_ES_ABR[mes]} {anio} - {MESES_ES_ABR[fin_mes]} {fin_anio}"
        return canon, d, h

    if tipo == "trimestral":
        _exigir_mes_anio(mes, anio)
        fin_anio, fin_mes = sumar_meses(anio, mes, 2)
        med_anio, med_mes = sumar_meses(anio, mes, 1)
        d = primer_dia(anio, mes)
        h = ultimo_dia(fin_anio, fin_mes)
        if anio == med_anio == fin_anio:
            canon = f"{MESES_ES_ABR[mes]}-{MESES_ES_ABR[med_mes]}-{MESES_ES_ABR[fin_mes]} {fin_anio}"
        else:
            canon = (f"{MESES_ES_ABR[mes]} {anio} - "
                     f"{MESES_ES_ABR[fin_mes]} {fin_anio}")
        return canon, d, h

    if tipo == "semestral":
        if not anio or semestre not in (1, 2):
            raise ValueError("Semestral requiere año y semestre (1 ó 2).")
        if semestre == 1:
            d = primer_dia(anio, 1)
            h = ultimo_dia(anio, 6)
            canon = f"ene-jun {anio}"
        else:
            d = primer_dia(anio, 7)
            h = ultimo_dia(anio, 12)
            canon = f"jul-dic {anio}"
        return canon, d, h

    if tipo == "anual":
        if not anio:
            raise ValueError("Anual requiere año.")
        d = primer_dia(anio, 1)
        h = ultimo_dia(anio, 12)
        canon = f"{anio}"
        return canon, d, h

    # personalizado
    if not all([desde_anio, desde_mes, hasta_anio, hasta_mes]):
        raise ValueError("Personalizado requiere desde y hasta (mes y año).")
    d = primer_dia(desde_anio, desde_mes)
    h = ultimo_dia(hasta_anio, hasta_mes)
    if d > h:
        raise ValueError("La fecha 'desde' del período no puede ser posterior a 'hasta'.")
    # Si en realidad cubre un mes solo, normalizar a 'mensual'
    if desde_anio == hasta_anio and desde_mes == hasta_mes:
        canon = f"{MESES_ES[desde_mes]} {desde_anio}"
    elif desde_anio == hasta_anio:
        canon = f"{MESES_ES_ABR[desde_mes]}-{MESES_ES_ABR[hasta_mes]} {desde_anio}"
    else:
        canon = (f"{MESES_ES_ABR[desde_mes]} {desde_anio} - "
                 f"{MESES_ES_ABR[hasta_mes]} {hasta_anio}")
    return canon, d, h


def _exigir_mes_anio(mes, anio):
    if not anio or not mes:
        raise ValueError("Falta mes o año.")
    if not (1 <= mes <= 12):
        raise ValueError(f"Mes inválido: {mes}")


# ─── Parseo legacy (best effort) ────────────────────────────────────────────

# Captura: "mayo 2026", "mayo de 2026", "MAYO 2026"
_RE_MENSUAL = re.compile(
    r"^\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"(?:\s+de)?\s+(\d{2}|\d{4})\s*$",
    re.IGNORECASE,
)
# Captura: "mar-abr 2026", "ene/feb 25", "mar abr 2026"
_RE_BIMESTRAL = re.compile(
    r"^\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"\s*[-/ ]\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"\s+(\d{2}|\d{4})\s*$",
    re.IGNORECASE,
)
# Captura: "ene-feb-mar 2026"
_RE_TRIMESTRAL = re.compile(
    r"^\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"\s*[-/ ]\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"\s*[-/ ]\s*(" + "|".join(_ALIASES_MES.keys()) + r")"
    r"\s+(\d{2}|\d{4})\s*$",
    re.IGNORECASE,
)
# Captura: "2025", "2026"
_RE_ANUAL = re.compile(r"^\s*(\d{4})\s*$")


def _normalizar_anio(s: str) -> int:
    n = int(s)
    if n < 100:  # "25" → 2025
        n += 2000
    return n


def parsear_legacy(texto: Optional[str]) -> tuple[Optional[date], Optional[date]]:
    """
    Intenta extraer (periodo_desde, periodo_hasta) de un string viejo guardado
    como texto libre. Si no logra parsearlo, devuelve (None, None).

    Usado en la migración inicial para rellenar `periodo_desde` y `periodo_hasta`
    de los vencimientos ya cargados con texto libre. No es perfecto — los que
    no parsean quedan en NULL y se completan cuando Facu edite cada uno.
    """
    if not texto:
        return None, None
    t = texto.strip()
    if not t:
        return None, None

    # 1) Anual ("2025")
    m = _RE_ANUAL.match(t)
    if m:
        anio = int(m.group(1))
        return primer_dia(anio, 1), ultimo_dia(anio, 12)

    # 2) Trimestral ("ene-feb-mar 2026")
    m = _RE_TRIMESTRAL.match(t)
    if m:
        m1 = _ALIASES_MES.get(m.group(1).lower())
        m3 = _ALIASES_MES.get(m.group(3).lower())
        anio = _normalizar_anio(m.group(4))
        if m1 and m3:
            d = primer_dia(anio if m1 <= m3 else anio - 1, m1)
            h = ultimo_dia(anio, m3)
            return d, h

    # 3) Bimestral ("mar-abr 2026")
    m = _RE_BIMESTRAL.match(t)
    if m:
        m1 = _ALIASES_MES.get(m.group(1).lower())
        m2 = _ALIASES_MES.get(m.group(2).lower())
        anio = _normalizar_anio(m.group(3))
        if m1 and m2:
            anio_inicio = anio if m1 <= m2 else anio - 1
            d = primer_dia(anio_inicio, m1)
            h = ultimo_dia(anio, m2)
            return d, h

    # 4) Mensual ("mayo 2026", "may 2026", "MAYO 2026")
    m = _RE_MENSUAL.match(t)
    if m:
        mes = _ALIASES_MES.get(m.group(1).lower())
        anio = _normalizar_anio(m.group(2))
        if mes:
            return primer_dia(anio, mes), ultimo_dia(anio, mes)

    return None, None
