"""
Mandar avisos (notificaciones push) a través del Portal Piccolina.

FUENTE ÚNICA: _compartido/portal_avisos.py. Cada dashboard tiene una COPIA
idéntica en su carpeta app/. Reemplaza los avisos por Telegram.

Uso:
    from portal_avisos import avisar          # (o from app.portal_avisos ...)
    avisar(["facu", "caro"], "🛒 Pedido nuevo", "Café Tal · entrega mañana",
           ruta="/orders/123", categoria="mayoristas.pedido")

- `para`: usuarios del PORTAL (facu, caro, ivo, alma...), no los del dashboard.
- `ruta`: la pantalla de ESTE dashboard que se abre al tocar el aviso.
- `clave`: opcional. Si ya se mandó un aviso con esa clave, el Portal no lo
  repite (ej. "sueldos-prueba-empleado-12" para avisar una sola vez).
- Usa las mismas variables que la puerta del pase: PORTAL_URL,
  PORTAL_SSO_SLUG y PORTAL_SSO_SECRET. Si faltan, no hace nada.
- Nunca rompe al que lo llama: si el Portal no contesta, lo anota en el log
  y sigue. Por defecto sale en segundo plano (no demora la pantalla).
"""
import json
import logging
import os
import threading
import urllib.request

from itsdangerous import URLSafeTimedSerializer

# Debe ser IGUAL al del Portal (app/push.py → SALT_AVISAR).
SALT_AVISAR = "portal-piccolina-avisar-v1"
log = logging.getLogger("portal_avisos")


def configurado():
    return bool(os.getenv("PORTAL_URL") and os.getenv("PORTAL_SSO_SLUG") and os.getenv("PORTAL_SSO_SECRET"))


def _armar_pedido(para, titulo, cuerpo, ruta, categoria, clave):
    slug = os.environ["PORTAL_SSO_SLUG"]
    firmado = URLSafeTimedSerializer(os.environ["PORTAL_SSO_SECRET"], salt=SALT_AVISAR).dumps({
        "d": slug, "para": list(para), "titulo": titulo, "cuerpo": cuerpo or "",
        "ruta": ruta or "", "categoria": categoria or "", "clave": clave or "",
    })
    return f"{os.environ['PORTAL_URL'].rstrip('/')}/api/avisar", json.dumps({"d": slug, "t": firmado}).encode()


def _mandar(url, cuerpo):
    try:
        req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # nunca romper por un aviso
        log.warning("No se pudo mandar el aviso al Portal: %s", str(e)[:200])
        return None


def avisar(para, titulo, cuerpo="", ruta="", categoria="", clave=None, esperar=False):
    """Pide al Portal que mande el aviso. Devuelve la respuesta solo si esperar=True."""
    if not configurado() or not para:
        return None
    url, datos = _armar_pedido(para, titulo, cuerpo, ruta, categoria, clave)
    if esperar:
        return _mandar(url, datos)
    threading.Thread(target=_mandar, args=(url, datos), daemon=True).start()
    return None
