import os
import json
import re
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# ==============================
# Cargar variables de entorno
# ==============================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

if not BOT_TOKEN:
    raise RuntimeError("❌ Falta BOT_TOKEN en las variables de entorno")
if not SHEET_ID:
    raise RuntimeError("❌ Falta SHEET_ID en las variables de entorno")
if not GOOGLE_CREDENTIALS:
    raise RuntimeError("❌ Falta GOOGLE_CREDENTIALS en las variables de entorno")

# ==============================
# Normalizar SHEET_ID (por si pegan URL o caracteres raros)
# ==============================
def normalizar_sheet_id(valor: str) -> str:
    valor = valor.strip().strip('"').strip("'")
    valor = valor.replace("\u00a0", "")  # NBSP
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", valor)
    if match:
        valor = match.group(1)
    valor = re.sub(r"[^a-zA-Z0-9_-]", "", valor)
    return valor

SHEET_ID = normalizar_sheet_id(SHEET_ID)

print("[ENV] BOT_TOKEN definido:", bool(BOT_TOKEN))
print("[ENV] SHEET_ID final:", repr(SHEET_ID))
print("[ENV] GOOGLE_CREDENTIALS definido:", bool(GOOGLE_CREDENTIALS))

# ==============================
# Conexión con Google Sheets
# ==============================
creds_dict = json.loads(GOOGLE_CREDENTIALS)

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)

print("📄 Intentando abrir Google Sheet por ID...")
sh = gc.open_by_key(SHEET_ID)

worksheet = sh.sheet1
print("✅ Google Sheet conectado correctamente")

# ==============================
# Mapeo de estados (LO QUE PEDISTE)
# ==============================
ESTADOS = {
    "R": ("🔴", "Restricción"),
    "A": ("🟡", "Acuerdo"),
    "N": ("🟢", "Normal"),
}

# ==============================
# Utilidades para buscar columnas
# ==============================
def buscar_columna(fila: dict, contiene_subcadenas):
    for clave, valor in fila.items():
        nombre = str(clave).strip().lower()
        if all(sub in nombre for sub in contiene_subcadenas):
            return valor
    return None

# ==============================
# Interpretar código (torre + apto) flexible
# ==============================
def interpretar_codigo(texto: str):
    """
    Devuelve:
      - torre, apto si el usuario escribió dos números separados (ej: 12-1001, 12 1001)
      - None, None si viene todo pegado (ej: 11103, t121001) => se resuelve por splits contra la hoja
    También devuelve digits (solo números).
    """
    texto = texto.strip().lower()

    # Caso con dos números separados: "12-1001" / "12 1001" / "T12-1001"
    grupos = re.findall(r"\d+", texto)
    if len(grupos) >= 2:
        torre = grupos[0]
        apto = grupos[1]
        return torre, apto, "".join(grupos)

    # Caso todo pegado: "t121001" / "111202" / "11103"
    digits = "".join(ch for ch in texto if ch.isdigit())
    return None, None, digits

# ==============================
# Comando /start
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, envíame la torre y apartamento.\n\n"
        "Ejemplos válidos:\n"
        "• 1-101\n"
        "• 12-1001\n"
        "• 11103\n"
        "• 111202\n"
        "• t121001\n"
        "• 12 1001\n"
        "• T12 1001"
    )

# ==============================
# Handler principal
# ==============================
async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    torre_str, apto_str, digits = interpretar_codigo(texto)

    print(f"[LOG] Entrada: '{texto}' -> torre={torre_str}, apto={apto_str}, digits={digits}")

    if not digits or len(digits) < 3:
        await update.message.reply_text(
            "❌ Formato incorrecto.\nEjemplos: 1-101, 12-1001, 11103, 111202, t121001"
        )
        return

    datos = worksheet.get_all_records()
    print(f"[LOG] Registros cargados: {len(datos)}")

    # Index para buscar rápido
    index_por_par = {}
    index_por_concat = {}

    for fila in datos:
        t = str(fila.get("Torre", "")).strip()
        a = str(fila.get("Apartamento", "")).strip()
        if not t or not a:
            continue

        index_por_par[(t, a)] = fila
        index_por_concat[t + a] = fila  # ejemplo: torre=12 apto=1001 => "121001"

    # 1) Si el usuario mandó dos números separados (12-1001 / 12 1001)
    if torre_str and apto_str:
        fila = index_por_par.get((torre_str, apto_str))
        if not fila:
            await update.message.reply_text("❌ No encontré información para ese apartamento.")
            return

        estado_raw = str(fila.get("Estado", "")).strip().upper()
        emoji, estado_txt = ESTADOS.get(estado_raw, ("⚪", "No especificado"))

        saldo = buscar_columna(fila, ["saldo"]) or "N/A"
        placa_carro = buscar_columna(fila, ["placa", "carro"]) or "No registrado"
        placa_moto = buscar_columna(fila, ["placa", "moto"]) or "No registrada"

        respuesta = (
            f"🏢 *Torre:* {fila.get('Torre')}\n"
            f"🏠 *Apartamento:* {fila.get('Apartamento')}\n"
            f"🧍‍♂️ *Propietario:* {fila.get('Propietario')}\n"
            f"💰 *Saldo:* {saldo}\n"
            f"{emoji} *Estado:* {estado_txt}\n"
            f"🚗 *Placa carro:* {placa_carro}\n"
            f"🏍️ *Placa moto:* {placa_moto}"
        )
        await update.message.reply_text(respuesta, parse_mode="Markdown")
        return

    # 2) Si viene todo pegado (11103, 111202, t121001, etc.)
    #    Primero intento exacto por concatenación
    fila = index_por_concat.get(digits)

    #    Si no, intento partir en todas las posiciones posibles
    if not fila:
        for i in range(1, len(digits)):
            t = digits[:i]
            a = digits[i:]
            fila = index_por_par.get((t, a))
            if fila:
                break

    if not fila:
        await update.message.reply_text("❌ No encontré información para ese apartamento.")
        return

    estado_raw = str(fila.get("Estado", "")).strip().upper()
    emoji, estado_txt = ESTADOS.get(estado_raw, ("⚪", "No especificado"))

    saldo = buscar_columna(fila, ["saldo"]) or "N/A"
    placa_carro = buscar_columna(fila, ["placa", "carro"]) or "No registrado"
    placa_moto = buscar_columna(fila, ["placa", "moto"]) or "No registrada"

    respuesta = (
        f"🏢 *Torre:* {fila.get('Torre')}\n"
        f"🏠 *Apartamento:* {fila.get('Apartamento')}\n"
        f"🧍‍♂️ *Propietario:* {fila.get('Propietario')}\n"
        f"💰 *Saldo:* {saldo}\n"
        f"{emoji} *Estado:* {estado_txt}\n"
        f"🚗 *Placa carro:* {placa_carro}\n"
        f"🏍️ *Placa moto:* {placa_moto}"
    )

    await update.message.reply_text(respuesta, parse_mode="Markdown")

# ==============================
# Iniciar el bot
# ==============================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buscar))

    print("🤖 BOT ACTIVO EN RAILWAY")
    app.run_polling()

if __name__ == "__main__":
    main()

