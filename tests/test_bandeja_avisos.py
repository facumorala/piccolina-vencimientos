"""
Campanita del encabezado (bandeja de avisos, 27-oct-2026; copia de Compras RVR
adaptada a SQLAlchemy "puro"). Ver app/bandeja_avisos.py.

- cada aviso push queda guardado para cada destinatario,
- la ruta del push lleva la marca para dejarlo "visto" al tocarlo,
- la campanita muestra el número de sin ver SOLO de quien mira,
- entrar a /avisos/ los deja vistos (y esa vez se ven resaltados),
- un aviso "de una sola vez" (clave) no se guarda dos veces,
- si la bandeja falla, el push sale igual.

Usa una base SQLite temporal (nunca la de producción).

    .venv/Scripts/python -m pytest tests/test_bandeja_avisos.py -q
"""
import os
import re
import sys
import tempfile
import uuid

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(RAIZ, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Si extensions todavía no se importó, que arranque con una base temporal.
if "extensions" not in sys.modules:
    _tmp = tempfile.mkdtemp(prefix="vtos_bandeja_")
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "bandeja.db").replace("\\", "/")
os.environ["FLASK_TESTING"] = "1"

import bcrypt  # noqa: E402

import bandeja_avisos as B  # noqa: E402
import extensions  # noqa: E402
import portal_avisos  # noqa: E402
from app_factory import create_app  # noqa: E402
from bandeja_avisos import AvisoBandeja  # noqa: E402
from models import User  # noqa: E402

SUFIJO = uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def app():
    assert str(extensions.engine.url).startswith("sqlite"), "¡Solo en una base local!"
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def gente(app):
    """Facu (en el Portal) y una contadora sin usuario del Portal."""
    db = extensions.SessionFactory()
    ids = {}
    for clave, rol in (("facu", "facu"), ("conta", "contadoras")):
        u = User(username=f"zz{clave}{SUFIJO}", email=f"zz{clave}{SUFIJO}@prueba",
                 name=f"ZZ {clave}", rol=rol, active=True,
                 password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode())
        db.add(u)
        db.commit()
        ids[clave] = u.id
    db.close()
    yield ids
    db = extensions.SessionFactory()
    db.query(User).filter(User.id.in_(ids.values())).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture
def pushes(app, monkeypatch):
    db = extensions.SessionFactory()
    db.query(AvisoBandeja).delete()
    db.commit()
    db.close()
    mandados = []
    monkeypatch.setattr(portal_avisos, "avisar",
                        lambda para, titulo, cuerpo="", **k: mandados.append(
                            dict(para=list(para), titulo=titulo, **k)) or {"ok": True})
    monkeypatch.setenv("PORTAL_SSO_USUARIOS", f"facu=zzfacu{SUFIJO},caro=zzcaro{SUFIJO}")
    return mandados


def _avisos():
    db = extensions.SessionFactory()
    try:
        return db.query(AvisoBandeja).order_by(AvisoBandeja.para, AvisoBandeja.id).all()
    finally:
        db.close()


def _cliente(app, user_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id
        s["user_name"] = "ZZ"
    return c


def _numero(html):
    """El número que el script le pone a la campanita."""
    m = re.search(r"var n = (\d+);", html)
    return int(m.group(1)) if m else None


def test_guarda_uno_por_destinatario_y_marca_la_ruta(pushes):
    B.avisar(["facu", "caro"], "⏰ Hoy vence IVA", "x", ruta="/vencimientos/?dia=2026-10-27",
             categoria="vencimientos.hoy")
    avisos = _avisos()
    assert [a.para for a in avisos] == ["caro", "facu"]
    assert avisos[0].grupo == avisos[1].grupo
    assert avisos[0].ruta == "/vencimientos/?dia=2026-10-27"          # guardada limpia
    assert pushes[0]["ruta"] == f"/vencimientos/?dia=2026-10-27&aviso_visto={avisos[0].grupo}"


def test_aviso_con_clave_no_se_guarda_dos_veces(pushes):
    for _ in range(2):
        B.avisar(["facu"], "x", ruta="/vencimientos/", clave="vence-hoy-2026-10-27")
    assert len(_avisos()) == 1
    assert len(pushes) == 2     # el Portal es el que corta el push repetido


def test_si_la_bandeja_falla_el_push_sale_igual(pushes, monkeypatch):
    monkeypatch.setattr(B, "guardar", lambda *a, **k: None)
    B.avisar(["facu"], "x", ruta="/vencimientos/")
    assert pushes and pushes[0]["ruta"] == "/vencimientos/"


def test_los_avisos_de_vencimientos_pasan_por_la_bandeja():
    from services import avisos_portal
    assert avisos_portal.avisar is B.avisar


def test_numero_bandeja_y_visto(app, gente, pushes):
    B.avisar(["facu"], "uno", ruta="/vencimientos/")
    B.avisar(["facu", "caro"], "dos", ruta="/vencimientos/")
    c = _cliente(app, gente["facu"])
    assert _numero(c.get("/home").get_data(as_text=True)) == 2
    lista = c.get("/avisos/").get_data(as_text=True)
    assert "uno" in lista and 'title="Nuevo"' in lista
    assert _numero(c.get("/home").get_data(as_text=True)) == 0
    vistos = {a.para: a.visto_en for a in _avisos() if a.titulo == "dos"}
    assert vistos["facu"] is not None and vistos["caro"] is None   # solo lo de quien mira


def test_tocar_el_push_lo_deja_visto(app, gente, pushes):
    B.avisar(["facu", "caro"], "x", ruta="/vencimientos/")
    grupo = _avisos()[0].grupo
    c = _cliente(app, gente["facu"])
    c.get(f"/vencimientos/?aviso_visto={grupo}")
    vistos = {a.para: a.visto_en for a in _avisos()}
    assert vistos["facu"] is not None and vistos["caro"] is None


def test_abrir_un_aviso_lleva_a_su_pantalla(app, gente, pushes):
    B.avisar(["facu", "caro"], "x", ruta="/planes/")
    de = {a.para: a.id for a in _avisos()}
    c = _cliente(app, gente["facu"])
    r = c.get(f"/avisos/{de['facu']}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/planes/")
    assert c.get(f"/avisos/{de['caro']}").status_code == 404   # el de otro, no


def test_quien_no_esta_en_el_portal_no_tiene_campanita(app, gente, pushes):
    B.avisar(["facu"], "x", ruta="/vencimientos/")
    c = _cliente(app, gente["conta"])
    assert _numero(c.get("/home").get_data(as_text=True)) is None
    assert "No pude reconocer tu usuario del Portal" in c.get("/avisos/").get_data(as_text=True)


def test_sin_login_la_bandeja_pide_entrar(app):
    r = app.test_client().get("/avisos/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
