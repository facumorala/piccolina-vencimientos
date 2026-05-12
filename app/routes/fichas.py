"""
Manual / Fichas: instructivo por tipo de vencimiento.

Una ficha por TIPO (IVA, Edenor 1, Alquiler, etc.). Casilleros sugeridos:
qué es / de dónde se saca / cuándo aparece / cómo se paga / contactos / notas.

Acceso:
  - Sección "Manual" en el nav principal — listado agrupado por categoría.
  - Ícono 📖 al lado de cada vencimiento del listado — abre modal con la ficha.
"""
from flask import (
    Blueprint, render_template, request, flash, redirect, url_for, jsonify,
)

from extensions import get_db
from models import Ficha, LogActividad, CATEGORIAS
from auth_helpers import login_required, current_user
from nav_helpers import redirect_back

bp = Blueprint("fichas", __name__)


# ─── Listado (sección Manual del menú) ────────────────────────────────────────

@bp.route("/")
@login_required
def list_view():
    db = get_db()
    q = (request.args.get("q") or "").strip().lower()

    query = db.query(Ficha)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Ficha.tipo.ilike(like)) |
            (Ficha.titulo.ilike(like)) |
            (Ficha.que_es.ilike(like))
        )
    fichas = query.order_by(Ficha.categoria.asc(), Ficha.tipo.asc()).all()

    # Agrupar por categoría
    agrupadas = {}
    for f in fichas:
        agrupadas.setdefault(f.categoria, []).append(f)

    return render_template(
        "fichas/list.html",
        agrupadas=agrupadas,
        categorias=CATEGORIAS,
        q=q,
    )


# ─── Detalle (vista de lectura) ───────────────────────────────────────────────

@bp.route("/<string:tipo>")
@login_required
def detalle(tipo):
    db = get_db()
    f = db.query(Ficha).filter(Ficha.tipo == tipo).first()
    if not f:
        # Si no existe, mandar al formulario de creación con el tipo precargado
        return redirect(url_for("fichas.editar", tipo=tipo))
    return render_template("fichas/detalle.html", f=f)


# ─── Formulario (crear / editar) ──────────────────────────────────────────────

@bp.route("/<string:tipo>/editar", methods=["GET", "POST"])
@login_required
def editar(tipo):
    db = get_db()
    f = db.query(Ficha).filter(Ficha.tipo == tipo).first()
    es_nueva = f is None

    if request.method == "POST":
        categoria = request.form.get("categoria") or ""
        if categoria not in CATEGORIAS:
            flash("Categoría inválida.", "error")
            return redirect_back("fichas.list_view")

        u = current_user()
        if es_nueva:
            f = Ficha(tipo=tipo, categoria=categoria)
            db.add(f)
        else:
            f.categoria = categoria

        f.titulo = (request.form.get("titulo") or "").strip() or None
        f.que_es = (request.form.get("que_es") or "").strip() or None
        f.de_donde_se_saca = (request.form.get("de_donde_se_saca") or "").strip() or None
        f.cuando_aparece = (request.form.get("cuando_aparece") or "").strip() or None
        f.como_se_paga = (request.form.get("como_se_paga") or "").strip() or None
        f.contactos = (request.form.get("contactos") or "").strip() or None
        f.notas = (request.form.get("notas") or "").strip() or None
        f.actualizado_por_id = u.id if u else None

        db.commit()
        accion = "ficha_crear" if es_nueva else "ficha_editar"
        verbo = "Creó" if es_nueva else "Actualizó"
        _log_actividad(db, accion, f"{verbo} la ficha de {f.tipo}")
        flash(f"Ficha de '{f.tipo}' {'creada' if es_nueva else 'actualizada'}.", "success")
        return redirect(url_for("fichas.detalle", tipo=f.tipo))

    return render_template(
        "fichas/form.html",
        f=f,
        tipo=tipo,
        es_nueva=es_nueva,
        categorias=CATEGORIAS,
    )


# ─── Endpoint JSON para el modal del listado de vencimientos ─────────────────

@bp.route("/<string:tipo>/json")
@login_required
def json_view(tipo):
    db = get_db()
    f = db.query(Ficha).filter(Ficha.tipo == tipo).first()
    if not f:
        return jsonify({
            "existe": False,
            "tipo": tipo,
            "url_crear": url_for("fichas.editar", tipo=tipo),
        })
    return jsonify({
        "existe": True,
        "tipo": f.tipo,
        "titulo": f.titulo or f.tipo,
        "categoria": f.categoria,
        "que_es": f.que_es,
        "de_donde_se_saca": f.de_donde_se_saca,
        "cuando_aparece": f.cuando_aparece,
        "como_se_paga": f.como_se_paga,
        "contactos": f.contactos,
        "notas": f.notas,
        "actualizado_en": f.actualizado_en.strftime("%d/%m/%Y") if f.actualizado_en else None,
        "actualizado_por": f.actualizado_por.name if f.actualizado_por else None,
        "url_editar": url_for("fichas.editar", tipo=f.tipo),
        "url_detalle": url_for("fichas.detalle", tipo=f.tipo),
    })


# ─── Helper ───────────────────────────────────────────────────────────────────

def _log_actividad(db, accion, descripcion):
    """Solo registra si el actor NO es Facu (los avisos Telegram son para Facu)."""
    u = current_user()
    if not u or u.rol == "facu":
        return
    log = LogActividad(
        user_id=u.id,
        accion=accion,
        vencimiento_id=None,
        plan_id=None,
        descripcion=descripcion,
    )
    db.add(log)
    db.commit()
