"""
Helpers de navegación: redirect_back() para que las acciones inline (POST)
vuelvan a la pantalla donde estaba el usuario y se actualicen solas. Evita
quedarse en una URL distinta con datos viejos en la pantalla anterior.

Patrón idéntico al Mayorista — ver memory/patron_refresh_post_accion.md.
"""
from urllib.parse import urlparse
from flask import request, redirect, url_for


def _safe_referrer():
    """Devuelve el Referer si pertenece al mismo host; None si no."""
    ref = request.referrer or ""
    if not ref:
        return None
    try:
        ref_host = urlparse(ref).netloc
        cur_host = urlparse(request.host_url).netloc
    except Exception:
        return None
    if ref_host and ref_host != cur_host:
        return None
    # Evita rebote infinito si el referrer es la URL del POST
    if ref == request.url:
        return None
    return ref


def redirect_back(default_endpoint=None, **kwargs):
    """Redirige al Referer si es válido, sino al default_endpoint."""
    ref = _safe_referrer()
    if ref:
        return redirect(ref)
    if default_endpoint:
        return redirect(url_for(default_endpoint, **kwargs))
    return redirect("/")
