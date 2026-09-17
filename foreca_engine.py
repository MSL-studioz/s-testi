"""
FORECA-TYYLINEN MONIMALLI- & ALUEELLINEN SPATIAL-KORJAUS (foreca_engine.py)
Asema: Helsinki-Vantaan lentoasema (FMISID 101004, EFHK)
Optimoitu: Oracle Always Free VPS (1 Gt RAM) - Puhdas ilmainen NumPy
"""
import requests
import numpy as np

NEARBY_STATIONS = [
    {"fmisid": "100968", "name": "Helsinki Malmi lentokenttä", "dist_km": 8.4, "weight": 0.28},
    {"fmisid": "100971", "name": "Helsinki Kumpula",           "dist_km": 11.2, "weight": 0.22},
    {"fmisid": "101011", "name": "Tuusula Tuusulanjärvi",       "dist_km": 11.8, "weight": 0.20},
    {"fmisid": "100973", "name": "Helsinki Kaisaniemi",         "dist_km": 15.6, "weight": 0.14},
    {"fmisid": "101018", "name": "Espoo Nuuksio",               "dist_km": 19.8, "weight": 0.10},
    {"fmisid": "101007", "name": "Nurmijärvi Röykkä",           "dist_km": 20.4, "weight": 0.06},
]

MODEL_PRIORS = {
    "ecmwf_ifs025":  {"weight": 0.34},
    "fmi_harmonie":  {"weight": 0.31},
    "dwd_icon_eu":   {"weight": 0.18},
    "ukmo_seamless": {"weight": 0.10},
    "gfs_seamless":  {"weight": 0.07},
}

def fetch_multi_model_forecast(lat=60.3172, lon=24.9633):
    models = "ecmwf_ifs025,dwd_icon_eu,gfs_seamless,ukmo_seamless"
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&"
        f"current=temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m&"
        f"models={models}&timezone=Europe%2FHelsinki"
    )
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json().get("current", {})
    except Exception as e:
        return {}

def calculate_foreca_consensus(multi_data, station_observations=None, base_tarmac_adj=0.15):
    if station_observations is None:
        station_observations = {}

    t_ecmwf = multi_data.get("temperature_2m_ecmwf_ifs025", 8.0)
    t_icon = multi_data.get("temperature_2m_dwd_icon_eu", 8.0)
    t_gfs = multi_data.get("temperature_2m_gfs_seamless", 8.0)
    t_ukmo = multi_data.get("temperature_2m_ukmo_seamless", 8.0)
    t_harmonie = (t_ecmwf + t_icon) / 2.0

    model_temps = {
        "ecmwf_ifs025": t_ecmwf,
        "fmi_harmonie": t_harmonie,
        "dwd_icon_eu": t_icon,
        "ukmo_seamless": t_ukmo,
        "gfs_seamless": t_gfs,
    }

    weighted_model_temp = sum(model_temps[m] * MODEL_PRIORS[m]["weight"] for m in model_temps)

    residuals, weights = [], []
    for st in NEARBY_STATIONS:
        fmisid = st["fmisid"]
        if fmisid in station_observations:
            obs = station_observations[fmisid].get("obs_temp")
            raw_fc = station_observations[fmisid].get("raw_forecast")
            if obs is not None and raw_fc is not None:
                residuals.append(obs - raw_fc)
                weights.append(st["weight"])

    if residuals:
        norm_w = np.array(weights) / sum(weights)
        regional_bias = float(np.dot(residuals, norm_w))
    else:
        regional_bias = 0.0

    foreca_blended = weighted_model_temp + (regional_bias * 0.45) + base_tarmac_adj
    return {
        "foreca_blended": round(foreca_blended, 2),
        "raw_mean": round(float(np.mean(list(model_temps.values()))), 2),
        "model_temps": model_temps,
        "regional_bias": round(regional_bias, 2)
    }
