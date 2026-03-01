import streamlit as st

def inject_styles():
    st.markdown(r"""
<style>
:root{
  --sidebar-w: 340px;
  --accent: #FF4B4B;
  --accent2: #FF8A00;
  --text: rgba(255,255,255,0.92);
  --muted: rgba(255,255,255,0.62);
  --glass: rgba(255,255,255,0.06);
}

header[data-testid="stHeader"] { visibility: hidden; height: 0px; }

.block-container{
  padding-top: 7.2rem !important;
  padding-bottom: 2rem !important;
}

/* sidebar no toggle */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"],
button[data-testid="stSidebarCollapseButton"],
button[data-testid="stSidebarCollapsedControl"]{
  display: none !important;
}

section[data-testid="stSidebar"]{
  width: var(--sidebar-w) !important;
  min-width: var(--sidebar-w) !important;
  display: block !important;
  visibility: visible !important;
  transform: none !important;
}

/* sidebar naik */
section[data-testid="stSidebar"] > div { padding-top: 0rem !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]{
  padding-top: 0rem !important;
  margin-top: -18px !important;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3{
  margin-top: 0.2rem !important;
}

/* fixed header */
.fixed-header{
  position: fixed;
  top: 0;
  left: var(--sidebar-w);
  width: calc(100% - var(--sidebar-w));
  z-index: 9999;
  padding: 18px 22px 16px 22px;
  background: linear-gradient(180deg, rgba(14,17,23,0.92), rgba(14,17,23,0.65));
  backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.fixed-header::after{
  content:"";
  position:absolute;
  left:0; right:0; bottom:-1px;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), var(--accent2), transparent);
  opacity: 0.45;
}
.h-wrap{
  max-width: 1200px;
  margin: 0 auto;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap: 16px;
}
.h-left{ display:flex; align-items:center; gap: 14px; }
.h-right{ display:flex; align-items:center; gap: 10px; }
.h-icon{
  width: 44px; height: 44px;
  border-radius: 14px;
  background:
    radial-gradient(circle at 30% 30%, rgba(255,75,75,0.55), rgba(255,255,255,0.02) 60%),
    linear-gradient(135deg, rgba(255,75,75,0.18), rgba(255,138,0,0.10));
  border: 1px solid rgba(255,255,255,0.10);
  box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  display:flex; align-items:center; justify-content:center;
  font-size: 20px;
}
.h-title{ display:flex; flex-direction:column; gap: 2px; }
.h-title h1{
  margin:0; font-size: 1.25rem; font-weight: 800;
  letter-spacing: 0.4px; color: var(--text);
}
.h-title p{ margin:0; font-size: 0.90rem; color: var(--muted); }

.pill{
  padding: 8px 12px;
  border-radius: 999px;
  background: var(--glass);
  border: 1px solid rgba(255,255,255,0.10);
  color: rgba(255,255,255,0.85);
  font-size: 0.82rem;
  display:flex;
  align-items:center;
  gap: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.22);
}
.dot{
  width: 8px; height: 8px;
  border-radius: 999px;
  background: rgba(90, 255, 155, 0.95);
  box-shadow: 0 0 0 4px rgba(90,255,155,0.12);
}
.pill.accent{
  border: 1px solid rgba(255,75,75,0.22);
  background: linear-gradient(135deg, rgba(255,75,75,0.20), rgba(255,138,0,0.10));
}
.pill.accent b{ color: rgba(255,255,255,0.95); }

/* buttons */
div.stButton > button {
  width: 100%;
  background-color: #FF4B4B;
  color: white;
  font-weight: 600;
  border-radius: 10px;
  height: 45px;
  border: none;
  transition: all 0.2s;
}
div.stButton > button:hover {
  background-color: #ff1f1f;
  transform: scale(1.01);
  box-shadow: 0 6px 16px rgba(255, 75, 75, 0.28);
}

div.stDownloadButton > button {
  width: 100%;
  background-color: #2196F3;
  color: white;
  font-weight: 600;
  border-radius: 10px;
  height: 45px;
  border: none;
  transition: all 0.2s;
}
div.stDownloadButton > button:hover {
  background-color: #0b7dda;
  transform: scale(1.01);
  box-shadow: 0 6px 16px rgba(33, 150, 243, 0.28);
}
</style>

<div class="fixed-header">
  <div class="h-wrap">
    <div class="h-left">
      <div class="h-icon">🎬</div>
      <div class="h-title">
        <h1>Content Generator</h1>
        <p>Generate • Render • Review • Upload — all in one control panel</p>
      </div>
    </div>
    <div class="h-right">
      <div class="pill"><span class="dot"></span>System Ready</div>
      <div class="pill accent">Mode: <b>Automation Studio</b></div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
