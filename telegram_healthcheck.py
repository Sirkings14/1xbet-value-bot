import os
import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))


def api(method, **kwargs):
    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        timeout=TIMEOUT,
        **kwargs,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}: {payload}")
    return payload["result"]


def main():
    print("=== TELEGRAM HEALTH CHECK ===")
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing")

    bot = api("getMe")
    print(f"Bot reachable: yes (@{bot.get('username', 'unknown')})")

    chat = api("getChat", params={"chat_id": CHAT_ID})
    chat_type = chat.get("type", "unknown")
    title = chat.get("title") or chat.get("username") or chat.get("first_name") or "unknown"
    print(f"Configured chat reachable: yes (type={chat_type}, name={title})")
    print(f"Configured chat id accepted: yes (id={chat.get('id')})")


if __name__ == "__main__":
    main()
