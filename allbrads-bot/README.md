# All Brads Bot 🏉

Bot de WhatsApp para registrar pagos de cuotas de plantes en Google Sheets.

## Flujo
1. Jugador manda foto del comprobante
2. Bot pregunta nombre del jugador
3. Bot pregunta monto
4. Bot pregunta meses (o "auto")
5. Bot actualiza el Sheet con colores automáticos

## Variables de entorno (Railway)
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN  
- GOOGLE_CREDENTIALS → pegar el contenido completo del JSON de Google Cloud

## Deploy en Railway
1. Sube este repo a GitHub
2. Conecta Railway al repo
3. Agrega las variables de entorno
4. Railway te da la URL pública → úsala en Twilio webhook

## Twilio Webhook
En Twilio → Messaging → Settings → WhatsApp Sandbox:
- Webhook URL: https://TU-URL.railway.app/webhook
- Method: POST
