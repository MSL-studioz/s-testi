import datetime
import math
import time
import xml.etree.ElementTree as ET
import requests
import sqlite3
import numpy as np

# --- ASETUKSET ---
KANAVA = "saa-testi666"  # ntfy.sh kanava
FMISID = 101004          # Helsinki-Vantaa METAR
LATITUDE = 60.3172
LONGITUDE = 24.9633
DB_FILE = "saabotti_v2.db"
MODEL_VERSION = "2.1_heavy_duty"

# --- TIETOKANTA ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
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

# --- DATAN NOUTO (FMI) ---
def hae_fmi_historia(paivat=35):
    """Hakee menneet toteutuneet maksimilämpötilat."""
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
        
        # Universaali parsiminen ilman nimiavaruuslukitusta
        aika_list = [e.text[:10] for e in root.iter() if e.tag.endswith('Time')]
        arvo_list = [e.text for e in root.iter() if e.tag.endswith('ParameterValue')]
        
        for aika, arvo in zip(aika_list, arvo_list):
            if arvo != "NaN" and arvo is not None:
                data[aika] = float(arvo)
        
        print(f"[FMI] Historia noudettu: {len(data)} päivää.")
        return data
    except Exception as e:
        print(f"[FMI] Virhe historiassa: {e}")
        return {}

def hae_fmi_nykyhetki():
    """Hakee viimeisimmän 10min mittauksen."""
    url = "https://opendata.fmi.fi/wfs"
    params = {
        "service": "WFS", "version": "2.0.0", "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::simple",
        "fmisid": FMISID, "parameters": "t2m"
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        root = ET.fromstring(res.content)
        vals = [e.text for e in root.iter() if e.tag.endswith('ParameterValue')]
        return float(vals[-1]) if vals else None
    except:
        return None

# --- DATAN NOUTO (OPEN-METEO) ---
def hae_om_data(past=True, days=35):
    """Hakee ECMWF-mallin raakaennusteet tai historian."""
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
        params["forecast_days"] = 4

    try:
        res = requests.get(url, params=params, timeout=15).json()
        d = res["daily"]
        return {t: (tmax, cloud, rad) for t, tmax, cloud, rad in 
                zip(d["time"], d["temperature_2m_max"], d["cloud_cover_max"], d["shortwave_radiation_sum"])
                if tmax is not None}
    except Exception as e:
        print(f"[OM] Virhe: {e}")
        return {}

# --- AI-MALLI (WLS) ---
def opeta_malli():
    """Laskee dynaamiset kertoimet painotetulla regressiolla."""
    fmi = hae_fmi_historia()
    om = hae_om_data(past=True)
    
    dates = sorted(list(set(fmi.keys()) & set(om.keys())))
    print(f"[AI] Yhteisiä datapisteitä opiskeluun: {len(dates)}")

    if len(dates) < 7:
        print("[AI] VAROITUS: Liian vähän dataa. Käytetään oletuskertoimia.")
        return np.array([1.0, 0.0, 0.0, 0.0])

    Y = np.array([fmi[d] for d in dates])
    X = np.array([[om[d][0], om[d][1], om[d][2], 1.0] for d in dates])
    
    # Viimeisen 5 päivän painotus (2.5x)
    weights = np.ones(len(dates))
    if len(weights) > 5:
        weights[-5:] = 2.5
    W = np.diag(weights)

    try:
        # Ratkaistaan kertoimet: (X^T W X)^-1 X^T W Y
        beta = np.linalg.inv(X.T @ W @ X) @ X.T @ W @ Y
        print(f"[AI] Malli kalibroitu: T={beta[0]:.2f}, Pilvi={beta[1]:.2f}, Säteily={beta[2]:.4f}")
        return beta
    except:
        return np.array([1.0, 0.0, 0.0, 0.0])

def ennusta(beta, raaka):
    """Laskee korjatun lämpötilan kertoimien perusteella."""
    return round(beta[0]*raaka[0] + beta[1]*raaka[1] + beta[2]*raaka[2] + beta[3], 1)

# --- PÄÄOHJELMA ---
def main():
    init_db()
    print(f"Käynnistetään {MODEL_VERSION}...")

    beta = opeta_malli()
    ennusteet_raaka = hae_om_data(past=False)
    paivat = sorted(ennusteet_raaka.keys())

    if not paivat:
        print("Virhe: Ennustedataa ei saatu. Tarkista verkko.")
        return

    # Alustava ilmoitus
    tanaan_ennuste = ennusta(beta, ennusteet_raaka[paivat[0]])
    msg = "AI-Ennusteet (METAR 101004):\n"
    for pvm in paivat:
        t_ai = ennusta(beta, ennusteet_raaka[pvm])
        msg += f"{pvm}: {t_ai} °C\n"
    laheta_puhelimeen("Sääbotti Aktivoitu", msg)

    toteutunut_huippu = -999.0
    edellinen_paiva = datetime.date.today()

    while True:
        try:
            tanaan = datetime.date.today()

            # Päivän vaihtuessa
            if tanaan != edellinen_paiva:
                if toteutunut_huippu != -999.0:
                    with sqlite3.connect(DB_FILE) as conn:
                        conn.execute("INSERT OR REPLACE INTO logs VALUES (?, ?, ?, ?)", 
                                    (edellinen_paiva.isoformat(), tanaan_ennuste, toteutunut_huippu, MODEL_VERSION))
                
                beta = opeta_malli()
                ennusteet_raaka = hae_om_data(past=False)
                paivat = sorted(ennusteet_raaka.keys())
                tanaan_ennuste = ennusta(beta, ennusteet_raaka[paivat[0]])
                
                msg = "\n".join([f"{p}: {ennusta(beta, ennusteet_raaka[p])} °C" for p in paivat])
                laheta_puhelimeen("Uusi Päivä: AI-Ennuste", msg)
                
                toteutunut_huippu = -999.0
                edellinen_paiva = tanaan

            # Seuranta
            mittari = hae_fmi_nykyhetki()
            if mittari is not None:
                if mittari > toteutunut_huippu:
                    vanha = toteutunut_huippu
                    toteutunut_huippu = mittari
                    klo = datetime.datetime.now().strftime("%H:%M")
                    print(f"[{klo}] Mittari: {mittari} °C | Ennuste: {tanaan_ennuste}")

                    if vanha != -999.0 and toteutunut_huippu > tanaan_ennuste:
                        laheta_puhelimeen("Ennuste ylittyi!", 
                                         f"Mitattu: {toteutunut_huippu} °C\nAI-Ennuste: {tanaan_ennuste} °C")

        except Exception as e:
            print(f"Virhe silmukassa: {e}")

        time.sleep(600)

if __name__ == "__main__":
    main()
