import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
import json
from datetime import datetime
import tempfile
import re
import io
from PIL import Image
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Trafikk & Mobilitet | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  
except ImportError:
    fitz = None

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import streamlit.components.v1 as components


# ── NVDB API (Statens vegvesen) for trafikkdata ──────────────────────

NVDB_API_BASE = "https://nvdbapiles-v3.atlas.vegvesen.no"

def fetch_nvdb_traffic(lat: float, lon: float, radius_m: int = 300):
    """Hent ÅDT og fartsgrense fra NVDB API (Statens vegvesen)."""
    if not HAS_REQUESTS:
        return {"error": "requests mangler"}
    
    results = {"adt": None, "fartsgrense": None, "vegkategori": None, "veglenkeid": None, "vegnummer": None}
    headers = {"Accept": "application/vnd.vegvesen.nvdb-v3-rev1+json"}
    
    # Hent nærmeste veg
    try:
        resp = requests.get(
            f"{NVDB_API_BASE}/veg",
            params={"lat": lat, "lon": lon, "maks_avstand": radius_m, "srid": "4326"},
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            results["vegkategori"] = data.get("veglenkesekvens", {}).get("veglenke", [{}])[0].get("vegkategori") if data.get("veglenkesekvens") else None
            results["vegnummer"] = data.get("vegnummer", {}).get("nummer") if isinstance(data.get("vegnummer"), dict) else None
    except Exception:
        pass
    
    # Hent ÅDT (vegobjekttype 540 = Trafikkmengde)
    try:
        resp = requests.get(
            f"{NVDB_API_BASE}/vegobjekter/540",
            params={"inkluder": "egenskaper", "srid": "4326",
                    "kartutsnitt": f"{lon-0.005},{lat-0.005},{lon+0.005},{lat+0.005}"},
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for obj in data.get("objekter", []):
                for egenskap in obj.get("egenskaper", []):
                    if egenskap.get("id") == 4621:  # ÅDT, total
                        results["adt"] = int(egenskap.get("verdi", 0))
                    elif egenskap.get("id") == 4623:  # ÅDT, tunge
                        results["adt_tunge"] = int(egenskap.get("verdi", 0))
                if results["adt"]:
                    break
    except Exception:
        pass
    
    # Hent fartsgrense (vegobjekttype 105)
    try:
        resp = requests.get(
            f"{NVDB_API_BASE}/vegobjekter/105",
            params={"inkluder": "egenskaper", "srid": "4326",
                    "kartutsnitt": f"{lon-0.003},{lat-0.003},{lon+0.003},{lat+0.003}"},
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for obj in data.get("objekter", []):
                for egenskap in obj.get("egenskaper", []):
                    if egenskap.get("id") == 2021:  # Fartsgrense
                        results["fartsgrense"] = int(egenskap.get("verdi", 0))
                if results["fartsgrense"]:
                    break
    except Exception:
        pass
    
    return results


# ── Geodata støykart API (gjenbrukt fra Akustikk) ───────────────────

STOYKART_URL = "https://services.geodataonline.no/arcgis/rest/services/Geonorge/Stoykart_veg/MapServer"

def fetch_stoykart_for_traffic(lat: float, lon: float, buffer_m: int = 400):
    """Hent støykart-bilde for trafikkrapport."""
    if not HAS_REQUESTS:
        return None
    try:
        import math
        k0=0.9996;a=6378137.0;e=0.0818192;lon0=15.0
        lat_rad=math.radians(lat);lon_rad=math.radians(lon);lon0_rad=math.radians(lon0)
        N=a/math.sqrt(1-e**2*math.sin(lat_rad)**2);T=math.tan(lat_rad)**2
        C=(e**2/(1-e**2))*math.cos(lat_rad)**2;A_val=(lon_rad-lon0_rad)*math.cos(lat_rad)
        M=a*((1-e**2/4-3*e**4/64)*lat_rad-(3*e**2/8+3*e**4/32)*math.sin(2*lat_rad)+(15*e**4/256)*math.sin(4*lat_rad))
        easting=k0*N*(A_val+(1-T+C)*A_val**3/6)+500000
        northing=k0*(M+N*math.tan(lat_rad)*(A_val**2/2))
        
        params = {"bbox": f"{easting-buffer_m},{northing-buffer_m},{easting+buffer_m},{northing+buffer_m}",
                  "bboxSR":"25833","imageSR":"25833","size":"800,600","format":"png","transparent":"true","f":"image"}
        resp = requests.get(f"{STOYKART_URL}/export", params=params, timeout=15)
        if resp.status_code == 200 and "image" in resp.headers.get("content-type",""):
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception:
        pass
    return None


# ── Interaktiv trafikk-editor ────────────────────────────────────────

def render_traffic_editor(images_with_markers, bridge_label: str, component_key: str):
    """Interaktiv editor for trafikkmarkører."""
    if not images_with_markers:
        return
    
    img_data = images_with_markers[0]
    image = img_data["image"]
    markers = img_data.get("markers", [])
    
    buf = io.BytesIO()
    thumb = image.copy()
    if max(thumb.size) > 1400:
        ratio = 1400 / max(thumb.size)
        thumb = thumb.resize((int(thumb.width*ratio), int(thumb.height*ratio)), Image.LANCZOS)
    thumb.save(buf, format="PNG", optimize=True)
    img_uri = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    
    payload = json.dumps({"image": img_uri, "markers": markers}, ensure_ascii=False)
    
    html = f"""
    <div style="font-family:system-ui;color:#e2e8f0">
      <div style="display:flex;gap:3px;padding:6px 8px;background:#0a1929;border:1px solid #1a2a3a;border-radius:10px 10px 0 0;flex-wrap:wrap;align-items:center">
        <button onclick="TE.setTool('select')" id="tt_select" class="tt active" style="--tc:#38bdf8">Velg</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="TE.setTool('adkomst')" id="tt_adkomst" class="tt" style="--tc:#22c55e">→ Adkomst</button>
        <button onclick="TE.setTool('innkjoring')" id="tt_innkjoring" class="tt" style="--tc:#3b82f6">→ Innkjøring</button>
        <button onclick="TE.setTool('gangfelt')" id="tt_gangfelt" class="tt" style="--tc:#f59e0b">▭ Gangfelt</button>
        <button onclick="TE.setTool('konflikt')" id="tt_konflikt" class="tt" style="--tc:#ef4444">⚠ Konflikt</button>
        <button onclick="TE.setTool('sikt')" id="tt_sikt" class="tt" style="--tc:#a78bfa">◠ Siktlinje</button>
        <button onclick="TE.setTool('parkering')" id="tt_parkering" class="tt" style="--tc:#06b6d4">▭ P-areal</button>
        <button onclick="TE.setTool('varemottak')" id="tt_varemottak" class="tt" style="--tc:#f97316">▭ Varemottak</button>
        <button onclick="TE.setTool('renovasjon')" id="tt_renovasjon" class="tt" style="--tc:#84cc16">→ Renovasjon</button>
        <button onclick="TE.setTool('adt')" id="tt_adt" class="tt" style="--tc:#94a3b8">● ÅDT</button>
        <span style="flex:1"></span>
        <button onclick="TE.del()" style="background:rgba(239,68,68,0.15);color:#ef4444;border-color:rgba(239,68,68,0.3)">Slett</button>
        <button onclick="TE.save()" style="background:#38bdf8;color:#06111a;font-weight:700;border-color:#38bdf8">Lagre</button>
      </div>
      <canvas id="TC" style="width:100%;display:block;background:#0d1b2a;border:1px solid #1a2a3a;border-top:none;cursor:crosshair"></canvas>
      <div style="display:flex;gap:8px;padding:5px 10px;background:#0a1929;border:1px solid #1a2a3a;border-top:none;border-radius:0 0 10px 10px;align-items:center">
        <span id="TE_st" style="font-size:10px;color:#475569;font-family:monospace;flex:1"></span>
        <label style="font-size:10px;color:#64748b">Etikett:</label>
        <input id="TE_lb" type="text" style="background:#1a2a3a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;padding:2px 8px;font-size:11px;width:160px" oninput="TE.updLbl(this.value)"/>
      </div>
      <textarea id="TE_ex" style="display:none"></textarea>
    </div>
    <style>.tt{{background:rgba(30,41,59,0.8);color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer;font-weight:500}}.tt:hover{{background:rgba(56,189,248,0.1)}}.tt.active{{background:var(--tc,#38bdf8);color:#fff;font-weight:700;border-color:var(--tc)}}</style>
    <script>
    window.TE=(function(){{
      const P={payload};const cv=document.getElementById('TC'),ctx=cv.getContext('2d'),sts=document.getElementById('TE_st'),lb=document.getElementById('TE_lb'),ex=document.getElementById('TE_ex');
      const img=new Image();img.src=P.image;let els=P.markers.map((m,i)=>{{return{{...m,id:'t'+i}}}});
      let tool='select',sel=-1,drag=null,sp=null,IW=0,IH=0;
      const TM={{adkomst:['arrow','#22c55e','Adkomst'],innkjoring:['arrow','#3b82f6','Innkjøring bil'],gangfelt:['rect','rgba(245,158,11,0.3)','Gangfelt/fortau'],konflikt:['circle','#ef4444','Konfliktpunkt'],sikt:['arrow','#a78bfa','Siktlinje'],parkering:['rect','rgba(6,182,212,0.25)','Parkering'],varemottak:['rect','rgba(249,115,22,0.25)','Varemottak'],renovasjon:['arrow','#84cc16','Renovasjonskjøring'],adt:['circle','#94a3b8','ÅDT']}};
      function uid(){{return Math.random().toString(36).slice(2,10)}}
      function dist(a,b,c,d){{return Math.sqrt((c-a)**2+(d-b)**2)}}
      function render(){{
        if(!img.complete)return;ctx.clearRect(0,0,cv.width,cv.height);ctx.drawImage(img,0,0,IW,IH);
        els.forEach((e,i)=>{{
          const isSel=i===sel,x=(e.x_pct/100)*IW,y=(e.y_pct/100)*IH,c=e.color||'#38bdf8';
          ctx.lineWidth=isSel?4:2.5;ctx.strokeStyle=c;
          if(e.type==='circle'){{
            const r=IW*0.03;ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.stroke();
            if(isSel){{ctx.fillStyle='rgba(255,255,255,0.15)';ctx.fill()}}
            const txt=e.label||'';ctx.font='bold '+Math.max(10,IW*0.015)+'px system-ui';const tw=ctx.measureText(txt).width;
            ctx.fillStyle='rgba(10,25,41,0.8)';ctx.fillRect(x-tw/2-4,y-8,tw+8,16);ctx.fillStyle=c;ctx.fillText(txt,x-tw/2,y+5);
          }}else if(e.type==='rect'){{
            const w=(e.w_pct||8)/100*IW,h=(e.h_pct||6)/100*IH;
            ctx.fillStyle=c;ctx.fillRect(x,y,w,h);ctx.strokeStyle=c.replace(/[\d.]+\)/,'1)');ctx.strokeRect(x,y,w,h);
            ctx.font='bold 10px system-ui';ctx.fillStyle='#fff';ctx.fillText(e.label||'',x+4,y+14);
          }}else if(e.type==='arrow'){{
            const x2=(e.x2_pct||e.x_pct+5)/100*IW,y2=(e.y2_pct||e.y_pct)/100*IH;
            ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.stroke();
            const ang=Math.atan2(y2-y,x2-x),hd=12;ctx.beginPath();ctx.moveTo(x2,y2);ctx.lineTo(x2-hd*Math.cos(ang-0.5),y2-hd*Math.sin(ang-0.5));ctx.lineTo(x2-hd*Math.cos(ang+0.5),y2-hd*Math.sin(ang+0.5));ctx.closePath();ctx.fillStyle=c;ctx.fill();
            if(isSel){{[{{x:x,y:y}},{{x:x2,y:y2}}].forEach(pt=>{{ctx.beginPath();ctx.arc(pt.x,pt.y,5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.stroke()}})}}
            const mx=(x+x2)/2,my=(y+y2)/2;ctx.font='bold 10px system-ui';const txt=e.label||'';const tw=ctx.measureText(txt).width;
            ctx.fillStyle='rgba(10,25,41,0.8)';ctx.fillRect(mx-tw/2-3,my-16,tw+6,14);ctx.fillStyle=c;ctx.fillText(txt,mx-tw/2,my-5);
          }}
        }});
        sts.textContent=els.length+' markører | '+(tool==='select'?'Velg/flytt':TM[tool]?TM[tool][2]:tool);
      }}
      function resize(){{const mw=cv.parentElement.clientWidth||900,r=Math.min(1,mw/img.width);IW=Math.max(1,Math.round(img.width*r));IH=Math.max(1,Math.round(img.height*r));cv.width=IW;cv.height=IH;render()}}
      function gP(e){{const r=cv.getBoundingClientRect();return{{x:(e.clientX-r.left)*(cv.width/r.width),y:(e.clientY-r.top)*(cv.height/r.height)}}}}
      function hit(x,y){{for(let i=els.length-1;i>=0;i--){{const e=els[i],ex=(e.x_pct/100)*IW,ey=(e.y_pct/100)*IH;if(dist(x,y,ex,ey)<IW*0.04)return i}}return -1}}
      cv.addEventListener('mousedown',function(e){{const p=gP(e);sp=p;if(tool==='select'){{sel=hit(p.x,p.y);if(sel>=0){{drag='move';lb.value=els[sel].label||''}}else lb.value=''}}else{{const tm=TM[tool];if(!tm)return;const xp=p.x/IW*100,yp=p.y/IH*100;if(tm[0]==='circle'){{els.push({{id:uid(),type:'circle',x_pct:xp,y_pct:yp,color:tm[1],label:tm[2]}})}}else if(tm[0]==='rect'){{els.push({{id:uid(),type:'rect',x_pct:xp,y_pct:yp,w_pct:8,h_pct:6,color:tm[1],label:tm[2]}});drag='drawRect'}}else{{els.push({{id:uid(),type:'arrow',x_pct:xp,y_pct:yp,x2_pct:xp,y2_pct:yp,color:tm[1],label:tm[2]}});drag='drawArrow'}}sel=els.length-1;lb.value=tm[2]}}render()}});
      cv.addEventListener('mousemove',function(e){{if(sel<0||!drag||!sp)return;const p=gP(e),el=els[sel];if(drag==='move'){{const dx=(p.x-sp.x)/IW*100,dy=(p.y-sp.y)/IH*100;el.x_pct+=dx;el.y_pct+=dy;sp=p}}else if(drag==='drawRect'){{el.w_pct=(p.x/IW*100)-el.x_pct;el.h_pct=(p.y/IH*100)-el.y_pct}}else if(drag==='drawArrow'){{el.x2_pct=p.x/IW*100;el.y2_pct=p.y/IH*100}}render()}});
      window.addEventListener('mouseup',function(){{drag=null;render()}});
      document.addEventListener('keydown',function(e){{if(e.target.tagName==='INPUT')return;if((e.key==='Delete'||e.key==='Backspace')&&sel>=0){{els.splice(sel,1);sel=-1;render()}}}});
      return{{
        setTool:function(t){{tool=t;sel=-1;document.querySelectorAll('.tt').forEach(b=>b.classList.remove('active'));const btn=document.getElementById('tt_'+t);if(btn)btn.classList.add('active');cv.style.cursor=t==='select'?'default':'crosshair'}},
        del:function(){{if(sel>=0){{els.splice(sel,1);sel=-1;lb.value='';render()}}}},
        updLbl:function(v){{if(sel>=0&&els[sel]){{els[sel].label=v;render()}}}},
        save:function(){{ex.value=JSON.stringify(els,null,2);try{{const ta=window.parent.document.querySelector('textarea[aria-label="'+'{bridge_label}'+'"]');if(!ta)throw 0;const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(ta,ex.value);ta.dispatchEvent(new Event('input',{{bubbles:true}}));ta.dispatchEvent(new Event('change',{{bubbles:true}}));sts.textContent='Lagret!'}}catch(e){{sts.textContent='Kopier JSON manuelt.';ex.style.display='block'}}}}
      }};
      img.onload=resize;window.addEventListener('resize',resize);
    }})();
    </script>
    """
    components.html(f"<!-- {component_key} -->\n" + html, height=700, scrolling=False)

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

def clean_pdf_text(text):
    if not text: return ""
    # Sikkerhetsnett: Bytter ut spesialtegn for å unngå font-krasj i PDF
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "-"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. PREMIUM CSS ---
st.markdown("""
<style>
    :root { --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label, .stMultiSelect label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. SESSION STATE & HARDDISK GJENOPPRETTING ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}

if st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    st.info("RITra-agenten trenger kontekst om prosjektet for å generere et relevant trafikknotat.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# --- 4. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
pd_state = st.session_state.project_data

# --- 5. DYNAMISK PDF MOTOR (CORPORATE EDITION) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RITra-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200); self.line(25, 25, 185, 25); self.set_y(30)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')
    def check_space(self, height):
        if self.get_y() + height > 270: 
            self.add_page(); self.set_margins(25, 25, 25); self.set_x(25)

def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50) 
    
    pdf.set_y(95); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 12, clean_pdf_text("TRAFIKKNOTAT OG MOBILITET (RITra)"), 0, 'L')
    pdf.ln(2)
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 10, clean_pdf_text(f"PROSJEKT: {pdf.p_name}"), 0, 'L'); pdf.ln(25)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RITra AI Engine"), ("REGELVERK:", "Norsk Standard / TEK17")]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    toc = [
        "1. OPPSUMMERING OG KONKLUSJON", "2. VURDERING AV GRUNNLAG", 
        "3. VIKTIGSTE FORUTSETNINGER", "4. TRAFIKALT HOVEDGREP", 
        "5. KRITISKE PUNKTER (Sikkerhet, kapasitet, logistikk)", "6. RISIKO OG USIKKERHET",
        "7. ANBEFALT LØSNING / ALTERNATIVER", "8. BEHOV FOR VIDERE AVKLARINGER"
    ]
    for t in toc:
        pdf.set_x(25); pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(3)
            continue
        
        # Fjerner markdown-stjerner
        safe_text = line.replace('**', '').replace('_', '')
        safe_text = clean_pdf_text(safe_text)
        
        # Hovedoverskrifter (H1)
        if safe_text.startswith('# ') or re.match(r'^\d+\.\s[A-Z]', safe_text):
            pdf.check_space(30)
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(0, 7, safe_text.replace('#', '').strip())
            pdf.ln(2)
            
        # Underoverskrifter (H2/H3)
        elif safe_text.startswith('## ') or safe_text.startswith('### '):
            pdf.check_space(20)
            pdf.ln(5)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 11)
            pdf.set_text_color(50, 65, 85)
            pdf.multi_cell(0, 6, safe_text.replace('#', '').strip().upper())
            pdf.ln(1)
            
        else:
            # MAGISK CORPORATE PARSER FOR NØKKELORD
            kv_match = re.match(r'^(Tema|Vurdering|Risiko|Tiltak|Konsekvens|Anbefaling|Krav|Kapasitet|Status):\s*(.*)', safe_text, re.IGNORECASE)
            
            if kv_match:
                key = kv_match.group(1).upper()
                val = kv_match.group(2)
                
                pdf.check_space(15)
                # Tegner en lekker, fet, gråblå "Label"
                pdf.set_x(30)
                pdf.set_font('Helvetica', 'B', 8)
                pdf.set_text_color(120, 140, 160)
                pdf.cell(0, 5, key, 0, 1)
                
                # Tegner selve innholdet rent og ryddig under
                pdf.set_x(30)
                pdf.set_font('Helvetica', '', 10)
                pdf.set_text_color(40, 40, 40)
                pdf.multi_cell(0, 5, val)
                pdf.ln(2)
                
            # Gjør om stygge bindestreker til trygge ASCII-streker med innrykk
            elif safe_text.startswith('- ') or safe_text.startswith('* '):
                pdf.check_space(10)
                pdf.set_x(30)
                pdf.set_font('Helvetica', '', 10)
                pdf.set_text_color(40, 40, 40)
                bullet_text = "- " + safe_text[2:] 
                pdf.multi_cell(0, 5, bullet_text)
                pdf.ln(1)
                
            # Vanlig brødtekst
            else:
                pdf.check_space(10)
                pdf.set_x(25)
                pdf.set_font('Helvetica', '', 10)
                pdf.set_text_color(40, 40, 40)
                pdf.multi_cell(0, 5, safe_text)
                pdf.ln(1)

    if maps and len(maps) > 0:
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT BILDEDOKUMENTASJON", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG", quality=85)
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                    pdf.image(tmp.name, x=105-(img_w/2), y=pdf.get_y(), w=img_w)
                else:
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Dokument visuelt analysert av RITra-agenten."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))

# --- 6. STREAMLIT UI ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🚦 Trafikknotat & Mobilitet (RITra)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for generering av tidligfase trafikkanalyser, adkomstvurdering og mobilitetsplaner.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state.get('p_name')}** er automatisk synkronisert (SSOT).")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state.get("p_name"), disabled=True)
    b_type = c2.text_input("Bygningstype", value=pd_state.get("b_type"), disabled=True)
    c3, c4 = st.columns(2)
    bta = st.number_input("Bruttoareal (BTA m2)", value=int(pd_state.get("bta", 0)), disabled=True)
    tomteareal = st.number_input("Tomteareal (m2)", value=int(pd_state.get("tomteareal", 0)), disabled=True)

with st.expander("2. Trafikale Rammebetingelser", expanded=True):
    st.info("Angi krav fra reguleringsplan/kommune. Agenten vil vurdere tegningene opp mot disse målene.")
    c5, c6 = st.columns(2)
    bil_norm = c5.selectbox("Parkeringsnorm Bil", ["Minimumsnorm (Krever mange plasser)", "Maksimumsnorm (Restriktiv, sentrumsnært)", "Ingen spesifikke krav / Uavklart"], index=1)
    sykkel_norm = c6.selectbox("Parkeringsnorm Sykkel", ["Høy (Krav til trygg innendørs parkering og vask)", "Standard", "Ingen krav"], index=0)
    kollektiv = st.selectbox("Kollektivdekning i området", ["Veldig god (Nær knutepunkt)", "Middels (Gangavstand til buss/bane)", "Dårlig (Avhengig av bil)"], index=1)

    # ── NVDB og Geodata ──
    st.markdown("##### Trafikkdata fra NVDB og støykart")
    nvdb_col1, nvdb_col2, nvdb_col3 = st.columns(3)
    nvdb_lat = nvdb_col1.number_input("Breddegrad", value=63.43, format="%.4f", key="nvdb_lat")
    nvdb_lon = nvdb_col2.number_input("Lengdegrad", value=10.40, format="%.4f", key="nvdb_lon")
    nvdb_radius = nvdb_col3.number_input("Søkeradius (m)", value=300, min_value=50, max_value=1000, key="nvdb_radius")
    
    fetch_col1, fetch_col2 = st.columns(2)
    if fetch_col1.button("📡 Hent trafikkdata fra NVDB", key="fetch_nvdb", use_container_width=True):
        with st.spinner("Henter ÅDT og fartsgrense fra Statens vegvesen NVDB..."):
            nvdb_data = fetch_nvdb_traffic(nvdb_lat, nvdb_lon, nvdb_radius)
            st.session_state["nvdb_data"] = nvdb_data
            if nvdb_data.get("adt"):
                st.success(f"ÅDT: **{nvdb_data['adt']}** kjt/døgn | Fartsgrense: **{nvdb_data.get('fartsgrense', '?')} km/t**")
            else:
                st.warning("Fant ikke ÅDT-data i NVDB for denne posisjonen. Prøv å justere koordinatene.")
    
    if fetch_col2.button("📡 Hent støykart fra Geodata", key="fetch_stoy_traffic", use_container_width=True):
        with st.spinner("Henter veitrafikkstøy fra Geodata Online..."):
            stoy_img = fetch_stoykart_for_traffic(nvdb_lat, nvdb_lon, buffer_m=nvdb_radius + 100)
            if stoy_img:
                st.session_state["traffic_stoykart"] = stoy_img
                st.success("Støykart hentet!")
            else:
                st.warning("Kunne ikke hente støykart.")
    
    if "nvdb_data" in st.session_state and st.session_state["nvdb_data"].get("adt"):
        nd = st.session_state["nvdb_data"]
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("ÅDT (total)", f"{nd.get('adt', '?')} kjt/døgn")
        mc2.metric("Fartsgrense", f"{nd.get('fartsgrense', '?')} km/t")
        mc3.metric("Vegkategori", nd.get("vegkategori", "?"))
    
    if "traffic_stoykart" in st.session_state:
        st.image(st.session_state["traffic_stoykart"], caption="Støykart veitrafikk (Geonorge)", use_container_width=True)

with st.expander("3. Visuelt Grunnlag (Situasjonsplan / Kjellerplan)", expanded=True):
    st.info("Last opp situasjonsplan, utomhusplan eller kjellerplan. Agenten vil vurdere innkjøring, svingradier for renovasjon/varelevering og konflikter med myke trafikanter.")
    
    saved_images = []
    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.glob("*.jpg")):
            saved_images.append(Image.open(p).convert("RGB"))
            
    if len(saved_images) > 0:
        st.success(f"📎 Fant {len(saved_images)} felles arkitekttegninger fra Project Setup. Disse vurderes automatisk for trafikale løsninger!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp situasjonsplan under.")
        
    files = st.file_uploader("Last opp tegninger/kart (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Generer Trafikknotat", type="primary", use_container_width=True):
    
    images_for_ai = saved_images.copy()
        
    if files:
        with st.spinner("📐 Leser ut supplerende filer..."):
            try:
                for f in files: 
                    f.seek(0)
                    if f.name.lower().endswith('pdf'):
                        if fitz is not None: 
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            for page_num in range(min(4, len(doc))): 
                                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                                img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                img.thumbnail((1200, 1200))
                                images_for_ai.append(img)
                            doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        img.thumbnail((1200, 1200))
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til RITra-agenten for vurdering.")
                
    with st.spinner(f"🤖 Vurderer adkomst, logistikk og mobilitet..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # Inkluder støykart hvis tilgjengelig
        if "traffic_stoykart" in st.session_state:
            stoy_rgb = st.session_state["traffic_stoykart"].convert("RGB")
            stoy_rgb.thumbnail((1200, 1200))
            images_for_ai.insert(0, stoy_rgb)
        
        nvdb_info = ""
        if "nvdb_data" in st.session_state:
            nd = st.session_state["nvdb_data"]
            if nd.get("adt"):
                nvdb_info = f"""
        TRAFIKKDATA FRA NVDB (Statens vegvesen):
        - ÅDT (total): {nd.get('adt', '?')} kjt/døgn
        - ÅDT (tunge): {nd.get('adt_tunge', '?')} kjt/døgn
        - Fartsgrense: {nd.get('fartsgrense', '?')} km/t
        - Vegkategori: {nd.get('vegkategori', '?')}
        - Vegnummer: {nd.get('vegnummer', '?')}
        Bruk disse FAKTISKE tallene i rapporten i stedet for å estimere.
        """

        prompt_text = f"""
        Du er en senior trafikkrådgiver (RITra) for norske bygge- og eiendomsprosjekter. Din oppgave er UTELUKKENDE å skrive innholdet i et formelt Trafikknotat.

        PROSJEKT: {p_name} ({pd_state.get('b_type')}, Bygg {pd_state.get('bta')} m2, Tomt {pd_state.get('tomteareal')} m2).
        LOKASJON: {pd_state.get('adresse')}.
        {nvdb_info}
        {"STØYKART: Første bilde er et offisielt veitrafikkstøykart fra Geonorge. Bruk dette for å vurdere støybelastning på fasader." if "traffic_stoykart" in st.session_state else ""}
        
        KUNDENS PROSJEKTBESKRIVELSE: 
        "{pd_state.get('p_desc', '')}"
        
        PARKERING OG MOBILITETSKRAV:
        - Bilparkering: {bil_norm}
        - Sykkelparkering: {sykkel_norm}
        - Kollektivdekning: {kollektiv}
        
        EKSTREMT VIKTIGE REGLER:
        1. START direkte med "# 1. OPPSUMMERING OG KONKLUSJON". IKKE skriv introduksjon.
        2. Skriv som en fagperson som har studert prosjektet — aldri nevn bilder/filer.
        3. IKKE bruk Markdown-tabeller (forbudt tegn: "|").
        4. {"Bruk ÅDT=" + str(st.session_state.get('nvdb_data', {}).get('adt', '?')) + " og fartsgrense=" + str(st.session_state.get('nvdb_data', {}).get('fartsgrense', '?')) + " km/t som FAKTISKE tall." if st.session_state.get('nvdb_data', {}).get('adt') else "Estimer ÅDT basert på lokasjon og vegtype."}
        
        MANDAT:
        - Vurder adkomst, intern logistikk (varemottak, renovasjon), parkering, og myke trafikanter.
        - Ta spesielt hensyn til konflikter mellom biltrafikk og myke trafikanter.
        - Beregn parkeringsbehov basert på normen og BTA.
        - Vurder siktforhold ved avkjørsler (Håndbok N100).
        
        STRUKTUR:
        # 1. OPPSUMMERING OG KONKLUSJON
        # 2. VURDERING AV GRUNNLAG
        # 3. VIKTIGSTE FORUTSETNINGER (inkl. ÅDT og fartsgrense)
        # 4. TRAFIKALT HOVEDGREP
        # 5. KRITISKE PUNKTER
        # 6. RISIKO OG USIKKERHET
        # 7. ANBEFALT LØSNING / MOBILITETSPLAN
        # 8. BEHOV FOR VIDERE AVKLARINGER

        VIKTIG — JSON FOR TRAFIKKMARKØRER:
        Returner HELT NEDERST en JSON-blokk med markører for situasjonsplanen:
        ```json
        [
          {{"type": "arrow", "x_pct": 30, "y_pct": 50, "x2_pct": 40, "y2_pct": 50, "color": "#22c55e", "label": "Adkomst bil"}},
          {{"type": "circle", "x_pct": 45, "y_pct": 60, "color": "#ef4444", "label": "Konfliktpunkt"}},
          {{"type": "rect", "x_pct": 60, "y_pct": 70, "w_pct": 15, "h_pct": 10, "color": "rgba(6,182,212,0.3)", "label": "P-kjeller innkjøring"}}
        ]
        ```
        Typer: arrow (pil), circle (punkt), rect (område). Koordinater i prosent (0-100).
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            
            # ── Parse trafikkmarkører fra JSON ──
            ai_raw = res.text
            clean_text = ai_raw
            traffic_markers = []
            json_match = re.search(r'```json\s*(.*?)\s*```', ai_raw, re.DOTALL)
            if json_match:
                try:
                    traffic_markers = json.loads(json_match.group(1))
                    clean_text = re.sub(r'```json\s*.*?\s*```', '', ai_raw, flags=re.DOTALL)
                except Exception:
                    pass
            
            st.session_state["traffic_markers"] = traffic_markers
            st.session_state["traffic_images"] = images_for_ai
            st.session_state["traffic_ai_text"] = clean_text
            
            with st.spinner("Kompilerer Trafikk-PDF med corporate design..."):
                pdf_data = create_full_report_pdf(p_name, pd_state.get('c_name', ''), clean_text, images_for_ai)
                
                # --- SENDER TIL QA-KØ ---
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-RITRA{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state.get('p_name', 'Nytt Prosjekt'),
                    "module": "Trafikk & Mobilitet",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior RITra",
                    "status": "Pending Engineering Review",
                    "class": "badge-roadmap",
                    "pdf_bytes": pdf_data
                }

                # Lagre til disk for Dashboard
                try:
                    report_dir = DB_DIR / "reports"
                    report_dir.mkdir(exist_ok=True)
                    pdf_path = report_dir / f"Builtly_RITra_{p_name}.pdf"
                    pdf_path.write_bytes(pdf_data)
                    
                    reviews_file = DB_DIR / "pending_reviews.json"
                    existing = {}
                    if reviews_file.exists():
                        try: existing = json.loads(reviews_file.read_text(encoding="utf-8"))
                        except Exception: existing = {}
                    existing[doc_id] = {
                        "title": pd_state.get('p_name', ''), "module": "Trafikk & Mobilitet",
                        "drafter": "Builtly AI", "reviewer": "Senior RITra",
                        "status": "Pending Engineering Review", "class": "badge-roadmap",
                        "pdf_file": str(pdf_path), "timestamp": datetime.now().isoformat(),
                    }
                    reviews_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

            st.session_state.generated_ritra_pdf = pdf_data
            st.session_state.generated_ritra_filename = f"Builtly_RITra_{p_name}.pdf"

            # Lagre til Supabase dashboard
            try:
                from builtly_auth import save_report
                save_report(
                    project_name=pd_state.get("p_name", p_name),
                    report_name=f"Trafikk — Builtly_RITra_{p_name}.pdf",
                    module="RITra (Trafikk)",
                    file_path=f"Builtly_RITra_{p_name}.pdf",
                )
            except Exception:
                pass
            st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_ritra_pdf" in st.session_state:
    st.success("✅ Trafikknotat er ferdigstilt og sendt til QA-køen for godkjenning!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Trafikknotat", st.session_state.generated_ritra_pdf, st.session_state.generated_ritra_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å vurdere", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))

    # ── Interaktiv redigering av trafikkmarkører ──
    if st.session_state.get("traffic_markers") is not None and st.session_state.get("traffic_images"):
        with st.expander("Rediger trafikkmarkører (adkomst, konflikter, sikt)", expanded=False):
            st.caption("Flytt, legg til eller slett markører for adkomst, innkjøring, konfliktpunkter, parkering etc.")
            
            bridge_label = "TRAFFIC_MARKER_BRIDGE"
            images_data = [{"image": img, "markers": st.session_state.get("traffic_markers", [])} for img in st.session_state.get("traffic_images", [])[:1]]
            
            render_traffic_editor(images_data, bridge_label=bridge_label, component_key="traffic_editor_main")
            
            marker_buffer_key = "traffic_marker_buffer"
            if marker_buffer_key not in st.session_state:
                st.session_state[marker_buffer_key] = json.dumps(st.session_state.get("traffic_markers", []), ensure_ascii=False, indent=2)
            
            st.text_area("Markør-data", key=marker_buffer_key, height=80, label_visibility="collapsed")
            
            ec = st.columns(2)
            if ec[0].button("Bruk endringer", key="traffic_apply", use_container_width=True):
                try:
                    new_markers = json.loads(st.session_state.get(marker_buffer_key, "[]") or "[]")
                    st.session_state["traffic_markers"] = new_markers
                    st.success("Markører oppdatert!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Feil: {exc}")
            if ec[1].button("Nullstill", key="traffic_reset", use_container_width=True):
                st.session_state[marker_buffer_key] = json.dumps(st.session_state.get("traffic_markers", []), ensure_ascii=False, indent=2)
                st.rerun()
