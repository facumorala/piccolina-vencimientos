"""
Bandeja de avisos: la campanita del encabezado (27-oct-2026, opción A).

Cada aviso push que este dashboard manda por el Portal queda ADEMÁS guardado
acá, uno por destinatario. Así, si alguien no abrió la notificación del
celular (o tiene los avisos apagados), la ve en la campanita con el número
rojo de "sin ver".

Copia de Compras RVR (commit f6d36a7) adaptada a SQLAlchemy "puro" (sin
Flask-SQLAlchemy): misma lógica, otra forma de abrir la base. La misma
versión vive en VENTAS MAYORISTAS. Si se cambia una, revisar las otras.

Cómo se usa:
    import bandeja_avisos
    bandeja_avisos.avisar(["facu"], "Título", "Cuerpo", ruta="/x", categoria="...")
  → guarda en la bandeja y manda el push (mismos parámetros que
    portal_avisos.avisar). Reemplaza a llamar a portal_avisos.avisar directo.

    bandeja_avisos.init_app(app, current_user)   # en create_app
  → crea la tabla (si falta), la pantalla /avisos/ y el número de la campanita.

Ojo: los avisos salen del scheduler (sin request), así que `guardar` abre su
propia sesión con SessionFactory; las pantallas usan la del request (get_db).

Quién es quién: los avisos van a usuarios del PORTAL (facu, caro, juli...).
La persona que está mirando el dashboard se reconoce por la sesión del pase
del Portal; si entró con su contraseña de siempre, por el mapa
PORTAL_SSO_USUARIOS ("facu=facu,...") leído al revés (username o email).

Visto:
  - al tocar el aviso en el celular (la ruta lleva ?aviso_visto=<grupo>),
  - al entrar a la pantalla de avisos (se marcan todos; los que eran nuevos
    se ven resaltados esa vez).
Nunca rompe al que llama: si la bandeja falla, el push sale igual.
"""
import logging
import os
import uuid
from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from sqlalchemy import Column, DateTime, Integer, String, Text

import extensions
from models import Base
from tiempo import ahora_ar

log = logging.getLogger(__name__)

# Lo que se muestra en la pantalla y cuenta para el número.
DIAS_VISIBLES = 30
MAX_EN_PANTALLA = 60
PARAM_VISTO = "aviso_visto"


class AvisoBandeja(Base):
    """Un aviso para UNA persona (usuario del Portal)."""
    __tablename__ = "aviso_bandeja"

    id = Column(Integer, primary_key=True)
    para = Column(String(50), nullable=False, index=True)
    titulo = Column(String(200), nullable=False)
    cuerpo = Column(Text, nullable=False, default="")
    ruta = Column(String(300), nullable=False, default="")
    categoria = Column(String(60), nullable=False, default="")
    # Avisos "de una sola vez" (misma clave que usa el Portal): no se repiten.
    clave = Column(String(200), index=True)
    # Los destinatarios de un mismo envío comparten grupo (va en la ruta del push).
    grupo = Column(String(32), nullable=False, index=True)
    creado_en = Column(DateTime, nullable=False, default=ahora_ar, index=True)
    visto_en = Column(DateTime)


# ── Guardar + mandar ─────────────────────────────────────────────────────
def _ruta_con_marca(ruta, grupo):
    if not ruta:
        return ruta
    sep = "&" if "?" in ruta else "?"
    return f"{ruta}{sep}{PARAM_VISTO}={grupo}"


def guardar(para, titulo, cuerpo="", ruta="", categoria="", clave=None):
    """Guarda el aviso en la bandeja de cada destinatario. Devuelve el grupo
    (o None si no guardó nada: ya estaba por la clave, o falló)."""
    para = [p.strip().lower() for p in dict.fromkeys(para or []) if p and p.strip()]
    if not para:
        return None
    db = extensions.SessionFactory()
    try:
        if clave:
            ya = {a.para for a in db.query(AvisoBandeja).filter_by(clave=clave).all()}
            para = [p for p in para if p not in ya]
            if not para:
                return None
        grupo = uuid.uuid4().hex
        for p in para:
            db.add(AvisoBandeja(
                para=p, titulo=(titulo or "")[:200], cuerpo=cuerpo or "",
                ruta=(ruta or "")[:300], categoria=(categoria or "")[:60],
                clave=clave or None, grupo=grupo))
        db.commit()
        return grupo
    except Exception as e:  # la bandeja nunca frena un aviso
        db.rollback()
        log.warning("[bandeja] no pude guardar '%s': %s", titulo, e)
        return None
    finally:
        db.close()


def avisar(para, titulo, cuerpo="", ruta="", categoria="", clave=None, esperar=False):
    """Guarda en la bandeja y manda el push por el Portal. Mismos parámetros y
    misma respuesta que portal_avisos.avisar."""
    import portal_avisos
    grupo = guardar(para, titulo, cuerpo, ruta=ruta, categoria=categoria, clave=clave)
    if grupo:
        ruta = _ruta_con_marca(ruta, grupo)
    return portal_avisos.avisar(para, titulo, cuerpo, ruta=ruta, categoria=categoria,
                                clave=clave, esperar=esperar)


# ── Quién está mirando ──────────────────────────────────────────────────
def persona_actual(user):
    """Usuario del Portal de quien está mirando, o None."""
    if user is None:
        return None
    try:
        from portal_sso import CLAVE_PERSONA
        p = (session.get(CLAVE_PERSONA) or "").strip().lower()
        if p:
            return p
    except Exception:
        pass
    nombres = {str(getattr(user, campo, "") or "").strip().lower()
               for campo in ("username", "nombre", "email")} - {""}
    for par in (os.getenv("PORTAL_SSO_USUARIOS") or "").split(","):
        if "=" in par:
            portal, local = (x.strip() for x in par.split("=", 1))
            if portal and local.lower() in nombres:
                return portal.lower()
    return None


def _desde():
    return ahora_ar() - timedelta(days=DIAS_VISIBLES)


def sin_ver(db, persona):
    if not persona:
        return 0
    return db.query(AvisoBandeja).filter(
        AvisoBandeja.para == persona, AvisoBandeja.visto_en.is_(None),
        AvisoBandeja.creado_en >= _desde()).count()


def marcar_grupo(db, persona, grupo):
    if not (persona and grupo):
        return
    db.query(AvisoBandeja).filter(
        AvisoBandeja.para == persona, AvisoBandeja.grupo == grupo,
        AvisoBandeja.visto_en.is_(None)).update(
        {AvisoBandeja.visto_en: ahora_ar()}, synchronize_session=False)
    db.commit()


# ── Pantalla + campanita ────────────────────────────────────────────────
def init_app(app, current_user):
    # Las tablas del modelo se crean al importar extensions (antes de que se
    # cargue este archivo): la de la bandeja se crea acá si falta.
    Base.metadata.create_all(extensions.engine, tables=[AvisoBandeja.__table__])

    bp = Blueprint("bandeja_avisos", __name__, url_prefix="/avisos")

    @bp.route("/")
    def lista():
        user = current_user()
        if not user:
            return redirect(url_for("auth.login", next="/avisos/"))
        db = extensions.get_db()
        persona = persona_actual(user)
        avisos = []
        if persona:
            avisos = (db.query(AvisoBandeja)
                      .filter(AvisoBandeja.para == persona, AvisoBandeja.creado_en >= _desde())
                      .order_by(AvisoBandeja.creado_en.desc(), AvisoBandeja.id.desc())
                      .limit(MAX_EN_PANTALLA).all())
        nuevos = {a.id for a in avisos if a.visto_en is None}
        if nuevos:
            # Entrar a la bandeja = verlos. Esta vez se muestran resaltados.
            db.query(AvisoBandeja).filter(AvisoBandeja.id.in_(nuevos)).update(
                {AvisoBandeja.visto_en: ahora_ar()}, synchronize_session=False)
            db.commit()
        return render_template("avisos/lista.html", avisos=avisos, nuevos=nuevos,
                               persona=persona, dias=DIAS_VISIBLES, avisos_sin_ver=0)

    @bp.route("/<int:aviso_id>")
    def abrir(aviso_id):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login", next="/avisos/"))
        db = extensions.get_db()
        aviso = db.get(AvisoBandeja, aviso_id)
        if aviso is None or aviso.para != persona_actual(user):
            abort(404)
        if aviso.visto_en is None:
            aviso.visto_en = ahora_ar()
            db.commit()
        destino = aviso.ruta or "/"
        if not destino.startswith("/") or destino.startswith("//"):
            destino = "/"
        return redirect(destino)

    app.register_blueprint(bp)

    @app.before_request
    def _marcar_visto_desde_el_push():
        grupo = request.args.get(PARAM_VISTO)
        if not grupo:
            return
        db = extensions.get_db()
        try:
            marcar_grupo(db, persona_actual(current_user()), grupo[:32])
        except Exception as e:
            db.rollback()
            log.warning("[bandeja] no pude marcar el aviso visto: %s", e)

    @app.context_processor
    def _campanita():
        # Sin usuario del Portal no recibe avisos: la campanita queda como
        # estaba, sin link.
        db = extensions.get_db()
        try:
            persona = persona_actual(current_user())
            return {"avisos_persona": persona, "avisos_sin_ver": sin_ver(db, persona)}
        except Exception:
            db.rollback()
            return {"avisos_persona": None, "avisos_sin_ver": 0}
