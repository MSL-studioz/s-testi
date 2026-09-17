"""
SÄÄBOTTI (main.py)
Asema: Helsinki-Vantaan lentoasema (FMISID 101004, EFHK)
Ympäristö: Oracle Cloud VPS (1GB RAM + 2GB Swap)
Muisti-optimoitu: Puhdas NumPy (ei ulkopuolisia maksullisia AI-palveluita)
"""

import os
import sys
import time
import math
import sqlite3
import logging
import requests
import numpy as np
from datetime import datetime, timezone

from foreca_engine import fetch_multi_model_forecast, calculate_foreca_consensus

DB_PATH = os.path.expanduser("~/s-testi/saabotti_v2.db")
STATION_FMISID = "101004"
STATION_LAT = 60.3172
STATION_LON = 24.9633
NTFY_TOPIC = "saabotti_efhk_alerts"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT UNIQUE,
                obs_temp REAL,
                raw_temp REAL,
                cal_temp REAL,
                foreca_temp REAL,
                cloud_cover REAL,
                radiation REAL,
                humidity REAL,
                wind_speed REAL,
                metar_raw TEXT
            );
        """)
        try:
            conn.execute("ALTER TABLE weather_records ADD COLUMN foreca_temp REAL;")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weather_time ON weather_records(timestamp);")
    logging.info("Tietokanta alustettu: %s", DB_PATH)

def fetch_metar_efhk():
    url = "https://aviationweather.gov/api/data/metar?ids=EFHK&format=json"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data and len(data) > 0:
                item = data[0]
                return {
                    "raw": item.get("rawOb", ""),
                    "temp": item.get("temp", None),
                    "dewp": item.get("dewp", None),
                    "wind_dir": item.get("wdir", None),
                    "wind_speed_kt": item.get("wspd", None),
                    "qnh": item.get("altim", None),
                    "time": item.get("reportTime", datetime.now(timezone.utc).isoformat())
                }
    except Exception as e:
        logging.warning("METAR haku epäonnistui: %s", e)
    return None

def fetch_open_meteo_raw():
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={STATION_LAT}&longitude={STATION_LON}&"
        f"current=temperature_2m,relative_humidity_2m,cloud_cover,direct_radiation,wind_speed_10m&"
        f"hourly=temperature_2m,cloud_cover,direct_radiation&forecast_days=2&timezone=Europe%2FHelsinki"
    )
    r = requests.get(url, timeout=6)
    r.raise_for_status()
    return r.json()

def wls_calibrate(history_rows, current_features, decay_rate=0.04):
    if len(history_rows) < 8:
        return current_features[0]

    X_list, y_list, w_list = [], [], []
    now = datetime.now(timezone.utc)

    for row in history_rows:
        ts_str, obs_t, raw_t, cloud, rad = row
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            hours_ago = max(0.0, (now - ts).total_seconds() / 3600.0)
            weight = math.exp(-decay_rate * hours_ago)

            X_list.append([1.0, float(raw_t), float(cloud) / 100.0, float(rad) / 1000.0])
            y_list.append(float(obs_t))
            w_list.append(weight)
        except Exception:
            continue

    if len(y_list) < 8:
        return current_features[0]

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)
    W = np.diag(w_list)

    try:
        XtW = X.T @ W
        XtWX = XtW @ X
        XtWy = XtW @ y
        beta = np.linalg.solve(XtWX, XtWy)

        x_curr = np.array([1.0, current_features[0], current_features[1] / 100.0, current_features[2] / 1000.0])
        calibrated = float(beta @ x_curr)
        return round(calibrated, 2)
    except np.linalg.LinAlgError:
        return current_features[0]

def send_ntfy_alert(message, title="Sääbotti Hälytys", priority="default"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": priority},
            timeout=5
        )
    except Exception as e:
        logging.warning("ntfy.sh lähetys epäonnistui: %s", e)

def run_cycle():
    metar = fetch_metar_efhk()
    om = fetch_open_meteo_raw()

    curr_om = om.get("current", {})
    raw_temp = curr_om.get("temperature_2m", 0.0)
    cloud_cover = curr_om.get("cloud_cover", 0.0)
    radiation = curr_om.get("direct_radiation", 0.0)
    humidity = curr_om.get("relative_humidity_2m", 0.0)
    wind_speed = curr_om.get("wind_speed_10m", 0.0)

    obs_temp = metar.get("temp") if (metar and metar.get("temp") is not None) else raw_temp

    try:
        multi_data = fetch_multi_model_forecast(STATION_LAT, STATION_LON)
        foreca_res = calculate_foreca_consensus(multi_data)
        foreca_temp = foreca_res.get("foreca_blended", raw_temp)
    except Exception as e:
        logging.warning("Foreca virhe: %s", e)
        foreca_temp = raw_temp

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, obs_temp, raw_temp, cloud_cover, radiation
            FROM weather_records
            WHERE obs_temp IS NOT NULL AND raw_temp IS NOT NULL
            ORDER BY id DESC LIMIT 120;
        """)
        rows = cursor.fetchall()

    cal_temp = wls_calibrate(rows, [raw_temp, cloud_cover, radiation])
    now_str = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO weather_records 
            (timestamp, obs_temp, raw_temp, cal_temp, foreca_temp, cloud_cover, radiation, humidity, wind_speed, metar_raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (now_str, obs_temp, raw_temp, cal_temp, foreca_temp, cloud_cover, radiation, humidity, wind_speed, metar.get("raw") if metar else ""))

    logging.info("Sykli valmis | Obs: %s°C, Raw: %s°C, WLS: %s°C, Foreca: %s°C", obs_temp, raw_temp, cal_temp, foreca_temp)

    if obs_temp is not None and obs_temp <= -15.0:
        send_ntfy_alert(f"Kova pakkanen EFHK: {obs_temp}°C (Foreca: {foreca_temp}°C)", title="Pakkasvaroitus", priority="high")
    if wind_speed >= 20.0:
        send_ntfy_alert(f"Voimakas tuuli EFHK: {wind_speed} m/s", title="Tuulivaroitus", priority="high")

def main():
    init_db()
    logging.info("Sääbotti käynnistetty tausta-ajoon (Screen: 'saabotti')...")
    while True:
        try:
            run_cycle()
        except Exception as e:
            logging.error("Virhe syklissä: %s", e)
        time.sleep(600)

if __name__ == "__main__":
    main()
