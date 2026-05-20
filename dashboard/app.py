import streamlit as st
import pandas as pd
import numpy as np
import json, os, glob
from datetime import datetime
import plotly.graph_objects as go

st.set_page_config(
    page_title="Agrolinking Intelligence",
    layout="wide",
    initial_sidebar_state="collapsed"
)

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL_DIR    = os.path.join(BASE, "outputs", "forecasts", "validated")
ZONAL_DIR  = os.path.join(BASE, "outputs", "forecasts", "zonal")
ALERT_DIR  = os.path.join(BASE, "outputs", "daily_alerts")
DIFF_PATH  = os.path.join(BASE, "data", "external", "state_price_differentials.csv")
ZONES_PATH = os.path.join(BASE, "data", "external", "zones_config.json")

COMMODITIES = [
    "Hibiscus","Sesame","Ginger","Cocoa","Soybeans","Cashew Nuts",
    "Sorghum","Beans (white)","Beans (red)","Maize (white)","Maize (yellow)","Wheat"
]
ZONE_ORDER = ["North West","North Central","North East","South West","South East","South South"]

if "page" not in st.session_state:
    st.session_state.page = "Dashboard"
if "dark" not in st.session_state:
    st.session_state.dark = False

page = st.session_state.page
dark = st.session_state.dark

# ── THEME TOKENS ──────────────────────────────────────────────────────────────
if dark:
    BG      = "#0D1A0D"
    BG2     = "#111F11"
    CARD    = "#162016"
    CARD2   = "#1C281C"
    BORDER  = "#243024"
    BORDER2 = "#2E3E2E"
    TEXT    = "#F0F7F0"
    T2      = "#B8D4B8"
    T3      = "#7A9A7A"
    T4      = "#4A6A4A"
    DARK_F  = "#FFFFFF"
    LAUREL  = "#2ECC55"
    SGLOW   = "#FFCE35"
    PBG     = "#0D1A0D"
    GRID    = "#1C281C"
    UP_BG   = "#0A2A0A"
    UP_TX   = "#4ADE80"
    DN_BG   = "#2A0A0A"
    DN_TX   = "#F87171"
    FL_BG   = "#2A2200"
    FL_TX   = "#FACC15"
    L1      = "#2ECC55"
    L2      = "#FFCE35"
    HDR_BG  = "#111F11"
    HDR_BD  = "#243024"
else:
    BG      = "#F7F7F4"
    BG2     = "#F0F0EB"
    CARD    = "#FFFFFF"
    CARD2   = "#F5F5F0"
    BORDER  = "#E8E8E0"
    BORDER2 = "#D8D8D0"
    TEXT    = "#1A1A14"
    T2      = "#3D3D30"
    T3      = "#7A7A68"
    T4      = "#AAAAA0"
    DARK_F  = "#053307"
    LAUREL  = "#007f07"
    SGLOW   = "#FFCE35"
    PBG     = "#F7F7F4"
    GRID    = "#E8E8E0"
    UP_BG   = "#EAFAEA"
    UP_TX   = "#0A6B0A"
    DN_BG   = "#FAEAEA"
    DN_TX   = "#B02020"
    FL_BG   = "#FAFAE8"
    FL_TX   = "#7A6000"
    L1      = "#007f07"
    L2      = "#D4A800"
    HDR_BG  = "#FFFFFF"
    HDR_BD  = "#E8E8E0"

# ── CSS ───────────────────────────────────────────────────────────────────────
css = f"""<style>
/* Force all backgrounds */
html, body, [class*="css"], .main, [data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"], section.main, .block-container {{
  background-color: {BG} !important;
  color: {TEXT} !important;
}}
.main .block-container {{
  padding: 0 !important;
  max-width: 100% !important;
}}
[data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], footer, #MainMenu,
[data-testid="stSidebar"], [data-testid="collapsedControl"] {{
  display: none !important;
}}
/* Tabs */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  background: transparent !important;
  border-bottom: 2px solid {BORDER} !important;
  gap: 0 !important;
}}
[data-testid="stTabs"] [data-baseweb="tab"] {{
  background: transparent !important;
  color: {T3} !important;
  font-size: 13.5px !important;
  font-weight: 600 !important;
  padding: 10px 22px !important;
  border-bottom: 2px solid transparent !important;
  margin-bottom: -2px !important;
  transition: color 0.2s ease !important;
  letter-spacing: 0.01em !important;
}}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {{
  color: {TEXT} !important;
}}
[data-testid="stTabs"] [aria-selected="true"] {{
  color: {DARK_F} !important;
  border-bottom: 2px solid {LAUREL} !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-panel"] {{
  padding: 28px 0 0 !important;
  background: transparent !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{
  display: none !important;
}}
/* Selectbox */
[data-testid="stSelectbox"] > div > div {{
  background: {CARD} !important;
  border: 1.5px solid {BORDER} !important;
  border-radius: 10px !important;
  color: {TEXT} !important;
  font-size: 14px !important;
}}
/* Buttons */
button[kind="primary"] {{
  background: {DARK_F} !important;
  color: {'white' if not dark else '#0D1A0D'} !important;
  border: none !important;
  border-radius: 8px !important;
  font-weight: 600 !important;
  font-size: 13.5px !important;
  letter-spacing: 0.01em !important;
  transition: all 0.2s ease !important;
}}
button[kind="secondary"] {{
  background: {CARD} !important;
  color: {T2} !important;
  border: 1.5px solid {BORDER} !important;
  border-radius: 8px !important;
  font-weight: 500 !important;
  font-size: 13.5px !important;
  transition: all 0.2s ease !important;
}}
/* Dataframe */
[data-testid="stDataFrame"] {{
  border-radius: 12px !important;
  overflow: hidden !important;
  border: 1.5px solid {BORDER} !important;
}}
[data-testid="stDataFrame"] div[data-testid="stDataFrameResizable"] {{
  background: {CARD} !important;
}}
[data-testid="stDataFrame"] th {{
  background: {CARD2} !important;
  color: {T3} !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.08em !important;
  border-bottom: 1.5px solid {BORDER} !important;
  padding: 10px 14px !important;
}}
[data-testid="stDataFrame"] td {{
  background: {CARD} !important;
  color: {T2} !important;
  font-size: 13.5px !important;
  border-bottom: 1px solid {BORDER} !important;
  padding: 9px 14px !important;
}}
/* Widget labels */
[data-testid="stWidgetLabel"] {{ display: none !important; }}
/* Info */
[data-testid="stInfo"] {{
  background: {UP_BG} !important;
  border: 1px solid {BORDER} !important;
  border-radius: 10px !important;
  color: {T2} !important;
}}
/* Scrollbar */
::-webkit-scrollbar {{ width: 5px; height: 5px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: {BORDER2}; border-radius: 4px; }}
/* Animations */
@keyframes fadeUp {{
  from {{ opacity: 0; transform: translateY(18px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes fadeIn {{
  from {{ opacity: 0; }}
  to   {{ opacity: 1; }}
}}
@keyframes scaleIn {{
  from {{ opacity: 0; transform: scale(0.97); }}
  to   {{ opacity: 1; transform: scale(1); }}
}}
@keyframes dotPulse {{
  0%, 100% {{ opacity: 1; transform: scale(1); }}
  50%       {{ opacity: 0.4; transform: scale(0.7); }}
}}
.ag-up   {{ animation: fadeUp 0.5s cubic-bezier(0.22,1,0.36,1) both; }}
.ag-up1  {{ animation: fadeUp 0.5s 0.06s cubic-bezier(0.22,1,0.36,1) both; }}
.ag-up2  {{ animation: fadeUp 0.5s 0.12s cubic-bezier(0.22,1,0.36,1) both; }}
.ag-up3  {{ animation: fadeUp 0.5s 0.18s cubic-bezier(0.22,1,0.36,1) both; }}
.ag-up4  {{ animation: fadeUp 0.5s 0.24s cubic-bezier(0.22,1,0.36,1) both; }}
.ag-sc   {{ animation: scaleIn 0.4s cubic-bezier(0.22,1,0.36,1) both; }}
/* Card hover via CSS only */
.ag-card {{
  transition: transform 0.22s cubic-bezier(0.22,1,0.36,1),
              box-shadow 0.22s cubic-bezier(0.22,1,0.36,1),
              border-color 0.22s ease;
}}
.ag-card:hover {{
  transform: translateY(-3px);
  box-shadow: 0 12px 32px rgba(0,0,0,0.10);
  border-color: rgba(0,127,7,0.4) !important;
}}
</style>"""

st.markdown(css, unsafe_allow_html=True)

# ── DATA ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_forecast():
    files = sorted(glob.glob(os.path.join(VAL_DIR, "forecast_validated_*.json")))
    if not files: return {}, ""
    with open(files[-1]) as f: return json.load(f), os.path.basename(files[-1])

@st.cache_data(ttl=300)
def load_zonal():
    files = sorted(glob.glob(os.path.join(ZONAL_DIR, "zonal_forecast_*.json")))
    if not files: return {}
    with open(files[-1]) as f: return json.load(f)

@st.cache_data(ttl=3600)
def load_diff():
    return pd.read_csv(DIFF_PATH) if os.path.exists(DIFF_PATH) else pd.DataFrame()

@st.cache_data(ttl=3600)
def load_zones():
    if not os.path.exists(ZONES_PATH): return {}
    with open(ZONES_PATH) as f: return json.load(f)

@st.cache_data(ttl=300)
def load_alert(prefix="alert_validated"):
    files = sorted(glob.glob(os.path.join(ALERT_DIR, f"{prefix}_*.txt")))
    if not files: return ""
    with open(files[-1], encoding="utf-8") as f: return f.read()

forecast, fc_file = load_forecast()
zonal    = load_zonal()
diff_df  = load_diff()
zones    = load_zones()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def fp(n):
    if not n: return "--"
    if n >= 1_000_000: return f"N{n/1e6:.2f}M"
    if n >= 1_000:     return f"N{n/1_000:.0f}K"
    return f"N{n:,.0f}"

def pchip(pct):
    if pct > 0.05:
        return f'<span style="padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;background:{UP_BG};color:{UP_TX};white-space:nowrap;">+{pct:.1f}%</span>'
    elif pct < -0.05:
        return f'<span style="padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;background:{DN_BG};color:{DN_TX};white-space:nowrap;">{pct:.1f}%</span>'
    return f'<span style="padding:3px 10px;border-radius:20px;font-size:11.5px;font-weight:700;background:{FL_BG};color:{FL_TX};white-space:nowrap;">0.0%</span>'

def vchip(err):
    if err is None: return ""
    if err <= 5:
        return f'<span style="padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;background:{UP_BG};color:{UP_TX};">Verified</span>'
    if err <= 15:
        return f'<span style="padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;background:{FL_BG};color:{FL_TX};">Review</span>'
    return f'<span style="padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;background:{DN_BG};color:{DN_TX};">Caution</span>'

def pt(title_text=""):
    d = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=T3, size=11),
        margin=dict(l=12, r=12, t=44, b=12),
        showlegend=False,
        xaxis=dict(gridcolor=GRID, linecolor=BORDER, tickfont=dict(size=10), zeroline=False),
        yaxis=dict(gridcolor=GRID, linecolor=BORDER, tickfont=dict(size=10), zeroline=False),
    )
    if title_text:
        d["title"] = dict(text=title_text, font=dict(color=DARK_F, size=14), x=0, pad=dict(l=0))
    return d

def section_head(eyebrow, heading, body=""):
    sub = f'<p style="font-size:14px;color:{T3};margin:6px 0 0;line-height:1.65;max-width:580px;">{body}</p>' if body else ""
    st.markdown(f"""
    <div class="ag-up" style="margin-bottom:28px;">
      <div style="font-size:10.5px;font-weight:700;color:{LAUREL};text-transform:uppercase;
        letter-spacing:0.14em;margin-bottom:8px;">{eyebrow}</div>
      <h1 style="font-size:32px;font-weight:800;color:{DARK_F};letter-spacing:-0.035em;
        line-height:1.08;margin:0;">{heading}</h1>
      {sub}
    </div>""", unsafe_allow_html=True)

def stat_card(fn, title, val, sub, yellow=False):
    bg  = SGLOW   if yellow else CARD
    bdr = "#E8B800" if yellow else BORDER
    tc  = "#1A1A00" if yellow else DARK_F
    tc2 = "#4A3800" if yellow else T3
    return f"""<div class="ag-card ag-up{fn}" style="background:{bg};border:1.5px solid {bdr};
      border-radius:16px;padding:24px 26px;box-shadow:0 2px 8px rgba(0,0,0,0.04);height:100%;">
      <div style="font-size:10px;font-weight:700;color:{tc2};text-transform:uppercase;
        letter-spacing:0.12em;margin-bottom:14px;">{title}</div>
      <div class="ag-num" style="font-size:36px;font-weight:800;color:{tc};letter-spacing:-0.045em;
        line-height:1;margin-bottom:10px;">{val}</div>
      <div style="font-size:12px;color:{tc2};">{sub}</div>
    </div>"""

def price_card(c, pr, lp, pct, err, stagger=1):
    return f"""<div class="ag-card ag-c{stagger}" style="background:{CARD};border:1.5px solid {BORDER};
      border-radius:16px;padding:20px 22px;margin-bottom:14px;
      box-shadow:0 2px 8px rgba(0,0,0,0.04);">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;">
        <div style="width:10px;height:10px;border-radius:50%;background:{LAUREL};
          margin-top:2px;box-shadow:0 0 0 3px {UP_BG};"></div>
        {pchip(pct)}
      </div>
      <div style="font-size:10.5px;font-weight:700;color:{T3};text-transform:uppercase;
        letter-spacing:0.1em;margin-bottom:6px;">{c}</div>
      <div style="font-size:25px;font-weight:800;color:{DARK_F};letter-spacing:-0.035em;
        line-height:1.1;margin-bottom:14px;">
        {fp(pr)}<span style="font-size:10.5px;color:{T4};font-weight:400;margin-left:2px;">/MT</span>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;
        padding-top:12px;border-top:1px solid {BORDER};">
        <span style="font-size:11.5px;color:{T3};">prev {fp(lp)}</span>
        {vchip(err)}
      </div>
    </div>"""

# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="background:{HDR_BG};border-bottom:1px solid {HDR_BD};
  position:sticky;top:0;z-index:999;padding:0 52px;height:66px;
  display:flex;align-items:center;justify-content:space-between;
  box-shadow:0 1px 16px rgba(0,0,0,{0.12 if dark else 0.05});">
  <div style="display:flex;align-items:center;gap:16px;">
    <div style="font-size:20px;font-weight:800;color:{DARK_F};letter-spacing:-0.045em;
      display:flex;align-items:center;line-height:1;">
      agr<span style="display:inline-flex;align-items:center;justify-content:center;
        width:17px;height:17px;border-radius:50%;background:{LAUREL};
        margin:0 1px;position:relative;top:-1px;">
        <span style="display:block;width:6px;height:6px;border-radius:50%;
          background:{SGLOW};"></span>
      </span>linking
    </div>
    <div style="width:1px;height:16px;background:{BORDER};"></div>
    <div style="font-size:11px;font-weight:600;color:{T3};
      letter-spacing:0.1em;text-transform:uppercase;">Commodity Intelligence</div>
  </div>
  <div style="display:flex;align-items:center;gap:16px;">
    <div style="display:flex;align-items:center;gap:7px;font-size:12px;color:{T3};">
      <span style="display:inline-block;width:7px;height:7px;border-radius:50%;
        background:{LAUREL};animation:dotPulse 2s infinite;flex-shrink:0;"></span>
      Live &nbsp; {datetime.now().strftime('%d %b %Y, %H:%M')} WAT
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── NAV ───────────────────────────────────────────────────────────────────────
pages_list = ["Dashboard", "Commodities", "Forecasts", "Zonal Prices", "Alerts"]
nav_cols   = st.columns(len(pages_list) + 2)
for i, pg in enumerate(pages_list):
    with nav_cols[i]:
        if st.button(pg, key=f"nav_{pg}",
                     type="primary" if page == pg else "secondary",
                     use_container_width=True):
            st.session_state.page = pg
            st.rerun()
with nav_cols[len(pages_list)]:
    label = "Light" if dark else "Dark"
    if st.button(label, key="theme_toggle", type="secondary", use_container_width=True):
        st.session_state.dark = not dark
        st.rerun()

st.markdown(f'<div class="ag-page" style="padding:36px 52px 56px;background:{BG};">', unsafe_allow_html=True)

# ════════════════════ DASHBOARD ═══════════════════════════════════════════════
if page == "Dashboard":
    if not forecast:
        st.info("No forecast data found. Run the pipeline first.")
    else:
        total   = len(forecast)
        w5      = sum(1 for f in forecast.values() if f.get("validation",{}).get("error_pct_after",99) <= 5)
        avg_err = np.mean([f.get("validation",{}).get("error_pct_after",0) for f in forecast.values()])
        run_d   = list(forecast.values())[0].get("run_date","--")[:10]

        section_head(
            "Daily Intelligence Report",
            "Nigerian Agricultural Commodity Prices",
            "Cross-referenced ensemble forecasts for 12 commodities across 6 geopolitical zones. "
            "Validated against Agricome Africa, WFP Nigeria, and live market sources."
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(stat_card("1", "Commodities Tracked", str(total), "Active forecasts"), unsafe_allow_html=True)
        m2.markdown(stat_card("2", "Verified Accuracy", f"{w5}/{total}", "Within 5% of market", yellow=True), unsafe_allow_html=True)
        m3.markdown(stat_card("3", "Avg Model Error", f"{avg_err:.1f}%", "Post-correction"), unsafe_allow_html=True)
        m4.markdown(stat_card("4", "Last Pipeline Run", run_d, "Full validation date"), unsafe_allow_html=True)

        st.markdown('<div style="height:36px"></div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="margin-bottom:18px;">
          <h2 style="font-size:22px;font-weight:700;color:{DARK_F};letter-spacing:-0.03em;margin:0 0 4px;">
            Today's Market Prices</h2>
          <p style="font-size:13px;color:{T3};margin:0;">National wholesale benchmarks, validated daily</p>
        </div>""", unsafe_allow_html=True)

        for ri, row_c in enumerate([COMMODITIES[:4], COMMODITIES[4:8], COMMODITIES[8:]]):
            cols2 = st.columns(len(row_c))
            for ci, (c, col) in enumerate(zip(row_c, cols2)):
                if c not in forecast: continue
                fi  = forecast[c]; lp = fi.get("last_known_price", 0)
                dh  = fi.get("horizons",{}).get("daily",{})
                pr  = dh.get("ensemble",{}).get("values",[lp])[0] if dh else lp
                pct = (pr - lp)/lp*100 if lp > 0 else 0
                err = fi.get("validation",{}).get("error_pct_after", None)
                stagger = ri * 4 + ci + 1
                with col:
                    st.markdown(price_card(c, pr, lp, pct, err, stagger), unsafe_allow_html=True)

# ════════════════════ COMMODITIES ═════════════════════════════════════════════
elif page == "Commodities":
    section_head("Deep Analysis", "Commodity Deep Dive",
        "Select a commodity and forecast horizon for detailed price trajectory analysis.")

    ca, cb, _ = st.columns([2, 2, 2])
    with ca: sel_c = st.selectbox("Commodity", COMMODITIES, key="comm_sel")
    with cb: hz    = st.selectbox("Horizon", ["daily","weekly","2_weeks","monthly","3_months","6_months"], index=4, key="hz_sel")

    if sel_c in forecast:
        fi  = forecast[sel_c]; lp = fi.get("last_known_price", 0)
        h   = fi.get("horizons",{}).get(hz,{})
        vs  = h.get("ensemble",{}).get("values",[]) if h else []
        ep  = vs[-1] if vs else lp
        pct = (ep - lp)/lp*100 if lp > 0 else 0
        err = fi.get("validation",{}).get("error_pct_after", 0)
        act = fi.get("validation",{}).get("correction_applied","none")

        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        m1, m2, m3, m4 = st.columns(4)
        clr = UP_TX if pct > 0 else DN_TX
        for col, fn, t, v, cv, sub in [
            (m1,"1","Last Known Price", fp(lp), DARK_F, fi.get("last_known_date","--")),
            (m2,"2","Forecast Price",   fp(ep), clr, hz.replace("_"," ").title()),
            (m3,"3","Price Movement",   f"{pct:+.1f}%", clr, "vs last known"),
            (m4,"4","Validation Error", f"{err:.1f}%", DARK_F, f"Correction: {act}"),
        ]:
            with col:
                st.markdown(f"""
                <div class="ag-up{fn}" style="background:{CARD};border:1.5px solid {BORDER};
                  border-radius:16px;padding:22px 24px;box-shadow:0 2px 8px rgba(0,0,0,0.04);">
                  <div style="font-size:10px;font-weight:700;color:{T3};text-transform:uppercase;
                    letter-spacing:0.12em;margin-bottom:12px;">{t}</div>
                  <div style="font-size:28px;font-weight:800;color:{cv};letter-spacing:-0.04em;">{v}</div>
                  <div style="font-size:12px;color:{T3};margin-top:8px;">{sub}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)

        hz_keys   = ["daily","weekly","2_weeks","monthly","3_months","6_months"]
        hz_labels = ["Today","1 Week","2 Weeks","1 Month","3 Months","6 Months"]
        y_vals, lo_vals, hi_vals = [], [], []
        for hk in hz_keys:
            hd  = fi.get("horizons",{}).get(hk,{})
            vs2 = hd.get("ensemble",{}).get("values",[lp]) if hd else [lp]
            lo  = hd.get("ensemble",{}).get("lower_ci",vs2) if hd else vs2
            hi  = hd.get("ensemble",{}).get("upper_ci",vs2) if hd else vs2
            y_vals.append(vs2[-1] if vs2 else lp)
            lo_vals.append(lo[-1] if lo else y_vals[-1])
            hi_vals.append(hi[-1] if hi else y_vals[-1])

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hz_labels+hz_labels[::-1], y=hi_vals+lo_vals[::-1],
            fill="toself", fillcolor="rgba(0,127,7,0.08)",
            line=dict(color="rgba(0,0,0,0)"), showlegend=False
        ))
        fig.add_trace(go.Scatter(
            x=hz_labels, y=y_vals, mode="lines+markers",
            line=dict(color=LAUREL, width=2.5),
            marker=dict(size=9, color=CARD, line=dict(color=LAUREL, width=2.5)),
            hovertemplate="<b>%{x}</b><br>N%{y:,.0f}/MT<extra></extra>"
        ))
        d = pt(f"{sel_c} Price Forecast")
        d["yaxis"]["tickprefix"] = "N"; d["yaxis"]["tickformat"] = ",.0f"
        fig.update_layout(**d)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        if h and h.get("dates"):
            rows2 = []
            prev = lp
            for d_date, v in zip(h["dates"], h.get("ensemble",{}).get("values",[])):
                p2 = (v-prev)/prev*100 if prev > 0 else 0
                rows2.append({"Date": d_date, "Price": f"N{v:,.0f}/MT", "Week-on-Week": f"{p2:+.1f}%"})
                prev = v
            st.dataframe(pd.DataFrame(rows2), use_container_width=True, hide_index=True)

# ════════════════════ FORECASTS ═══════════════════════════════════════════════
elif page == "Forecasts":
    section_head("All Commodities", "Forecast Summary",
        "Validated ensemble forecasts across all 12 commodities. Select a forecast horizon.")

    ca2, _ = st.columns([2, 4])
    with ca2: hz = st.selectbox("Horizon", ["daily","weekly","2_weeks","monthly","3_months","6_months"], index=4, key="fc_hz")

    rows = []
    for c in COMMODITIES:
        if c not in forecast: continue
        fi  = forecast[c]; lp = fi.get("last_known_price", 0)
        h   = fi.get("horizons",{}).get(hz,{})
        vs  = h.get("ensemble",{}).get("values",[]) if h else []
        ep  = vs[-1] if vs else lp
        pct = (ep-lp)/lp*100 if lp > 0 else 0
        err = fi.get("validation",{}).get("error_pct_after", None)
        act = fi.get("validation",{}).get("correction_applied","--")
        rows.append({
            "Commodity":  c,
            "Last Known": f"N{lp:,.0f}",
            "Forecast":   f"N{ep:,.0f}",
            "Change":     f"{pct:+.1f}%",
            "Val Error":  f"{err:.1f}%" if err is not None else "--",
            "Correction": act,
            "Status":     "Verified" if err is not None and err <= 5 else ("Review" if err is not None and err <= 15 else "Caution"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ════════════════════ ZONAL PRICES ════════════════════════════════════════════
elif page == "Zonal Prices":
    section_head("Subnational Intelligence", "Zonal Price Intelligence",
        "6 geopolitical zones across 12 states. Structural price differentials applied "
        "to validated national forecasts. (*) = primary production commodity.")

    if not zonal or "zones" not in zonal:
        st.info("Run: python pipeline/07_zonal_forecast.py")
        st.stop()

    zd = zonal["zones"]; bm = zonal.get("best_market",{})
    tab1, tab2, tab3, tab4 = st.tabs(["Zone Overview","State Detail","Best Buy Market","Production Advantage"])

    with tab1:
        sa, _ = st.columns([2, 4])
        with sa:
            sel_comm = st.selectbox("Commodity", COMMODITIES, key="zc", index=COMMODITIES.index("Maize (white)"))

        z_rows = []
        for zone in ZONE_ORDER:
            if zone not in zd: continue
            for state, comms in zd[zone]["states"].items():
                if sel_comm not in comms: continue
                cd     = comms[sel_comm]
                price  = cd["state_price"]; factor = cd["price_factor"]
                star   = "*" if cd.get("is_primary") else ""
                d_chg  = cd.get("day_change_pct", 0)
                hw     = cd.get("horizons",{}).get("weekly",{})
                wp     = hw.get("values",[price])[0] if hw.get("values") else price
                z_rows.append({"Zone":zone,"State":f"{star}{state}","Current Price":price,
                                "1-Week":wp,"Factor":factor,"Day Change":d_chg,
                                "vs National":f"{(factor-1)*100:+.1f}%"})

        if z_rows:
            srt   = sorted(z_rows, key=lambda x: x["Current Price"])
            clrs  = [L1 if r["Factor"]<1.0 else L2 if r["Factor"]<1.1 else "#E05555" for r in srt]
            names = [r["State"].replace("*","").strip()+" ("+r["Zone"].split()[-1]+")" for r in srt]
            fig   = go.Figure(go.Bar(
                x=names, y=[r["Current Price"] for r in srt],
                marker=dict(color=clrs, line=dict(color="rgba(0,0,0,0)"),
                            opacity=0.88),
                text=[fp(r["Current Price"]) for r in srt],
                textposition="outside",
                textfont=dict(size=10, color=T2),
                hovertemplate="<b>%{x}</b><br>N%{y:,.0f}/MT<extra></extra>"
            ))
            d = pt(f"{sel_comm} — State Price Comparison")
            d["xaxis"]["tickangle"] = -30
            d["yaxis"]["tickprefix"] = "N"; d["yaxis"]["tickformat"] = ",.0f"
            fig.update_layout(**d)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            df_s = pd.DataFrame(srt)
            df_s["Current Price"] = df_s["Current Price"].apply(fp)
            df_s["1-Week"]        = df_s["1-Week"].apply(fp)
            df_s["Factor"]        = df_s["Factor"].apply(lambda x: f"{x:.2f}x")
            df_s["Day Change"]    = df_s["Day Change"].apply(lambda x: f"{x:+.2f}%")
            st.dataframe(df_s[["Zone","State","Current Price","1-Week","Factor","Day Change","vs National"]],
                         use_container_width=True, hide_index=True)

    with tab2:
        cz, cs, ch = st.columns([2, 2, 2])
        with cz: sel_zone  = st.selectbox("Zone", list(zd.keys()), key="dz")
        with cs:
            sl = list(zd.get(sel_zone,{}).get("states",{}).keys())
            sel_state = st.selectbox("State", sl, key="ds")
        with ch: sel_hz = st.selectbox("Horizon", ["daily","weekly","monthly","3_months","6_months"], index=2, key="dh")

        if sel_zone in zd and sel_state in zd[sel_zone]["states"]:
            sc  = zd[sel_zone]["states"][sel_state]
            zi  = zones.get(sel_zone,{})
            pri = zi.get("state_primary",{}).get(sel_state,[])
            pri_str = ", ".join(pri) if pri else "General market"

            st.markdown(f"""
            <div style="background:{DARK_F};border-radius:16px;padding:24px 28px;margin-bottom:22px;">
              <div style="font-size:20px;font-weight:700;color:white;letter-spacing:-0.025em;margin-bottom:8px;">
                {sel_state}
                <span style="color:{SGLOW};margin:0 10px;opacity:0.7;">|</span>
                {sel_zone}
              </div>
              <div style="font-size:13px;color:rgba(255,255,255,0.5);margin-bottom:14px;line-height:1.55;">
                {zi.get("description","")}</div>
              <div>
                <span style="font-size:11px;font-weight:700;color:{SGLOW};text-transform:uppercase;
                  letter-spacing:0.1em;margin-right:8px;">Primary:</span>
                <span style="font-size:13px;color:rgba(255,255,255,0.7);">{pri_str}</span>
              </div>
            </div>""", unsafe_allow_html=True)

            rows3 = []
            for comm in COMMODITIES:
                if comm not in sc: continue
                cd     = sc[comm]
                hh     = cd.get("horizons",{}).get(sel_hz,{})
                hv     = hh.get("values",[None]) if hh else [None]
                fv     = hv[0] if hv else cd["state_price"]
                nat_p  = cd["national_price"]; factor = cd["price_factor"]
                d_chg  = cd.get("day_change_pct", 0)
                star   = "* " if cd.get("is_primary") else ""
                rows3.append({
                    "Commodity":        f"{star}{comm}",
                    "National":         fp(nat_p),
                    "State Price":      fp(cd["state_price"]),
                    f"Forecast":        fp(fv),
                    "Factor":           f"{factor:.2f}x",
                    "Day Change":       f"{d_chg:+.2f}%",
                    "vs National":      f"{(factor-1)*100:+.1f}%",
                })
            st.dataframe(pd.DataFrame(rows3), use_container_width=True, hide_index=True)

            factors = [sc.get(c,{}).get("price_factor",1.0) for c in COMMODITIES]
            fig2 = go.Figure()
            fig2.add_trace(go.Scatterpolar(
                r=factors, theta=COMMODITIES, fill="toself",
                line=dict(color=LAUREL, width=2),
                fillcolor="rgba(0,127,7,0.09)", name=sel_state
            ))
            fig2.add_trace(go.Scatterpolar(
                r=[1.0]*len(COMMODITIES), theta=COMMODITIES,
                line=dict(color=SGLOW, dash="dot", width=1.5),
                name="National avg", fill="none"
            ))
            fig2.update_layout(
                polar=dict(
                    bgcolor="rgba(0,0,0,0)",
                    radialaxis=dict(visible=True, range=[0.6,1.45], gridcolor=GRID,
                                    color=T3, tickfont=dict(size=9)),
                    angularaxis=dict(gridcolor=GRID, color=T3, tickfont=dict(size=9))
                ),
                paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=30,r=30,t=50,b=30),
                font=dict(color=TEXT, size=10), showlegend=True,
                legend=dict(bgcolor="rgba(0,0,0,0)", font_size=11),
                title=dict(text=f"{sel_state} Price Factors vs National Average",
                           font=dict(color=DARK_F, size=13), x=0)
            )
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})

    with tab3:
        st.markdown(f'<p style="font-size:14px;color:{T2};margin-bottom:20px;line-height:1.65;max-width:620px;">Optimal sourcing locations per commodity based on structural price differentials. Sources: WFP subnational surveys, NAERLS crop reports, AFEX market data.</p>', unsafe_allow_html=True)

        if bm:
            bm_rows = []
            for c in COMMODITIES:
                if c not in bm: continue
                b = bm[c]
                bm_rows.append({
                    "Commodity":      c,
                    "Best Buy State": b["best_buy"].split(" (")[0],
                    "Best Price":     fp(b["best_buy_price"]),
                    "Most Expensive": b["highest_price"].split(" (")[0],
                    "Highest Price":  fp(b["highest_price_val"]),
                    "Spread":         f"{b['national_spread_pct']:.0f}%",
                    "Saving":         fp(b["highest_price_val"] - b["best_buy_price"]),
                })
            st.dataframe(pd.DataFrame(bm_rows), use_container_width=True, hide_index=True)

            spreads = [bm.get(c,{}).get("national_spread_pct",0) for c in COMMODITIES]
            fig3 = go.Figure(go.Bar(
                x=COMMODITIES, y=spreads,
                marker=dict(
                    color=spreads,
                    colorscale=[[0, LAUREL],[0.5, SGLOW],[1,"#E05555"]],
                    showscale=False, line=dict(color="rgba(0,0,0,0)"), opacity=0.85
                ),
                text=[f"{s:.0f}%" for s in spreads],
                textposition="outside", textfont=dict(size=10, color=T2)
            ))
            d = pt("Price Spread: Cheapest vs Most Expensive State")
            d["xaxis"]["tickangle"] = -30
            d["yaxis"]["ticksuffix"] = "%"
            fig3.update_layout(**d)
            st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar": False})

    with tab4:
        st.markdown(f'<p style="font-size:14px;color:{T2};margin-bottom:20px;line-height:1.65;max-width:620px;">States with structural production advantage: price factor below 0.90 on primary commodities. These are the national low-cost sourcing hubs due to proximity to production zones.</p>', unsafe_allow_html=True)

        if not diff_df.empty:
            prod = diff_df[(diff_df["price_factor"]<0.90) & (diff_df["is_primary_product"]==True)].sort_values("price_factor").copy()
            sp_prices = []
            for _, row in prod.iterrows():
                zn  = next((z for z,zi2 in zd.items() if row["state"] in zi2.get("states",{})), None)
                p2  = zd.get(zn,{}).get("states",{}).get(row["state"],{}).get(row["commodity"],{}).get("state_price") if zn else None
                sp_prices.append(p2)
            prod["State Price"] = sp_prices
            disp = prod[["zone","state","commodity","price_factor","State Price"]].copy()
            disp.columns = ["Zone","State","Commodity","Factor","State Price"]
            disp["Factor"]        = disp["Factor"].apply(lambda x: f"{x:.2f}x")
            disp["State Price"]   = disp["State Price"].apply(fp)
            disp["Below National"] = prod.apply(lambda r: f"{(1-r['price_factor'])*100:.0f}% cheaper", axis=1)
            st.dataframe(disp, use_container_width=True, hide_index=True)

# ════════════════════ ALERTS ══════════════════════════════════════════════════
elif page == "Alerts":
    section_head("Distribution Ready", "Daily Price Alerts",
        "Ready for WhatsApp broadcast, email, and stakeholder reporting. Updated daily at 08:00 WAT.")

    c1a, c2a = st.columns([1, 1])
    with c1a:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:12px;">
          <div style="width:8px;height:8px;border-radius:50%;background:{LAUREL};
            box-shadow:0 0 0 3px {UP_BG};"></div>
          <span style="font-size:11px;font-weight:700;color:{T3};text-transform:uppercase;
            letter-spacing:0.12em;">National Validated Alert</span>
        </div>""", unsafe_allow_html=True)
        al = load_alert("alert_validated")
        st.markdown(f"""
        <div style="background:{CARD};border:1.5px solid {BORDER};border-radius:16px;
          padding:22px;font-family:monospace;font-size:12.5px;line-height:1.9;
          color:{T2};white-space:pre-wrap;height:520px;overflow-y:auto;
          box-shadow:0 2px 8px rgba(0,0,0,0.04);">
          {al if al else "Run pipeline/06_validate.py first."}
        </div>""", unsafe_allow_html=True)

    with c2a:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:12px;">
          <div style="width:8px;height:8px;border-radius:50%;background:{SGLOW};
            box-shadow:0 0 0 3px {FL_BG};"></div>
          <span style="font-size:11px;font-weight:700;color:{T3};text-transform:uppercase;
            letter-spacing:0.12em;">Zonal State-Level Alert</span>
        </div>""", unsafe_allow_html=True)
        za = load_alert("alert_zonal")
        st.markdown(f"""
        <div style="background:{CARD};border:1.5px solid {BORDER};border-radius:16px;
          padding:22px;font-family:monospace;font-size:12px;line-height:1.85;
          color:{T2};white-space:pre-wrap;height:520px;overflow-y:auto;
          box-shadow:0 2px 8px rgba(0,0,0,0.04);">
          {za if za else "Run pipeline/07_zonal_forecast.py first."}
        </div>""", unsafe_allow_html=True)

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown('</div>', unsafe_allow_html=True)
st.markdown(f"""
<div style="background:{DARK_F};padding:28px 52px;display:flex;
  justify-content:space-between;align-items:center;">
  <div>
    <div style="font-size:17px;font-weight:800;color:white;letter-spacing:-0.04em;
      margin-bottom:5px;">Agrolinking Intelligence</div>
    <div style="font-size:11.5px;color:rgba(255,255,255,0.3);">
      Commodity Intelligence Platform &nbsp;&nbsp; {fc_file}</div>
  </div>
  <div style="text-align:right;">
    <div style="font-size:12px;color:rgba(255,255,255,0.4);margin-bottom:4px;">
      Agricome &nbsp;|&nbsp; WFP Nigeria &nbsp;|&nbsp; World Bank/FAO</div>
    <div style="font-size:11px;color:rgba(255,255,255,0.2);">
      Redefining the Future of Agricultural Connection in Africa</div>
  </div>
</div>
""", unsafe_allow_html=True)