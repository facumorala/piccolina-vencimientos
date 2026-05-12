"""
Modelos SQLAlchemy del Dashboard Vencimientos Piccolina.

Decisiones de diseño documentadas:
- Toda la lógica de recurrencia vive en el propio Vencimiento (flag es_recurrente).
  No hay tabla aparte de "Reglas Recurrentes" — el generador mensual lee los
  vencimientos del mes anterior marcados como recurrentes y los replica.
- Estimado se aplica tanto a montos como a fechas (flags independientes).
- Los Planes (categoría Financiaciones) generan automáticamente sus N cuotas
  como Vencimientos al crearse.
- Las 3 fechas distintas: periodo_facturado (string libre, ej "mar-abr 2026"),
  fecha_vencimiento (cuándo pagar), fecha_pago (cuándo se pagó efectivamente).
"""
from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Date, DateTime,
    Numeric, ForeignKey, Text, Index, inspect, text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


# ─── Constantes de dominio ────────────────────────────────────────────────────

CATEGORIAS = ["impuestos", "cargas_sociales", "financiaciones", "honorarios", "servicios"]
ROLES = ["facu", "contadoras"]
METODOS_PAGO = ["efectivo", "transfer", "debito_automatico", "tarjeta"]
ESTADOS_PLAN = ["activo", "terminado", "incumplido"]
FUENTES_PLAN = ["AFIP", "otro"]


# ─── User ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    email = Column(String(180), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    password_hash = Column(String(255), nullable=False)
    rol = Column(String(20), nullable=False, default="contadoras")  # 'facu' | 'contadoras'
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Vencimiento ──────────────────────────────────────────────────────────────

class Vencimiento(Base):
    __tablename__ = "vencimientos"
    id = Column(Integer, primary_key=True)

    # Clasificación
    categoria = Column(String(40), nullable=False)        # ver CATEGORIAS
    tipo = Column(String(80), nullable=False)             # libre con autocompletado: 'IVA', 'Edenor 1', etc.
    concepto = Column(String(200), nullable=False)        # 'IIBB nov 2025', 'Edenor medidor 1 mar-abr 26'

    # Período facturado: a qué consumo/mes corresponde la factura, independiente
    # de cuándo se paga. Texto libre porque puede ser 'mar-abr 2026' (bimestral)
    # o '2025' (Bienes Personales) o 'mayo 2026' (mensual).
    periodo_facturado = Column(String(80), nullable=True)

    # Importe
    monto = Column(Numeric(14, 2), nullable=True)        # nullable porque puede faltar dato
    monto_estimado = Column(Boolean, default=False, nullable=False)
    estimado_monto_de = Column(String(40), nullable=True)  # 'abril 2026'

    # Fecha de vencimiento
    fecha_vencimiento = Column(Date, nullable=True)      # nullable porque puede faltar dato
    fecha_estimada = Column(Boolean, default=False, nullable=False)

    # Pago
    pagado = Column(Boolean, default=False, nullable=False)
    fecha_pago = Column(Date, nullable=True)
    metodo_pago = Column(String(30), nullable=True)      # ver METODOS_PAGO

    # Intereses por pago fuera de término (cargados manualmente cuando la contadora
    # avisa que se generaron). Se suman al `monto` original para el total a pagar
    # y alimentan el KPI "lo que estoy perdiendo por pagar tarde" del home.
    monto_intereses = Column(Numeric(14, 2), nullable=True)
    link_intereses = Column(Text, nullable=True)         # URL o nota del comprobante de intereses

    # Recurrencia
    es_recurrente = Column(Boolean, default=True, nullable=False)   # opuesto: esporádico
    pausado = Column(Boolean, default=False, nullable=False)        # si está pausado no se replica

    # Plan (si está cubierto por una financiación)
    plan_id = Column(Integer, ForeignKey("planes.id", ondelete="SET NULL"), nullable=True)
    plan_cuota_nro = Column(Integer, nullable=True)      # ej 7 (de 7/8)

    # Otros
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    plan = relationship("Plan", back_populates="vencimientos_cubiertos", foreign_keys=[plan_id])


# Índices para queries comunes
Index("ix_vencimientos_fecha_pagado", Vencimiento.fecha_vencimiento, Vencimiento.pagado)
Index("ix_vencimientos_categoria", Vencimiento.categoria)
Index("ix_vencimientos_plan_id", Vencimiento.plan_id)


# ─── Plan / Financiación ──────────────────────────────────────────────────────

class Plan(Base):
    __tablename__ = "planes"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), nullable=False)         # libre: 'Plan A', 'Plan AFIP IVA 2025', etc.
    fuente = Column(String(20), nullable=False, default="AFIP")  # 'AFIP' | 'otro'

    monto_cuota = Column(Numeric(14, 2), nullable=False)
    cuotas_totales = Column(Integer, nullable=False)
    dia_del_mes = Column(Integer, nullable=False)        # 1-31
    fecha_primera_cuota = Column(Date, nullable=False)

    obligaciones_cubiertas = Column(Text, nullable=True)  # qué obligaciones cubre este plan
    estado = Column(String(20), nullable=False, default="activo")  # ver ESTADOS_PLAN

    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Vencimientos generados automáticamente al crear el plan (las cuotas)
    # son Vencimientos con plan_id apuntando a este plan y plan_cuota_nro.
    cuotas = relationship(
        "Vencimiento",
        primaryjoin="and_(Plan.id==Vencimiento.plan_id, Vencimiento.plan_cuota_nro.isnot(None))",
        foreign_keys="[Vencimiento.plan_id]",
        viewonly=True,
    )

    # Vencimientos cubiertos por este plan (los viejos que el plan absorbió).
    vencimientos_cubiertos = relationship(
        "Vencimiento",
        back_populates="plan",
        foreign_keys="[Vencimiento.plan_id]",
    )


# ─── Ficha / Instructivo ──────────────────────────────────────────────────────

class Ficha(Base):
    """
    Manual interno: una ficha por TIPO de vencimiento (IVA, Edenor 1, Alquiler…).
    Funciona como knowledge base para que Facu o cualquier persona nueva sepa
    de dónde se saca el comprobante, cómo se paga, cuándo aparece, etc.

    El campo `tipo` matchea contra Vencimiento.tipo (no hay FK, es texto).
    Así una sola ficha sirve para todos los vencimientos de ese tipo.
    """
    __tablename__ = "fichas"
    id = Column(Integer, primary_key=True)

    categoria = Column(String(40), nullable=False)         # ver CATEGORIAS
    tipo = Column(String(80), unique=True, nullable=False) # clave única — matchea Vencimiento.tipo
    titulo = Column(String(200), nullable=True)            # display largo opcional (ej "IVA — Impuesto al Valor Agregado")

    # 6 casilleros sugeridos, todos opcionales (texto plano con saltos de línea)
    que_es = Column(Text, nullable=True)
    de_donde_se_saca = Column(Text, nullable=True)
    cuando_aparece = Column(Text, nullable=True)
    como_se_paga = Column(Text, nullable=True)
    contactos = Column(Text, nullable=True)
    notas = Column(Text, nullable=True)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    actualizado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    actualizado_por = relationship("User")


Index("ix_fichas_tipo", Ficha.tipo)
Index("ix_fichas_categoria", Ficha.categoria)


# ─── Log de actividad ─────────────────────────────────────────────────────────

class LogActividad(Base):
    """
    Registra acciones de las contadoras (Agus) para que el bot mande aviso a Facu.
    No se muestra en la UI — es solo para alimentar las notificaciones Telegram.
    """
    __tablename__ = "log_actividad"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    accion = Column(String(40), nullable=False)           # 'crear' | 'editar' | 'pagar' | 'eliminar' | 'crear_plan' | etc.
    vencimiento_id = Column(Integer, ForeignKey("vencimientos.id", ondelete="SET NULL"), nullable=True)
    plan_id = Column(Integer, ForeignKey("planes.id", ondelete="SET NULL"), nullable=True)
    descripcion = Column(String(400), nullable=False)
    notificado_telegram = Column(Boolean, default=False, nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")


Index("ix_log_actividad_notificado", LogActividad.notificado_telegram, LogActividad.creado_en)


# ─── Factory de engine y session ──────────────────────────────────────────────

def get_engine(database_url: str):
    """Crea el engine SQLAlchemy.

    Normaliza la URL de Railway al driver psycopg v3 (compatible con Python 3.13):
      - postgres://...        → postgresql+psycopg://...
      - postgresql://...      → postgresql+psycopg://...
    Cualquier otra URL (ej sqlite:///) se deja pasar tal cual.
    """
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
    return create_engine(database_url, future=True, pool_pre_ping=True)


def init_db(engine):
    """Crea las tablas si no existen y devuelve la SessionFactory."""
    Base.metadata.create_all(engine)
    _aplicar_migraciones_ligeras(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _aplicar_migraciones_ligeras(engine):
    """
    Mini-migrador: agrega columnas nuevas a tablas existentes con ALTER TABLE.
    SQLAlchemy `create_all` solo crea tablas faltantes, no columnas faltantes.
    Acá listamos los ADD COLUMN que hicieron falta entre versiones.
    """
    columnas_a_agregar = [
        # (tabla, columna, tipo SQL portable)
        ("vencimientos", "monto_intereses", "NUMERIC(14, 2)"),
        ("vencimientos", "link_intereses",  "TEXT"),
    ]
    insp = inspect(engine)
    with engine.begin() as conn:
        for tabla, columna, tipo in columnas_a_agregar:
            if not insp.has_table(tabla):
                continue
            existentes = {c["name"] for c in insp.get_columns(tabla)}
            if columna in existentes:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))
                print(f"[migracion] {tabla}.{columna} agregada.", flush=True)
            except Exception as e:
                print(f"[migracion] No se pudo agregar {tabla}.{columna}: {e}", flush=True)
