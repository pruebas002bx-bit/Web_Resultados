import os
import ssl
import io
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from functools import wraps
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
    import io
    import matplotlib
    matplotlib.use('Agg') # Obligatorio para generar gráficas en la nube sin interfaz gráfica
    import matplotlib.pyplot as plt
    
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
            # Captura correcta de la fecha si el formato viene con hora AM/PM
            r_date_str = r.timestamp.split(' ')[0]
            r_date = datetime.strptime(r_date_str, '%d/%m/%Y')
            if limit_from and r_date < limit_from: continue
            if limit_to and r_date > limit_to: continue
            records.append(r)
    except: pass

    if not records: return "No hay registros para este rango.", 404

    scores = [r.score for r in records]
    avg = sum(scores)/len(scores)
    
    # --- MÉTODO LOCAL: GRÁFICA INTERNA (Matplotlib) ---
    chart_data = io.BytesIO()
    try:
        plt.figure(figsize=(10, 3.5))
        x_labels = [f"M{i+1}" for i in range(len(scores))]
        
        # Diseño Triple A: Línea roja, marcadores, relleno inferior
        plt.plot(x_labels, scores, color='#B91C1C', marker='o', linewidth=2.5, markersize=7)
        plt.fill_between(x_labels, scores, color='#B91C1C', alpha=0.1)
        
        plt.ylim(0, 105)
        plt.ylabel("Puntaje Logrado", fontsize=10, fontweight='bold', color='#333333')
        plt.grid(True, linestyle='--', alpha=0.4)
        
        # Elimina bordes feos del gráfico
        for spine in plt.gca().spines.values():
            spine.set_color('#DDDDDD')
            
        plt.tight_layout()
        plt.savefig(chart_data, format='png', dpi=150, transparent=False)
        plt.close()
        chart_data.seek(0) # Clave para que el PDF pueda leer la imagen
    except Exception as e:
        print(f"Error Matplotlib: {e}")
        chart_data = None
    # ---------------------------------------------------

    class TacticPDF(FPDF):
        def header(self):
            # LOGO CON FONDO BLANCO
            try: 
                import requests
                logo_res = requests.get('https://i.ibb.co/j9Pp0YLz/Logo-2.png', timeout=5)
                if logo_res.status_code == 200:
                    self.image(io.BytesIO(logo_res.content), 10, 8, 40, name="logo.png")
            except: pass
            
            self.set_font('helvetica', 'B', 22)
            self.set_text_color(0, 0, 0)
            self.cell(0, 10, 'EXPEDIENTE TACTICO DE RENDIMIENTO', align='R', ln=True)
            
            self.set_font('helvetica', 'B', 9)
            self.set_text_color(185, 28, 28)
            self.cell(0, 6, 'SISTEMA ALPHA CLOUD - REPORTE OFICIAL CONFIDENCIAL', align='R', ln=True)
            self.ln(5)
            
            self.set_draw_color(185, 28, 28)
            self.set_line_width(1.2)
            self.line(10, 32, 200, 32)
            self.ln(12)

        def footer(self):
            self.set_y(-15)
            self.set_font('helvetica', 'I', 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f'Generado por Alpha Cloud Systems | Pagina {self.page_no()}', align='C')

    pdf = TacticPDF()
    pdf.add_page()
    
    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 11)
    pdf.cell(190, 9, ' I. PERFIL DEL OPERADOR Y RESUMEN ESTADISTICO', ln=True, fill=True)
    
    pdf.set_fill_color(249, 250, 251)
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.2)
    pdf.rect(10, pdf.get_y(), 190, 28, 'DF')
    pdf.ln(3)
    
    pdf.set_font('helvetica', 'B', 9)
    pdf.set_text_color(185, 28, 28)
    pdf.cell(47, 6, 'NOMBRE DEL TIRADOR', align='C')
    pdf.cell(47, 6, 'IDENTIFICACION', align='C')
    pdf.cell(47, 6, 'UNIDAD / GRUPO', align='C')
    pdf.cell(49, 6, 'FECHA DE REPORTE', align='C', ln=True)
    
    pdf.set_font('helvetica', 'B', 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(47, 8, records[0].shooter_name.upper(), align='C')
    pdf.cell(47, 8, s_id, align='C')
    pdf.cell(47, 8, filter_val.upper(), align='C')
    pdf.cell(49, 8, datetime.now().strftime("%d/%m/%Y"), align='C', ln=True)
    pdf.ln(10)
    
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(0, 0, 0)
    pdf.cell(47, 10, f"MAXIMO: {max(scores)}", border=1, align='C')
    pdf.cell(47, 10, f"MINIMO: {min(scores)}", border=1, align='C')
    
    pdf.set_fill_color(185, 28, 28)
    pdf.set_text_color(255, 255, 255)
    pdf.set_draw_color(185, 28, 28)
    pdf.cell(47, 10, f"PROMEDIO: {avg:.1f}%", border=1, align='C', fill=True)
    
    pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(0, 0, 0)
    pdf.cell(49, 10, f"MISIONES: {len(records)}", border=1, align='C', ln=True)
    pdf.ln(12)

    # --- SECCIÓN GRÁFICA ---
    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 11)
    pdf.cell(190, 9, ' II. TELEMETRIA DE PROGRESO', ln=True, fill=True)
    pdf.ln(5)
    
    if chart_data:
        try:
            # El atributo name="graficamatplot.png" es vital para FPDF
            pdf.image(chart_data, x=15, w=180, name="graficamatplot.png")
            pdf.ln(2)
        except Exception as e:
            print("Error imprimiendo grafica en PDF:", e)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(190, 30, "[Error interno al plasmar la grafica]", border=1, align='C', ln=True)
            pdf.ln(5)
    else:
        pdf.set_text_color(100, 100, 100)
        pdf.set_font('helvetica', 'I', 10)
        pdf.cell(190, 30, "[Datos de telemetria grafica no disponibles]", border=1, align='C', ln=True)
        pdf.ln(5)

    pdf.set_fill_color(0, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 11)
    pdf.cell(190, 9, ' III. DESGLOSE DETALLADO DE SESIONES', ln=True, fill=True)
    
    pdf.set_fill_color(185, 28, 28)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('helvetica', 'B', 9)
    pdf.cell(45, 9, 'FECHA / HORA', fill=True, align='C')
    pdf.cell(80, 9, 'ESCENARIO', fill=True, align='C')
    pdf.cell(35, 9, 'ESTACION', fill=True, align='C')
    pdf.cell(30, 9, 'PUNTAJE', fill=True, align='C', ln=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('helvetica', '', 9)
    pdf.set_draw_color(229, 231, 235)
    
    fill = False
    for r in records:
        pdf.set_fill_color(249, 250, 251) if fill else pdf.set_fill_color(255, 255, 255)
        
        pdf.cell(45, 8, r.timestamp, border='B', align='C', fill=True)
        pdf.cell(80, 8, r.scenario.upper()[:35], border='B', align='C', fill=True)
        pdf.cell(35, 8, r.sim_id[:15], border='B', align='C', fill=True)
        
        pdf.set_font('helvetica', 'B', 10)
        if r.score >= 90: pdf.set_text_color(185, 28, 28)
        pdf.cell(30, 8, str(r.score), border='B', align='C', fill=True, ln=True)
        
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('helvetica', '', 9)
        fill = not fill

    if pdf.get_y() > 240:
        pdf.add_page()
    else:
        pdf.ln(35)
        
    y_sig = pdf.get_y()
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    
    pdf.line(20, y_sig, 85, y_sig)
    pdf.line(125, y_sig, 190, y_sig)
    
    pdf.set_font('helvetica', 'B', 9)
    pdf.cell(95, 5, 'FIRMA DEL OPERADOR', align='C')
    pdf.cell(95, 5, 'CERTIFICACION AUTORIZADA', align='C', ln=True)
    
    pdf.set_font('helvetica', '', 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(95, 4, f'ID: {s_id}', align='C')
    pdf.cell(95, 4, filter_val.upper(), align='C', ln=True)

    pdf_bytes = bytes(pdf.output())
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Expediente_Tactico_{s_id}.pdf"
    )


@app.route('/api/upload_score', methods=['POST'])
def upload_score():
    from datetime import timedelta
    try:
        data = request.json
        
        # Ajuste de Zona Horaria: Servidor Render (UTC) - 5 Horas (Colombia)
        hora_colombia = datetime.utcnow() - timedelta(hours=5)
        
        # Formato exacto: DD/MM/YYYY HH:MM AM/PM (Ej: 18/03/2026 04:41 AM)
        fecha_correcta = hora_colombia.strftime("%d/%m/%Y %I:%M %p")
        
        new_record = ScoreRecord(
            sim_id=data.get('sim_id', 'DESCONOCIDO'), 
            shooter_name=data.get('shooter_name', 'TIRADOR'),
            shooter_id=data.get('shooter_id', 'N/D'), 
            group_name=data.get('group_name', 'NINGUNO'),
            scenario=data.get('scenario', 'ESCENARIO'), 
            score=int(data.get('score', 0)),
            timestamp=fecha_correcta
        )
        db.session.add(new_record)
        db.session.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))