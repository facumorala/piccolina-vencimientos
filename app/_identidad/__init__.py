"""
Identidad visual compartida de Piccolina.

Este paquete contiene los activos de marca y los partials Jinja2 que arman
la cara visual del sistema. Lo usan todos los dashboards: Compras, Mayorista,
Minorista, Sueldos, Financiero, Vencimientos.

Uso desde una app Flask:

    from _compartido.identidad import register_identidad

    app = Flask(...)
    register_identidad(app)

Después en cualquier template de la app:

    {% extends 'identidad/base_identidad.html' %}
    {% block title %}Mi página{% endblock %}
    {% block content %}
       ...
    {% endblock %}

Y para overridear el color de marca (por ej. Mayoristas en verde):

    {% extends 'identidad/base_identidad.html' %}
    {% block brand_var %}--helecho{% endblock %}
    {% block brand_var_2 %}#007a06{% endblock %}
    {% block brand_var_soft %}#e7f1e6{% endblock %}
"""
import os

from flask import Blueprint
from jinja2 import ChoiceLoader, FileSystemLoader

_HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_HERE, "static")
TEMPLATES_DIR = os.path.join(_HERE, "templates")


def make_identidad_blueprint() -> Blueprint:
    """
    Crea un blueprint que sirve los static de la identidad bajo /_identidad/...
    No registra rutas de aplicación — solo asset serving.
    """
    bp = Blueprint(
        "identidad",
        __name__,
        static_folder=STATIC_DIR,
        static_url_path="/_identidad",
        template_folder=TEMPLATES_DIR,
    )
    return bp


def register_identidad(app):
    """
    Registra el blueprint y agrega el folder de templates al loader de Jinja
    de la app, para que `{% extends 'identidad/base_identidad.html' %}` funcione.
    """
    bp = make_identidad_blueprint()
    app.register_blueprint(bp)

    # Mergear el folder de templates compartidos con el de la app, así Jinja
    # encuentra `identidad/...` además de los templates propios del dashboard.
    app.jinja_loader = ChoiceLoader([
        app.jinja_loader,
        FileSystemLoader(TEMPLATES_DIR),
    ])
