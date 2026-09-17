import datetime
import math
import time
import xml.etree.ElementTree as ET
import requests
import sqlite3
import numpy as np

# --- ASETUKSET ---
KANAVA = "saa-testi666" 
FMISID = 101004  # Helsinki-Vantaa METAR
LATITUDE = 60.3172
LONGITUDE = 24.9633
DB_FILE = "saabotti_v2.db"

# --- TIETOKANTA JA LOGITUS ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        # Tallennetaan toteutuneet ja ennustetut vertailua varten
        conn.execute("""CREATE TABLE IF NOT EXISTS logs 
            (date TEXT PRIMARY KEY, predicted REAL, actual REAL, model_version TEXT)""")
        conn.commit()

def laheta_puhelimeen(otsikko, viesti):
    try:
        requests.post(
            f"https://ntfy.sh/{KANAVA}",
            data=viesti.encode("utf-8"),
            headers={"Title": otsikko.encode("utf-8")},
            timeout=10,
        )
    except Exception as e:
        print(f"Viestivirhe: {e}")

# --- DATA-HAKU ---
def hae_fmi_historia(paivat=35):
    tanaan = datetime.date.today()
    alku = (tanaan - datetime.timedelta(days=paivat)).strftime("%Y-%m-%dT00:00:00Z")
    loppu = (tanaan - datetime.timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
    
    url = "https://opendata.fmi.fi/wfs"
    params = {
        "service": "WFS", "version": "2.0.0", "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::daily::simple",
        "fmisid": FMISID, "parameters": "tmax", "starttime": alku, "endtime": loppu
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        root = ET.fromstring(res.content)
        data = {}
        # Huomioidaan WFS-nimiavaruudet
        ns = {'wfs': 'http://www.opengis.net/wfs/2.0', 'omso': 'http://inspire.ec.europa.eu/schemas/omso/3.0'}
        for member in root.findall('.//wfs:member', ns):
            aika_elem = member.find('.//omso:Time', ns)
            arvo_elem = member.find('.//omso:ParameterValue', ns)
            if aika_elem is not None and arvo_elem is not None:
                aika = aika_elem.text[:10]
                if arvo_elem.text != "NaN":
                    data[aika] = float(arvo_elem.text)
        return data
    except Exception as e:
        print(f"FMI-historiavirhe: {e}")
        return {}

def hae_om_data(past=True, days=35):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "daily": ["temperature_2m_max", "cloud_cover_max", "shortwave_radiation_sum"],
        "models": "ecmwf_ifs025", "timezone": "Europe/Helsinki"
    }
    if past:
        params["past_days"] = days
        params["forecast_days"] = 0
    else:
        params["forecast_days"] = 4 # Tänään + 3 pv

    try:
        res = requests.get(url, params=params, timeout=15).json()
        d = res["daily"]
        return {t: (tmax, cloud, rad) for t, tmax, cloud, rad in 
                zip(d["time"], d["temperature_2m_max"], d["cloud_cover_max"], d["shortwave_radiation_sum"])}
    except Exception as e:
        print(f"Open-Meteo virhe: {e}")
        return {}

# --- MALLIN OPETUS (Heavy-Duty WLS) ---
def opeta_malli():
    print("Kalibroidaan AI-mallia (WLS Regression)...")
    fmi = hae_fmi_historia()
    om = hae_om_data(past=True)
    
    dates = sorted(list(set(fmi.keys()) & set(om.keys())))
    if len(dates) < 7:
        print("Liian vähän dataa hienostuneeseen malliin, käytetään oletuksia.")
        return np.array([1.0, 0.0, 0.0, 0.0])

    # Y = FMI Tmax, X = [Raaka_T, Pilvisyys, Säteily, Vakio]
    Y = np.array([fmi[d] for d in dates])
    X = np.array([[om[d][0], om[d][1], om[d][2], 1.0] for d in dates])
    
    # Painotus: viimeiset 5 päivää ovat 2.5x tärkeämpiä
    weights = np.ones(len(dates))
    if len(weights) > 5:
        weights[-5:] = 2.5
    W = np.diag(weights)

    try:
        # Beta = (X^T * W * X)^-1 * X^T * W * Y
        beta = np.linalg.inv(X.T @ W @ X) @ X.T @ W @ Y
        print(f"Malli optimoitu. Kertoimet: T={beta[0]:.2f}, Cloud={beta[1]:.2f}, Rad={beta[2]:.4f}")
        return beta
    except:
        return np.array([1.0, 0.0, 0.0, 0.0])

def laske_ennuste(beta, raaka_data):
    # beta[0]*T + beta[1]*Cloud + beta[2]*Rad + beta[3]
    return round(beta[0]*raaka_data[0] + beta[1]*raaka_data[1] + beta[2]*raaka_data[2] + beta[3], 1)

def hae_fmi_nykyhetki():
    try:
        url = "https://opendata.fmi.fi/wfs"
        params = {
            "service": "WFS", "version": "2.0.0", "request": "getFeature",
            "storedquery_id": "fmi::observations::weather::simple",
            "fmisid": FMISID, "parameters": "t2m"
        }
        res = requests.get(url, params=params, timeout=10)
        root = ET.fromstring(res.content)
        val = root.findall(".//{http://inspire.ec.europa.eu/schemas/omso/3.0}ParameterValue")[-1].text
        return float(val)
    except: return None

# --- PÄÄSILMUKKA ---
init_db()
print("Käynnistetään Heavy-Duty Sääbotti v2.0...")

beta = opeta_malli()
ennusteet_raaka = hae_om_data(past=False)
paivat = sorted(ennusteet_raaka.keys())

# Generoidaan viesti puhelimeen
ennuste_viesti = "Ennusteet (METAR 101004):\n"
tanaan_ennuste = None

for i, pvm in enumerate(paivat):
    t_ai = laske_ennuste(beta, ennusteet_raaka[pvm])
    if i == 0: tanaan_ennuste = t_ai
    ennuste_viesti += f"{pvm}: {t_ai} °C\n"

laheta_puhelimeen("Sää AI: 4 Päivän Ennuste", ennuste_viesti)

toteutunut_huippu = -999.0
edellinen_paiva = datetime.date.today()

while True:
    try:
        tanaan = datetime.date.today()

        if tanaan != edellinen_paiva:
            # Tallenna edellisen päivän toteuma tietokantaan vertailua varten
            if toteutunut_huippu != -999.0:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute("INSERT OR REPLACE INTO logs VALUES (?, ?, ?, ?)", 
                                (edellinen_paiva.isoformat(), tanaan_ennuste, toteutunut_huippu, "v2_heavy"))

            # Uusi päivä, uusi opetus
            beta = opeta_malli()
            ennusteet_raaka = hae_om_data(past=False)
            paivat = sorted(ennusteet_raaka.keys())
            tanaan_ennuste = laske_ennuste(beta, ennusteet_raaka[paivat[0]])
            
            msg = "\n".join([f"{p}: {laske_ennuste(beta, ennusteet_raaka[p])} °C" for p in paivat])
            laheta_puhelimeen("Päivän AI-päivitys", msg)
            
            toteutunut_huippu = -999.0
            edellinen_paiva = tanaan

        # Reaaliaikainen seuranta
        mittari = hae_fmi_nykyhetki()
        klo = datetime.datetime.now().strftime("%H:%M")

        if mittari is not None:
            if mittari > toteutunut_huippu:
                vanha = toteutunut_huippu
                toteutunut_huippu = mittari
                print(f"[{klo}] Uusi huippu: {toteutunut_huippu} °C (Ennuste: {tanaan_ennuste})")

                if vanha != -999.0 and tanaan_ennuste and toteutunut_huippu > tanaan_ennuste:
                    laheta_puhelimeen("Hälytys: Ennuste ylittyi!", 
                                     f"METAR: {toteutunut_huippu} °C\nAI-Ennuste: {tanaan_ennuste} °C")

    except Exception as e:
        print(f"Virhe silmukassa: {e}")

    time.sleep(600) # 10 minuutin välein
