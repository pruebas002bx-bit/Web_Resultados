import os
import ssl
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from functools import wraps
from io import BytesIO
from xhtml2pdf import pisa
from sqlalchemy import func
from fpdf import FPDF


app = Flask(__name__)
app.secret_key = "alpha_tactical_ultra_secret"
CORS(app)

# Configuración de Base de Datos
db_url = os.environ.get('DATABASE_URL', 'sqlite:///local_fallback.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+pg8000://", 1)
elif db_url.startswith("postgresql://") and "pg8000" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)

if "?" in db_url:
    db_url = db_url.split("?")[0]

if "pg8000" in db_url:
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'ssl_context': ssl_context}}

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELOS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False) # admin, partner, membresia
    group_name = db.Column(db.String(100), nullable=True) 
    shooter_id = db.Column(db.String(50), nullable=True)
    location = db.Column(db.String(100), nullable=True)

class ScoreRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sim_id = db.Column(db.String(100), nullable=False)
    shooter_name = db.Column(db.String(100), nullable=False)
    shooter_id = db.Column(db.String(50), nullable=True)
    group_name = db.Column(db.String(100), nullable=True)
    scenario = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", password="admin123", role="admin")
        db.session.add(admin)
        db.session.commit()

# --- RUTAS ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(
            username=request.form['username'], 
            password=request.form['password'],
            role=request.form['role']
        ).first()
        if user:
            session.update({'user_id': user.id, 'role': user.role, 'username': user.username,
                            'filter_val': user.group_name if user.role == 'partner' else user.shooter_id})
            return redirect(url_for('dashboard'))
    return render_template('dashboard.html', login_view=True)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register_user', methods=['POST'])
@login_required
def register_user():
    if session['role'] != 'admin': return jsonify({"status": "denied"}), 403
    data = request.json
    try:
        new_user = User(
            username=data['username'], password=data['password'], role=data['role'],
            group_name=data.get('group_name'), shooter_id=data.get('shooter_id'),
            location=data.get('location')
        )
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"status": "success"})
    except: return jsonify({"status": "error"}), 400

@app.route('/')
@login_required
def dashboard():
    role = session['role']
    query = ScoreRecord.query
    unique_shooters = []
    
    if role == 'partner':
        records = query.filter_by(group_name=session['filter_val']).order_by(ScoreRecord.id.desc()).all()
        # Obtener lista única de tiradores para el PDF
        unique_shooters = db.session.query(ScoreRecord.shooter_id, ScoreRecord.shooter_name)\
            .filter_by(group_name=session['filter_val'])\
            .distinct(ScoreRecord.shooter_id).all()
    elif role == 'membresia':
        records = query.filter_by(shooter_id=session['filter_val']).order_by(ScoreRecord.id.desc()).all()
    else:
        records = query.order_by(ScoreRecord.id.desc()).all()
        
    return render_template('dashboard.html', records=records, role=role, 
                           username=session['username'], shooters=unique_shooters)

@app.route('/generate_pdf')
@login_required
def generate_pdf():
    if session['role'] != 'partner': return "Acceso Denegado", 403
    
    s_id = request.args.get('id')
    d_from = request.args.get('from')
    d_to = request.args.get('to')
    
    query = ScoreRecord.query.filter_by(group_name=session['filter_val'], shooter_id=s_id)
    if d_from: query = query.filter(ScoreRecord.timestamp >= d_from)
    if d_to: query = query.filter(ScoreRecord.timestamp <= d_to + " 23:59:59")
    
    records = query.order_by(ScoreRecord.timestamp.asc()).all()
    if not records: return "No se encontraron registros", 404

    # --- LÓGICA DE GENERACIÓN PDF PROFESIONAL ---
    class TacticPDF(FPDF):
        def header(self):
            # Logo y Título
            self.image('https://i.ibb.co/j9Pp0YLz/Logo-2.png', 10, 8, 33)
            self.set_font('helvetica', 'B', 20)
            self.set_text_color(0, 0, 0)
            self.cell(80)
            self.cell(100, 10, 'EXPEDIENTE TÁCTICO ALPHA', border=0, align='R')
            self.ln(5)
            self.set_font('helvetica', 'B', 8)
            self.set_text_color(185, 28, 28) # Rojo
            self.cell(180, 10, 'DOCUMENTO OFICIAL DE EVALUACIÓN - CONFIDENCIAL', align='R')
            self.ln(20)
            self.set_draw_color(185, 28, 28)
            self.line(10, 35, 200, 35)

    pdf = TacticPDF()
    pdf.add_page()
    
    # Datos del Tirador
    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(190, 10, ' RESUMEN DE INTELIGENCIA Y RENDIMIENTO', ln=True, fill=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', '', 10)
    pdf.ln(5)
    
    scores = [r.score for r in records]
    avg = sum(scores)/len(scores)
    
    # Cuadro de datos
    pdf.cell(95, 8, f"Tirador: {records[0].shooter_name.upper()}", border='B')
    pdf.cell(95, 8, f"ID: {s_id}", border='B', ln=True)
    pdf.cell(95, 8, f"Grupo: {session['filter_val']}", border='B')
    pdf.cell(95, 8, f"Sesiones: {len(records)}", border='B', ln=True)
    
    pdf.ln(5)
    pdf.set_font('helvetica', 'B', 10)
    pdf.cell(47, 10, f"MAX: {max(scores)}", border=1, align='C')
    pdf.cell(47, 10, f"MIN: {min(scores)}", border=1, align='C')
    pdf.set_text_color(185, 28, 28)
    pdf.cell(47, 10, f"PROMEDIO: {avg:.2f}%", border=1, align='C')
    pdf.set_text_color(0, 0, 0)
    pdf.cell(49, 10, f"ESTADO: {'CALIFICADO' if avg >= 80 else 'EN REPASO'}", border=1, align='C', ln=True)

    # Tabla de Resultados
    pdf.ln(10)
    pdf.set_fill_color(185, 28, 28)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(50, 10, 'FECHA', fill=True, align='C')
    pdf.cell(70, 10, 'ESCENARIO', fill=True, align='C')
    pdf.cell(40, 10, 'ESTACIÓN', fill=True, align='C')
    pdf.cell(30, 10, 'PUNTAJE', fill=True, align='C', ln=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', '', 9)
    for r in records:
        pdf.cell(50, 8, r.timestamp, border='B', align='C')
        pdf.cell(70, 8, r.scenario[:25].upper(), border='B', align='C')
        pdf.cell(40, 8, r.sim_id, border='B', align='C')
        pdf.set_font('helvetica', 'B', 9)
        pdf.cell(30, 8, str(r.score), border='B', align='C', ln=True)
        pdf.set_font('helvetica', '', 9)

    # Firmas
    pdf.ln(30)
    pdf.line(20, pdf.get_y(), 80, pdf.get_y())
    pdf.line(130, pdf.get_y(), 190, pdf.get_y())
    pdf.set_font('helvetica', 'B', 8)
    pdf.cell(95, 5, 'FIRMA DEL TIRADOR', align='C')
    pdf.cell(95, 5, f'CERTIFICACIÓN: {session["filter_val"]}', align='C', ln=True)

    response = make_response(pdf.output())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Reporte_Alpha_{s_id}.pdf'
    return response

@app.route('/api/upload_score', methods=['POST'])
def upload_score():
    try:
        data = request.json
        new_record = ScoreRecord(
            sim_id=data.get('sim_id'), shooter_name=data.get('shooter_name'),
            shooter_id=data.get('shooter_id'), group_name=data.get('group_name'),
            scenario=data.get('scenario'), score=int(data.get('score', 0)),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        db.session.add(new_record)
        db.session.commit()
        return jsonify({"status": "success"}), 200
    except: return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))