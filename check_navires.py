"""
Radar navires - Saye Armand
Verifie les navires actuellement a quai a Pointe-Noire et Abidjan
(source : myshiptracking.com, gratuit) et envoie une alerte Telegram
des qu'un nouveau navire apparait.
"""

import json
import os
import re
from pathlib import Path

import requests

PORTS = {
    "Pointe-Noire": "https://www.myshiptracking.com/ports/port-of-pointe-noire-in-cg-congo-id-3364",
    "Abidjan": "https://www.myshiptracking.com/ports/port-of-abidjan-in-ci-ivory-coast-id-3337",
}

STATE_FILE = Path.cwd() / "navires_state.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

VESSEL_LINK_RE = re.compile(
    r'/vessels/[a-z0-9\-]+-mmsi-(\d+)-imo-\d+"[^>]*>\s*([^<]+?)\s*<'
)


def fetch_vessels_in_port(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text
    start = html.find("Vessels In Port")
    end = html.find("Expected Arrivals")
    section = html[start:end] if start != -1 and end != -1 else html
    vessels = {}
    for mmsi, name in VESSEL_LINK_RE.findall(section):
        vessels[mmsi] = name.strip()
    return vessels


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Fichier d'etat ecrit : {STATE_FILE} (existe: {STATE_FILE.exists()})")


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configure - message qui aurait ete envoye :")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(
        url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=20
    )
    if r.status_code != 200:
        print(f"Erreur envoi Telegram ({r.status_code}) : {r.text}")
    else:
        print("Message Telegram envoye avec succes.")


def main():
    print(f"Repertoire de travail : {Path.cwd()}")
    state = load_state()
    new_state = {}
    alerts = []

    for port, url in PORTS.items():
        print(f"Verification de {port}...")
        try:
            vessels = fetch_vessels_in_port(url)
            print(f"  {len(vessels)} navire(s) trouve(s) a {port}")
        except Exception as e:
            print(f"  Erreur en recuperant {port} : {e}")
            new_state[port] = state.get(port, {})
            continue

        new_state[port] = vessels
        previous = state.get(port, {})
        newly_arrived = {
            mmsi: name for mmsi, name in vessels.items() if mmsi not in previous
        }

        if newly_arrived:
            names = ", ".join(newly_arrived.values())
            alerts.append(f"Nouveau(x) navire(s) a {port} : {names}")

    if alerts:
        send_telegram("Radar navires Saye Armand :\n" + "\n".join(alerts))
        print("Alerte envoyee :", alerts)
    else:
        print("Aucun nouveau navire detecte.")

    save_state(new_state)


if __name__ == "__main__":
    main()
