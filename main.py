import datetime
import math
from statistics import StatisticsError, linear_regression
import time
import xml.etree.ElementTree as ET
import requests

# --- ASETUKSET ---
KANAVA = "saa-testi666"  # Vaihda oma ntfy-kanavasi
FMISID = 101004  # Helsinki-Vantaan lentoasema
LATITUDE = 60.3172
LONGITUDE = 24.9633


def laheta_puhelimeen(otsikko, viesti):
    """Lähettää ilmoituksen puhelimeen ntfy.sh-palvelun kautta."""
    try:
        requests.post(
            f"https://ntfy.sh/{KANAVA}",
            data=viesti.encode("utf-8"),
            headers={"Title": otsikko.encode("utf-8")},
            timeout=10,
        )
    except Exception as e:
        print(f"Viestivirhe: {e}")


def opeta_korjausmalli():
    """Hakee edellisen 30 pv ennusteet ja FMI-toteumat, ja laskee korjauskertoimet."""
    print("Haetaan historiaa ja kalibroidaan ennustemallia...")

    tanaan = datetime.date.today()
    alku_pvm = (tanaan - datetime.timedelta(days=32)).strftime("%Y-%m-%d")
    loppu_pvm = (tanaan - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 1. Haetaan menneet ennusteet (Open-Meteo)
    ennuste_historia = {}
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "past_days": 31,
            "forecast_days": 0,
            "daily": ["temperature_2m_max"],
            "models": ["ecmwf_ifs025", "dwd_icon"],
            "timezone": "Europe/Helsinki",
        }
        res = requests.get(url, params=params, timeout=15).json()
        paivat = res["daily"]["time"]
        ecmwf = res["daily"]["temperature_2m_max_ecmwf_ifs025"]
        icon = res["daily"]["temperature_2m_max_dwd_icon"]

        for d, m1, m2 in zip(paivat, ecmwf, icon):
            if m1 is not None and m2 is not None:
                ennuste_historia[d] = (m1 * 0.6) + (m2 * 0.4)
    except Exception as e:
        print(f"Ennustehistorian noutovirhe: {e}")

    # 2. Haetaan menneet toteutuneet maksimilämmöt (FMI)
    fmi_historia = {}
    try:
        alku_iso = (tanaan - datetime.timedelta(days=32)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        loppu_iso = (tanaan - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%dT23:59:59Z"
        )
        fmi_url = "https://opendata.fmi.fi/wfs"
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": "fmi::observations::weather::daily::simple",
            "fmisid": FMISID,
            "parameters": "tmax",
            "starttime": alku_iso,
            "endtime": loppu_iso,
        }
        res = requests.get(fmi_url, params=params, timeout=15)
        root = ET.fromstring(res.content)

        for member in root:
            aika = None
            arvo = None
            for elem in member.iter():
                if elem.tag.endswith("Time"):
                    aika = elem.text[:10]  # Poimitaan YYYY-MM-DD
                elif elem.tag.endswith("ParameterValue"):
                    try:
                        val = float(elem.text)
                        if not math.isnan(val):
                            arvo = val
                    except (ValueError, TypeError):
                        pass

            if aika and arvo is not None:
                fmi_historia[aika] = arvo
    except Exception as e:
        print(f"FMI-historian noutovirhe: {e}")

    # 3. Yhdistetään datapisteet (x = malliennuste, y = FMI:n todellinen mittaus)
    x = []
    y = []
    for pvm, raw_f in ennuste_historia.items():
        if pvm in fmi_historia:
            x.append(raw_f)
            y.append(fmi_historia[pvm])

    print(f"Kerätty {len(x)} vertailukelpoista päivää mallin opetukseen.")

    # 4. Lasketaan sovituskerroin (Linear regression)
    if len(x) >= 7:
        try:
            slope, intercept = linear_regression(x, y)
            print(
                f"Malli kalibroitu: T_tarkka = {slope:.2f} * T_raaka + ({intercept:.2f})"
            )
            return slope, intercept
        except StatisticsError:
            pass

    # Varasuunnitelma: jos datapisteitä on vähän, lasketaan pelkkä keskimääräinen virhe
    if x:
        keskivirhe = sum(y_i - x_i for x_i, y_i in zip(x, y)) / len(x)
        print(f"Käytetään keskimääräistä poikkeamaa: {keskivirhe:+.2f} °C")
        return 1.0, keskivirhe

    return 1.0, 0.0  # Ei korjausta, jos dataa ei saatu


def hae_paivan_raakaennuste():
    """Hakee kuluvan päivän maksimiennusteen suoraan malleista."""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "hourly": "temperature_2m",
            "models": ["ecmwf_ifs025", "dwd_icon"],
            "forecast_days": 1,
            "timezone": "Europe/Helsinki",
        }
        res = requests.get(url, params=params, timeout=10).json()
        m1 = max(res["hourly"]["temperature_2m_ecmwf_ifs025"])
        m2 = max(res["hourly"]["temperature_2m_dwd_icon"])
        return round((m1 * 0.6) + (m2 * 0.4), 1)
    except Exception as e:
        print(f"Ennustevirhe: {e}")
        return None


def hae_fmi_mittaus():
    """Hakee lentoaseman viimeisimmän 10 minuutin mittauksen."""
    try:
        url = "https://opendata.fmi.fi/wfs"
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": "fmi::observations::weather::simple",
            "fmisid": FMISID,
            "parameters": "t2m",
        }
        res = requests.get(url, params=params, timeout=10)
        root = ET.fromstring(res.content)
        temp = None
        for elem in root.iter():
            if elem.tag.endswith("ParameterValue"):
                temp = float(elem.text)
        return temp
    except Exception as e:
        print(f"FMI-virhe: {e}")
        return None


# --- PÄÄSILMUKKA ---
print("Käynnistetään itseoppiva sääbotti...")

# Opetetaan malli heti käynnistyksessä
slope, intercept = opeta_korjausmalli()

raaka = hae_paivan_raakaennuste()
paivan_ennuste = round((raaka * slope) + intercept, 1) if raaka else None

laheta_puhelimeen(
    "Sääbotti käynnistetty (ML-kalibroitu)",
    f"Raakaennuste: {raaka} °C\nKalibroitu huippu: {paivan_ennuste} °C\n(Kerroin: {slope:.2f}, vakio: {intercept:+.2f})",
)

toteutunut_huippu = -999.0
edellinen_paiva = datetime.date.today()

while True:
    try:
        tanaan = datetime.date.today()

        # Päivän vaihtuessa opetetaan malli uudelleen tuoreimmalla datalla
        if tanaan != edellinen_paiva:
            slope, intercept = opeta_korjausmalli()
            raaka = hae_paivan_raakaennuste()
            paivan_ennuste = (
                round((raaka * slope) + intercept, 1) if raaka else None
            )

            laheta_puhelimeen(
                "Uusi päivä – Päivitetty ennuste",
                f"Raakaennuste: {raaka} °C -> Kalibroitu: {paivan_ennuste} °C",
            )
            toteutunut_huippu = -999.0
            edellinen_paiva = tanaan

        mittari = hae_fmi_mittaus()
        klo = datetime.datetime.now().strftime("%H:%M")

        if mittari is not None:
            print(
                f"[{klo}] Mittari: {mittari:.1f} °C | Kalibroitu ennuste: {paivan_ennuste} °C | Huippu: {toteutunut_huippu:.1f} °C"
            )

            if mittari > toteutunut_huippu:
                vanha = toteutunut_huippu
                toteutunut_huippu = mittari

                # Hälytys, jos mitattu lämpötila rikkoo kalibroidun ennusteen
                if (
                    vanha != -999.0
                    and paivan_ennuste is not None
                    and toteutunut_huippu > paivan_ennuste
                ):
                    laheta_puhelimeen(
                        "Ennuste rikkoutui!",
                        f"Mitattu Helsinki-Vantaalla klo {klo}: {toteutunut_huippu:.1f} °C\nKalibroitu ennuste oli: {paivan_ennuste:.1f} °C (Raaka: {raaka:.1f} °C)",
                    )

    except Exception as e:
        print(f"Odottamaton silmukkavirhe: {e}")

    time.sleep(600)
