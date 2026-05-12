"""Auth: /login y /logout. Session manual con bcrypt."""
import bcrypt
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from extensions import get_db, limiter
from models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "10 per 15 minutes",
    methods=["POST"],
    error_message="Demasiados intentos. Esperá 15 minutos y volvé.",
)
def login():
    if request.method == "POST":
        username = request.form.get("username", "").lower().strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.query(User).filter_by(username=username, active=True).first()
        if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            session.permanent = True
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_rol"] = user.rol
            flash(f"Bienvenido/a, {user.name}!", "success")
            return redirect(request.args.get("next") or url_for("home.dashboard"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
