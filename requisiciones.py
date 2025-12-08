import os
from datetime import date, datetime

from flask import (
    Flask, request, redirect, url_for, render_template, session, send_file, flash
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

    # Autorización por Mantenimiento 1 / 2
    autorizado = db.Column(db.Boolean, default=True)                # False si requiere autorización
    autorizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    fecha_autorizacion = db.Column(db.DateTime)

    # Resumen de compra
    proveedor = db.Column(db.String(255))      # resumen (se puede llenar con lista de proveedores materiales)
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

    # NUEVO: proveedor específico por material
    proveedor = db.Column(db.String(255))


# ============================================================
# INICIALIZACIÓN DE DB Y USUARIOS DE PRUEBA
# ============================================================

def seed_users():
    """
    Crea usuarios de prueba:
      - mantenimiento1 / m1 (Mantenimiento, AUTORIZA)
      - mantenimiento2 / m2 (Mantenimiento, AUTORIZA)
      - mantenimiento3 / m3 (Mantenimiento, SOLO SOLICITA)
      - almacen / a      (Almacén)
      - compras1 / c1    (Compras)
      - compras2 / c2    (Compras)
    """
    if User.query.count() > 0:
        return

    demo_users = [
        User(username="mantenimiento1", password="m1", role="Mantenimiento"),
        User(username="mantenimiento2", password="m2", role="Mantenimiento"),
        User(username="mantenimiento3", password="m3", role="Mantenimiento"),
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


def es_autorizador(user: User) -> bool:
    """Solo mantenimiento1 y mantenimiento2 pueden autorizar."""
    if not user or user.role != "Mantenimiento":
        return False
    return user.username in ("mantenimiento1", "mantenimiento2")


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
            return render_template(
                "login.html",
                error="Usuario o contraseña incorrectos."
            )
        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = user.role
        return redirect(url_for("dashboard"))

    return render_template("login.html", error=None)


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

    # Ahora TODOS los roles ven TODAS las requisiciones,
    # y cada quien filtra según lo que necesite.
    query = Requisition.query

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
        "Pendiente Autorización",
        "Solicitado",
        "Revisado - En Stock",
        "Revisado - Autorizada",
        "Comprado",
        "Comprado (Parcial)",
    ]

    return render_template(
        "dashboard.html",
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

        # Si el que solicita es mantenimiento3 -> requiere autorización
        if user.username == "mantenimiento3":
            estado_inicial = "Pendiente Autorización"
            autorizado = False
        else:
            estado_inicial = "Solicitado"
            autorizado = True

        req = Requisition(
            fecha_solicitud=date.today(),
            fecha_mantenimiento=fecha_mant,
            proyecto=proyecto,
            utilizacion=utilizacion,
            prioridad=prioridad,
            estado=estado_inicial,
            solicitante_id=user.id,
            autorizado=autorizado
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

    return render_template("new_requisition.html")


@app.route("/requisiciones/<int:req_id>")
@login_required
def view_requisition(req_id):
    req = Requisition.query.get_or_404(req_id)
    user = current_user()
    return render_template("view_requisition.html", req=req, user=user, es_autorizador=es_autorizador)


# ---------- AUTORIZACIÓN M1 / M2 ----------

@app.route("/requisiciones/<int:req_id>/autorizar", methods=["POST"])
@login_required
def autorizar_requisicion(req_id):
    user = current_user()
    req = Requisition.query.get_or_404(req_id)

    if not es_autorizador(user):
        flash("Solo mantenimiento1 y mantenimiento2 pueden autorizar requisiciones.")
        return redirect(url_for("view_requisition", req_id=req.id))

    if req.autorizado:
        flash("La requisición ya estaba autorizada.")
        return redirect(url_for("view_requisition", req_id=req.id))

    req.autorizado = True
    req.autorizado_por = user.id
    req.fecha_autorizacion = datetime.utcnow()
    # Cambio de estado a 'Solicitado' para que Almacén/Compras la trabajen
    req.estado = "Solicitado"

    db.session.commit()
    flash("Requisición autorizada correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


# ---------- ALMACÉN ----------

@app.route("/requisiciones/<int:req_id>/almacen", methods=["POST"])
@login_required
def process_almacen(req_id):
    user = current_user()
    if user.role != "Almacén":
        flash("Solo Almacén puede procesar requisiciones en este módulo.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    # Si no está autorizada (caso mantenimiento3), Almacén no debe trabajarla.
    if not req.autorizado:
        flash("Esta requisición aún no ha sido autorizada por Mantenimiento.")
        return redirect(url_for("view_requisition", req_id=req.id))

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


# ---------- COMPRAS ----------

@app.route("/requisiciones/<int:req_id>/compras", methods=["POST"])
@login_required
def process_compras(req_id):
    user = current_user()
    if user.role != "Compras":
        flash("Solo Compras puede registrar compras.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    tipo_compra = request.form.get("tipo_compra")
    if tipo_compra not in ["total", "parcial"]:
        flash("Debes indicar si la compra fue total o parcial.")
        return redirect(url_for("view_requisition", req_id=req.id))

    total = 0.0
    compra_parcial_detectada = False
    proveedores_usados = set()

    for m in req.materiales:
        cu_str = request.form.get(f"cu_{m.id}", "0")
        comp_str = request.form.get(f"comprado_{m.id}", "0")
        prov_str = request.form.get(f"prov_{m.id}", "").strip()

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
        m.proveedor = prov_str or None

        if m.proveedor:
            proveedores_usados.add(m.proveedor)

        total += cu_val * comp_val

        if comp_val < m.cantidad:
            compra_parcial_detectada = True

    # Resumen de proveedores en la cabecera (para referencia)
    if proveedores_usados:
        req.proveedor = ", ".join(sorted(proveedores_usados))
    else:
        req.proveedor = None

    req.costo_total = total

    if tipo_compra == "total" and not compra_parcial_detectada:
        req.estado = "Comprado"
    else:
        req.estado = "Comprado (Parcial)"

    db.session.commit()
    flash("Compra registrada correctamente. Almacén puede revisar las piezas compradas.")
    return redirect(url_for("view_requisition", req_id=req.id))

# ---------- EXPORT CSV ----------

@app.route("/export_csv")
@login_required
def export_csv():
    """
    Exporta TODAS las requisiciones (para todos los roles) a un CSV
    que Excel puede abrir sin problema (delimitador coma, UTF-8).
    """
    requisitions = Requisition.query.order_by(Requisition.id).all()

    si = StringIO()
    writer = csv.writer(si, delimiter=',')

    writer.writerow([
        "ID", "Fecha_Solicitud", "Fecha_Mantenimiento", "Proyecto",
        "Utilizacion", "Prioridad", "Estado", "Solicitante_ID",
        "Autorizado", "Autorizado_Por", "Fecha_Autorizacion",
        "Proveedor_Resumen", "Costo_Total",
        "Revisado_Por", "Fecha_Revision",
        "Materiales_Detalle"
    ])

    for r in requisitions:
        materiales_str = " | ".join(
            f"{m.cantidad} {m.unidad} {m.descripcion} "
            f"(Rev:{m.revisado_qty} Stock:{m.stock_available} "
            f"CU:{m.costo_unitario} Comprado:{m.comprado_qty} "
            f"Prov:{m.proveedor or ''})"
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
            "SI" if r.autorizado else "NO",
            r.autorizado_por or "",
            r.fecha_autorizacion.isoformat() if r.fecha_autorizacion else "",
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
        download_name="requisiciones_todas.csv"
    )

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))