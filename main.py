from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from roboflow import Roboflow
from PIL import Image
import io
import os
import pyodbc
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional
import secrets

app = FastAPI(title="FLIMD Cayuco")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= CONEXIÓN A SQL SERVER =================
DB_CONFIG = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=DESKTOP-RABSJJK\\FLIMD;"
    "DATABASE=FlimdCayucoDB;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
    "charset=utf8;"   # Para evitar problemas con acentos
)

def get_db_connection():
    try:
        return pyodbc.connect(DB_CONFIG)
    except Exception as e:
        print(f"Error de conexión a BD: {e}")
        raise HTTPException(status_code=500, detail=f"Error de base de datos: {str(e)}")

# ================= CONFIGURACIÓN DE AUTENTICACIÓN =================
# Usa una variable de entorno para la clave secreta, o genera una por defecto (solo para desarrollo)
SECRET_KEY = os.environ.get("SECRET_KEY", "tu_clave_secreta_muy_larga_y_aleatoria_1234567890")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 día

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def verify_password(plain_password, hashed_password):
    return plain_password == hashed_password  # comparación en texto plano

def get_password_hash(password):
    return password  # guarda la contraseña en texto plano

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudieron validar las credenciales",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT UsuarioID, Nombre, Email, TipoPlan FROM dbo.Usuarios WHERE Email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    if user is None:
        raise credentials_exception
    return {"id": user[0], "nombre": user[1], "email": user[2], "tipo_plan": user[3]}

# ================= MODELOS PYDANTIC =================
class UserRegister(BaseModel):
    nombre: str
    email: str
    password: str
    tipo_plan: str = "gratis"

class PlanUpdate(BaseModel):
    tipo_plan: str

# ================= ENDPOINTS DE AUTENTICACIÓN =================
@app.post("/register")
async def register(user: UserRegister):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Verificar si ya existe
    cursor.execute("SELECT 1 FROM dbo.Usuarios WHERE Email = ?", (user.email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="El email ya está registrado")
    # Hashear contraseña
    hashed = get_password_hash(user.password)
    # Insertar usuario
    cursor.execute(
        "INSERT INTO dbo.Usuarios (Nombre, Email, PasswordHash, TipoPlan, FechaRegistro) VALUES (?, ?, ?, ?, GETDATE())",
        (user.nombre, user.email, hashed, user.tipo_plan)
    )
    conn.commit()
    conn.close()
    return {"msg": "Usuario creado exitosamente"}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    email = form_data.username
    password = form_data.password
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT UsuarioID, Nombre, Email, PasswordHash, TipoPlan FROM dbo.Usuarios WHERE Email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    if not user or not verify_password(password, user[3]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    access_token = create_access_token(data={"sub": user[2]})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "usuario": {"id": user[0], "nombre": user[1], "email": user[2], "tipo_plan": user[4]}
    }

@app.get("/usuarios/me")
async def read_users_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.put("/usuarios/actualizar-plan")
async def actualizar_plan(plan: PlanUpdate, current_user: dict = Depends(get_current_user)):
    """Actualiza el tipo de plan del usuario (gratis/premium)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dbo.Usuarios SET TipoPlan = ? WHERE UsuarioID = ?",
        (plan.tipo_plan, current_user["id"])
    )
    conn.commit()
    conn.close()
    return {"msg": f"Plan actualizado a {plan.tipo_plan}", "tipo_plan": plan.tipo_plan}

# ================= ENDPOINTS DE ENFERMEDADES Y TRATAMIENTOS (públicos) =================
@app.get("/enfermedades")
async def listar_enfermedades():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT EnfermedadID, ClaseRoboflow, NombreComun, DescripcionDetallada FROM dbo.Enfermedades")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "clase": r[1], "nombre": r[2], "descripcion": r[3]} for r in rows]

@app.get("/enfermedades/{clase_roboflow}")
async def obtener_enfermedad_por_clase(clase_roboflow: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT EnfermedadID, ClaseRoboflow, NombreComun, DescripcionDetallada FROM dbo.Enfermedades WHERE ClaseRoboflow = ?",
        (clase_roboflow,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"EnfermedadID": row[0], "ClaseRoboflow": row[1], "NombreComun": row[2], "DescripcionDetallada": row[3]}

@app.get("/tratamientos/{enfermedad_id}")
async def obtener_tratamiento(enfermedad_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT TratamientoPremium FROM dbo.Tratamientos WHERE EnfermedadID = ?", (enfermedad_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"TratamientoPremium": row[0]}

# ================= ENDPOINT DE PREDICCIÓN (requiere login) =================
ROBOFLOW_API_KEY = "oMUdXeVVYIKARb6F1MFy"
PROJECT_NAME = "melon-disease-q4cpw-l2ps0"
MODEL_VERSION = 1

model = None
try:
    rf = Roboflow(api_key=ROBOFLOW_API_KEY)
    project = rf.workspace().project(PROJECT_NAME)
    model = project.version(MODEL_VERSION).model
    print("✅ Modelo IA conectado")
except Exception as e:
    print(f"❌ Error Roboflow: {e}")

@app.post("/predict")
async def predict(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if model is None:
        raise HTTPException(status_code=500, detail="Modelo no configurado")

    temp_filename = "temp_prediction.jpg"
    try:
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(temp_filename, "JPEG")

        prediction_response = model.predict(temp_filename, confidence=40, overlap=30).json()
        predictions = prediction_response.get("predictions", [])

        if not predictions:
            return {
                "enfermedad": "No detectada",
                "confianza": "0%",
                "descripcion": "No se identificaron síntomas claros.",
                "tratamiento": "N/A",
                "clase_raw": ""
            }

        top = max(predictions, key=lambda x: x["confidence"])
        clase_detectada = top["class"]
        confianza = f"{int(top['confidence'] * 100)}%"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT EnfermedadID, NombreComun, DescripcionDetallada FROM dbo.Enfermedades WHERE ClaseRoboflow = ?",
            (clase_detectada,)
        )
        enfermedad_row = cursor.fetchone()
        if not enfermedad_row:
            nombre_comun = clase_detectada
            descripcion = "Información no disponible en la base de datos."
            tratamiento = "N/A"
        else:
            enfermedad_id = enfermedad_row[0]
            nombre_comun = enfermedad_row[1]
            descripcion = enfermedad_row[2]
            cursor.execute("SELECT TratamientoPremium FROM dbo.Tratamientos WHERE EnfermedadID = ?", (enfermedad_id,))
            tratamiento_row = cursor.fetchone()
            tratamiento = tratamiento_row[0] if tratamiento_row else "Sin tratamiento registrado."
        conn.close()

        # (Opcional) Guardar en historial con current_user["id"]
        # ...

        return {
            "enfermedad": nombre_comun,
            "confianza": confianza,
            "descripcion": descripcion,
            "tratamiento": tratamiento,
            "clase_raw": clase_detectada,
            "usuario_plan": current_user["tipo_plan"]
        }
    except Exception as e:
        print(f"Error en /predict: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)