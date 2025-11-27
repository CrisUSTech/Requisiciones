import os
from datetime import date, datetime

from flask import (
    Flask, request, redirect, url_for, render_template_string, session, send_file, flash
)
from flask_sqlalchemy import SQLAlchemy
from io import StringIO
import csv

# ============================================================
# CONFIGURACIÓN FLASK + SQLALCHEMY
# ============================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

db = SQLAlchemy()


def get_database_uri():
    """
    Si existe DATABASE_URL (Render / Aiven), la usa.
    Si no, usa SQLite local (local.db).
    Corrige 'postgres://' -> 'postgresql://'.
    """
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return db_url
    return "sqlite:///local.db"


app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ============================================================
# MODELOS
# ============================================================

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)  # texto plano (demo)
    role = db.Column(db.String(50), nullable=False)       # Mantenimiento / Almacén / Compras


class Requisition(db.Model):
    __tablename__ = "requisitions"
    id = db.Column(db.Integer, primary_key=True)
    fecha_solicitud = db.Column(db.Date, nullable=False, default=date.today)
    fecha_mantenimiento = db.Column(db.Date, nullable=False)
    proyecto = db.Column(db.String(255), nullable=False)
    utilizacion = db.Column(db.Text)
    prioridad = db.Column(db.String(20), nullable=False)   # Alta / Media / Baja
    estado = db.Column(db.String(50), nullable=False, default="Solicitado")
    solicitante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    proveedor = db.Column(db.String(255))
    costo_total = db.Column(db.Float, default=0.0)
    revisado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    fecha_revision = db.Column(db.DateTime)
    finalizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    fecha_finalizacion = db.Column(db.DateTime)

    materiales = db.relationship(
        "Material",
        backref="requisition",
        cascade="all, delete-orphan",
        lazy=True
    )


class Material(db.Model):
    __tablename__ = "materials"
    id = db.Column(db.Integer, primary_key=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey("requisitions.id"), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    unidad = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    revisado_qty = db.Column(db.Integer, default=0)      # lo que almacén revisó
    stock_available = db.Column(db.Integer, default=0)   # stock real
    costo_unitario = db.Column(db.Float, default=0.0)
    comprado_qty = db.Column(db.Integer, default=0)      # cantidad efectivamente comprada


# ============================================================
# INICIALIZACIÓN DE DB Y USUARIOS DE PRUEBA
# ============================================================

def seed_users():
    """
    Crea usuarios de prueba:
      - mantenimiento1 / m1 (Mantenimiento)
      - mantenimiento2 / m2 (Mantenimiento)
      - almacen / a (Almacén)
      - compras1 / c1 (Compras)
      - compras2 / c2 (Compras)
    """
    if User.query.count() > 0:
        return

    demo_users = [
        User(username="mantenimiento1", password="m1", role="Mantenimiento"),
        User(username="mantenimiento2", password="m2", role="Mantenimiento"),
        User(username="almacen",        password="a",  role="Almacén"),
        User(username="compras1",       password="c1", role="Compras"),
        User(username="compras2",       password="c2", role="Compras"),
    ]
    db.session.add_all(demo_users)
    db.session.commit()


with app.app_context():
    db.create_all()
    seed_users()


# ============================================================
# TEMPLATES (HTML) – TODO EN UN SOLO ARCHIVO
# ============================================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Sistema de Requisiciones</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- Tailwind CDN (simple, sin build) -->
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 min-h-screen">
    <nav class="bg-white border-b border-slate-200 shadow-sm">
        <div class="max-w-6xl mx-auto px-4 py-3 flex justify-between items-center">
            <div class="font-bold text-slate-800">
                Sistema Colaborativo de Requisiciones
            </div>
            <div>
            {% if session.get('user_id') %}
                <span class="text-sm text-slate-600 mr-4">
                    Usuario: <b>{{ session.get('username') }}</b> ({{ session.get('role') }})
                </span>
                <a href="{{ url_for('logout') }}" 
                   class="text-sm px-3 py-1 rounded bg-red-500 text-white hover:bg-red-600">
                   Cerrar sesión
                </a>
            {% endif %}
            </div>
        </div>
    </nav>

    <main class="max-w-6xl mx-auto px-4 py-6">
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="mb-4">
              {% for msg in messages %}
                <div class="bg-yellow-100 border border-yellow-300 text-yellow-800 px-4 py-2 rounded mb-2 text-sm">
                    {{ msg }}
                </div>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>
</body>
</html>
"""

LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="max-w-md mx-auto bg-white p-6 rounded-lg shadow">
    <h1 class="text-xl font-semibold mb-4 text-slate-800 text-center">Iniciar sesión</h1>

    {% if error %}
      <div class="mb-4 text-sm bg-red-100 text-red-700 border border-red-300 px-3 py-2 rounded">
        {{ error }}
      </div>
    {% endif %}

    <form method="post" class="space-y-4">
        <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Usuario</label>
            <input type="text" name="username" required
                   class="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring focus:ring-blue-200">
        </div>
        <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Contraseña</label>
            <input type="password" name="password" required
                   class="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring focus:ring-blue-200">
        </div>
        <div>
            <button type="submit"
                    class="w-full bg-blue-600 text-white text-sm font-semibold px-3 py-2 rounded hover:bg-blue-700">
                Entrar
            </button>
        </div>
    </form>

    <div class="mt-4 text-xs text-slate-500">
        <p>Cuentas de prueba:</p>
        <ul class="list-disc ml-4 mt-1">
            <li>mantenimiento1 / m1 (Mantenimiento)</li>
            <li>mantenimiento2 / m2 (Mantenimiento)</li>
            <li>almacen / a (Almacén)</li>
            <li>compras1 / c1 (Compras)</li>
            <li>compras2 / c2 (Compras)</li>
        </ul>
    </div>
</div>
{% endblock %}
"""

DASHBOARD_TEMPLATE = """
{% extends "base.html" %}
{% block content %}

<h1 class="text-xl font-semibold mb-4 text-slate-800">
    Panel – {{ session.get('role') }}
</h1>

<div class="mb-4 flex justify-between items-center">
    <form method="get" class="flex gap-2 items-end">
        <div>
            <label class="block text-xs text-slate-600">Proyecto/Tema contiene</label>
            <input type="text" name="proyecto" value="{{ request.args.get('proyecto','') }}"
                   class="border border-slate-300 rounded px-2 py-1 text-xs">
        </div>
        <div>
            <label class="block text-xs text-slate-600">Prioridad</label>
            <select name="prioridad"
                    class="border border-slate-300 rounded px-2 py-1 text-xs">
                <option value="">Todas</option>
                <option value="Alta"  {% if request.args.get('prioridad')=='Alta' %}selected{% endif %}>Alta</option>
                <option value="Media" {% if request.args.get('prioridad')=='Media' %}selected{% endif %}>Media</option>
                <option value="Baja"  {% if request.args.get('prioridad')=='Baja' %}selected{% endif %}>Baja</option>
            </select>
        </div>
        <div>
            <label class="block text-xs text-slate-600">Fecha Mant. (YYYY-MM-DD)</label>
            <input type="date" name="fecha_mantenimiento" value="{{ request.args.get('fecha_mantenimiento','') }}"
                   class="border border-slate-300 rounded px-2 py-1 text-xs">
        </div>
        <div>
            <label class="block text-xs text-slate-600">Estado</label>
            <select name="estado"
                    class="border border-slate-300 rounded px-2 py-1 text-xs">
                <option value="">Todos</option>
                {% for e in estados %}
                  <option value="{{ e }}" {% if request.args.get('estado')==e %}selected{% endif %}>{{ e }}</option>
                {% endfor %}
            </select>
        </div>
        <div>
            <button type="submit"
                    class="bg-slate-700 text-white text-xs px-3 py-1 rounded hover:bg-slate-800 mt-4">
                Filtrar
            </button>
        </div>
    </form>

    <div class="flex gap-2">
        {% if session.get('role') == 'Mantenimiento' %}
        <a href="{{ url_for('new_requisition') }}"
           class="text-xs px-3 py-2 rounded bg-blue-600 text-white hover:bg-blue-700">
           + Nueva requisición
        </a>
        {% endif %}
        <a href="{{ url_for('export_csv') }}"
           class="text-xs px-3 py-2 rounded bg-emerald-600 text-white hover:bg-emerald-700">
           Exportar CSV
        </a>
    </div>
</div>

{% if requisitions %}
<div class="overflow-x-auto bg-white rounded-lg shadow border border-slate-200">
<table class="min-w-full text-xs">
    <thead class="bg-slate-100 text-slate-700">
        <tr>
            <th class="px-3 py-2 border-b">ID</th>
            <th class="px-3 py-2 border-b">Proyecto/Tema</th>
            <th class="px-3 py-2 border-b">Prioridad</th>
            <th class="px-3 py-2 border-b">Fecha Mant.</th>
            <th class="px-3 py-2 border-b">Estado</th>
            <th class="px-3 py-2 border-b">Costo Total</th>
            <th class="px-3 py-2 border-b">Acciones</th>
        </tr>
    </thead>
    <tbody>
    {% for r in requisitions %}
        <tr class="hover:bg-slate-50">
            <td class="px-3 py-2 border-b">{{ r.id }}</td>
            <td class="px-3 py-2 border-b">{{ r.proyecto }}</td>
            <td class="px-3 py-2 border-b">{{ r.prioridad }}</td>
            <td class="px-3 py-2 border-b">{{ r.fecha_mantenimiento }}</td>
            <td class="px-3 py-2 border-b">{{ r.estado }}</td>
            <td class="px-3 py-2 border-b">
                {% if r.costo_total %}
                    ${{ "%.2f"|format(r.costo_total) }}
                {% else %}
                    N/A
                {% endif %}
            </td>
            <td class="px-3 py-2 border-b">
                <a href="{{ url_for('view_requisition', req_id=r.id) }}"
                   class="text-xs px-2 py-1 rounded bg-slate-700 text-white hover:bg-slate-800">
                   Ver / Procesar
                </a>
            </td>
        </tr>
    {% endfor %}
    </tbody>
</table>
</div>
{% else %}
<p class="text-sm text-slate-500">No hay requisiciones que coincidan con el filtro.</p>
{% endif %}

{% endblock %}
"""

NEW_REQ_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<h1 class="text-xl font-semibold mb-4 text-slate-800">Nueva Requisición (Mantenimiento)</h1>

<form method="post" class="space-y-4 bg-white p-4 rounded-lg shadow border border-slate-200">
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Fecha Mantenimiento</label>
            <input type="date" name="fecha_mantenimiento" required
                   class="w-full border border-slate-300 rounded px-2 py-1 text-sm">
        </div>
        <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Proyecto / Tema</label>
            <input type="text" name="proyecto" required
                   class="w-full border border-slate-300 rounded px-2 py-1 text-sm">
        </div>
    </div>

    <div>
        <label class="block text-xs font-medium text-slate-700 mb-1">Utilización / Propósito</label>
        <textarea name="utilizacion" rows="2"
                  class="w-full border border-slate-300 rounded px-2 py-1 text-sm"></textarea>
    </div>

    <div>
        <label class="block text-xs font-medium text-slate-700 mb-1">Prioridad</label>
        <select name="prioridad" class="border border-slate-300 rounded px-2 py-1 text-sm">
            <option value="Alta">Alta</option>
            <option value="Media" selected>Media</option>
            <option value="Baja">Baja</option>
        </select>
    </div>

    <div class="border-t pt-4">
        <h2 class="text-sm font-semibold mb-2 text-slate-800">Materiales</h2>
        <p class="text-xs text-slate-500 mb-2">
            Puedes agregar hasta 10 materiales; si necesitas más, puedes crear otra requisición.
        </p>
        <table class="min-w-full text-xs border border-slate-200">
            <thead class="bg-slate-100">
                <tr>
                    <th class="px-2 py-1 border-b">Descripción</th>
                    <th class="px-2 py-1 border-b">Unidad</th>
                    <th class="px-2 py-1 border-b">Cantidad</th>
                </tr>
            </thead>
            <tbody>
            {% for i in range(1,6) %}
                <tr>
                    <td class="px-2 py-1 border-b">
                        <input type="text" name="desc_{{i}}" class="w-full border border-slate-300 rounded px-2 py-1 text-xs">
                    </td>
                    <td class="px-2 py-1 border-b">
                        <input type="text" name="unidad_{{i}}" value="PZA"
                               class="w-full border border-slate-300 rounded px-2 py-1 text-xs">
                    </td>
                    <td class="px-2 py-1 border-b">
                        <input type="number" name="cant_{{i}}" min="0"
                               class="w-full border border-slate-300 rounded px-2 py-1 text-xs">
                    </td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="flex justify-end gap-2 pt-2">
        <a href="{{ url_for('dashboard') }}"
           class="px-3 py-2 text-xs rounded border border-slate-300 text-slate-700 hover:bg-slate-50">
           Cancelar
        </a>
        <button type="submit"
                class="px-3 py-2 text-xs rounded bg-blue-600 text-white hover:bg-blue-700">
            Guardar requisición
        </button>
    </div>
</form>
{% endblock %}
"""

VIEW_REQ_TEMPLATE = """
{% extends "base.html" %}
{% block content %}

<h1 class="text-xl font-semibold mb-4 text-slate-800">
    Requisición #{{ req.id }} – {{ req.proyecto }}
</h1>

<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="bg-white p-4 rounded border border-slate-200 text-sm">
        <p><b>Fecha solicitud:</b> {{ req.fecha_solicitud }}</p>
        <p><b>Fecha mantenimiento:</b> {{ req.fecha_mantenimiento }}</p>
        <p><b>Prioridad:</b> {{ req.prioridad }}</p>
        <p><b>Estado:</b> {{ req.estado }}</p>
        <p><b>Solicitante ID:</b> {{ req.solicitante_id }}</p>
        <p><b>Proveedor:</b> {{ req.proveedor or 'N/A' }}</p>
        <p><b>Costo total:</b> 
        {% if req.costo_total %}
           ${{ "%.2f"|format(req.costo_total) }}
        {% else %}
           N/A
        {% endif %}
        </p>
    </div>
    <div class="bg-white p-4 rounded border border-slate-200 text-sm">
        <p><b>Utilización:</b></p>
        <p class="mt-1 whitespace-pre-wrap">{{ req.utilizacion or 'N/A' }}</p>
        <hr class="my-2">
        <p><b>Revisado por (Almacén):</b> {{ req.revisado_por or 'N/A' }}</p>
        <p><b>Fecha revisión:</b> {{ req.fecha_revision or 'N/A' }}</p>
        <p><b>Finalizado por:</b> {{ req.finalizado_por or 'N/A' }}</p>
        <p><b>Fecha finalización:</b> {{ req.fecha_finalizacion or 'N/A' }}</p>
    </div>
</div>

<h2 class="text-sm font-semibold mb-2 text-slate-800">Materiales</h2>
<div class="overflow-x-auto bg-white rounded border border-slate-200 mb-4">
<table class="min-w-full text-xs">
    <thead class="bg-slate-100">
        <tr>
            <th class="px-2 py-1 border-b">Descripción</th>
            <th class="px-2 py-1 border-b">Unidad</th>
            <th class="px-2 py-1 border-b">Cant. Solic.</th>
            <th class="px-2 py-1 border-b">Rev. Almacén</th>
            <th class="px-2 py-1 border-b">Stock</th>
            <th class="px-2 py-1 border-b">C/U</th>
            <th class="px-2 py-1 border-b">Comprado</th>
        </tr>
    </thead>
    <tbody>
    {% for m in req.materiales %}
        <tr>
            <td class="px-2 py-1 border-b">{{ m.descripcion }}</td>
            <td class="px-2 py-1 border-b">{{ m.unidad }}</td>
            <td class="px-2 py-1 border-b">{{ m.cantidad }}</td>
            <td class="px-2 py-1 border-b">{{ m.revisado_qty }}</td>
            <td class="px-2 py-1 border-b">{{ m.stock_available }}</td>
            <td class="px-2 py-1 border-b">
                {% if m.costo_unitario %}
                    ${{ "%.2f"|format(m.costo_unitario) }}
                {% else %}
                    N/A
                {% endif %}
            </td>
            <td class="px-2 py-1 border-b">{{ m.comprado_qty }}</td>
        </tr>
    {% endfor %}
    </tbody>
</table>
</div>

{% if session.get('role') == 'Almacén' %}
<!-- Formulario de ALMACÉN -->
<h3 class="text-sm font-semibold mb-2 text-slate-800">Acciones de Almacén</h3>
<form method="post" action="{{ url_for('process_almacen', req_id=req.id) }}"
      class="bg-white p-4 rounded border border-slate-200 text-xs space-y-3">
    <p class="text-slate-600">Actualiza las cantidades revisadas y stock para cada material.</p>

    <table class="min-w-full text-xs mb-3">
        <thead class="bg-slate-100">
            <tr>
                <th class="px-2 py-1 border-b">Material</th>
                <th class="px-2 py-1 border-b">Revisado</th>
                <th class="px-2 py-1 border-b">Stock</th>
            </tr>
        </thead>
        <tbody>
        {% for m in req.materiales %}
            <tr>
                <td class="px-2 py-1 border-b">{{ m.descripcion }} ({{ m.cantidad }} {{ m.unidad }})</td>
                <td class="px-2 py-1 border-b">
                    <input type="number" name="rev_{{ m.id }}" min="0" max="{{ m.cantidad }}" 
                           value="{{ m.revisado_qty }}"
                           class="w-24 border border-slate-300 rounded px-1 py-1 text-xs">
                </td>
                <td class="px-2 py-1 border-b">
                    <input type="number" name="stock_{{ m.id }}" min="0"
                           value="{{ m.stock_available }}"
                           class="w-24 border border-slate-300 rounded px-1 py-1 text-xs">
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>

    <p class="text-xs text-slate-600 mb-1">¿Resultado de la revisión?</p>
    <div class="flex gap-3">
        <label class="inline-flex items-center text-xs">
            <input type="radio" name="accion" value="Revisado - En Stock" required class="mr-1">
            Todo en stock (Revisado - En Stock)
        </label>
        <label class="inline-flex items-center text-xs">
            <input type="radio" name="accion" value="Revisado - Autorizada" required class="mr-1">
            Faltan cosas (Autorizada para Compras)
        </label>
    </div>

    <div class="flex justify-end gap-2 pt-2">
        <button type="submit"
                class="px-3 py-2 text-xs rounded bg-blue-600 text-white hover:bg-blue-700">
            Guardar revisión de Almacén
        </button>
    </div>
</form>
{% endif %}

{% if session.get('role') == 'Compras' and req.estado in ['Revisado - Autorizada', 'Comprado (Parcial)'] %}
<!-- Formulario de COMPRAS -->
<h3 class="text-sm font-semibold mb-2 text-slate-800 mt-6">Acciones de Compras</h3>
<form method="post" action="{{ url_for('process_compras', req_id=req.id) }}"
      class="bg-white p-4 rounded border border-slate-200 text-xs space-y-3">
    <div>
        <label class="block text-xs font-medium text-slate-700 mb-1">Proveedor</label>
        <input type="text" name="proveedor" value="{{ req.proveedor or '' }}"
               required
               class="w-full border border-slate-300 rounded px-2 py-1 text-xs">
    </div>

    <table class="min-w-full text-xs mb-3">
        <thead class="bg-slate-100">
            <tr>
                <th class="px-2 py-1 border-b">Material</th>
                <th class="px-2 py-1 border-b">C/U</th>
                <th class="px-2 py-1 border-b">Cant. a comprar</th>
            </tr>
        </thead>
        <tbody>
        {% for m in req.materiales %}
            <tr>
                <td class="px-2 py-1 border-b">
                    {{ m.descripcion }} ({{ m.cantidad }} {{ m.unidad }})
                </td>
                <td class="px-2 py-1 border-b">
                    <input type="number" name="cu_{{ m.id }}" min="0" step="0.01"
                           value="{{ m.costo_unitario or '' }}"
                           class="w-24 border border-slate-300 rounded px-1 py-1 text-xs" required>
                </td>
                <td class="px-2 py-1 border-b">
                    <input type="number" name="comprado_{{ m.id }}" min="0" max="{{ m.cantidad }}"
                           value="{{ m.comprado_qty or m.cantidad }}"
                           class="w-24 border border-slate-300 rounded px-1 py-1 text-xs" required>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>

    <div class="flex gap-3 text-xs">
        <label class="inline-flex items-center">
            <input type="radio" name="tipo_compra" value="total" required class="mr-1">
            Se compró TODO lo de este ID
        </label>
        <label class="inline-flex items-center">
            <input type="radio" name="tipo_compra" value="parcial" required class="mr-1">
            Se compró SOLO ciertas cantidades
        </label>
    </div>

    <div class="flex justify-end gap-2 pt-2">
        <button type="submit"
                class="px-3 py-2 text-xs rounded bg-emerald-600 text-white hover:bg-emerald-700">
            Registrar compra
        </button>
    </div>
</form>
{% endif %}

<div class="mt-4">
    <a href="{{ url_for('dashboard') }}"
       class="text-xs px-3 py-2 rounded border border-slate-300 text-slate-700 hover:bg-slate-50">
       Volver al panel
    </a>
</div>

{% endblock %}
"""


# ============================================================
# HELPERS
# ============================================================

def current_user():
    if "user_id" not in session:
        return None
    return User.query.get(session["user_id"])


def login_required(fn):
    # Decorador simple
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
# RUTAS
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = request.form.get("username", "").strip().lower()
        pwd = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.username) == uname).first()
        if not user or user.password != pwd:
            return render_template_string(
                LOGIN_TEMPLATE,
                error="Usuario o contraseña incorrectos."
            )
        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = user.role
        return redirect(url_for("dashboard"))

    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    # Filtros
    q_proyecto = request.args.get("proyecto", "").strip().lower()
    q_prioridad = request.args.get("prioridad", "")
    q_fecha_mant = request.args.get("fecha_mantenimiento", "")
    q_estado = request.args.get("estado", "")

    query = Requisition.query

    # Visibilidad por rol:
    if user.role == "Mantenimiento":
        query = query.filter_by(solicitante_id=user.id)
    elif user.role == "Almacén":
        # Ve solicitadas y autorizadas / en stock
        query = query.filter(Requisition.estado.in_([
            "Solicitado", "Revisado - En Stock", "Revisado - Autorizada"
        ]))
    elif user.role == "Compras":
        query = query.filter(Requisition.estado.in_([
            "Revisado - Autorizada", "Comprado", "Comprado (Parcial)"
        ]))

    if q_proyecto:
        query = query.filter(Requisition.proyecto.ilike(f"%{q_proyecto}%"))
    if q_prioridad:
        query = query.filter_by(prioridad=q_prioridad)
    if q_fecha_mant:
        try:
            dt = date.fromisoformat(q_fecha_mant)
            query = query.filter_by(fecha_mantenimiento=dt)
        except ValueError:
            pass
    if q_estado:
        query = query.filter_by(estado=q_estado)

    requisitions = query.order_by(Requisition.id.desc()).all()

    estados_posibles = [
        "Solicitado",
        "Revisado - En Stock",
        "Revisado - Autorizada",
        "Comprado",
        "Comprado (Parcial)",
    ]

    return render_template_string(
        DASHBOARD_TEMPLATE,
        requisitions=requisitions,
        estados=estados_posibles
    )


@app.route("/requisiciones/nueva", methods=["GET", "POST"])
@login_required
def new_requisition():
    user = current_user()
    if user.role != "Mantenimiento":
        flash("Solo Mantenimiento puede crear requisiciones.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            fecha_mant_str = request.form.get("fecha_mantenimiento")
            fecha_mant = date.fromisoformat(fecha_mant_str)
        except Exception:
            flash("Fecha de mantenimiento inválida.")
            return redirect(url_for("new_requisition"))

        proyecto = request.form.get("proyecto", "").strip()
        utilizacion = request.form.get("utilizacion", "").strip()
        prioridad = request.form.get("prioridad", "Media")

        req = Requisition(
            fecha_solicitud=date.today(),
            fecha_mantenimiento=fecha_mant,
            proyecto=proyecto,
            utilizacion=utilizacion,
            prioridad=prioridad,
            estado="Solicitado",
            solicitante_id=user.id
        )
        db.session.add(req)
        db.session.flush()  # para obtener ID

        # Materiales
        materiales_validos = 0
        for i in range(1, 11):
            desc = request.form.get(f"desc_{i}", "").strip()
            unidad = request.form.get(f"unidad_{i}", "").strip()
            cant_str = request.form.get(f"cant_{i}", "").strip()
            if not desc or not unidad or not cant_str:
                continue
            try:
                cant = int(cant_str)
                if cant <= 0:
                    continue
            except ValueError:
                continue
            mat = Material(
                requisition_id=req.id,
                descripcion=desc,
                unidad=unidad,
                cantidad=cant
            )
            db.session.add(mat)
            materiales_validos += 1

        if materiales_validos == 0:
            db.session.rollback()
            flash("Debes capturar al menos un material válido.")
            return redirect(url_for("new_requisition"))

        db.session.commit()
        flash(f"Requisición #{req.id} creada correctamente.")
        return redirect(url_for("dashboard"))

    return render_template_string(NEW_REQ_TEMPLATE)


@app.route("/requisiciones/<int:req_id>")
@login_required
def view_requisition(req_id):
    req = Requisition.query.get_or_404(req_id)
    # En un proyecto real, aquí también validarías permisos de ver
    return render_template_string(VIEW_REQ_TEMPLATE, req=req)


@app.route("/requisiciones/<int:req_id>/almacen", methods=["POST"])
@login_required
def process_almacen(req_id):
    user = current_user()
    if user.role != "Almacén":
        flash("Solo Almacén puede procesar requisiciones en este módulo.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    # Actualizar cantidades
    for m in req.materiales:
        rev_str = request.form.get(f"rev_{m.id}", "0")
        stock_str = request.form.get(f"stock_{m.id}", "0")
        try:
            rev_val = int(rev_str)
            stock_val = int(stock_str)
        except ValueError:
            rev_val = 0
            stock_val = 0
        if rev_val < 0 or rev_val > m.cantidad:
            rev_val = 0
        if stock_val < 0:
            stock_val = 0
        m.revisado_qty = rev_val
        m.stock_available = stock_val

    accion = request.form.get("accion")
    if accion not in ["Revisado - En Stock", "Revisado - Autorizada"]:
        flash("Acción de almacén inválida.")
        return redirect(url_for("view_requisition", req_id=req.id))

    req.estado = accion
    req.revisado_por = user.id
    req.fecha_revision = datetime.utcnow()

    db.session.commit()
    flash("Revisión de almacén guardada correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


@app.route("/requisiciones/<int:req_id>/compras", methods=["POST"])
@login_required
def process_compras(req_id):
    user = current_user()
    if user.role != "Compras":
        flash("Solo Compras puede registrar compras.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)
    proveedor = request.form.get("proveedor", "").strip()
    if not proveedor:
        flash("Proveedor obligatorio.")
        return redirect(url_for("view_requisition", req_id=req.id))

    tipo_compra = request.form.get("tipo_compra")
    if tipo_compra not in ["total", "parcial"]:
        flash("Debes indicar si la compra fue total o parcial.")
        return redirect(url_for("view_requisition", req_id=req.id))

    total = 0.0
    compra_parcial_detectada = False

    for m in req.materiales:
        cu_str = request.form.get(f"cu_{m.id}", "0")
        comp_str = request.form.get(f"comprado_{m.id}", "0")

        try:
            cu_val = float(cu_str)
        except ValueError:
            cu_val = 0.0
        try:
            comp_val = int(comp_str)
        except ValueError:
            comp_val = 0

        if cu_val < 0:
            cu_val = 0.0
        if comp_val < 0:
            comp_val = 0
        if comp_val > m.cantidad:
            comp_val = m.cantidad

        m.costo_unitario = cu_val
        m.comprado_qty = comp_val
        total += cu_val * comp_val

        if comp_val < m.cantidad:
            compra_parcial_detectada = True

    req.proveedor = proveedor
    req.costo_total = total

    if tipo_compra == "total" and not compra_parcial_detectada:
        req.estado = "Comprado"
    else:
        req.estado = "Comprado (Parcial)"

    db.session.commit()
    flash("Compra registrada correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


@app.route("/export_csv")
@login_required
def export_csv():
    """
    Exporta las requisiciones visibles para el rol actual a un CSV
    que Excel puede abrir sin problema (delimitador coma, UTF-8).
    """
    user = current_user()
    query = Requisition.query

    if user.role == "Mantenimiento":
        query = query.filter_by(solicitante_id=user.id)
    elif user.role == "Almacén":
        query = query.filter(Requisition.estado.in_([
            "Solicitado", "Revisado - En Stock", "Revisado - Autorizada"
        ]))
    elif user.role == "Compras":
        query = query.filter(Requisition.estado.in_([
            "Revisado - Autorizada", "Comprado", "Comprado (Parcial)"
        ]))

    requisitions = query.order_by(Requisition.id).all()

    si = StringIO()
    writer = csv.writer(si, delimiter=',')

    writer.writerow([
        "ID", "Fecha_Solicitud", "Fecha_Mantenimiento", "Proyecto",
        "Utilizacion", "Prioridad", "Estado", "Solicitante_ID",
        "Proveedor", "Costo_Total", "Revisado_Por", "Fecha_Revision",
        "Materiales_Detalle"
    ])

    for r in requisitions:
        materiales_str = " | ".join(
            f"{m.cantidad} {m.unidad} {m.descripcion} "
            f"(Rev:{m.revisado_qty} Stock:{m.stock_available} "
            f"CU:{m.costo_unitario} Comprado:{m.comprado_qty})"
            for m in r.materiales
        )

        writer.writerow([
            r.id,
            r.fecha_solicitud.isoformat() if r.fecha_solicitud else "",
            r.fecha_mantenimiento.isoformat() if r.fecha_mantenimiento else "",
            r.proyecto,
            r.utilizacion or "",
            r.prioridad,
            r.estado,
            r.solicitante_id,
            r.proveedor or "",
            f"{r.costo_total:.2f}" if r.costo_total is not None else "0.00",
            r.revisado_por or "",
            r.fecha_revision.isoformat() if r.fecha_revision else "",
            materiales_str
        ])

    output = StringIO()
    output.write(si.getvalue())
    output.seek(0)

    return send_file(
        output,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"requisiciones_{user.role}.csv"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))