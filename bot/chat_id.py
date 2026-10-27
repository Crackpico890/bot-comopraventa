"""Ayuda: escribe cualquier cosa a tu bot en Telegram y ejecuta esto para ver tu chat_id."""
import os

import httpx

token = os.environ["TELEGRAM_TOKEN"]
r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
         for u in r.get("result", []) if "message" in u}
if not chats:
    print("No hay mensajes. Escribe 'hola' a tu bot en Telegram y vuelve a ejecutar esto.")
for cid, name in chats.items():
    print(f"TELEGRAM_CHAT_ID={cid}   ({name})")
