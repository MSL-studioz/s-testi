#!/usr/bin/env python3
"""
SÄÄBOTTI & KONEOPPIMISKALIBROINTI (main.py) - KORJATTU VERSIO
Asema: Helsinki-Vantaan lentoasema (EFHK / FMISID 101004)
"""

import os
import sys
import time
import math
import logging
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import numpy as np
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("saabotti-ml")

DB_PATH = os.path.expanduser("~/s-testi/saabotti_v2.db")
EFHK_LAT = 60.3172
EFHK_LON = 24.9633
FMISID = 101004

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weather_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT UNIQUE,
                obs_temp REAL,
                raw_temp REAL,
                cal_temp REAL,
                foreca_temp REAL,
                cloud_cover REAL,
                wind_speed REAL,
                wind_dir REAL,
                humidity REAL,
                pressure REAL,
                radiation REAL,
                metar_raw TEXT,
                ecmwf_temp REAL,
                icon_temp REAL,
                gfs_temp REAL,
                max_today REAL,
                max_tomorrow REAL,
                max_dayafter REAL
            );
        """)
        
        # Varmistetaan KAIKKI sarakkeet dynaamisesti olemassa olevaan tauluun
        existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(weather_records);").fetchall()]
        required_cols = [
            ("obs_temp", "REAL"),
            ("raw_temp", "REAL"),
            ("cal_temp", "REAL"),
            ("foreca_temp", "REAL"),
            ("cloud_cover", "REAL"),
            ("wind_speed", "REAL"),
            ("wind_dir", "REAL"),
            ("humidity", "REAL"),
            ("pressure", "REAL"),
            ("radiation", "REAL"),
            ("metar_raw", "TEXT"),
            ("ecmwf_temp", "REAL"),
            ("icon_temp", "REAL"),
            ("gfs_temp", "REAL"),
            ("max_today", "REAL"),
            ("max_tomorrow", "REAL"),
            ("max_dayafter", "REAL")
        ]
        for col_name, col_type in required_cols:
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE weather_records ADD COLUMN {col_name} {col_type};")
                    logger.info(f"Lisätty puuttunut sarake: {col_name}")
                except Exception as e:
                    logger.warning(f"Sarakkeen {col_name} lisäys: {e}")
        conn.commit()
    logger.info("Tietokanta ja skeema tarkastettu onnistuneesti.")

def safe_get(arr, idx, default=None):
    """Turvallinen listasta haku ilman IndexError-riskiä."""
    if arr and isinstance(arr, list) and 0 <= idx < len(arr):
        val = arr[idx]
        return val if val is not None else default
    return default

def bootstrap_history_if_needed():
    """Lataa automaattisesti menneen 30 päivän havainnot jos kanta on tyhjä."""
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.cursor().execute("SELECT COUNT(*) FROM weather_records WHERE obs_temp IS NOT NULL;").fetchone()[0]
    
    if count >= 150:
        logger.info(f"Kannassa on jo {count} havaintoa. Ohitetaan historiadatan lataus.")
        return

    logger.info("⚠️ Kannassa vähän tai ei lainkaan dataa! Ladataan 30 päivän arkistohistoria...")
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    archive_url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": EFHK_LAT,
        "longitude": EFHK_LON,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,cloud_cover,wind_speed_10m,wind_direction_10m,surface_pressure",
        "timezone": "UTC"
    }

    try:
        r = requests.get(archive_url, params=params, timeout=20)
        if r.status_code != 200:
            logger.warning(f"Arkistodatan haku epäonnistui (koodi {r.status_code}).")
            return

        data = r.json().get("hourly", {})
        times = data.get("time", [])
        temps = data.get("temperature_2m", [])
        clouds = data.get("cloud_cover", [])
        winds = data.get("wind_speed_10m", [])
        dirs = data.get("wind_direction_10m", [])
        pressures = data.get("surface_pressure", [])

        if not times:
            return

        inserted = 0
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for t_str, t_val, c_val, w_val, d_val, p_val in zip(times, temps, clouds, winds, dirs, pressures):
                if t_val is None:
                    continue
                sim_ecm = t_val + 0.3
                sim_icon = t_val - 0.2
                sim_gfs = t_val + 0.1
                cursor.execute("""
                    INSERT OR IGNORE INTO weather_records (
                        timestamp, obs_temp, raw_temp, cal_temp, foreca_temp,
                        cloud_cover, wind_speed, wind_dir, pressure,
                        ecmwf_temp, icon_temp, gfs_temp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    t_str + ":00Z", t_val, sim_ecm, t_val, t_val,
                    c_val, w_val, d_val, p_val,
                    sim_ecm, sim_icon, sim_gfs
                ))
                inserted += 1
            conn.commit()

        logger.info(f"✅ Historiadata ladattu! Tallennettu {inserted} tuntihavaintoa mallin koulutusta varten.")
    except Exception as e:
        logger.error(f"Virhe historiadatan alustuksessa: {e}")

def calculate_solar_elevation(dt: datetime) -> float:
    day_of_year = dt.timetuple().tm_yday
    declination = 23.45 * math.sin(math.radians((360 / 365) * (day_of_year - 81)))
    solar_time = (dt.hour + dt.minute / 60.0) + (EFHK_LON / 15.0)
    hour_angle = (solar_time - 12.0) * 15.0
    sin_elev = (math.sin(math.radians(EFHK_LAT)) * math.sin(math.radians(declination)) +
                math.cos(math.radians(EFHK_LAT)) * math.cos(math.radians(declination)) * math.cos(math.radians(hour_angle)))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))

def apply_radical_physics_correction(temp: float, cloud: float, wind_spd: float, wind_dir: float, dt: datetime) -> float:
    corr = 0.0
    elev = calculate_solar_elevation(dt)

    # 1. Säteilyinversio
    if elev < 0 and cloud < 20 and wind_spd < 2.5:
        corr -= (1.0 - (cloud / 20.0)) * (1.0 - (wind_spd / 2.5)) * 2.5
    # 2. Päivälämpeneminen kiitotiellä
    elif elev > 15 and cloud < 30:
        corr += (elev / 50.0) * (1.0 - (cloud / 100.0)) * 1.2
    # 3. Suomenlahden merituuli
    if elev > 10 and 140 <= wind_dir <= 220 and 2.5 <= wind_spd <= 8.0 and temp > 12.0:
        corr -= 1.4

    return temp + corr

def train_and_predict_l2_wls(history_rows, cur_features):
    if len(history_rows) < 15:
        return cur_features[0] * 0.45 + cur_features[1] * 0.35 + cur_features[2] * 0.20

    X, y, weights = [], [], []
    now_ts = datetime.now(timezone.utc).timestamp()

    for row in history_rows:
        obs, ecm, ico, gfs, cld, wspd, t_str = row
        if obs is None or ecm is None:
            continue

        try:
            row_ts = datetime.fromisoformat(t_str.replace("Z", "+00:00")).timestamp()
            age_days = (now_ts - row_ts) / 86400.0
            time_weight = math.exp(-age_days / 14.0)
        except:
            time_weight = 0.5

        X.append([ecm, ico or ecm, gfs or ecm, (cld or 50) / 100.0, (wspd or 3) / 10.0, 1.0])
        y.append(obs)
        weights.append(time_weight)

    if len(X) < 10:
        return cur_features[0]

    X = np.array(X)
    y = np.array(y)
    W = np.diag(weights)

    lambda_reg = 0.8
    XTW = X.T @ W
    A = XTW @ X + lambda_reg * np.eye(X.shape[1])
    b = XTW @ y
    beta = np.linalg.solve(A, b)

    cur_x = np.array([
        cur_features[0],
        cur_features[1],
        cur_features[2],
        cur_features[3] / 100.0,
        cur_features[4] / 10.0,
        1.0
    ])

    return float(cur_x @ beta)

def fetch_fmi_observation():
    url = "https://opendata.fmi.fi/wfs"
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::multipointcoverage",
        "fmisid": FMISID,
        "maxlocations": 1
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.content)
        text_content = ""
        for elem in root.iter():
            if elem.tag.endswith("doubleOrNilReasonTupleList"):
                text_content = elem.text.strip()
                break
        if not text_content:
            return None

        lines = text_content.strip().split("\n")
        vals = lines[-1].strip().split()
        
        def pv(v):
            try:
                x = float(v)
                return x if not math.isnan(x) else None
            except:
                return None

        metar_txt = ""
        try:
            mr = requests.get("https://aviationweather.gov/api/data/metar?ids=EFHK&format=raw", timeout=5)
            if mr.status_code == 200:
                metar_txt = mr.text.strip()
        except:
            pass

        return {
            "temp": pv(vals[0]),
            "wind_speed": pv(vals[1]),
            "wind_dir": pv(vals[3]),
            "humidity": pv(vals[4]),
            "pressure": pv(vals[9]),
            "cloud_cover": pv(vals[11]) if len(vals) > 11 else 50.0,
            "metar_raw": metar_txt
        }
    except Exception as e:
        logger.error(f"FMI-haku virhe: {e}")
        return None

def fetch_multimodel_forecast():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": EFHK_LAT,
        "longitude": EFHK_LON,
        "hourly": "temperature_2m,cloud_cover,wind_speed_10m,wind_direction_10m",
        "models": "ecmwf_ifs025,icon_seamless,gfs_seamless",
        "timezone": "UTC",
        "forecast_days": 4
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def run_bot():
    logger.info("=== Sääbotti ja Koneoppimiskoulutus käynnistyy (EFHK) ===")
    init_db()
    bootstrap_history_if_needed()

    while True:
        try:
            now = datetime.now(timezone.utc)
            logger.info(f"Analyysihetki: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

            obs = fetch_fmi_observation()
            if not obs or obs["temp"] is None:
                logger.warning("FMI-havaintoa ei saatu, odotetaan 60s...")
                time.sleep(60)
                continue

            fc_data = fetch_multimodel_forecast()
            if not fc_data or "hourly" not in fc_data:
                logger.warning("Ennustedataa ei saatu, odotetaan...")
                time.sleep(60)
                continue

            hourly = fc_data["hourly"]
            times = hourly.get("time", [])
            now_hour_str = now.strftime("%Y-%m-%dT%H:00")
            idx = times.index(now_hour_str) if now_hour_str in times else 0

            # Turvalliset arvot safe_get-funktiolla (estää IndexErrorin)
            t_ecm = safe_get(hourly.get("temperature_2m_ecmwf_ifs025"), idx, obs["temp"])
            t_ico = safe_get(hourly.get("temperature_2m_icon_seamless"), idx, t_ecm)
            t_gfs = safe_get(hourly.get("temperature_2m_gfs_seamless"), idx, t_ecm)
            cld = safe_get(hourly.get("cloud_cover"), idx, 50.0)
            wspd = safe_get(hourly.get("wind_speed_10m"), idx, 3.0)
            wdir = safe_get(hourly.get("wind_direction_10m"), idx, 180.0)
            raw_t = t_ecm if t_ecm is not None else obs["temp"]

            with sqlite3.connect(DB_PATH) as conn:
                history_rows = conn.cursor().execute("""
                    SELECT obs_temp, ecmwf_temp, icon_temp, gfs_temp, cloud_cover, wind_speed, timestamp
                    FROM weather_records
                    WHERE obs_temp IS NOT NULL
                    ORDER BY id DESC LIMIT 500;
                """).fetchall()

            cal_ml = train_and_predict_l2_wls(history_rows, [
                t_ecm or raw_t, t_ico or raw_t, t_gfs or raw_t, cld, wspd
            ])

            calibrated = apply_radical_physics_correction(cal_ml, cld, wspd, wdir, now)
            foreca_consensus = (t_ecm or raw_t) * 0.40 + (t_ico or raw_t) * 0.35 + (t_gfs or raw_t) * 0.25

            d_today = now.strftime("%Y-%m-%d")
            d_tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            d_dayafter = (now + timedelta(days=2)).strftime("%Y-%m-%d")

            t_tod, t_tom, t_day = [], [], []
            for t_s, val in zip(times, hourly.get("temperature_2m_ecmwf_ifs025", [])):
                if val is None:
                    continue
                if t_s.startswith(d_today):
                    t_tod.append(val)
                elif t_s.startswith(d_tomorrow):
                    t_tom.append(val)
                elif t_s.startswith(d_dayafter):
                    t_day.append(val)

            bias = calibrated - raw_t
            max_today = (max(t_tod) + bias) if t_tod else calibrated
            max_tomorrow = (max(t_tom) + bias * 0.8) if t_tom else calibrated
            max_dayafter = (max(t_day) + bias * 0.6) if t_day after if 'after' in locals() else (max(t_day) + bias * 0.6) if t_day else calibrated
            max_dayafter = (max(t_day) + bias * 0.6) if t_day else calibrated

            with sqlite3.connect(DB_PATH) as conn:
                conn.cursor().execute("""
                    INSERT OR REPLACE INTO weather_records (
                        timestamp, obs_temp, raw_temp, cal_temp, foreca_temp,
                        cloud_cover, wind_speed, wind_dir, humidity, pressure,
                        metar_raw, ecmwf_temp, icon_temp, gfs_temp,
                        max_today, max_tomorrow, max_dayafter
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    now.isoformat(), obs["temp"], raw_t, calibrated, foreca_consensus,
                    cld, wspd, wdir, obs["humidity"], obs["pressure"],
                    obs["metar_raw"], t_ecm, t_ico, t_gfs,
                    max_today, max_tomorrow, max_dayafter
                ))
                conn.commit()

            logger.info(f"✅ Tulos: METAR={obs['temp']}°C | WLS-ML={calibrated:.2f}°C (bias {bias:+.2f}°C) | Huiput: Tänään {max_today:.1f}°C, Huomenna {max_tomorrow:.1f}°C, Ylihuom {max_dayafter:.1f}°C")

        except Exception as e:
            logger.error(f"Virhe pääsilmukassa: {e}", exc_info=True)

        time.sleep(600)

if __name__ == "__main__":
    run_bot()
