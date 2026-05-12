"""Decoradores de autenticación y autorización."""
from functools import wraps
from flask import session, redirect, url_for, request, flash

from extensions import get_db
from models import User


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def facu_required(f):
    """Solo Facu (no contadoras). Para administración de usuarios."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.url))
        u = get_db().get(User, session["user_id"])
        if not u or u.rol != "facu":
            flash("Acción solo disponible para Facu.", "error")
            return redirect(url_for("home.dashboard"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" in session:
        return get_db().get(User, session["user_id"])
    return None
