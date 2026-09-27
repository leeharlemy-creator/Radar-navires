"""
Radar navires - Royal Eagle Control
Verifie les navires actuellement a quai ET attendus a Pointe-Noire et
Abidjan (source : myshiptracking.com, gratuit) et envoie une alerte
Telegram des qu'un nouveau navire apparait, avec heure locale et la
vraie section (IN PORT = a quai / EXPECTED = attendu, pas encore arrive).
"""

import json
import os
import re
import time
from pathlib import Path

import requests

PORTS = {
    "Pointe-Noire": "https://www.myshiptracking.com/ports/port-of-pointe-noire-in-cg-congo-id-3364",
    "Abidjan": "https://www.myshiptracking.com/ports/port-of-abidjan-in-ci-ivory-coast-id-3337",
}

STATE_FILE = Path.cwd() / "navires_state.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# En-tetes renforces pour ressembler davantage a un vrai navigateur (le site
# a commence a bloquer les requetes trop simples avec une erreur 403).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.myshiptracking.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

VESSEL_LINK_RE = re.compile(
    r'/vessels/([a-z0-9]+(?:-[a-z0-9]+)*)-mmsi-(\d+)-imo-\d+'
)

# Format reel observe sur le site : "2026-08-30 <b>18:26</b>" (heure locale, "LT")
ETA_NEAR_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2})\s*<b>(\d{1,2}:\d{2})</b>'
)

SECTION_MARKERS = [
    (re.compile(r'IN\s*PORT', re.IGNORECASE), "in_port"),
    (re.compile(r'EXPECTED', re.IGNORECASE), "expected"),
]


def slug_to_name(slug):
    return " ".join(word.capitalize() for word in slug.split("-"))


def section_pour_position(html, position):
    avant = html[:position]
    derniere_section = None
    derniere_position = -1
    for pattern, label in SECTION_MARKERS:
        for m in pattern.finditer(avant):
            if m.start() > derniere_position:
                derniere_position = m.start()
                derniere_section = label
    return derniere_section or "expected"


def fetch_vessels_in_port(url, port_name, session):
    resp = session.get(url, headers=HEADERS, timeout=25)
    html = resp.text

    print(f"  Statut HTTP : {resp.status_code}")
    print(f"  Taille de la reponse : {len(html)} caracteres")

    resp.raise_for_status()

    vessels = {}
    compte_sections = {"in_port": 0, "expected": 0}
    for match in VESSEL_LINK_RE.finditer(html):
        slug, mmsi = match.group(1), match.group(2)
        nom = slug_to_name(slug)

        fenetre = html[match.end():match.end() + 400]
        eta_match = ETA_NEAR_RE.search(fenetre)
        eta_brut = f"{eta_match.group(1)} {eta_match.group(2)}" if eta_match else ""

        section = section_pour_position(html, match.start())
        compte_sections[section] = compte_sections.get(section, 0) + 1

        if mmsi not in vessels:
            vessels[mmsi] = {"nom": nom, "eta_brut": eta_brut, "section": section}

    print(f"  Repartition : {compte_sections.get('in_port', 0)} a quai (IN PORT), "
          f"{compte_sections.get('expected', 0)} attendus (EXPECTED)")

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
        print("Les donnees des navires seront quand meme sauvegardees normalement.")


def main():
    print(f"Repertoire de travail : {Path.cwd()}")
    state = load_state()
    new_state = {}
    alerts = []
    erreurs_403 = []

    # Une session partagee (avec ses cookies) ressemble davantage a une vraie
    # navigation qu'une requete isolee a chaque fois.
    session = requests.Session()

    for i, (port, url) in enumerate(PORTS.items()):
        print(f"Verification de {port}...")
        try:
            if i > 0:
                time.sleep(3)  # petite pause entre les deux ports, moins "robotique"
            vessels = fetch_vessels_in_port(url, port, session)
            print(f"  {len(vessels)} navire(s) trouve(s) a {port}")
        except requests.exceptions.HTTPError as e:
            print(f"  Erreur en recuperant {port} : {e}")
            if e.response is not None and e.response.status_code == 403:
                erreurs_403.append(port)
            new_state[port] = state.get(port, {})
            continue
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
        send_telegram("Radar navires Royal Eagle Control :\n" + "\n".join(alerts))
        print("Alerte(s) detectee(s) :", alerts)
    elif erreurs_403 and len(erreurs_403) == len(PORTS):
        # Le site bloque desormais nos requetes (403 sur tous les ports) :
        # on prevUS Armand une seule fois par situation, pas a chaque run,
        # pour ne pas le spammer si le blocage dure plusieurs jours.
        deja_signale = state.get("_403_signale", False)
        if not deja_signale:
            send_telegram(
                "⚠️ Royal Eagle Control : myshiptracking.com bloque desormais "
                "les requetes automatiques du radar (erreur 403). Aucune "
                "donnee ne peut etre recuperee tant que ce blocage dure. "
                "A signaler a Claude pour chercher une solution."
            )
        new_state["_403_signale"] = True
        print("Blocage 403 confirme sur tous les ports - alerte envoyee (une seule fois).")
    else:
        print("Aucun nouveau navire detecte.")
        if "_403_signale" in state:
            new_state["_403_signale"] = False  # le blocage semble leve, on reinitialise

    save_state(new_state)


if __name__ == "__main__":
    main()
