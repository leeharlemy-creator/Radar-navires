"""
Radar navires - Saye Armand
Verifie les navires actuellement a quai a Pointe-Noire et Abidjan
(source : myshiptracking.com, gratuit) et envoie une alerte Telegram
des qu'un nouveau navire apparait, avec heure locale si disponible.
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
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

VESSEL_LINK_RE = re.compile(
    r'/vessels/([a-z0-9]+(?:-[a-z0-9]+)*)-mmsi-(\d+)-imo-\d+'
)

# Format reel observe sur le site : "2026-08-30 <b>18:26</b>" (heure locale, "LT")
ETA_NEAR_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2})\s*<b>(\d{1,2}:\d{2})</b>'
)


def slug_to_name(slug):
    return " ".join(word.capitalize() for word in slug.split("-"))


def fetch_vessels_in_port(url, port_name):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    html = resp.text

    print(f"  Statut HTTP : {resp.status_code}")
    print(f"  Taille de la reponse : {len(html)} caracteres")

    resp.raise_for_status()

    vessels = {}
    for match in VESSEL_LINK_RE.finditer(html):
        slug, mmsi = match.group(1), match.group(2)
        nom = slug_to_name(slug)

        fenetre = html[match.end():match.end() + 400]
        eta_match = ETA_NEAR_RE.search(fenetre)
        eta_brut = f"{eta_match.group(1)} {eta_match.group(2)}" if eta_match else ""

        if mmsi not in vessels:
            vessels[mmsi] = {"nom": nom, "eta_brut": eta_brut}

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
            vessels = fetch_vessels_in_port(url, port)
            print(f"  {len(vessels)} navire(s) trouve(s) a {port}")
            avec_heure = sum(1 for v in vessels.values() if v['eta_brut'])
            print(f"  {avec_heure} navire(s) avec heure detectee")
        except Exception as e:
            print(f"  Erreur en recuperant {port} : {e}")
            new_state[port] = state.get(port, {})
            continue

        new_state[port] = vessels
        previous = state.get(port, {})
        newly_arrived = {
            mmsi: v for mmsi, v in vessels.items() if mmsi not in previous
        }

        if newly_arrived:
            noms = ", ".join(
                f"{v['nom']} ({v['eta_brut']})" if v['eta_brut'] else v['nom']
                for v in newly_arrived.values()
            )
            alerts.append(f"Nouveau(x) navire(s) a {port} : {noms}")

    if alerts:
        send_telegram("Radar navires Saye Armand :\n" + "\n".join(alerts))
        print("Alerte envoyee :", alerts)
    else:
        print("Aucun nouveau navire detecte.")

    save_state(new_state)


if __name__ == "__main__":
    main()
