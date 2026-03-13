from flask import Flask, render_template, request, redirect, session, url_for
from functools import wraps
import sqlite3
import re
import logging
from datetime import datetime, timedelta
import secrets
import bcrypt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

# ========== CONFIGURACIÓN DE EMAIL (GMAIL) ==========
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'medicare202612@gmail.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'ovhf kyix cxqd nkgt')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'medicare202612@gmail.com')

# ========== LOGS ==========
logging.basicConfig(
    filename='auditoria.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ========== FUNCIONES DE HASH ==========
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ========== CONTEXT PROCESSOR ==========
@app.context_processor
def inject_now():
    return {'now': datetime.now}

# ========== CONEXIÓN A BD ==========
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# ========== INICIALIZAR BD ==========
def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Tabla usuarios
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  nombre TEXT NOT NULL,
                  email TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  rol TEXT NOT NULL,
                  reset_token TEXT,
                  reset_token_expiry TIMESTAMP,
                  creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Tabla turnos (con creado_por para secretaria)
    c.execute('''CREATE TABLE IF NOT EXISTS turnos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  paciente_id INTEGER NOT NULL,
                  medico_id INTEGER NOT NULL,
                  fecha TEXT NOT NULL,
                  motivo TEXT NOT NULL,
                  estado TEXT DEFAULT 'pendiente',
                  creado_por INTEGER,
                  creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (paciente_id) REFERENCES usuarios(id),
                  FOREIGN KEY (medico_id) REFERENCES usuarios(id))''')
    
    # Tabla historial médico
    c.execute('''CREATE TABLE IF NOT EXISTS historial
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  paciente_id INTEGER NOT NULL,
                  medico_id INTEGER NOT NULL,
                  fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  diagnostico TEXT NOT NULL,
                  receta TEXT,
                  notas TEXT,
                  FOREIGN KEY (paciente_id) REFERENCES usuarios(id),
                  FOREIGN KEY (medico_id) REFERENCES usuarios(id))''')
    
    # Crear admin si no existe
    c.execute("SELECT * FROM usuarios WHERE email = 'admin@medicare.com'")
    if not c.fetchone():
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  ('Administrador', 'admin@medicare.com', hash_password('Admin123!'), 'admin'))
        logging.info("Admin inicial creado")
    
    # Crear secretaria si no existe
    c.execute("SELECT * FROM usuarios WHERE email = 'secretaria@medicare.com'")
    if not c.fetchone():
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  ('Laura Secretaria', 'secretaria@medicare.com', hash_password('Secre123!'), 'secretaria'))
        logging.info("Secretaria creada")
    
    # Crear médico si no existe
    c.execute("SELECT * FROM usuarios WHERE rol = 'medico'")
    if not c.fetchone():
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  ('Dr. Juan Pérez', 'medico@medicare.com', hash_password('Medico123!'), 'medico'))
        logging.info("Médico creado")
    
    # Crear paciente de ejemplo
    c.execute("SELECT * FROM usuarios WHERE email = 'paciente@ejemplo.com'")
    if not c.fetchone():
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  ('Carlos Paciente', 'paciente@ejemplo.com', hash_password('Paciente123!'), 'paciente'))
        logging.info("Paciente de ejemplo creado")
    
    conn.commit()
    conn.close()

# ========== FUNCIÓN PARA ENVIAR EMAIL ==========
def enviar_email(destinatario, asunto, cuerpo):
    try:
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_DEFAULT_SENDER']
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg.attach(MIMEText(cuerpo, 'html'))
        
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)
        server.quit()
        
        logging.info(f"✅ Email enviado a: {destinatario}")
        return True, "Email enviado correctamente"
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Error enviando email: {error_msg}")
        print("\n" + "="*60)
        print("🔐 ERROR EN EMAIL - Link manual disponible")
        print("="*60)
        print(f"Error: {error_msg}")
        print(f"Destinatario: {destinatario}")
        import re
        link_match = re.search(r'href="([^"]+)"', cuerpo)
        if link_match:
            print(f"Link: {link_match.group(1)}")
        print("="*60 + "\n")
        return False, f"Error: {error_msg}"

# ========== DECORADORES ==========
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_rol') != 'admin':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def secretaria_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_rol') not in ['secretaria', 'admin']:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def medico_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_rol') != 'medico':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# ========== ERRORES ==========
@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', mensaje="Página no encontrada"), 404

@app.errorhandler(500)
def internal_error(error):
    logging.error(f"Error 500: {error}")
    return render_template('error.html', mensaje="Error interno del servidor"), 500

# ========== RECUPERACIÓN DE CONTRASEÑA ==========
@app.route('/recuperar', methods=['GET', 'POST'])
def recuperar():
    if request.method == 'POST':
        email = request.form['email']
        
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
        user = c.fetchone()
        
        if user:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now() + timedelta(hours=1)
            
            c.execute("UPDATE usuarios SET reset_token = ?, reset_token_expiry = ? WHERE email = ?",
                     (token, expiry, email))
            conn.commit()
            
            reset_link = url_for('reset_password', token=token, _external=True)
            asunto = "Recuperación de contraseña - MediCare"
            cuerpo = f"""
            <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #38bdf8;">Recuperación de contraseña</h2>
                <p>Hola <strong>{user['nombre']}</strong>,</p>
                <p>Haz clic en el siguiente enlace para restablecer tu contraseña:</p>
                <p><a href="{reset_link}" style="background: #38bdf8; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Restablecer contraseña</a></p>
                <p>Este enlace expira en 1 hora.</p>
            </body>
            </html>
            """
            
            exito, mensaje = enviar_email(email, asunto, cuerpo)
            if exito:
                return render_template('login.html', mensaje="✅ Revisa tu email")
            else:
                return render_template('recuperar.html', error=mensaje, demo_link=reset_link)
        else:
            return render_template('login.html', mensaje="✅ Si el email existe, recibirás instrucciones")
        
        conn.close()
    
    return render_template('recuperar.html')

@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM usuarios WHERE reset_token = ? AND reset_token_expiry > ?",
             (token, datetime.now()))
    user = c.fetchone()
    
    if not user:
        return render_template('error.html', mensaje="Enlace inválido o expirado")
    
    if request.method == 'POST':
        password = request.form['password']
        confirmar = request.form['confirmar']
        
        if password != confirmar:
            return render_template('reset.html', token=token, error="Las contraseñas no coinciden")
        
        if len(password) < 8:
            return render_template('reset.html', token=token, error="Mínimo 8 caracteres")
        if not re.search(r"[A-Z]", password):
            return render_template('reset.html', token=token, error="Falta mayúscula")
        if not re.search(r"[a-z]", password):
            return render_template('reset.html', token=token, error="Falta minúscula")
        if not re.search(r"\d", password):
            return render_template('reset.html', token=token, error="Falta número")
        
        hashed = hash_password(password)
        c.execute("UPDATE usuarios SET password = ?, reset_token = NULL, reset_token_expiry = NULL WHERE id = ?",
                 (hashed, user['id']))
        conn.commit()
        conn.close()
        
        return render_template('login.html', mensaje="✅ Contraseña actualizada")
    
    conn.close()
    return render_template('reset.html', token=token)

# ========== LOGIN ==========
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    password = request.form['password']
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
    user = c.fetchone()
    conn.close()
    
    if user and check_password(password, user['password']):
        session.permanent = True
        session['user_id'] = user['id']
        session['user_nombre'] = user['nombre']
        session['user_email'] = user['email']
        session['user_rol'] = user['rol']
        
        logging.info(f"Login exitoso - {email} - {user['rol']}")
        
        if user['rol'] == 'admin':
            return redirect('/admin')
        elif user['rol'] == 'secretaria':
            return redirect('/secretaria')
        elif user['rol'] == 'medico':
            return redirect('/medico')
        else:
            return redirect('/paciente')
    
    return render_template('login.html', error="Credenciales inválidas")

# ========== REGISTRO DE PACIENTES ==========
@app.route('/registro', methods=['POST'])
def registro():
    nombre = request.form['nombre']
    email = request.form['email']
    password = request.form['password']
    
    # Validaciones
    if len(nombre) < 3:
        return render_template('login.html', error="Nombre debe tener al menos 3 caracteres")
    
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return render_template('login.html', error="Email inválido")
    
    if len(password) < 8:
        return render_template('login.html', error="Contraseña muy corta")
    if not re.search(r"[A-Z]", password):
        return render_template('login.html', error="Debe incluir una mayúscula")
    if not re.search(r"[a-z]", password):
        return render_template('login.html', error="Debe incluir una minúscula")
    if not re.search(r"\d", password):
        return render_template('login.html', error="Debe incluir un número")
    
    conn = get_db()
    c = conn.cursor()
    try:
        hashed = hash_password(password)
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  (nombre, email, hashed, 'paciente'))
        conn.commit()
        logging.info(f"Registro exitoso - {email}")
    except sqlite3.IntegrityError:
        conn.close()
        return render_template('login.html', error="Email ya registrado")
    conn.close()
    
    return render_template('login.html', mensaje="Registro exitoso. Ya puedes iniciar sesión.")

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ========== PANEL ADMIN ==========
@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios ORDER BY id DESC")
    usuarios = c.fetchall()
    conn.close()
    return render_template('admin.html', usuarios=usuarios)

@app.route('/crear_usuario', methods=['POST'])
@login_required
@admin_required
def crear_usuario():
    nombre = request.form['nombre']
    email = request.form['email']
    password = request.form['password']
    rol = request.form['rol']
    
    conn = get_db()
    c = conn.cursor()
    try:
        hashed = hash_password(password)
        c.execute("INSERT INTO usuarios (nombre, email, password, rol) VALUES (?, ?, ?, ?)",
                  (nombre, email, hashed, rol))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return redirect('/admin?error=Email ya existe')
    conn.close()
    return redirect('/admin')

# ========== PANEL SECRETARIA ==========
@app.route('/secretaria')
@login_required
@secretaria_required
def secretaria_panel():
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id, nombre FROM usuarios WHERE rol = 'medico'")
    medicos = c.fetchall()
    
    c.execute("SELECT id, nombre FROM usuarios WHERE rol = 'paciente'")
    pacientes = c.fetchall()
    
    c.execute('''SELECT t.id, t.fecha, t.motivo, t.estado, 
                 p.nombre as paciente, m.nombre as medico
                 FROM turnos t 
                 JOIN usuarios p ON t.paciente_id = p.id 
                 JOIN usuarios m ON t.medico_id = m.id 
                 ORDER BY t.fecha DESC''')
    turnos = c.fetchall()
    
    conn.close()
    return render_template('secretaria.html', medicos=medicos, pacientes=pacientes, turnos=turnos)

@app.route('/secretaria/crear_turno', methods=['POST'])
@login_required
@secretaria_required
def secretaria_crear_turno():
    paciente_id = request.form['paciente_id']
    medico_id = request.form['medico_id']
    fecha = request.form['fecha']
    hora = request.form['hora']
    motivo = request.form['motivo']
    
    if not paciente_id or not medico_id or not fecha or not hora or not motivo:
        return redirect('/secretaria?error=Todos los campos requeridos')
    
    if len(motivo) < 5:
        return redirect('/secretaria?error=Motivo muy corto')
    
    try:
        hora_int = int(hora.split(':')[0])
        min_int = int(hora.split(':')[1])
    except:
        return redirect('/secretaria?error=Hora inválida')
    
    if hora_int < 8 or hora_int >= 23:
        return redirect('/secretaria?error=Horario no permitido (8am - 11pm)')
    if min_int not in [0, 30]:
        return redirect('/secretaria?error=Solo turnos cada 30 min')
    
    fecha_hora = f"{fecha} {hora}:00"
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO turnos (paciente_id, medico_id, fecha, motivo, creado_por) 
                 VALUES (?, ?, ?, ?, ?)''',
              (paciente_id, medico_id, fecha_hora, motivo, session['user_id']))
    conn.commit()
    conn.close()
    
    return redirect('/secretaria')

@app.route('/secretaria/eliminar_turno/<int:turno_id>')
@login_required
@secretaria_required
def secretaria_eliminar_turno(turno_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM turnos WHERE id = ?", (turno_id,))
    conn.commit()
    conn.close()
    return redirect('/secretaria')

# ========== PANEL MÉDICO ==========
@app.route('/medico')
@login_required
@medico_required
def medico_panel():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''SELECT t.id, t.fecha, t.motivo, t.estado, u.nombre, t.paciente_id 
                 FROM turnos t 
                 JOIN usuarios u ON t.paciente_id = u.id 
                 WHERE t.medico_id = ?
                 ORDER BY t.fecha DESC''', (session['user_id'],))
    turnos = c.fetchall()
    
    conn.close()
    return render_template('medico.html', turnos=turnos)

@app.route('/medico/historial/<int:paciente_id>')
@login_required
@medico_required
def medico_ver_historial(paciente_id):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM usuarios WHERE id = ?", (paciente_id,))
    paciente = c.fetchone()
    
    c.execute('''SELECT h.*, u.nombre as medico_nombre 
                 FROM historial h 
                 JOIN usuarios u ON h.medico_id = u.id 
                 WHERE h.paciente_id = ?
                 ORDER BY h.fecha DESC''', (paciente_id,))
    historial = c.fetchall()
    
    conn.close()
    return render_template('historial.html', paciente=paciente, historial=historial)

@app.route('/medico/agregar_historial/<int:paciente_id>', methods=['POST'])
@login_required
@medico_required
def medico_agregar_historial(paciente_id):
    diagnostico = request.form['diagnostico']
    receta = request.form.get('receta', '')
    notas = request.form.get('notas', '')
    turno_id = request.form.get('turno_id')
    
    conn = get_db()
    c = conn.cursor()
    
    # Guardar historial
    c.execute('''INSERT INTO historial (paciente_id, medico_id, diagnostico, receta, notas)
                 VALUES (?, ?, ?, ?, ?)''',
              (paciente_id, session['user_id'], diagnostico, receta, notas))
    
    # Marcar turno como completado automáticamente si se pasó el turno_id
    if turno_id:
        c.execute('''UPDATE turnos SET estado = 'completado' 
                     WHERE id = ? AND medico_id = ? AND estado != 'cancelado' ''',
                  (turno_id, session['user_id']))
        logging.info(f"Turno {turno_id} completado automáticamente al guardar historial")
    
    conn.commit()
    conn.close()
    
    return redirect(f'/medico/historial/{paciente_id}?turno_id={turno_id or ""}')

@app.route('/medico/iniciar_consulta/<int:turno_id>')
@login_required
@medico_required
def medico_iniciar_consulta(turno_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''UPDATE turnos SET estado = 'en curso' 
                 WHERE id = ? AND medico_id = ? AND estado = 'pendiente' ''',
              (turno_id, session['user_id']))
    conn.commit()
    conn.close()
    logging.info(f"Turno {turno_id} marcado como 'en curso' por médico {session['user_id']}")
    return redirect('/medico')

# ========== PANEL PACIENTE ==========
@app.route('/paciente')
@login_required
def paciente_panel():
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id, nombre FROM usuarios WHERE rol = 'medico'")
    medicos = c.fetchall()
    
    c.execute('''SELECT t.id, t.fecha, t.motivo, t.estado, u.nombre 
                 FROM turnos t 
                 JOIN usuarios u ON t.medico_id = u.id 
                 WHERE t.paciente_id = ?
                 ORDER BY t.fecha DESC''', (session['user_id'],))
    turnos = c.fetchall()
    
    c.execute('''SELECT h.*, u.nombre as medico_nombre 
                 FROM historial h 
                 JOIN usuarios u ON h.medico_id = u.id 
                 WHERE h.paciente_id = ?
                 ORDER BY h.fecha DESC''', (session['user_id'],))
    historial = c.fetchall()
    
    conn.close()
    return render_template('paciente.html', medicos=medicos, turnos=turnos, historial=historial)

@app.route('/paciente/crear_turno', methods=['POST'])
@login_required
def paciente_crear_turno():
    if session['user_rol'] != 'paciente':
        return redirect('/paciente')
    
    medico_id = request.form['medico_id']
    fecha = request.form['fecha']
    hora = request.form['hora']
    motivo = request.form['motivo']
    
    try:
        hora_int = int(hora.split(':')[0])
        min_int = int(hora.split(':')[1])
    except:
        return redirect('/paciente?error=Hora inválida')
    
    if hora_int < 8 or hora_int >= 23:
        return redirect('/paciente?error=Horario no permitido')
    if min_int not in [0, 30]:
        return redirect('/paciente?error=Solo turnos cada 30 min')
    
    fecha_hora = f"{fecha} {hora}:00"
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO turnos (paciente_id, medico_id, fecha, motivo, creado_por) 
                 VALUES (?, ?, ?, ?, ?)''',
              (session['user_id'], medico_id, fecha_hora, motivo, session['user_id']))
    conn.commit()
    conn.close()
    
    return redirect('/paciente')

@app.route('/paciente/cancelar_turno/<int:turno_id>')
@login_required
def paciente_cancelar_turno(turno_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE turnos SET estado = 'cancelado' WHERE id = ? AND paciente_id = ?",
              (turno_id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/paciente')

# ========== VISOR BD ==========
@app.route('/db-viewer')
@login_required
@admin_required
def db_viewer():
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tablas = c.fetchall()
    
    datos = {}
    for tabla in tablas:
        nombre = tabla['name']
        c.execute(f"SELECT * FROM {nombre} ORDER BY id DESC")
        filas = c.fetchall()
        datos[nombre] = [dict(fila) for fila in filas]
    
    conn.close()
    return render_template('db_viewer.html', datos=datos)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)