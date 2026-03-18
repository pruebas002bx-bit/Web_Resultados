import os
import ssl
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# NUEVO MÉTODO: SIN CONTRASEÑAS EN EL CÓDIGO (Anti-Bloqueo de GitHub)
# Render usará la variable de entorno que configures en su panel.
db_url = os.environ.get('DATABASE_URL', 'sqlite:///local_fallback.db')

# 1. Corrección del driver a pg8000
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+pg8000://", 1)
elif db_url.startswith("postgresql://") and "pg8000" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)

# 2. Corrección del SSL para pg8000
if "sslmode=require" in db_url:
    db_url = db_url.replace("?sslmode=require", "")
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'ssl_context': ssl_context}}

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Modelo de Base de Datos
class ScoreRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sim_id = db.Column(db.String(100), nullable=False)
    shooter_name = db.Column(db.String(100), nullable=False)
    scenario = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.String(50), nullable=False)

# Crear tablas si no existen
with app.app_context():
    db.create_all()

# Endpoint (API) para recibir datos desde Alpha.py
@app.route('/api/upload_score', methods=['POST'])
def upload_score():
    try:
        data = request.json
        new_record = ScoreRecord(
            sim_id=data.get('sim_id', 'DESCONOCIDO'),
            shooter_name=data.get('shooter_name', 'Tirador'),
            scenario=data.get('scenario', 'Escenario'),
            score=int(data.get('score', 0)),
            timestamp=data.get('timestamp', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.session.add(new_record)
        db.session.commit()
        return jsonify({"status": "success", "message": "Record saved"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Ruta principal que muestra el archivo HTML
@app.route('/')
def dashboard():
    # Obtener los últimos 100 registros, ordenados del más reciente al más antiguo
    records = ScoreRecord.query.order_by(ScoreRecord.id.desc()).limit(100).all()
    # Pasa la variable 'records' al archivo dashboard.html dentro de la carpeta 'templates'
    return render_template('dashboard.html', records=records)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))