"""
Entry point del dashboard Vencimientos. Lo arranca tanto:
- En local: `python -m app.app` (o `cd app && python app.py`).
- En Railway: `gunicorn --chdir app app:app ...` (ver Procfile).
"""
import os
from dotenv import load_dotenv

load_dotenv()

from app_factory import create_app, start_background_services

app = create_app()

# Servicios background (bot + scheduler). Solo si no estamos en modo test.
if os.getenv("FLASK_TESTING") != "1":
    start_background_services()


if __name__ == "__main__":
    # Modo local de desarrollo.
    port = int(os.getenv("PORT", "5005"))
    debug = os.getenv("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
