"""Registra todos los blueprints del dashboard Vencimientos."""
from .auth import bp as auth_bp
from .home import bp as home_bp
from .vencimientos import bp as vencimientos_bp
from .planes import bp as planes_bp
from .fichas import bp as fichas_bp
from .config import bp as config_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(vencimientos_bp, url_prefix="/vencimientos")
    app.register_blueprint(planes_bp, url_prefix="/planes")
    app.register_blueprint(fichas_bp, url_prefix="/manual")
    app.register_blueprint(config_bp, url_prefix="/config")
