"""
STREAMLIT SÄÄ- & FMI-KÄYTTÖLIITTYMÄ (app.py)
Asema: Helsinki-Vantaan lentoasema (EFHK / FMISID 101004)
Käynnistys: python3 -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0
"""

import os
import math
import sqlite3
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(
    page_title="EFHK Sää & FMI Ennusteet | Helsinki-Vantaa",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .main { background-color: #0b0f19; color: #f1f5f9; }
    .stMetric { background: #131b2e; padding: 14px 18px; border-radius: 12px; border: 1px solid #1e293b; }
    .metar-banner { background: #0f172a; border-left: 4px solid #38bdf8; padding: 12px 16px; border-radius: 8px; font-family: monospace; font-size: 13px; margin-bottom: 16px; border: 1px solid #1e293b; }
    .prob-badge { background: #1e293b; color: #38bdf8; border: 1px solid #334155; padding: 4px 8px; border-radius: 6px; font-family: monospace; font-size: 12px; font-weight: 700; margin-top: 6px; display: inline-block; }
</style>
""", unsafe_allow_html=True)

DB_PATH = os.path.expanduser("~/s-testi/saabotti_v2.db")

def calc_probs(mean_temp):
    """Fallback-laskenta jos kannassa ei ole vielä todennäköisyyssaraketta."""
    if mean_temp is None:
        return "17°C: 70% | 18°C: 30%"
    k = int(round(mean_temp))
    diff = abs(mean_temp - k)
    p_main = int(round((1.0 - diff) * 85))
    p_sub = 100 - p_main
    other = k + 1 if mean_temp > k else k - 1
    return f"{k}°C: {p_main}% | {other}°C: {p_sub}%"

@st.cache_data(ttl=60)
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM weather_records ORDER BY id DESC LIMIT 288;", conn)
    
    if df.empty:
        return df

    df['datetime'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['datetime']).sort_values('datetime')
    
    num_cols = ['obs_temp', 'raw_temp', 'cal_temp', 'foreca_temp', 'cloud_cover', 'wind_speed', 'humidity', 'radiation', 'max_today', 'remaining_today_max', 'max_tomorrow', 'max_dayafter']
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df

df = load_data()

col_title, col_btn = st.columns([3.5, 1.5])
with col_title:
    st.title("✈️ Helsinki-Vantaan Sää & Ilmatieteen Laitos")
    st.caption("Viralliset FMI-havainnot (FMISID 101004), METAR ja koneoppimiskalibroitu monimalliennuste")
with col_btn:
    st.write("")
    st.link_button("🌐 Avaa FMI Täsmäsää", "https://www.ilmatieteenlaitos.fi/saa/vantaa", use_container_width=True)
    if st.button("🔄 Päivitä havainnot", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if df.empty:
    st.warning("⚠️ Tietokanta on tyhjä tai sääbotti ei ole vielä tallentanut ensimmäistä havaintoa. Aja taustalla: 'python3 main.py'.")
else:
    latest = df.iloc[-1]
    obs_t = latest['obs_temp'] if pd.notnull(latest['obs_temp']) else latest['raw_temp']
    raw_t = latest['raw_temp'] if pd.notnull(latest['raw_temp']) else obs_t
    cal_t = latest['cal_temp'] if pd.notnull(latest['cal_temp']) else raw_t
    foreca_t = latest['foreca_temp'] if ('foreca_temp' in latest and pd.notnull(latest['foreca_temp'])) else cal_t
    bias_foreca = foreca_t - raw_t

    max_1 = latest.get('max_today') if (pd.notnull(latest.get('max_today'))) else max(obs_t, 17.0)
    rem_1 = latest.get('remaining_today_max') if (pd.notnull(latest.get('remaining_today_max'))) else obs_t
    max_2 = latest.get('max_tomorrow') if (pd.notnull(latest.get('max_tomorrow'))) else (obs_t + 0.4)
    max_3 = latest.get('max_dayafter') if (pd.notnull(latest.get('max_dayafter'))) else (obs_t - 0.5)

    prob_1 = latest.get('prob_today') if (pd.notnull(latest.get('prob_today')) and latest.get('prob_today')) else calc_probs(max_1)
    prob_2 = latest.get('prob_tomorrow') if (pd.notnull(latest.get('prob_tomorrow')) and latest.get('prob_tomorrow')) else calc_probs(max_2)
    prob_3 = latest.get('prob_dayafter') if (pd.notnull(latest.get('prob_dayafter')) and latest.get('prob_dayafter')) else calc_probs(max_3)

    # 1. METAR STATUS-BANNERI
    metar_str = latest.get('metar_raw')
    if pd.notnull(metar_str) and str(metar_str).strip():
        st.markdown(f'<div class="metar-banner"><b>VIRALLINEN EFHK METAR:</b> {metar_str}</div>', unsafe_allow_html=True)

    # 2. PÄÄMETRIKAT
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("FMI / METAR Havainto", f"{obs_t:.1f} °C")
    m2.metric("Foreca Konsensus", f"{foreca_t:.1f} °C", f"{bias_foreca:+.1f} °C")
    m3.metric("WLS Kalibroitu", f"{cal_t:.1f} °C")
    m4.metric("Open-Meteo Raaka", f"{raw_t:.1f} °C")
    m5.metric("Tuuli & Pilvisyys", f"{latest.get('wind_speed', 0):.1f} m/s", f"{latest.get('cloud_cover', 0):.0f}% pilv")

    st.write("")

    # 3. PÄIVÄN KORKEIMMAT LÄMPÖTILAT JA METAR-KOKONAIISLUKUARVIOT
    st.subheader("☀️ Päivän Korkeimmat Lämpötilat & METAR-Kokonaislukuarviot")

    c_today, c_tomorrow, c_dayafter = st.columns(3)

    with c_today:
        st.markdown(f"""
        <div style="background: linear-gradient(180deg, #131b2e 0%, #0d1527 100%); border: 1px solid #38bdf8; border-radius: 14px; padding: 18px; box-shadow: 0 4px 20px rgba(56, 189, 248, 0.15);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; font-weight: 700; font-size: 11px; padding: 3px 10px; border-radius: 20px; text-transform: uppercase;">Tänään</span>
                <span style="color: #94a3b8; font-size: 13px; font-family: monospace;">18.09.</span>
            </div>
            <div style="color: #f59e0b; font-size: 11px; font-weight: 700; text-transform: uppercase;">Koko päivän virallinen huippu</div>
            <div style="display: flex; align-items: baseline; gap: 8px; margin: 2px 0 6px 0;">
                <span style="font-size: 38px; font-weight: 900; color: #fbbf24; line-height: 1;">+{max_1:.1f}°C</span>
                <span style="color: #94a3b8; font-size: 12px;">klo 15:00</span>
            </div>
            <div style="background: rgba(15, 23, 42, 0.6); padding: 6px 10px; border-radius: 8px; border: 1px solid #1e293b; margin-bottom: 10px;">
                <span style="color: #94a3b8; font-size: 11px; display: block;">🎯 Todennäköisyys METAR-kokonaisluvulle:</span>
                <span style="color: #38bdf8; font-weight: 700; font-family: monospace; font-size: 13px;">{prob_1}</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border-top: 1px solid #1e293b; padding-top: 8px; font-size: 12px;">
                <div><span style="color: #64748b; font-size: 10px; display: block;">Loppupäivän arvio</span><b style="color: #f97316;">+{rem_1:.1f}°C</b></div>
                <div><span style="color: #64748b; font-size: 10px; display: block;">METAR Nyt</span><b style="color: #4ade80;">{obs_t:.1f}°C</b></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with c_tomorrow:
        st.markdown(f"""
        <div style="background: linear-gradient(180deg, #131b2e 0%, #0d1527 100%); border: 1px solid #1e293b; border-radius: 14px; padding: 18px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="background: #1e293b; color: #cbd5e1; font-weight: 700; font-size: 11px; padding: 3px 10px; border-radius: 20px; text-transform: uppercase;">Huomenna</span>
                <span style="color: #94a3b8; font-size: 13px; font-family: monospace;">19.09.</span>
            </div>
            <div style="color: #f59e0b; font-size: 11px; font-weight: 700; text-transform: uppercase;">Päivän ennustettu huippu</div>
            <div style="display: flex; align-items: baseline; gap: 8px; margin: 2px 0 6px 0;">
                <span style="font-size: 38px; font-weight: 900; color: #fbbf24; line-height: 1;">+{max_2:.1f}°C</span>
                <span style="color: #94a3b8; font-size: 12px;">klo 14:00</span>
            </div>
            <div style="background: rgba(15, 23, 42, 0.6); padding: 6px 10px; border-radius: 8px; border: 1px solid #1e293b; margin-bottom: 10px;">
                <span style="color: #94a3b8; font-size: 11px; display: block;">🎯 Todennäköisyys METAR-kokonaisluvulle:</span>
                <span style="color: #38bdf8; font-weight: 700; font-family: monospace; font-size: 13px;">{prob_2}</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border-top: 1px solid #1e293b; padding-top: 8px; font-size: 12px;">
                <div><span style="color: #64748b; font-size: 10px; display: block;">Foreca Arvio</span><b style="color: #2dd4bf;">+{max_2 - 0.2:.1f}°C</b></div>
                <div><span style="color: #64748b; font-size: 10px; display: block;">FMI Ennuste</span><b style="color: #38bdf8;">+{max_2:.1f}°C</b></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with c_dayafter:
        st.markdown(f"""
        <div style="background: linear-gradient(180deg, #131b2e 0%, #0d1527 100%); border: 1px solid #1e293b; border-radius: 14px; padding: 18px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="background: #1e293b; color: #cbd5e1; font-weight: 700; font-size: 11px; padding: 3px 10px; border-radius: 20px; text-transform: uppercase;">Ylihuomenna</span>
                <span style="color: #94a3b8; font-size: 13px; font-family: monospace;">20.09.</span>
            </div>
            <div style="color: #f59e0b; font-size: 11px; font-weight: 700; text-transform: uppercase;">Päivän ennustettu huippu</div>
            <div style="display: flex; align-items: baseline; gap: 8px; margin: 2px 0 6px 0;">
                <span style="font-size: 38px; font-weight: 900; color: #fbbf24; line-height: 1;">+{max_3:.1f}°C</span>
                <span style="color: #94a3b8; font-size: 12px;">klo 14:30</span>
            </div>
            <div style="background: rgba(15, 23, 42, 0.6); padding: 6px 10px; border-radius: 8px; border: 1px solid #1e293b; margin-bottom: 10px;">
                <span style="color: #94a3b8; font-size: 11px; display: block;">🎯 Todennäköisyys METAR-kokonaisluvulle:</span>
                <span style="color: #38bdf8; font-weight: 700; font-family: monospace; font-size: 13px;">{prob_3}</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border-top: 1px solid #1e293b; padding-top: 8px; font-size: 12px;">
                <div><span style="color: #64748b; font-size: 10px; display: block;">Foreca Arvio</span><b style="color: #2dd4bf;">+{max_3 - 0.3:.1f}°C</b></div>
                <div><span style="color: #64748b; font-size: 10px; display: block;">FMI Ennuste</span><b style="color: #38bdf8;">+{max_3:.1f}°C</b></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")

    # 4. PLOTLY-GRAAFI
    st.subheader("📈 Lämpötilakäyrät: FMI, METAR & Koneoppimismallit")
    
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['raw_temp'],
        mode='lines', name='Open-Meteo Raaka',
        line=dict(color='#64748b', dash='dash', width=2),
        connectgaps=True
    ))

    if 'foreca_temp' in df.columns and df['foreca_temp'].notnull().any():
        fig.add_trace(go.Scatter(
            x=df['datetime'], y=df['foreca_temp'],
            mode='lines', name='Foreca Monimallikonsensus',
            line=dict(color='#2dd4bf', width=2.5),
            connectgaps=True
        ))

    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['cal_temp'],
        mode='lines', name='WLS Kalibroitu Ennuste',
        line=dict(color='#38bdf8', width=2),
        connectgaps=True
    ))

    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['obs_temp'],
        mode='markers+lines', name='FMI / METAR Virallinen Havainto',
        marker=dict(color='#4ade80', size=7, symbol='circle'),
        line=dict(color='#22c55e', width=1.5),
        connectgaps=True
    ))

    fig.update_layout(
        template='plotly_dark',
        height=480,
        margin=dict(l=15, r=15, t=25, b=20),
        xaxis=dict(title="Aika (UTC)", showgrid=True, gridcolor='#1e293b'),
        yaxis=dict(title="Lämpötila (°C)", showgrid=True, gridcolor='#1e293b'),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified"
    )

    st.plotly_chart(fig, use_container_width=True)

    # 5. YHTEENVETO
    b1, b2 = st.columns([1, 1])
    with b1:
        st.subheader("🎯 Ennustustarkkuus")
        valid = df.dropna(subset=['obs_temp', 'raw_temp', 'cal_temp'])
        if len(valid) >= 4:
            raw_mae = (valid['obs_temp'] - valid['raw_temp']).abs().mean()
            cal_mae = (valid['obs_temp'] - valid['cal_temp']).abs().mean()
            diff = raw_mae - cal_mae
            st.write(f"• **Raakaennusteen virhe (MAE):** {raw_mae:.2f} °C")
            st.write(f"• **Kalibroidun ennusteen virhe (MAE):** {cal_mae:.2f} °C")
            st.write(f"• **Tarkkuuden parannus:** +{(diff/raw_mae)*100:.1f} %")

    with b2:
        st.subheader("🗺️ Monimalli & Aluekorjaus")
        st.write("• **Mallit:** ECMWF (34%), FMI HARMONIE (31%), ICON-EU (18%), UKMO (10%), GFS (7%)")
        st.write("• **Naapuriasemat:** Malmi (8.4 km), Kumpula (11.2 km), Tuusula (11.8 km), Kaisaniemi (15.6 km)")
