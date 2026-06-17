from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import re

app = Flask(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

MESES = ["Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

conversaciones = {}

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def buscar_jugador(sheet, nombre_input):
    nombre_input = nombre_input.strip().lower()
    records = sheet.get_all_values()
    mejores = []
    for i, row in enumerate(records[1:], start=2):
        nombre_completo = f"{row[1]} {row[2]}".lower()
        apellido = row[2].lower()
        if nombre_input in nombre_completo or nombre_input in apellido:
            mejores.append((i, row))
    return mejores

def calcular_cuota(tipo):
    return 20000 if tipo == "Estudiante" else 30000

def limpiar_monto(texto):
    return int(re.sub(r"[^\d]", "", str(texto)) or 0)

def aplicar_pago(sheet, fila, row, monto):
    cuota = calcular_cuota(row[3])
    try:
        saldo_favor = limpiar_monto(str(row[12]))
    except:
        saldo_favor = 0

    monto_total = monto + saldo_favor
    resumen = []
    col_inicio = 6  # columna F = Julio

    for i, mes in enumerate(MESES):
        col = col_inicio + i
        valor_actual = str(row[col - 1]).strip()

        if valor_actual == "✅":
            continue

        if valor_actual in ("", "⬜"):
            deuda_mes = cuota
        else:
            try:
                deuda_mes = limpiar_monto(valor_actual)
                if deuda_mes == 0:
                    deuda_mes = cuota
            except:
                deuda_mes = cuota

        if monto_total <= 0:
            break

        if monto_total >= deuda_mes:
            sheet.update_cell(fila, col, "✅")
            sheet.format(f"{chr(64+col)}{fila}", {
                "backgroundColor": {"red": 0.298, "green": 0.686, "blue": 0.314}
            })
            monto_total -= deuda_mes
            resumen.append(f"✅ {mes} — pagado completo")
        else:
            falta = deuda_mes - monto_total
            falta_fmt = f"${falta:,}".replace(",", ".")
            sheet.update_cell(fila, col, falta_fmt)
            sheet.format(f"{chr(64+col)}{fila}", {
                "backgroundColor": {"red": 1.0, "green": 0.756, "blue": 0.027}
            })
            resumen.append(f"🟡 {mes} — faltan {falta_fmt}")
            monto_total = 0
            break

    if monto_total > 0:
        sheet.update_cell(fila, 13, monto_total)
        resumen.append(f"💰 Saldo a favor guardado: ${monto_total:,}".replace(",", "."))
    else:
        sheet.update_cell(fila, 13, 0)

    return resumen

def parsear_nombre_monto(mensaje):
    """Intenta parsear 'Apellido, monto' del mensaje"""
    partes = mensaje.split(",")
    if len(partes) < 2:
        return None, None
    nombre_raw = partes[0].strip()
    monto = limpiar_monto(partes[1])
    if not nombre_raw or monto <= 0:
        return None, None
    return nombre_raw, monto

@app.route("/webhook", methods=["POST"])
def webhook():
    telefono = request.form.get("From", "")
    mensaje = request.form.get("Body", "").strip()
    num_media = int(request.form.get("NumMedia", 0))

    resp = MessagingResponse()
    msg = resp.message()

    estado = conversaciones.get(telefono, {"paso": "inicio"})

    # PASO 1: recibe foto → pedir nombre y monto
    if num_media > 0 and estado["paso"] == "inicio":
        conversaciones[telefono] = {"paso": "esperando_datos"}
        msg.body("📋 Comprobante recibido.\n\nResponde con apellido y monto:\n\n_Apellido, monto_\n\nEjemplo:\n_Herreros, 20000_")

    # PASO 1b: recibe foto estando en esperando_datos (nueva foto)
    elif num_media > 0 and estado["paso"] == "esperando_datos":
        msg.body("Ya recibí un comprobante. Envía el apellido y monto:\n\n_Apellido, monto_")

    # PASO 2: recibe nombre y monto (desde foto O directo)
    elif estado["paso"] in ("inicio", "esperando_datos"):
        # Si viene de inicio sin foto, igual procesamos si el formato es correcto
        nombre_raw, monto = parsear_nombre_monto(mensaje)

        if not nombre_raw:
            if estado["paso"] == "inicio":
                msg.body("👋 Para registrar un pago, envía una *foto del comprobante* o escribe:\n\n_Apellido, monto_\n\nEjemplo:\n_Herreros, 20000_")
            else:
                msg.body("⚠️ No entendí el formato. Envía así:\n\n_Apellido, monto_\n\nEjemplo:\n_Herreros, 20000_")
        else:
            try:
                sheet = get_sheet()
                resultados = buscar_jugador(sheet, nombre_raw)

                if len(resultados) == 0:
                    msg.body(f"❌ No encontré *{nombre_raw}* en el registro. Verifica el apellido e intenta de nuevo.")
                    conversaciones[telefono] = {"paso": "esperando_datos"}

                elif len(resultados) > 1:
                    opciones = "\n".join([f"{j+1}. {r[1]} {r[2]}" for j, (_, r) in enumerate(resultados)])
                    conversaciones[telefono] = {"paso": "confirmar_jugador", "opciones": resultados, "monto": monto}
                    msg.body(f"Encontré varios jugadores:\n\n{opciones}\n\nResponde con el número.")

                else:
                    fila, row = resultados[0]
                    cuota = calcular_cuota(row[3])
                    monto_fmt = f"${monto:,}".replace(",", ".")
                    cuota_fmt = f"${cuota:,}".replace(",", ".")
                    conversaciones[telefono] = {"paso": "confirmar_pago", "fila": fila, "row": row, "monto": monto}
                    msg.body(
                        f"¿Confirmas este pago?\n\n"
                        f"👤 *{row[1]} {row[2]}*\n"
                        f"💰 Monto: {monto_fmt}\n"
                        f"📋 Tipo: {row[3]} (cuota {cuota_fmt})\n\n"
                        f"Responde *SI* o *NO*"
                    )
            except Exception as e:
                msg.body(f"❌ Error conectando al registro. Intenta más tarde.\n_{str(e)}_")
                conversaciones.pop(telefono, None)

    # PASO 3: múltiples coincidencias
    elif estado["paso"] == "confirmar_jugador":
        try:
            idx = int(mensaje.strip()) - 1
            fila, row = estado["opciones"][idx]
            monto = estado["monto"]
            cuota = calcular_cuota(row[3])
            monto_fmt = f"${monto:,}".replace(",", ".")
            cuota_fmt = f"${cuota:,}".replace(",", ".")
            conversaciones[telefono] = {"paso": "confirmar_pago", "fila": fila, "row": row, "monto": monto}
            msg.body(
                f"¿Confirmas este pago?\n\n"
                f"👤 *{row[1]} {row[2]}*\n"
                f"💰 Monto: {monto_fmt}\n"
                f"📋 Tipo: {row[3]} (cuota {cuota_fmt})\n\n"
                f"Responde *SI* o *NO*"
            )
        except:
            msg.body("Responde con el número de la lista.")

    # PASO 4: confirmación final
    elif estado["paso"] == "confirmar_pago":
        if mensaje.upper() in ["SI", "SÍ", "S", "YES", "Y"]:
            try:
                sheet = get_sheet()
                # Refrescar fila desde el sheet por si cambió
                row_actual = sheet.row_values(estado["fila"])
                resumen = aplicar_pago(sheet, estado["fila"], row_actual, estado["monto"])
                resumen_texto = "\n".join(resumen)
                msg.body(f"✅ *Pago registrado*\n\n{resumen_texto}")
            except Exception as e:
                msg.body(f"❌ Error al registrar. Avisa al admin.\n_{str(e)}_")
            finally:
                conversaciones.pop(telefono, None)

        elif mensaje.upper() in ["NO", "N"]:
            conversaciones.pop(telefono, None)
            msg.body("Pago cancelado. Envía otro comprobante cuando quieras.")

        else:
            msg.body("Responde *SI* para confirmar o *NO* para cancelar.")

    else:
        conversaciones.pop(telefono, None)
        msg.body("👋 Para registrar un pago, envía una *foto del comprobante* o escribe:\n\n_Apellido, monto_\n\nEjemplo:\n_Herreros, 20000_")

    return str(resp)

@app.route("/", methods=["GET"])
def index():
    return "AllBrads Bot activo ✅"

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
