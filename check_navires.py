"""
Radar navires - Royal Eagle Control (v3 - VesselAPI)
Remplace myshiptracking.com (bloque, erreur 403) et AISstream.io (pas assez
de recepteurs terrestres dans le Golfe de Guinee) par VesselAPI, qui a
confirme a plusieurs reprises des positions reelles a Pointe-Noire et Abidjan.

ATTENTION QUOTA : VesselAPI est limite a 150 requetes/mois sur le compte
d'Armand. Ce script en consomme 2 par execution (1 par port). Programme
pour ne tourner qu'1 fois par jour (~30-60 requetes/mois selon configuration),
pour laisser de la marge aux clics manuels "Actualiser avec l'AIS" dans l'app.
"""

import json
import os
from pathlib import Path

import requests

VESSELAPI_KEY = os.environ.get("VESSELAPI_KEY")

# Memes zones que celles utilisees cote app (onglet Carte / bouton AIS).
ZONES = {
    "Pointe-Noire": {"latBottom": -5.2, "latTop": -4.3, "lonLeft": 11.4, "lonRight": 12.3},
    "Abidjan": {"latBottom": 4.9, "latTop": 5.6, "lonLeft": -4.5, "lonRight": -3.5},
}

STATE_FILE = Path.cwd() / "navires_state.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def statut_depuis_nav_status(nav_status):
    """0/8 = en route (transit), 1 = a l'ancre (mouillage/rade), 5 = amarre (a quai)."""
    if nav_status == 1:
        return "mouillage"
    if nav_status == 5:
        return "in_port"
    if nav_status in (0, 8):
        return "expected"
    return None


def fetch_vessels_in_zone(port_name, zone):
    url = (
        "https://api.vesselapi.com/v1/location/vessels/bounding-box"
        f"?filter.latBottom={zone['latBottom']}&filter.latTop={zone['latTop']}"
        f"&filter.lonLeft={zone['lonLeft']}&filter.lonRight={zone['lonRight']}"
    )
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {VESSELAPI_KEY}"}, timeout=25
    )
    print(f"  Statut HTTP : {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("vessels", [])
    print(f"  {len(items)} navire(s) brut(s) recu(s) pour {port_name}.")

    vessels = {}
    for item in items:
        mmsi = item.get("mmsi") or item.get("imo")
        nom = (item.get("vessel_name") or "").strip()
        if not mmsi or not nom:
            continue
        section = statut_depuis_nav_status(item.get("nav_status")) or "expected"
        vessels[str(mmsi)] = {"nom": nom, "eta_brut": "", "section": section}
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
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=25
        )
        if r.status_code != 200:
            print(f"Erreur envoi Telegram ({r.status_code}) : {r.text}")
        else:
            print("Message Telegram envoye avec succes.")
    except requests.exceptions.RequestException as e:
        print(f"Echec de connexion a Telegram (panne reseau ponctuelle) : {e}")


def main():
    print(f"Repertoire de travail : {Path.cwd()}")
    if not VESSELAPI_KEY:
        print("ERREUR : VESSELAPI_KEY manquant (secret GitHub non configure).")
        return

    state = load_state()
    new_state = {}
    alerts = []

    for port, zone in ZONES.items():
        print(f"Verification de {port}...")
        try:
            vessels = fetch_vessels_in_zone(port, zone)
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
            noms = ", ".join(v['nom'] for v in newly_arrived.values())
            alerts.append(f"Nouveau(x) navire(s) a {port} : {noms}")

    if alerts:
        send_telegram("Radar navires Royal Eagle Control (VesselAPI) :\n" + "\n".join(alerts))
        print("Alerte(s) envoyee(s) :", alerts)
    else:
        print("Aucun nouveau navire detecte.")

    save_state(new_state)


if __name__ == "__main__":
    main()
