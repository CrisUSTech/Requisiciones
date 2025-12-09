import os
from datetime import date, datetime

from flask import (
    Flask, request, redirect, url_for, render_template, session,
    send_file, flash
)
from flask_sqlalchemy import SQLAlchemy
from io import StringIO
import csv

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

db = SQLAlchemy()


def get_database_uri():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return db_url
    return "sqlite:///local.db"


app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ===========================
# MODELOS
# ===========================

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False)


class Requisition(db.Model):
    __tablename__ = "requisitions"
    id = db.Column(db.Integer, primary_key=True)

    fecha_solicitud = db.Column(db.Date, nullable=False, default=date.today)
    fecha_mantenimiento = db.Column(db.Date, nullable=False)

    proyecto = db.Column(db.String(255), nullable=False)
    utilizacion = db.Column(db.Text)
    # NUEVO: área donde se va a ocupar el material
    area_uso = db.Column(db.String(255))

    prioridad = db.Column(db.String(20), nullable=False)   # Alta / Media / Baja
    # Flujo de estados (nuevo set, pero compatible con los anteriores)
    estado = db.Column(db.String(50), nullable=False, default="Solicitado")

    solicitante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Autorización para mantenimiento3 (global de la requisición)
    autorizado = db.Column(db.Boolean, default=True)
    autorizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    fecha_autorizacion = db.Column(db.DateTime)

    # Resumen de compras
    proveedor = db.Column(db.String(255))
    costo_total = db.Column(db.Float, default=0.0)

    # Trazabilidad general (puede usarse para Almacén / Compras / Cierre)
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

    # Datos básicos
    descripcion = db.Column(db.Text, nullable=False)
    unidad = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)  # Cantidad solicitada

    # Campos antiguos relacionados con revisión/stock previo
    revisado_qty = db.Column(db.Integer, default=0)
    stock_available = db.Column(db.Integer, default=0)

    # Compras
    costo_unitario = db.Column(db.Float, default=0.0)
    comprado_qty = db.Column(db.Integer, default=0)
    proveedor = db.Column(db.String(255))  # proveedor por material

    # NUEVO: control extra para compras
    no_comprado = db.Column(db.Boolean, default=False)
    no_autorizado_compras = db.Column(db.Boolean, default=False)

    # NUEVO: recepción en almacén (checkbox llegó / no llegó)
    recibido_almacen = db.Column(db.Boolean, default=False)

    # NUEVO: retiro de material por Mantenimiento
    retirado_qty = db.Column(db.Integer, default=0)
    no_retirado = db.Column(db.Boolean, default=False)


# ===========================
# SEED USERS
# ===========================

def seed_users():
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


# ===========================
# HELPERS
# ===========================

def current_user():
    if "user_id" not in session:
        return None
    return User.query.get(session["user_id"])


def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def es_autorizador(user: User) -> bool:
    if not user or user.role != "Mantenimiento":
        return False
    return user.username in ("mantenimiento1", "mantenimiento2")


def actualizar_estado_recepcion(req: Requisition):
    """
    Actualiza el estado global de la requisición según lo recibido en almacén.
    Considera solo materiales realmente comprados y no marcados como no_comprado / no_autorizado_compras.
    """
    mats = [
        m for m in req.materiales
        if (m.comprado_qty or 0) > 0 and not m.no_comprado and not m.no_autorizado_compras
    ]

    if not mats:
        # No hay nada comprado relevante, no cambiamos el estado aquí.
        return

    total = len(mats)
    recibidos = sum(1 for m in mats if m.recibido_almacen)

    if recibidos == 0:
        req.estado = "No Recibido"
    elif recibidos < total:
        req.estado = "Recibido Parcial"
    else:
        req.estado = "Recibido"


def actualizar_estado_cierre(req: Requisition):
    """
    Marca la requisición como 'Cerrado' cuando todos los materiales recibidos
    ya fueron procesados por Mantenimiento (retirados o marcados como no retirados).
    """
    mats = [m for m in req.materiales if m.recibido_almacen]

    if not mats:
        # No hay nada recibido aún
        return

    for m in mats:
        if (m.retirado_qty or 0) <= 0 and not m.no_retirado:
            # Todavía hay materiales recibidos que no se han registrado como retirados
            return

    # Si llegamos aquí, todo lo recibido ya se procesó
    user = current_user()
    req.estado = "Cerrado"
    req.finalizado_por = user.id if user else None
    req.fecha_finalizacion = datetime.utcnow()


# ===========================
# RUTAS AUTH
# ===========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = request.form.get("username", "").strip().lower()
        pwd = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.username) == uname).first()
        if not user or user.password != pwd:
            return render_template("login.html", error="Usuario o contraseña incorrectos.")
        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = user.role
        return redirect(url_for("dashboard"))

    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ===========================
# DASHBOARD
# ===========================

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    q_proyecto = request.args.get("proyecto", "").strip().lower()
    q_prioridad = request.args.get("prioridad", "")
    q_fecha_mant = request.args.get("fecha_mantenimiento", "")
    q_estado = request.args.get("estado", "")

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

    # Nuevo conjunto de estados disponibles para filtros
    estados_posibles = [
        "Pendiente Autorización",
        "Solicitado",
        "En Stock",
        "No Disponible",
        "Comprado",
        "Comprado (Parcial)",
        "No Comprado",
        "No Autorizado Compras",
        "No Recibido",
        "Recibido Parcial",
        "Recibido",
        "Cerrado",
    ]

    return render_template(
        "dashboard.html",
        requisitions=requisitions,
        estados=estados_posibles
    )


# ===========================
# NUEVA REQUISICIÓN (MATERIALES DINÁMICOS)
# ===========================

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
        area_uso = request.form.get("area_uso", "").strip()
        prioridad = request.form.get("prioridad", "Media")

        # lógicas de autorización por usuario
        if user.username == "mantenimiento3":
            estado_ini = "Pendiente Autorización"
            autorizado = False
        else:
            estado_ini = "Solicitado"
            autorizado = True

        req = Requisition(
            fecha_solicitud=date.today(),
            fecha_mantenimiento=fecha_mant,
            proyecto=proyecto,
            utilizacion=utilizacion,
            area_uso=area_uso,
            prioridad=prioridad,
            estado=estado_ini,
            solicitante_id=user.id,
            autorizado=autorizado
        )
        db.session.add(req)
        db.session.flush()  # ID listo

        # MATERIALES DINÁMICOS: desc[], unidad[], cant[]
        descs = request.form.getlist("desc[]")
        unidades = request.form.getlist("unidad[]")
        cants = request.form.getlist("cant[]")

        materiales_validos = 0
        for desc, unidad, cant_str in zip(descs, unidades, cants):
            desc = (desc or "").strip()
            unidad = (unidad or "").strip()
            cant_str = (cant_str or "").strip()
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


# ===========================
# DETALLE
# ===========================

@app.route("/requisiciones/<int:req_id>")
@login_required
def view_requisition(req_id):
    req = Requisition.query.get_or_404(req_id)
    user = current_user()
    return render_template("view_requisition.html", req=req, user=user, es_autorizador=es_autorizador)


# ===========================
# AUTORIZAR (MANTENIMIENTO1/2 SOBRE REQS DE MANTENIMIENTO3)
# ===========================

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
    req.estado = "Solicitado"

    db.session.commit()
    flash("Requisición autorizada correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


# ===========================
# COMPRAS
# ===========================

@app.route("/requisiciones/<int:req_id>/compras", methods=["POST"])
@login_required
def process_compras(req_id):
    user = current_user()
    if user.role != "Compras":
        flash("Solo Compras puede registrar compras.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    # tipo_compra se mantiene por compatibilidad con el formulario,
    # pero el estado real se calcula con base en lo comprado y flags.
    tipo_compra = request.form.get("tipo_compra")

    total = 0.0
    proveedores_usados = set()

    for m in req.materiales:
        cu_str = request.form.get(f"cu_{m.id}", "0")
        comp_str = request.form.get(f"comprado_{m.id}", "0")
        prov_str = request.form.get(f"prov_{m.id}", "").strip()

        # checkboxes opcionales
        no_comp_key = f"no_comp_{m.id}"
        no_aut_key = f"no_aut_{m.id}"

        no_comprado = no_comp_key in request.form
        no_autorizado = no_aut_key in request.form

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
        m.comprado_qty = 0 if no_comprado or no_autorizado else comp_val
        m.proveedor = prov_str or None
        m.no_comprado = no_comprado
        m.no_autorizado_compras = no_autorizado

        if m.proveedor:
            proveedores_usados.add(m.proveedor)

        # Solo sumamos al total lo efectivamente comprado
        if not m.no_comprado and not m.no_autorizado_compras:
            total += cu_val * m.comprado_qty

    if proveedores_usados:
        req.proveedor = ", ".join(sorted(proveedores_usados))
    else:
        req.proveedor = None

    req.costo_total = total

    # Determinar estado global según lo comprado
    materiales = req.materiales

    # 1) Si todos los materiales quedaron como no autorizados por compras
    if materiales and all(m.no_autorizado_compras for m in materiales):
        req.estado = "No Autorizado Compras"
    else:
        # Considerar solo materiales que no fueron marcados como no autorizados
        elegibles = [m for m in materiales if not m.no_autorizado_compras]

        if not elegibles:
            req.estado = "No Comprado"
        else:
            comprables = [m for m in elegibles if not m.no_comprado]

            if not comprables:
                req.estado = "No Comprado"
            else:
                total_m = len(comprables)
                fully = 0
                any_positive = False
                for m in comprables:
                    if (m.comprado_qty or 0) > 0:
                        any_positive = True
                    if m.cantidad and (m.comprado_qty or 0) >= m.cantidad:
                        fully += 1

                if any_positive:
                    if fully == total_m:
                        req.estado = "Comprado"
                    else:
                        req.estado = "Comprado (Parcial)"
                else:
                    req.estado = "No Comprado"

    db.session.commit()
    flash("Compra registrada correctamente. Almacén puede revisar la llegada del material.")
    return redirect(url_for("view_requisition", req_id=req.id))


# ===========================
# ALMACÉN – RECEPCIÓN DE MATERIAL COMPRADO
# ===========================

@app.route("/requisiciones/<int:req_id>/almacen", methods=["POST"])
@login_required
def process_almacen(req_id):
    """
    En esta versión, Almacén únicamente marca si el material comprado llegó o no llegó,
    mediante checkboxes por material.
    """
    user = current_user()
    if user.role != "Almacén":
        flash("Solo Almacén puede procesar la recepción de compras.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    if req.estado not in ["Comprado", "Comprado (Parcial)"]:
        flash("Solo se puede registrar recepción para requisiciones compradas.")
        return redirect(url_for("view_requisition", req_id=req.id))

    for m in req.materiales:
        if (m.comprado_qty or 0) > 0 and not m.no_comprado and not m.no_autorizado_compras:
            key = f"llego_{m.id}"
            m.recibido_almacen = key in request.form
        else:
            # Material no comprado / no autorizado -> no se marca como recibido
            m.recibido_almacen = False

    # Actualizar estado global según recepción
    actualizar_estado_recepcion(req)
    req.revisado_por = user.id
    req.fecha_revision = datetime.utcnow()

    db.session.commit()
    flash("Recepción en almacén guardada correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


# ===========================
# MANTENIMIENTO – RETIRO DE MATERIAL
# ===========================

@app.route("/requisiciones/<int:req_id>/retiro", methods=["POST"])
@login_required
def procesar_retiro_mantenimiento(req_id):
    user = current_user()
    if user.role != "Mantenimiento":
        flash("Solo Mantenimiento puede registrar retiro de material.")
        return redirect(url_for("view_requisition", req_id=req_id))

    req = Requisition.query.get_or_404(req_id)

    if req.estado not in ["Recibido", "Recibido Parcial"]:
        flash("Solo se puede registrar retiro cuando la requisición ya fue recibida en almacén.")
        return redirect(url_for("view_requisition", req_id=req.id))

    for m in req.materiales:
        if not m.recibido_almacen:
            # Solo procesamos materiales que efectivamente llegaron a almacén
            continue

        key_qty = f"ret_{m.id}"
        key_no = f"no_ret_{m.id}"

        no_ret = key_no in request.form
        m.no_retirado = no_ret

        if not no_ret:
            valor = request.form.get(key_qty, "0").strip()
            try:
                qty = int(valor)
            except ValueError:
                qty = 0
            if qty < 0:
                qty = 0
            if m.comprado_qty is not None and qty > m.comprado_qty:
                qty = m.comprado_qty
            m.retirado_qty = qty
        else:
            m.retirado_qty = 0

    actualizar_estado_cierre(req)
    db.session.commit()
    flash("Retiro de material registrado correctamente.")
    return redirect(url_for("view_requisition", req_id=req.id))


# ===========================
# EXPORT CSV
# ===========================

@app.route("/export_csv")
@login_required
def export_csv():
    requisitions = Requisition.query.order_by(Requisition.id).all()

    si = StringIO()
    # Usamos ';' para que Excel (con separador de lista ';') abra mejor el archivo.
    writer = csv.writer(si, delimiter=';')

    writer.writerow([
        "ID", "Fecha_Solicitud", "Fecha_Mantenimiento", "Proyecto",
        "Area_Uso", "Utilizacion", "Prioridad", "Estado", "Solicitante_ID",
        "Autorizado", "Autorizado_Por", "Fecha_Autorizacion",
        "Proveedor_Resumen", "Costo_Total",
        "Revisado_Por", "Fecha_Revision",
        "Finalizado_Por", "Fecha_Finalizacion",
        "Materiales_Detalle"
    ])

    for r in requisitions:
        materiales_str = " | ".join(
            f"{m.cantidad} {m.unidad} {m.descripcion} "
            f"(CU:{m.costo_unitario} Comprado:{m.comprado_qty} "
            f"Prov:{m.proveedor or ''} "
            f"NoComprado:{'SI' if m.no_comprado else 'NO'} "
            f"NoAutCompras:{'SI' if m.no_autorizado_compras else 'NO'} "
            f"Recibido:{'SI' if m.recibido_almacen else 'NO'} "
            f"Retirado:{m.retirado_qty} "
            f"NoRetirado:{'SI' if m.no_retirado else 'NO'})"
            for m in r.materiales
        )

        writer.writerow([
            r.id,
            r.fecha_solicitud.isoformat() if r.fecha_solicitud else "",
            r.fecha_mantenimiento.isoformat() if r.fecha_mantenimiento else "",
            r.proyecto,
            r.area_uso or "",
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
            r.finalizado_por or "",
            r.fecha_finalizacion.isoformat() if r.fecha_finalizacion else "",
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


# ===========================
# MAIN
# ===========================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))