"""
Recursos compartidos: engine de DB, session factory, limiter, helpers de fecha.
Patrón idéntico al Mayorista para mantener consistencia entre dashboards.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Zona horaria Argentina (UTC-3)
AR_TZ = timezone(timedelta(hours=-3))


def today_ar() -> date:
    """Fecha de hoy en hora argentina."""
    return datetime.now(AR_TZ).date()


# Paths del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))       # …/sistema-vencimientos/app
ROOT_DIR = os.path.dirname(BASE_DIR)                        # …/sistema-vencimientos

# Asegurar que app/ esté en el sys.path para imports directos
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# ─── DB ──────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(BASE_DIR, "vencimientos.db")
_db_url = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

from models import get_engine, init_db  # noqa: E402

engine = get_engine(_db_url)
SessionFactory = init_db(engine)


def get_db():
    """Devuelve la session de DB del request actual (la crea si hace falta)."""
    from flask import g
    if "db" not in g:
        g.db = SessionFactory()
    return g.db


def close_db(exc):
    """Cierra la session de DB al final del request."""
    from flask import g
    db = g.pop("db", None)
    if db:
        db.close()


# ─── Rate limiter ────────────────────────────────────────────────────────────

limiter = Limiter(
    get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)


# ─── Seed ────────────────────────────────────────────────────────────────────

def run_seed():
    """Corre el seed si la DB está vacía o si FORCE_PASSWORD_RESET=true."""
    try:
        from models import User
        db = SessionFactory()
        count = db.query(User).count()
        force_reset = os.getenv("FORCE_PASSWORD_RESET", "").lower() in ("1", "true", "yes")
        print(f"[startup] Users in DB: {count}, FORCE_PASSWORD_RESET={force_reset}", flush=True)
        if count == 0 or force_reset:
            reason = "DB vacía" if count == 0 else "FORCE_PASSWORD_RESET=true"
            print(f"[startup] Running seed ({reason})...", flush=True)
            import seed as _seed
            _seed.seed()
            print("[startup] Seed completado.", flush=True)
            if force_reset:
                print("[startup] Contraseñas reseteadas. BORRA FORCE_PASSWORD_RESET de Railway.", flush=True)
        db.close()
    except Exception as e:
        print(f"[startup] Seed error: {e}", flush=True)
        import traceback as _tb
        _tb.print_exc()
