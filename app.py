import os
import ssl
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

db_url = os.environ.get('DATABASE_URL', 'sqlite:///local_fallback.db')

# 1. Corrección del driver a pg8000
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+pg8000://", 1)
elif db_url.startswith("postgresql://") and "pg8000" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)

# 2. Limpieza de parámetros
if "?" in db_url:
    db_url = db_url.split("?")[0]

# 3. Configuración SSL para Aiven
if "pg8000" in db_url:
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'ssl_context': ssl_context}}

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELO ACTUALIZADO CON NUEVAS COLUMNAS ---
class ScoreRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sim_id = db.Column(db.String(100), nullable=False)
    shooter_name = db.Column(db.String(100), nullable=False)
    shooter_id = db.Column(db.String(50), nullable=True) # Nueva Columna
    group_name = db.Column(db.String(100), nullable=True) # Nueva Columna
    scenario = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

with app.app_context():
    db.create_all()

# --- API ACTUALIZADA ---
@app.route('/api/upload_score', methods=['POST'])
def upload_score():
    try:
        data = request.json
        new_record = ScoreRecord(
            sim_id=data.get('sim_id', 'DESCONOCIDO'),
            shooter_name=data.get('shooter_name', 'Tirador'),
            shooter_id=data.get('shooter_id', 'N/D'), # Recibir Cédula
            group_name=data.get('group_name', 'NINGUNO'), # Recibir Grupo
            scenario=data.get('scenario', 'Escenario'),
            score=int(data.get('score', 0)),
            timestamp=data.get('timestamp', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.session.add(new_record)
        db.session.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def dashboard():
    records = ScoreRecord.query.order_by(ScoreRecord.id.desc()).limit(200).all()
    return render_template('dashboard.html', records=records)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))