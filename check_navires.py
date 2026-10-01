"""
Radar navires - Royal Eagle Control (v2 - AISstream.io)
Remplace le scraping de myshiptracking.com (desormais bloque, erreur 403)
par un vrai flux AIS officiel et gratuit : aisstream.io (WebSocket).

Se connecte, ecoute pendant une fenetre de temps limitee (le script tourne
une fois puis s'arrete, comme avant), collecte les positions + donnees
statiques (nom, destination, ETA) des navires vus dans les zones de
Pointe-Noire et Abidjan, puis alerte Telegram sur les nouveaux navires.
"""

import asyncio
import json
import os
from pathlib import Path

import requests
import websockets

AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY")

# Zones (boites englobantes) autour de chaque port. Format AISstream :
# [[lat_sud_ouest, lon_sud_ouest], [lat_nord_est, lon_nord_est]]
ZONES = {
    "Pointe-Noire": [[-5.2, 11.4], [-4.3, 12.3]],
    "Abidjan": [[4.9, -4.5], [5.6, -3.5]],
}

# Duree d'ecoute du flux avant de traiter et sauvegarder les resultats.
FENETRE_ECOUTE_SECONDES = 90

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


def eta_depuis_static_data(eta_dict):
    """Le champ Eta d'AISstream (dans ShipStaticData) arrive sous forme
    {'Month':.., 'Day':.., 'Hour':.., 'Minute':..} (annee non fournie)."""
    if not eta_dict:
        return ""
    try:
        mois = eta_dict.get("Month", 0)
        jour = eta_dict.get("Day", 0)
        heure = eta_dict.get("Hour", 0)
        minute = eta_dict.get("Minute", 0)
        if not mois or not jour:
            return ""
        from datetime import datetime
        maintenant = datetime.utcnow()
        annee = maintenant.year
        if mois < maintenant.month:
            annee += 1
        return f"{annee:04d}-{mois:02d}-{jour:02d} {heure:02d}:{minute:02d}"
    except Exception:
        return ""


async def ecouter_zones():
    """Se connecte a AISstream.io, ecoute pendant FENETRE_ECOUTE_SECONDES,
    et retourne un dict {port: {mmsi: {nom, eta_brut, section}}}."""
    resultats = {port: {} for port in ZONES}
    donnees_statiques = {}
    total_brut = 0
    total_rejetes = 0

    bounding_boxes = list(ZONES.values())

    try:
        async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
            subscribe_message = {
                "APIKey": AISSTREAM_API_KEY,
                "BoundingBoxes": bounding_boxes,
                "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
            }
            await ws.send(json.dumps(subscribe_message))
            print(f"  Abonnement envoye pour {len(bounding_boxes)} zone(s).")

            fin = asyncio.get_event_loop().time() + FENETRE_ECOUTE_SECONDES
            while True:
                temps_restant = fin - asyncio.get_event_loop().time()
                if temps_restant <= 0:
                    break
                try:
                    message_brut = await asyncio.wait_for(ws.recv(), timeout=temps_restant)
                except asyncio.TimeoutError:
                    break

                total_brut += 1
                try:
                    message = json.loads(message_brut)
                except Exception:
                    total_rejetes += 1
                    continue

                message_type = message.get("MessageType")
                if total_brut <= 5:
                    print(f"  [DIAGNOSTIC] Message {total_brut} (type={message_type}) : {str(message)[:500]}")
                meta = message.get("MetaData", {})
                mmsi = meta.get("MMSI")
                nom = (meta.get("ShipName") or "").strip()
                lat = meta.get("latitude")
                lon = meta.get("longitude")
                if mmsi and lat is not None and lon is not None:
                    pos = donnees_statiques.setdefault(mmsi, {})
                    pos["lat"] = lat
                    pos["lon"] = lon

                if message_type == "ShipStaticData":
                    data = message.get("Message", {}).get("ShipStaticData", {})
                    destination = (data.get("Destination") or "").strip()
                    eta_brut = eta_depuis_static_data(data.get("Eta"))
                    if mmsi:
                        entree = donnees_statiques.setdefault(mmsi, {})
                        if nom:
                            entree["nom"] = nom
                        if destination:
                            entree["destination"] = destination
                        if eta_brut:
                            entree["eta_brut"] = eta_brut

                elif message_type == "PositionReport":
                    data = message.get("Message", {}).get("PositionReport", {})
                    nav_status = data.get("NavigationalStatus")
                    section = statut_depuis_nav_status(nav_status)
                    if mmsi and section:
                        entree = donnees_statiques.setdefault(mmsi, {})
                        entree["section"] = section
                        if nom:
                            entree["nom"] = nom

    except Exception as e:
        print(f"  Erreur de connexion AISstream : {e}")

    print(f"  {total_brut} message(s) brut(s) recu(s), {total_rejetes} rejete(s).")

    for mmsi, info in donnees_statiques.items():
        nom = info.get("nom", "")
        if not nom:
            continue
        section = info.get("section", "expected")
        eta_brut = info.get("eta_brut", "")
        destination = (info.get("destination") or "").upper()

        port_cible = None
        lat, lon = info.get("lat"), info.get("lon")
        if lat is not None and lon is not None:
            for nom_port, ((s_lat, w_lon), (n_lat, e_lon)) in ZONES.items():
                if s_lat <= lat <= n_lat and w_lon <= lon <= e_lon:
                    port_cible = nom_port
                    break

        if not port_cible:
            if "POINTE" in destination or "PNR" in destination:
                port_cible = "Pointe-Noire"
            elif "ABIDJAN" in destination or "ABJ" in destination:
                port_cible = "Abidjan"

        if not port_cible:
            continue

        resultats[port_cible][str(mmsi)] = {
            "nom": nom,
            "eta_brut": eta_brut,
            "section": section,
        }

    return resultats


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
    if not AISSTREAM_API_KEY:
        print("ERREUR : AISSTREAM_API_KEY manquant (secret GitHub non configure).")
        return

    state = load_state()
    print(f"Ecoute du flux AISstream pendant {FENETRE_ECOUTE_SECONDES}s...")
    resultats = asyncio.run(ecouter_zones())

    new_state = {}
    alerts = []

    for port, vessels in resultats.items():
        print(f"{port} : {len(vessels)} navire(s) identifie(s) dans cette fenetre d'ecoute.")
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
        send_telegram("Radar navires Royal Eagle Control (AISstream) :\n" + "\n".join(alerts))
        print("Alerte(s) envoyee(s) :", alerts)
    else:
        print("Aucun nouveau navire detecte durant cette fenetre.")

    save_state(new_state)


if __name__ == "__main__":
    main()
