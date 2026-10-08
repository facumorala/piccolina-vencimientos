"""
Puerta del pase del Portal Piccolina (inicio de sesión unificado).

FUENTE ÚNICA: _compartido/portal_sso.py. Cada dashboard tiene una COPIA
idéntica en app/portal_sso.py. Si se cambia algo, cambiarlo acá y volver a
copiarlo a todos (y correr sus tests).

Qué hace:
- Agrega la ruta /sso. El Portal manda a la persona ahí con un "pase"
  firmado (?t=...). Si la firma es válida, el pase no venció (60 s), no se
  usó antes y es para ESTE sistema, la persona entra con su usuario local.
- La sesión que abre el pase dura 1 hora. Al vencer, si la persona sigue
  navegando, se la manda de vuelta al Portal, que (si sigue habilitada) le
  da un pase nuevo sin que note nada. Así, si Facu le corta el acceso a
  alguien en el Portal, queda afuera de todos los sistemas en menos de 1 h.
- El login de siempre del dashboard NO se toca: sigue andando aparte.
- Salto automático: en un navegador que ya entró alguna vez por el Portal
  (queda marcado con una cookie), si el dashboard pide login se lo manda
  directo al Portal y vuelve a la pantalla que había pedido (ej. el link de
  un pedido que llegó por Telegram). Los navegadores que nunca usaron el
  Portal (el celular de la caja, la contadora) siguen viendo el login normal.
  Para forzar el login normal: /login?local=1

Variables de entorno (si falta alguna, la puerta queda apagada y /sso da 404):
- PORTAL_URL           dirección del Portal (ej. https://portal...app)
- PORTAL_SSO_SLUG      nombre de este sistema en el Portal (ej. piccolina-compras)
- PORTAL_SSO_SECRET    llave secreta compartida SOLO entre este sistema y el Portal
- PORTAL_SSO_USUARIOS  quién es quién: "facu=facu.compras,caro=Caro"
                       (usuario del Portal = usuario de este dashboard)

Cómo se conecta (en create_app):
    from app.portal_sso import init_portal_sso
    init_portal_sso(app, buscar_usuario=..., iniciar_sesion=...)
"""
import os
import threading
import time
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, Response, abort, jsonify, redirect, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Debe ser IGUAL al del Portal (app/routes.py → SALT_PASE).
SALT_PASE = "portal-piccolina-sso-v1"
# Para la consulta de avisos (distinta, así un pedido de avisos no sirve para entrar).
SALT_AVISOS = "portal-piccolina-avisos-v1"
VIGENCIA_PASE = 60          # segundos que vale un pase
DURACION_SESION = 3600      # la sesión abierta por el Portal dura 1 hora
GRACIA_FORMULARIOS = 900    # 15 min extra para no perder un formulario a medio guardar

CLAVE_HASTA = "portal_sso_hasta"
CLAVE_PERSONA = "portal_sso_u"
# Marca de "este navegador usa el Portal" (dura ~1 año).
COOKIE_MARCA = "portal_sso_conocido"
DURACION_MARCA = 400 * 24 * 3600
# Recién cerró sesión en el dashboard: no saltar al Portal por 10 minutos.
COOKIE_SALIO = "portal_sso_salio"

# Pases ya usados (cada dashboard corre con un solo proceso en Railway).
_usados = {}
_candado = threading.Lock()


def _config():
    portal = (os.getenv("PORTAL_URL") or "").rstrip("/")
    slug = os.getenv("PORTAL_SSO_SLUG") or ""
    secreto = os.getenv("PORTAL_SSO_SECRET") or ""
    mapa = {}
    for par in (os.getenv("PORTAL_SSO_USUARIOS") or "").split(","):
        if "=" in par:
            k, v = par.split("=", 1)
            if k.strip() and v.strip():
                mapa[k.strip().lower()] = v.strip()
    if not (portal and slug and secreto):
        return None
    return {"portal": portal, "slug": slug, "secreto": secreto, "mapa": mapa}


def _marcar_usado(numero):
    """True si el pase es nuevo (y lo marca); False si ya se había usado."""
    ahora = time.time()
    with _candado:
        for n, vence in list(_usados.items()):
            if vence < ahora:
                del _usados[n]
        if numero in _usados:
            return False
        _usados[numero] = ahora + VIGENCIA_PASE + 5
        return True


def _ruta_segura(destino):
    if not destino or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return None
    return destino


def _ruta_interna(destino):
    """
    Deja solo la ruta interna de un 'next'. Algunos dashboards mandan la URL
    completa (https://este-dominio/pedido/5): si es de este mismo sitio se
    queda con '/pedido/5'; si es de otro sitio, la descarta.
    """
    if not destino:
        return None
    if destino.startswith(("http://", "https://")):
        partes = urlsplit(destino)
        if partes.netloc != request.host:
            return None
        destino = partes.path + (f"?{partes.query}" if partes.query else "")
    return _ruta_segura(destino)


def _pagina_error(titulo, detalle, portal, codigo):
    """Página simple y autocontenida (no depende de las plantillas del dashboard)."""
    volver = f'<p><a href="{portal}/">← Volver al Portal</a></p>' if portal else ""
    html = (
        "<!doctype html><html lang=es><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{titulo}</title>"
        "<body style=\"font-family:system-ui,sans-serif;background:#FDF8EF;color:#1E1C1D;"
        "max-width:420px;margin:15vh auto;padding:0 16px\">"
        f"<h1 style='font-weight:400;color:#990B1A'>{titulo}</h1><p>{detalle}</p>{volver}</body></html>"
    )
    return Response(html, status=codigo, mimetype="text/html")


def _es_navegacion():
    """GET de una página entera (no un pedido de fondo AJAX/HTMX)."""
    if request.method != "GET":
        return False
    if request.headers.get("HX-Request") or request.headers.get("X-Requested-With"):
        return False
    acepta = request.headers.get("Accept", "")
    return "text/html" in acepta or "*/*" in acepta or not acepta


def init_portal_sso(app, buscar_usuario, iniciar_sesion, prefijo="",
                    endpoint_login="auth.login", endpoint_logout="auth.logout", avisos=None):
    """
    buscar_usuario(identificador) → el usuario local ACTIVO, o None.
    iniciar_sesion(usuario)       → arma la sesión igual que el login normal
                                    (incluye session.clear()).
    prefijo                       → si el dashboard vive bajo una subruta
                                    (Vencimientos: "/vencimientos").
    endpoint_login                → la pantalla de login del dashboard.
    endpoint_logout               → la ruta de "cerrar sesión" del dashboard.
    avisos(usuario)               → opcional. Lista de pendientes para mostrar
                                    en el botón del Portal:
                                    [{"texto": "3 pedidos a confirmar",
                                      "cantidad": 3, "url": "/pedidos?..."}]
    """
    bp = Blueprint("portal_sso", __name__)

    @bp.route("/sso")
    def entrar():
        cfg = _config()
        if not cfg:
            abort(404)
        pase = request.args.get("t", "")
        try:
            datos = URLSafeTimedSerializer(cfg["secreto"], salt=SALT_PASE).loads(
                pase, max_age=VIGENCIA_PASE)
        except SignatureExpired:
            return _pagina_error("El pase venció",
                                 "Pasó más de un minuto. Volvé a tocar el botón en el Portal.",
                                 cfg["portal"], 400)
        except BadSignature:
            return _pagina_error("Pase inválido", "Entrá desde el Portal.", cfg["portal"], 400)

        if not isinstance(datos, dict) or datos.get("d") != cfg["slug"]:
            return _pagina_error("Pase inválido", "Este pase es para otro sistema.", cfg["portal"], 400)
        if not datos.get("n") or not _marcar_usado(datos["n"]):
            return _pagina_error("Pase ya usado",
                                 "Volvé a tocar el botón en el Portal.", cfg["portal"], 400)

        persona = str(datos.get("u", "")).lower()
        identificador = cfg["mapa"].get(persona)
        usuario = buscar_usuario(identificador) if identificador else None
        if not usuario:
            return _pagina_error("Sin acceso a este sistema",
                                 "Tu usuario del Portal no está habilitado acá. Hablá con Facu.",
                                 cfg["portal"], 403)

        iniciar_sesion(usuario)
        session[CLAVE_HASTA] = time.time() + DURACION_SESION
        session[CLAVE_PERSONA] = persona
        session.permanent = True

        destino = _ruta_segura(datos.get("next")) or (prefijo + "/" if prefijo else "/")
        resp = redirect(destino)
        resp.headers["Cache-Control"] = "no-store"
        # Marca este navegador como "usa el Portal" (para el salto automático).
        resp.set_cookie(COOKIE_MARCA, "1", max_age=DURACION_MARCA, httponly=True,
                        secure=request.is_secure, samesite="Lax")
        return resp

    @bp.route("/sso/avisos")
    def ver_avisos():
        """
        El Portal pregunta cuántos pendientes tiene una persona. El pedido viene
        firmado con la misma llave (pero otra "sal", así no sirve como pase de
        entrada). Solo devuelve textos y cantidades, nunca datos sensibles.
        """
        cfg = _config()
        if not cfg:
            abort(404)
        try:
            datos = URLSafeTimedSerializer(cfg["secreto"], salt=SALT_AVISOS).loads(
                request.args.get("t", ""), max_age=VIGENCIA_PASE)
        except (BadSignature, SignatureExpired):
            return jsonify({"error": "firma inválida"}), 403
        if not isinstance(datos, dict) or datos.get("d") != cfg["slug"]:
            return jsonify({"error": "otro sistema"}), 403
        identificador = cfg["mapa"].get(str(datos.get("u", "")).lower())
        usuario = buscar_usuario(identificador) if identificador else None
        if not usuario or avisos is None:
            return jsonify({"avisos": []})
        lista = []
        for a in avisos(usuario) or []:
            cantidad = int(a.get("cantidad") or 0)
            if cantidad > 0:
                url = _ruta_segura(a.get("url")) or ""
                lista.append({"texto": str(a.get("texto", ""))[:80], "cantidad": cantidad, "url": url})
        resp = jsonify({"avisos": lista})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app.register_blueprint(bp, url_prefix=prefijo or None)

    @app.before_request
    def renovar_con_portal():
        hasta = session.get(CLAVE_HASTA)
        if hasta is None or request.endpoint in ("portal_sso.entrar", "static"):
            return None
        if request.endpoint and request.endpoint.endswith(".static"):
            return None
        ahora = time.time()
        if ahora < hasta:
            return None
        cfg = _config()
        if cfg and _es_navegacion():
            # Pedirle al Portal un pase nuevo y volver a esta misma pantalla.
            session.clear()
            siguiente = request.full_path.rstrip("?")
            return redirect(f"{cfg['portal']}/ir/{cfg['slug']}?" + urlencode({"next": siguiente}))
        if ahora < hasta + GRACIA_FORMULARIOS:
            # Un guardado o un refresco de fondo: lo dejamos pasar un rato
            # para no perder lo que la persona estaba cargando.
            return None
        # Muy vencida y no es una navegación: se cierra la sesión y el
        # dashboard responde como siempre que no hay nadie logueado.
        session.clear()
        return None

    @app.before_request
    def saltar_login_con_portal():
        """En vez del formulario de login, ir al Portal (solo si este navegador ya lo usa)."""
        if request.endpoint != endpoint_login or request.method != "GET":
            return None
        if request.args.get("local") or not request.cookies.get(COOKIE_MARCA):
            return None
        if request.cookies.get(COOKIE_SALIO):
            return None  # acaba de cerrar sesión: mostrar el login normal
        cfg = _config()
        if not cfg:
            return None
        siguiente = _ruta_interna(request.args.get("next"))
        destino = f"{cfg['portal']}/ir/{cfg['slug']}"
        if siguiente:
            destino += "?" + urlencode({"next": siguiente})
        return redirect(destino)

    @app.after_request
    def marcar_salida(resp):
        # Al cerrar sesión, 10 minutos sin salto automático (si no, el Portal
        # lo volvería a hacer entrar al instante y "Salir" no serviría).
        if request.endpoint == endpoint_logout:
            resp.set_cookie(COOKIE_SALIO, "1", max_age=600, httponly=True,
                            secure=request.is_secure, samesite="Lax")
        elif request.endpoint == "portal_sso.entrar" and resp.status_code in (301, 302, 303):
            resp.delete_cookie(COOKIE_SALIO)
        return resp

    @app.context_processor
    def inyectar_portal():
        # Para el botón "Entrar con el Portal" de la pantalla de login.
        cfg = _config()
        if not cfg:
            return {"portal_url": None, "portal_entrar_url": None}
        return {"portal_url": cfg["portal"],
                "portal_entrar_url": f"{cfg['portal']}/ir/{cfg['slug']}"}
