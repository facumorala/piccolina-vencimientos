"""
Configuración: cambiar contraseña propia + alta/baja de contadoras (solo Facu).
"""
import bcrypt
from flask import Blueprint, render_template, request, flash, redirect, url_for, session

from extensions import get_db
from models import User
from auth_helpers import login_required, facu_required, current_user
from nav_helpers import redirect_back

bp = Blueprint("config", __name__)


@bp.route("/")
@login_required
def view():
    db = get_db()
    contadoras = []
    u = current_user()
    if u and u.rol == "facu":
        contadoras = db.query(User).filter(User.rol == "contadoras").all()
    return render_template("config/view.html", contadoras=contadoras)


@bp.route("/cambiar-password", methods=["POST"])
@login_required
def cambiar_password():
    db = get_db()
    u = current_user()
    actual = request.form.get("password_actual", "")
    nueva = request.form.get("password_nueva", "")
    if not bcrypt.checkpw(actual.encode(), u.password_hash.encode()):
        flash("La contraseña actual es incorrecta.", "error")
        return redirect_back("config.view")
    if len(nueva) < 6:
        flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        return redirect_back("config.view")
    u.password_hash = bcrypt.hashpw(nueva.encode(), bcrypt.gensalt()).decode()
    db.commit()
    flash("Contraseña actualizada.", "success")
    return redirect_back("config.view")


@bp.route("/contadoras/nueva", methods=["POST"])
@facu_required
def nueva_contadora():
    db = get_db()
    username = request.form.get("username", "").lower().strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").lower().strip()
    password = request.form.get("password", "")
    if not all([username, name, email, password]):
        flash("Todos los campos son obligatorios.", "error")
        return redirect_back("config.view")
    if db.query(User).filter_by(username=username).first():
        flash("Ya existe un usuario con ese username.", "error")
        return redirect_back("config.view")
    u = User(
        username=username,
        name=name,
        email=email,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        rol="contadoras",
        active=True,
    )
    db.add(u)
    db.commit()
    flash(f"Contadora '{name}' creada.", "success")
    return redirect_back("config.view")


@bp.route("/contadoras/<int:uid>/reset-password", methods=["POST"])
@facu_required
def reset_password_contadora(uid):
    db = get_db()
    u = db.get(User, uid)
    if not u or u.rol != "contadoras":
        flash("Usuaria no encontrada.", "error")
        return redirect_back("config.view")
    nueva = request.form.get("password_nueva", "")
    if len(nueva) < 6:
        flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        return redirect_back("config.view")
    u.password_hash = bcrypt.hashpw(nueva.encode(), bcrypt.gensalt()).decode()
    db.commit()
    flash(f"Contraseña de '{u.name}' actualizada.", "success")
    return redirect_back("config.view")


@bp.route("/contadoras/<int:uid>/baja", methods=["POST"])
@facu_required
def baja_contadora(uid):
    db = get_db()
    u = db.get(User, uid)
    if u and u.rol == "contadoras":
        u.active = False
        db.commit()
        flash(f"Contadora '{u.name}' dada de baja.", "success")
    return redirect_back("config.view")
