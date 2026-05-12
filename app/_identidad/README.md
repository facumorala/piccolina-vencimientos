# Identidad visual compartida

Sistema visual oficial de Piccolina, basado en el manual de identidad de Iribar &amp; de Girolamo.

Este paquete es la **única fuente de verdad** para la apariencia de los 6 dashboards de Piccolina (Compras, Mayorista, Minorista, Sueldos, Financiero, Vencimientos). Cualquier cambio de paleta, tipografía o componente entra acá una sola vez.

---

## Qué hay

```
_compartido/identidad/
├── __init__.py                ← register_identidad(app) — el único punto de integración
├── README.md                  ← este archivo
└── static/                    ← se sirve bajo /_identidad/...
│   ├── css/piccolina.css      ← tokens + componentes (paleta, .card, .btn, .chip, .tbl, …)
│   ├── fonts/                 ← Sunset Gothic Pro x6 (Light, Regular, Italic, Medium, Bold, Heavy)
│   └── logos/                 ← logo_sm.png, piccolina-crema-bordo_sm.png, etc.
└── templates/identidad/
    ├── base_identidad.html    ← layout base (extender desde el dashboard)
    ├── _brand_strip.html      ← franja superior 36px (bordó / verde / negro)
    ├── _header.html           ← nav 56px sticky con blur
    ├── _footer.html           ← footer con dot-pattern opcional
    ├── _page_header.html      ← kicker + h1 huge + subtitle + actions
    ├── _kpi_strip.html        ← 4 KPIs en línea, sin recuadros
    └── _icons.html            ← macros: icon('name', 'css_class') y sparkle()
```

---

## Cómo lo usa un dashboard Flask

### 1) Registrar el blueprint en `create_app()`

```python
# app/app_factory.py (o donde armes la Flask app)
from _compartido.identidad import register_identidad

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    # ... resto de config ...
    register_identidad(app)
    # ... registrar tus blueprints propios ...
    return app
```

`register_identidad(app)` hace dos cosas:
- Monta los static bajo `/_identidad/css/...`, `/_identidad/fonts/...`, `/_identidad/logos/...`.
- Agrega el folder `templates/` de identidad al loader de Jinja, así `{% extends 'identidad/base_identidad.html' %}` funciona desde tus templates.

### 2) Extender el layout base en cada página

```jinja
{# templates/dashboard.html del Mayorista #}
{% extends 'identidad/base_identidad.html' %}

{% block title %}Inicio · Mayoristas{% endblock %}

{# Override del color de marca: verde para Mayoristas #}
{% block brand_var %}--helecho{% endblock %}
{% block brand_var_2 %}#007a06{% endblock %}
{% block brand_var_soft %}#e7f1e6{% endblock %}

{% block content %}
  {# vars que consumen los partials desde el contexto #}
  {% set brand_variant = 'helecho' %}
  {% set brand_subtitle = 'Mayoristas' %}
  {% set app_label = 'Mayoristas' %}
  {% set nav_pages = [
       {'key': 'home',     'label': 'Inicio',  'href': url_for('home.dashboard')},
       {'key': 'orders',   'label': 'Pedidos', 'href': url_for('orders.list_view')},
       ...
     ]
  %}
  {% set current_page_key = 'home' %}
  {% set user_initials = 'FA' %}

  {% include 'identidad/_page_header.html' with context %}

  {% set kpis = [
       {'eyebrow': 'PEDIDOS PENDIENTES', 'value_html': pending_orders, 'hint': 'sin entregar'},
       ...
     ]
  %}
  {% include 'identidad/_kpi_strip.html' with context %}

  <section class="card">
    ... contenido ...
  </section>
{% endblock %}
```

> **Importante**: `brand_variant` y `brand_subtitle` los lee `_brand_strip.html`, que se incluye desde `base_identidad.html`. Si los seteás dentro del `{% block content %}` no llegan al strip — definilos antes (en un block dedicado o como variable global del request via context processor).

### 3) Color de marca por dashboard

| Dashboard      | `brand_var` | `brand_variant` | Hex      |
|----------------|-------------|-----------------|----------|
| Compras        | `--plum`    | `'plum'`        | `#990B1A` |
| Mayoristas     | `--helecho` | `'helecho'`     | `#005402` |
| Minorista      | `--plum`    | `'plum'`        | (TBD — definir en su sub-fase) |
| Sueldos        | `--plum`    | `'plum'`        | (TBD) |
| Financiero     | `--vulcan`  | `'vulcan'`      | `#1E1C1D` (modo "ejecutivo") |
| Vencimientos   | `--plum`    | `'plum'`        | (TBD) |

---

## Patrón sugerido para definir las variables globales del template

Para que `brand_variant`, `nav_pages`, `app_label`, `user_initials` etc. estén disponibles en TODOS los templates sin tener que setearlos página por página, registrar un `context_processor`:

```python
# routes/_context.py (o app_factory.py)
from flask import session

def setup_context_processors(app):
    @app.context_processor
    def inject_identidad_defaults():
        return {
            'brand_variant': 'helecho',          # hardcoded por dashboard
            'brand_subtitle': 'Mayoristas',
            'app_label': 'Mayoristas',
            'app_version': 'v0.4',
            'nav_pages': [...],                  # nav del dashboard
            'user_initials': _user_initials(),   # del session
            'notif_count': 0,
        }
```

Esto se ejecuta una vez en `create_app()` y simplifica todos los templates.

---

## Variantes del BrandStrip

`_brand_strip.html` lee `brand_variant`:

- `'plum'` (default) — fondo bordó, texto crema
- `'helecho'` — fondo verde, texto crema
- `'vulcan'` — fondo negro, texto crema

---

## Iconos disponibles

Macros en `_icons.html`:

```jinja
{% from 'identidad/_icons.html' import icon, sparkle %}

{{ icon('search', 'w-4 h-4') }}
{{ icon('warn', 'w-5 h-5 text-plum') }}
{{ sparkle(14, 'var(--plum)') }}
```

Lista completa: `arrow, arrowL, arrowUp, arrowDown, search, bell, plus, minus, bag, truck, cash, receipt, bread, warn, check, clock, filter, download, more, edit, pkg, trend, building`.

---

## Reglas que respetar (del manual)

1. **Logo siempre con área de resguardo**. No invadirlo con texto/iconos.
2. **No rotar / distorsionar / recolorear el logo** fuera de la paleta oficial.
3. **No aplicar sombras al logo.**
4. **Bordó (Persian Plum) es el único acento principal.** La complementaria (verde, mostaza, celeste) solo con propósito semántico (ok/warn/info), nunca decorativa.
5. **Fondo siempre blanco**, salvo el `brand-strip` y casos puntuales (callouts de warning con `#fffaf0`).
6. **Tono**: lúdico / retro / expresivo. **Nunca** infantil / improvisado.

---

## Versión

v1.0 — 30 abr 2026 — primera importación del paquete `NUEVO DISEÑO SISTEMAS.zip` enviado por Facu.
