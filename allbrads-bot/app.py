import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

app = Flask(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_NUMBER      = "whatsapp:+14155238886"
SPREADSHEET_ID     = "16YUyxHHt25fE_ZKeWji0jSWUZjnMJFkxn5IIc1G7SU4"

MESES = ["Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
CUOTAS = {"T": 30000, "E": 20000}

# ── Google Sheets ────────────────────────────────────────────────────────────
def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).sheet1

def get_all_players(sheet):
    records = sheet.get_all_values()
    # Row 1 = headers, data starts at row 2
    players = {}
    for i, row in enumerate(records[1:], start=2):
        if len(row) < 5:
            continue
        nombre    = row[1].strip().lower()
        apellido  = row[2].strip().lower()
        tipo      = "T" if row[3].strip() == "Trabajador" else "E"
        full_name = f"{nombre} {apellido}"
        players[full_name] = {"row": i, "tipo": tipo, "nombre": row[1], "apellido": row[2]}
    return players

def find_player(players, query):
    query = query.strip().lower()
    # Exact match
    if query in players:
        return players[query]
    # Partial match
    matches = [p for key, p in players.items() if query in key or key in query]
    if len(matches) == 1:
        return matches[0]
    # Apellido only
    matches = [p for key, p in players.items() if query in key.split(" ", 1)[-1] if " " in key]
    if len(matches) == 1:
        return matches[0]
    return None

def col_for_month(month_name):
    # Columns: A=#, B=Nombre, C=Apellido, D=Tipo, E=Cuota, F=Julio...K=Diciembre, L=Saldo, M=Total, N=%
    idx = MESES.index(month_name)
    return idx + 6  # F=6, G=7, ...

def get_cell_value(sheet, row, col):
    return sheet.cell(row, col).value or ""

def set_cell(sheet, row, col, value, color=None):
    sheet.update_cell(row, col, value)
    if color:
        color_map = {
            "green":  {"red": 0.298, "green": 0.686, "blue": 0.314},
            "red":    {"red": 0.957, "green": 0.263, "blue": 0.212},
            "yellow": {"red": 1.0,   "green": 0.757, "blue": 0.027},
            "white":  {"red": 1.0,   "green": 1.0,   "blue": 1.0},
        }
        body = {
            "requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": row - 1,
                        "endRowIndex": row,
                        "startColumnIndex": col - 1,
                        "endColumnIndex": col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": color_map[color]
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor"
                }
            }]
        }
        creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive",
                  "https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        import googleapiclient.discovery
        service = googleapiclient.discovery.build("sheets", "v4", credentials=creds)
        service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()

def get_saldo(sheet, row):
    val = sheet.cell(row, 12).value  # Column L
    try:
        return int(str(val).replace("$","").replace(".","").replace(",","").strip())
    except:
        return 0

def set_saldo(sheet, row, amount):
    sheet.update_cell(row, 12, amount if amount > 0 else 0)

def apply_payment(sheet, player_data, monto, meses_indicados=None):
    """
    Applies payment to the sheet. Returns a summary string.
    meses_indicados: list of month names if user specified, else None (auto-fill from oldest)
    """
    row  = player_data["row"]
    tipo = player_data["tipo"]
    cuota = CUOTAS[tipo]
    saldo_favor = get_saldo(sheet, row)
    total = monto + saldo_favor
    resumen = []

    if meses_indicados:
        meses_a_pagar = meses_indicados
    else:
        # Auto: fill from oldest unpaid
        meses_a_pagar = []
        for mes in MESES:
            col = col_for_month(mes)
            val = get_cell_value(sheet, row, col)
            if val != "✅":
                meses_a_pagar.append(mes)
            if len(meses_a_pagar) >= (total // cuota + (1 if total % cuota else 0)):
                break

    for mes in meses_a_pagar:
        if total <= 0:
            break
        col = col_for_month(mes)
        current = get_cell_value(sheet, row, col)

        # How much is already paid for this month (from partial)
        already_paid = 0
        if current not in ["✅", "", "🔴"] and current:
            try:
                pendiente = int(str(current).replace("$","").replace(".","").replace(",","").strip())
                already_paid = cuota - pendiente
            except:
                already_paid = 0

        needed = cuota - already_paid

        if total >= needed:
            # Full payment for this month
            set_cell(sheet, row, col, "✅", color="green")
            total -= needed
            resumen.append(f"✅ {mes}: pagado completo")
        else:
            # Partial
            pendiente = needed - total
            set_cell(sheet, row, col, f"${pendiente:,.0f}".replace(",","."), color="yellow")
            resumen.append(f"🟡 {mes}: faltan ${pendiente:,.0f}".replace(",","."))
            total = 0

    # Remaining becomes saldo a favor
    set_saldo(sheet, row, total)
    if total > 0:
        resumen.append(f"💰 Saldo a favor: ${total:,.0f}".replace(",","."))

    return resumen

# ── Session state (in-memory, simple) ────────────────────────────────────────
sessions = {}

def get_session(sender):
    if sender not in sessions:
        sessions[sender] = {"step": "idle"}
    return sessions[sender]

def clear_session(sender):
    sessions[sender] = {"step": "idle"}

# ── Webhook ──────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    sender       = request.form.get("From", "")
    body         = request.form.get("Body", "").strip()
    num_media    = int(request.form.get("NumMedia", 0))
    media_type   = request.form.get("MediaContentType0", "")
    
    session = get_session(sender)
    resp = MessagingResponse()
    msg  = resp.message()

    # ── STEP: idle — waiting for photo ──────────────────────────────────────
    if session["step"] == "idle":
        if num_media > 0 and "image" in media_type:
            session["step"] = "ask_player"
            msg.body("📸 Comprobante recibido ✅\n\n¿A nombre de quién es el pago? Escribe el nombre y apellido del jugador.")
        else:
            msg.body("👋 Hola! Para registrar un pago, mándame una foto del comprobante de transferencia.")

    # ── STEP: ask_player — got photo, waiting for name ──────────────────────
    elif session["step"] == "ask_player":
        sheet = get_sheet()
        players = get_all_players(sheet)
        player = find_player(players, body)
        
        if not player:
            msg.body(f"❌ No encontré a '{body}' en el registro. Verifica el nombre e intenta de nuevo, o escribe el apellido solamente.")
        else:
            session["player"] = player
            session["step"] = "ask_monto"
            msg.body(f"✅ Jugador encontrado: *{player['nombre']} {player['apellido']}* ({'Estudiante' if player['tipo']=='E' else 'Trabajador'} — cuota ${CUOTAS[player['tipo']]:,})\n\n¿Cuánto fue el monto transferido? (Solo el número, ej: 40000)")

    # ── STEP: ask_monto — got name, waiting for amount ──────────────────────
    elif session["step"] == "ask_monto":
        try:
            monto = int(body.replace("$","").replace(".","").replace(",","").strip())
        except:
            msg.body("❌ No entendí el monto. Escríbelo solo como número, ej: 40000")
            return str(resp)

        player = session["player"]
        cuota  = CUOTAS[player["tipo"]]
        saldo_favor = get_saldo(get_sheet(), player["row"])
        total  = monto + saldo_favor
        cuotas_completas = total // cuota
        remanente = total % cuota

        session["monto"] = monto
        session["step"]  = "ask_meses"

        resumen = f"💰 Monto: ${monto:,}\n"
        if saldo_favor > 0:
            resumen += f"➕ Saldo a favor anterior: ${saldo_favor:,}\n"
            resumen += f"📊 Total disponible: ${total:,}\n"
        resumen += f"📅 Cubre {int(cuotas_completas)} cuota(s) completa(s)"
        if remanente > 0:
            resumen += f" + ${remanente:,} parcial"
        resumen += "\n\n¿A qué mes(es) lo aplico?\n"
        resumen += "Escribe los meses separados por coma (ej: *Julio, Agosto*) o escribe *auto* para rellenar desde el mes más antiguo pendiente."
        msg.body(resumen.replace(",", ".") if False else resumen)

    # ── STEP: ask_meses — got amount, waiting for months ────────────────────
    elif session["step"] == "ask_meses":
        monto  = session["monto"]
        player = session["player"]

        if body.lower() == "auto":
            meses_indicados = None
        else:
            # Parse months from input
            meses_indicados = []
            for part in body.replace(";",",").split(","):
                part = part.strip().capitalize()
                matches = [m for m in MESES if m.lower().startswith(part.lower()[:3])]
                if matches:
                    meses_indicados.append(matches[0])
            if not meses_indicados:
                msg.body("❌ No reconocí los meses. Escríbelos así: *Julio, Agosto* o escribe *auto*")
                return str(resp)

        sheet   = get_sheet()
        resumen = apply_payment(sheet, player, monto, meses_indicados)
        
        reply  = f"✅ Pago registrado para *{player['nombre']} {player['apellido']}*:\n\n"
        reply += "\n".join(resumen)
        reply += "\n\n_El Sheet ya está actualizado_ 📊"
        msg.body(reply)
        clear_session(sender)

    return str(resp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
