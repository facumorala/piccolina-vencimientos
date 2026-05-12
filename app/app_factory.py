"""
Factory de la app Flask. Arma config, extensiones, blueprints, seed y
servicios en background (bot + scheduler). Idéntico patrón al Mayorista.
"""
import os
import secrets as _secrets
from datetime import timedelta

from flask import Flask, session, url_for

from extensions import limiter, close_db, run_seed
from jinja_setup import register_jinja
from routes import register_blueprints
from _identidad import register_identidad


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ── SECRET_KEY ──────────────────────────────────────────────────────────
    _secret = os.getenv("SECRET_KEY")
    if not _secret or _secret == "cambiar-en-prod-por-string-random-largo":
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY no configurada. Setear en Railway con un string "
                "random de 32+ caracteres."
            )
        _secret = _secrets.token_hex(32)
        print("[app] SECRET_KEY no seteada, usando random de desarrollo.", flush=True)
    app.secret_key = _secret

    # ── Cookies de sesión seguras ───────────────────────────────────────────
    app.config.update(
        SESSION_COOKIE_SECURE=(os.getenv("FLASK_ENV") == "production"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )

    # ── Extensiones ─────────────────────────────────────────────────────────
    limiter.init_app(app)
    app.teardown_appcontext(close_db)

    # ── Jinja (filtros + globals) ───────────────────────────────────────────
    register_jinja(app)

    # ── Identidad visual compartida (CSS, fuentes, logos, partials base) ────
    register_identidad(app)

    # ── Context processor: inyecta nav + user para todos los templates ──────
    _register_context(app)

    # ── Seed inicial (corre solo si DB vacía o FORCE_PASSWORD_RESET) ────────
    with app.app_context():
        run_seed()

    # ── Blueprints ──────────────────────────────────────────────────────────
    register_blueprints(app)

    # ── Cache off para HTML (forzar refresh tras POST+redirect) ─────────────
    @app.after_request
    def _no_cache_html(response):
        ctype = response.headers.get("Content-Type", "")
        if ctype.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return app


def _register_context(app: Flask):
    """Inyecta variables comunes en todos los templates (las que esperan los
    partials del blueprint _identidad)."""

    @app.context_processor
    def inject_defaults():
        # Iniciales del usuario logueado (ej "FM" → Facu Morala)
        initials = ""
        name = session.get("user_name") or ""
        if name:
            parts = [p for p in name.strip().split() if p]
            initials = (parts[0][:1] + (parts[1][:1] if len(parts) > 1 else "")).upper()

        # Nav: solo si está logueado
        nav_pages = []
        if session.get("user_id"):
            nav_pages = [
                {"key": "home",         "label": "Inicio",         "href": url_for("home.dashboard")},
                {"key": "vencimientos", "label": "Vencimientos",   "href": url_for("vencimientos.list_view")},
                {"key": "planes",       "label": "Financiaciones", "href": url_for("planes.list_view")},
                {"key": "fichas",       "label": "Manual",         "href": url_for("fichas.list_view")},
                {"key": "config",       "label": "Configuración",  "href": url_for("config.view")},
            ]

        # Determinar la página activa del nav según el endpoint actual
        from flask import request
        current_page_key = ""
        if request.endpoint:
            if request.endpoint.startswith("home"):
                current_page_key = "home"
            elif request.endpoint.startswith("vencimientos"):
                current_page_key = "vencimientos"
            elif request.endpoint.startswith("planes"):
                current_page_key = "planes"
            elif request.endpoint.startswith("fichas"):
                current_page_key = "fichas"
            elif request.endpoint.startswith("config"):
                current_page_key = "config"

        return {
            # Header
            "app_label":          "Vencimientos",
            "brand_subtitle":     "Vencimientos",
            "brand_variant":      "vulcan",   # negro arriba (overrideamos color via CSS)
            "nav_pages":          nav_pages,
            "current_page_key":   current_page_key,
            "user_initials":      initials,
            "user_name":          name,
            "user_rol":           session.get("user_rol", ""),
            "notif_count":        0,
            "search_placeholder": "Buscar vencimientos…",
            # Footer
            "app_version":        "v0.1",
            "show_pattern":       True,
        }


def start_background_services():
    """Arranca bot de Telegram + scheduler de avisos/generador mensual."""
    _start_telegram_bot()
    _start_scheduler()


def _start_telegram_bot():
    """Bot de Telegram en hilo daemon (modo polling)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[bot] TELEGRAM_BOT_TOKEN no configurado, bot desactivado.", flush=True)
        return
    try:
        import threading
        from services.telegram_bot import run_bot_polling
        t = threading.Thread(target=run_bot_polling, daemon=True)
        t.start()
        print("[bot] Bot de Telegram iniciado (polling).", flush=True)
    except Exception as e:
        print(f"[bot] Error iniciando bot: {e}", flush=True)


def _start_scheduler():
    """APScheduler con los jobs de avisos + generador mensual."""
    try:
        from services.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"[scheduler] Error iniciando scheduler: {e}", flush=True)
