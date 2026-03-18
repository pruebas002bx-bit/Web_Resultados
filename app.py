import os
import ssl
import io
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
    d_from_str = request.args.get('from', '').strip()
    d_to_str = request.args.get('to', '').strip()
    filter_val = session.get('filter_val', '').strip()
    
    all_records = ScoreRecord.query.filter(
        func.lower(ScoreRecord.group_name) == func.lower(filter_val),
        ScoreRecord.shooter_id == s_id
    ).all()
    
    records = []
    try:
        limit_from = datetime.strptime(d_from_str, '%Y-%m-%d') if d_from_str else None
        limit_to = datetime.strptime(d_to_str, '%Y-%m-%d') if d_to_str else None
        for r in all_records:
            r_date = datetime.strptime(r.timestamp.split(' ')[0], '%d/%m/%Y')
            if limit_from and r_date < limit_from: continue
            if limit_to and r_date > limit_to: continue
            records.append(r)
    except: pass

    if not records: return "No hay registros para este rango.", 404

    scores = [r.score for r in records]
    scores_str = ",".join(map(str, scores))
    avg = sum(scores)/len(scores)
    chart_url = f"https://chart.googleapis.com/chart?cht=lc&chs=600x200&chd=t:{scores_str}&chco=B91C1C&chf=bg,s,FFFFFF&chxt=y&chg=20,20,1,5"

    class TacticPDF(FPDF):
        def header(self):
            self.set_fill_color(185, 28, 28)
            self.rect(0, 0, 215, 35, 'F')
            try:
                # El logo se carga desde URL; si falla, el PDF no se rompe
                self.image('https://i.ibb.co/j9Pp0YLz/Logo-2.png', 10, 8, 25)
            except:
                pass
            self.set_text_color(255, 255, 255)
            self.set_font('helvetica', 'B', 20)
            self.cell(0, 12, 'EXPEDIENTE TACTICO DE RENDIMIENTO', align='R', ln=True)
            self.set_font('helvetica', 'B', 8)
            self.cell(0, 5, 'SISTEMA ALPHA CLOUD - REPORTE OFICIAL CONFIDENCIAL', align='R', ln=True)
            self.ln(15)

    pdf = TacticPDF()
    pdf.add_page()
    
    # Secciones de Datos
    pdf.set_font('helvetica', 'B', 10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(190, 10, 'I. DATOS DEL OPERADOR Y TELEMETRIA GLOBAL', ln=True)
    
    pdf.set_fill_color(249, 250, 251)
    pdf.rect(10, 55, 190, 30, 'F')
    
    pdf.set_font('helvetica', 'B', 9)
    pdf.set_text_color(185, 28, 28)
    pdf.cell(47, 8, 'NOMBRE DEL TIRADOR', align='C')
    pdf.cell(47, 8, 'IDENTIFICACION', align='C')
    pdf.cell(47, 8, 'GRUPO / EMPRESA', align='C')
    pdf.cell(49, 8, 'FECHA REPORTE', align='C', ln=True)
    
    pdf.set_font('helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(47, 8, records[0].shooter_name.upper(), align='C')
    pdf.cell(47, 8, s_id, align='C')
    pdf.cell(47, 8, filter_val.upper(), align='C')
    pdf.cell(49, 8, datetime.now().strftime("%d/%m/%Y"), align='C', ln=True)
    
    pdf.ln(15)
    pdf.set_font('helvetica', 'B', 10)
    pdf.cell(190, 10, 'II. ANALISIS DE PROGRESO (CURVA DE PUNTAJE)', ln=True)
    try:
        pdf.image(chart_url, x=15, w=180)
    except:
        pdf.cell(190, 10, "[Grafica no disponible]", align='C', ln=True)
    
    pdf.ln(10)
    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 10)
    pdf.cell(63, 10, f"MAXIMO: {max(scores)}", border=1, align='C', fill=True)
    pdf.cell(63, 10, f"PROMEDIO: {avg:.2f}%", border=1, align='C', fill=True)
    pdf.cell(64, 10, f"SESIONES: {len(records)}", border=1, align='C', fill=True, ln=True)

    pdf.ln(10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(190, 10, 'III. DESGLOSE DETALLADO DE SESIONES', ln=True)
    
    pdf.set_fill_color(185, 28, 28)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(50, 10, 'FECHA Y HORA', fill=True, align='C')
    pdf.cell(75, 10, 'ESCENARIO', fill=True, align='C')
    pdf.cell(35, 10, 'ESTACION', fill=True, align='C')
    pdf.cell(30, 10, 'PUNTAJE', fill=True, align='C', ln=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', '', 9)
    for r in records:
        if r.score >= 90: pdf.set_fill_color(254, 242, 242)
        else: pdf.set_fill_color(255, 255, 255)
        
        pdf.cell(50, 8, r.timestamp, border='B', align='C', fill=True)
        pdf.cell(75, 8, r.scenario[:28].upper(), border='B', align='C', fill=True)
        pdf.cell(35, 8, r.sim_id[:15], border='B', align='C', fill=True)
        pdf.set_font('helvetica', 'B', 9)
        pdf.cell(30, 8, str(r.score), border='B', align='C', fill=True, ln=True)
        pdf.set_font('helvetica', '', 9)

    pdf.ln(30)
    y_sig = pdf.get_y()
    pdf.line(20, y_sig, 85, y_sig)
    pdf.line(125, y_sig, 190, y_sig)
    pdf.set_font('helvetica', 'B', 8)
    pdf.cell(95, 5, 'FIRMA DEL OPERADOR', align='C')
    pdf.cell(95, 5, f'CERTIFICACION: {filter_val.upper()}', align='C', ln=True)

    # --- CORRECCIÓN FINAL ---
    # Convertimos a bytes y enviamos como archivo binario
    pdf_output = bytes(pdf.output())
    return send_file(
        io.BytesIO(pdf_output),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Reporte_Tactico_{s_id}.pdf"
    )


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