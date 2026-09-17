import datetime
import time
import xml.etree.ElementTree as ET
import requests

# ASETUKSET
KANAVA = "saa-testi666"  # <-- VAIHDA TÄHÄN SAMA KANAVANIMI KUIN PUHELIMESSASI
FMISID = 101004  # Helsinki-Vantaan lentoasema


def laheta_puhelimeen(otsikko, viesti):
    """Lähettää viestin suoraan ntfy-sovellukseen puhelimeesi."""
    try:
        requests.post(
            f"https://ntfy.sh/{KANAVA}",
            data=viesti.encode("utf-8"),
            headers={"Title": otsikko.encode("utf-8"), "Priority": "high"},
            timeout=10,
        )
    except Exception as e:
        print(f"Viestin lähetysvirhe: {e}")


def hae_paivan_ennustettu_maksimi():
    """Hakee aamulla huippumallien (ECMWF & ICON) ennustaman maksimin tälle päivälle."""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": 60.3172,
            "longitude": 24.9633,
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
        print(f"Ennusteen hakuvirhe: {e}")
        return 20.0  # Varaluku virhetilanteessa


def hae_fmi_mittaus():
    """Hakee Helsinki-Vantaan lentoaseman tuoreimman mittarilukeman."""
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
        aika = None
        for elem in root.iter():
            if elem.tag.endswith("Time"):
                aika = elem.text
            elif elem.tag.endswith("ParameterValue"):
                temp = float(elem.text)
        return temp, aika
    except Exception as e:
        print(f"FMI-hakuvirhe: {e}")
        return None, None


# --- PÄÄSILMUKKA ---
print("Käynnistetään Helsinki-Vantaan 24/7 sääbotti...")
laheta_puhelimeen("Botti käynnistyi", "Sääseuranta on nyt aktiivinen 24/7.")

paivan_ennuste = hae_paivan_ennustettu_maksimi()
toteutunut_huippu = -999.0
edellinen_paiva = datetime.date.today()

while True:
    try:
        tanaan = datetime.date.today()

        # Uuden vuorokauden vaihtuessa nollataan mittaukset ja haetaan uusi ennuste
        if tanaan != edellinen_paiva:
            paivan_ennuste = hae_paivan_ennustettu_maksimi()
            laheta_puhelimeen(
                "Päivän ennuste valmis",
                f"Uuden päivän ennustettu maksimi: {paivan_ennuste} °C",
            )
            toteutunut_huippu = -999.0
            edellinen_paiva = tanaan

        # Haetaan virallinen mittarilukema
        lampotila, havaintoaika = hae_fmi_mittaus()

        if lampotila is not None:
            klo = (
                havaintoaika.split("T")[1][:5]
                if havaintoaika
                else "Tuntematon"
            )
            print(
                f"[{klo}] Mittari: {lampotila:.1f} °C | Ennuste: {paivan_ennuste:.1f} °C | Päivän huippu: {toteutunut_huippu:.1f} °C"
            )

            # Jos mitataan uusi päivän korkein lämpötila
            if lampotila > toteutunut_huippu:
                vanha = toteutunut_huippu
                toteutunut_huippu = lampotila

                # Jos mitattu arvo ylittää aamulla annetun ennusteen
                if vanha != -999.0 and toteutunut_huippu > paivan_ennuste:
                    viesti = (
                        f"Ennuste rikkoutui klo {klo}!\n"
                        f"Mitattu mittarissa: {toteutunut_huippu:.1f} °C\n"
                        f"Aamun ennuste: {paivan_ennuste:.1f} °C"
                    )
                    laheta_puhelimeen("Huippu rikkoutui!", viesti)

    except Exception as e:
        print(f"Odottamaton virhe: {e}")

    # Nukutaan 10 minuuttia (600 sekuntia) ennen seuraavaa tarkistusta
    time.sleep(600)
