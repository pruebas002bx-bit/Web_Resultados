import os
import ssl
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from functools import wraps
from io import BytesIO
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
    
    s_id = request.args.get('id', '').strip()
    d_from = request.args.get('from', '').strip() # Recibe YYYY-MM-DD
    d_to = request.args.get('to', '').strip()
    filter_val = session.get('filter_val', '').strip()
    
    # --- CORRECCIÓN DE FORMATO DE FECHA PARA BÚSQUEDA ---
    # Convertimos YYYY-MM-DD a DD/MM/YYYY para coincidir con tu BD
    search_from = datetime.strptime(d_from, '%Y-%m-%d').strftime('%d/%m/%Y') if d_from else ""
    search_to = datetime.strptime(d_to, '%Y-%m-%d').strftime('%d/%m/%Y') if d_to else ""

    query = ScoreRecord.query.filter(
        func.lower(ScoreRecord.group_name) == func.lower(filter_val),
        ScoreRecord.shooter_id == s_id
    )
    
    # Filtro por texto de fecha (almacenado como string)
    if search_from:
        query = query.filter(ScoreRecord.timestamp >= f"{search_from} 00:00:00")
    if search_to:
        query = query.filter(ScoreRecord.timestamp <= f"{search_to} 23:59:59")
    
    records = query.order_by(ScoreRecord.timestamp.asc()).all()
    
    if not records:
        return f"Error: No hay datos para el ID {s_id} en las fechas seleccionadas.", 404

    class TacticPDF(FPDF):
        def header(self):
            # No descargar imagen en vivo para evitar Error 502
            self.set_fill_color(185, 28, 28)
            self.rect(0, 0, 215, 30, 'F')
            self.set_font('helvetica', 'B', 20)
            self.set_text_color(255, 255, 255)
            self.cell(0, 15, 'EXPEDIENTE TÁCTICO ALPHA', align='C', ln=True)
            self.ln(10)

    pdf = TacticPDF()
    pdf.add_page()
    
    # Datos del Tirador
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(190, 10, f" OPERADOR: {records[0].shooter_name.upper()}", ln=True, border='B')
    
    pdf.set_font('helvetica', '', 10)
    pdf.ln(5)
    scores = [r.score for r in records]
    
    pdf.cell(95, 8, f"Identificación: {s_id}")
    pdf.cell(95, 8, f"Grupo: {filter_val.upper()}", ln=True)
    pdf.cell(95, 8, f"Puntaje Máximo: {max(scores)}")
    pdf.cell(95, 8, f"Promedio: {sum(scores)/len(scores):.2f}%", ln=True)
    
    # Tabla de Resultados
    pdf.ln(10)
    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(50, 10, 'FECHA', fill=True, align='C')
    pdf.cell(75, 10, 'ESCENARIO', fill=True, align='C')
    pdf.cell(35, 10, 'ESTACIÓN', fill=True, align='C')
    pdf.cell(30, 10, 'PUNTAJE', fill=True, align='C', ln=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', '', 9)
    for r in records:
        pdf.cell(50, 8, r.timestamp, border='B', align='C')
        pdf.cell(75, 8, r.scenario[:28].upper(), border='B', align='C')
        pdf.cell(35, 8, r.sim_id[:15], border='B', align='C')
        pdf.cell(30, 8, str(r.score), border='B', align='C', ln=True)

    pdf.ln(30)
    pdf.cell(95, 5, '_________________________', align='C')
    pdf.cell(95, 5, '_________________________', align='C', ln=True)
    pdf.cell(95, 5, 'FIRMA DEL TIRADOR', align='C')
    pdf.cell(95, 5, 'FIRMA INSTRUCTOR', align='C')

    response = make_response(pdf.output())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Reporte_{s_id}.pdf'
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