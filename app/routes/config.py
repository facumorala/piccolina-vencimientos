"""
Configuración: cambiar contraseña propia + alta/baja de contadoras (solo Facu).
"""
import bcrypt
from flask import Blueprint, render_template, request, flash, redirect, url_for, session

from extensions import get_db
from models import User, Vencimiento
from auth_helpers import login_required, facu_required, current_user
from nav_helpers import redirect_back

bp = Blueprint("config", __name__)


@bp.route("/")
@login_required
def view():
    db = get_db()
    contadoras = []
    n_vencimientos = 0
    u = current_user()
    if u and u.rol == "facu":
        contadoras = db.query(User).filter(User.rol == "contadoras").all()
        # Cuántos vencimientos sueltos hay (los que borraría "Empezar de cero").
        # No cuenta las cuotas de financiaciones (esas se conservan).
        n_vencimientos = db.query(Vencimiento).filter(Vencimiento.plan_id.is_(None)).count()
    return render_template("config/view.html", contadoras=contadoras, n_vencimientos=n_vencimientos)


@bp.route("/empezar-de-cero", methods=["POST"])
@facu_required
def empezar_de_cero():
    """
    Borra TODOS los vencimientos sueltos para que Facu cargue de cero.

    Qué borra: los vencimientos que NO son cuotas de una financiación
    (plan_id IS NULL), pagados o no.
    Qué CONSERVA: las financiaciones (planes AFIP con sus cuotas), el Manual
    de instructivos (fichas) y los usuarios.

    Pide confirmación por texto ('EMPEZAR DE CERO') para que no se dispare por
    accidente. Acción irreversible.
    """
    db = get_db()
    confirmacion = (request.form.get("confirmacion") or "").strip().upper()
    if confirmacion != "EMPEZAR DE CERO":
        flash("Para borrar tenés que escribir EMPEZAR DE CERO tal cual. No se borró nada.", "error")
        return redirect_back("config.view")

    n = (
        db.query(Vencimiento)
        .filter(Vencimiento.plan_id.is_(None))
        .delete(synchronize_session=False)
    )
    db.commit()
    flash(
        f"Listo. Borré {n} vencimiento{'s' if n != 1 else ''}. "
        "Las financiaciones y el Manual quedaron intactos — podés cargar todo de cero.",
        "success",
    )
    return redirect_back("config.view")


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
