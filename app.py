"""
STREAMLIT WEBUI (app.py)
Asema: Helsinki-Vantaan lentoasema (FMISID 101004 / EFHK)
Ympäristö: Oracle Cloud Always Free VPS (portti 8501)
Käynnistys: python3 -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0
"""
import os
import sqlite3
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(
    page_title="Sääbotti EFHK | Helsinki-Vantaa",
    page_icon="🌤️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stMetric { background: #161b22; padding: 12px; border-radius: 8px; border: 1px solid #30363d; }
    .metar-box { background: #0d1117; border-left: 4px solid #58a6ff; padding: 10px 14px; font-family: monospace; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

DB_PATH = os.path.expanduser("~/s-testi/saabotti_v2.db")

@st.cache_data(ttl=120)
def load_weather_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""
            SELECT * FROM weather_records
            ORDER BY id DESC
            LIMIT 144;
        """, conn)
    if not df.empty:
        df['datetime'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('datetime')
    return df

st.title("🌤️ Sääbotti, WLS & Foreca-Konsensus | EFHK 101004")
st.caption("Oracle Cloud VPS (1 Gt RAM) • Monimalli- ja aluespatiakorjaus ilman ulkoisia maksullisia AI-palveluita")

df = load_weather_data()

if df.empty:
    st.info("Odotetaan sääbotin ensimmäistä tallennusta SQLite-kantaan... Käynnistä: 'screen -r saabotti'.")
else:
    latest = df.iloc[-1]
    
    c1, c2, c3, c4, c5 = st.columns(5)
    obs_t = latest['obs_temp'] if pd.notnull(latest['obs_temp']) else latest['raw_temp']
    raw_t = latest['raw_temp']
    cal_t = latest['cal_temp'] if pd.notnull(latest['cal_temp']) else raw_t
    foreca_t = latest['foreca_temp'] if ('foreca_temp' in latest and pd.notnull(latest['foreca_temp'])) else cal_t
    bias = foreca_t - raw_t

    c1.metric("METAR Havainto", f"{obs_t:.1f} °C")
    c2.metric("Foreca Konsensus", f"{foreca_t:.1f} °C", f"{bias:+.2f} °C korjaus")
    c3.metric("WLS Kalibroitu", f"{cal_t:.1f} °C")
    c4.metric("Open-Meteo Raaka", f"{raw_t:.1f} °C")
    c5.metric("Tuuli & Pilvisyys", f"{latest.get('wind_speed', 0):.1f} m/s", f"{latest.get('cloud_cover', 0):.0f}% pilv")

    if pd.notnull(latest.get('metar_raw')) and latest['metar_raw']:
        st.markdown(f'<div class="metar-box"><b>EFHK METAR LIVE:</b> {latest["metar_raw"]}</div>', unsafe_allow_html=True)

    st.write("")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['raw_temp'],
        mode='lines', name='Open-Meteo Raaka',
        line=dict(color='#8b949e', dash='dash', width=1.5)
    ))

    if 'foreca_temp' in df.columns:
        fig.add_trace(go.Scatter(
            x=df['datetime'], y=df['foreca_temp'],
            mode='lines', name='Foreca Monimallikonsensus',
            line=dict(color='#2dd4bf', width=3)
        ))

    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['cal_temp'],
        mode='lines', name='WLS Kalibroitu',
        line=dict(color='#58a6ff', width=2)
    ))

    fig.add_trace(go.Scatter(
        x=df['datetime'], y=df['obs_temp'],
        mode='markers', name='METAR Havainto',
        marker=dict(color='#3fb950', size=6, symbol='circle')
    ))

    fig.update_layout(
        template='plotly_dark',
        height=450,
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis_title="Aika (UTC)",
        yaxis_title="Lämpötila (°C)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

    col_left, col_right = st.columns([1, 1])
    with col_left:
        st.subheader("📊 Virheenkorjaus (MAE)")
        valid_df = df.dropna(subset=['obs_temp', 'raw_temp', 'cal_temp'])
        if len(valid_df) > 5:
            raw_mae = (valid_df['obs_temp'] - valid_df['raw_temp']).abs().mean()
            cal_mae = (valid_df['obs_temp'] - valid_df['cal_temp']).abs().mean()
            improvement = ((raw_mae - cal_mae) / raw_mae) * 100 if raw_mae > 0 else 0
            
            st.write(f"- Raakaennusteen virhe: **{raw_mae:.2f} °C**")
            st.write(f"- Kalibroitu virhe: **{cal_mae:.2f} °C**")
            st.write(f"- Tarkkuuden parannus: **{improvement:+.1f} %**")
            
            prog_val = min(1.0, max(0.0, (100 - min(100, cal_mae * 40)) / 100))
            st.progress(prog_val)

    with col_right:
        st.subheader("⚙️ Screen-komennot palvelimella")
        st.code("""screen -r saabotti    # Avaa sääbotti (Ctrl+C & python3 main.py)
screen -r webui       # Avaa Streamlit (Ctrl+C & python3 -m streamlit run app.py...)
# Irrottaudu: Ctrl + A ja sen jälkeen d""", language="bash")

st.sidebar.title("🛠️ EFHK Ohjaus")
st.sidebar.markdown("""
**Sijainti:** Helsinki-Vantaa  
**FMISID:** 101004  
**Mallit:** ECMWF, HARMONIE, ICON, UKMO, GFS  
**Alue:** 5–20 km naapuriasemat  
**RAM:** 1 Gt (Ei ulkoisia AI-kustannuksia)  
""")
st.sidebar.divider()
if st.sidebar.button("Päivitä näkymä"):
    st.cache_data.clear()
    st.rerun()
