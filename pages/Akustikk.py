import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import json
import io
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Akustikk (RIAku) | Builtly", layout="wide", initial_sidebar_state="collapsed")

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

# --- Auth integration (for saving reports to user account) ---
try:
    import builtly_auth
    _HAS_AUTH = True
except ImportError:
    _HAS_AUTH = False

if _HAS_AUTH:
    if not st.session_state.get("user_authenticated"):
        builtly_auth.try_restore_from_browser()
    elif st.session_state.get("_sb_access_token"):
        builtly_auth.restore_session()


try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import streamlit.components.v1 as components


# ── Støykart — åpne WMS-tjenester (Geonorge) + Geodata Online fallback ──

# Correct WMS endpoints for Norwegian noise maps:
# - Vei: Statens vegvesen T-1442 støyvarselkart via Geonorge WMS
# - Bane: Bane NOR støysoner via Geonorge WMS
# - Fly: Avinor støysoner lufthavn via Geonorge WMS
# - Geodata Online DOK Forurensning MapServer (contains noise sublayers, requires token)

STOY_DB_RANGES = {
    "Lden 55-60 dB": {"min": 55, "max": 60, "color": "#22c55e"},
    "Lden 60-65 dB": {"min": 60, "max": 65, "color": "#f59e0b"},
    "Lden 65-70 dB": {"min": 65, "max": 70, "color": "#ef4444"},
    "Lden 70-75 dB": {"min": 70, "max": 75, "color": "#dc2626"},
    "Lden >75 dB":   {"min": 75, "max": 999, "color": "#991b1b"},
}


def _latlon_to_utm33(lat: float, lon: float) -> tuple:
    """Convert lat/lon (WGS84) to UTM33N (EPSG:25833) for Norwegian maps."""
    import math
    k0, a, e, lon0 = 0.9996, 6378137.0, 0.0818192, 15.0
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0_rad = math.radians(lon0)
    N = a / math.sqrt(1 - e**2 * math.sin(lat_rad)**2)
    T = math.tan(lat_rad)**2
    C = (e**2 / (1 - e**2)) * math.cos(lat_rad)**2
    A_val = (lon_rad - lon0_rad) * math.cos(lat_rad)
    M = a * ((1 - e**2/4 - 3*e**4/64) * lat_rad
            - (3*e**2/8 + 3*e**4/32) * math.sin(2*lat_rad)
            + (15*e**4/256) * math.sin(4*lat_rad))
    easting = k0 * N * (A_val + (1-T+C) * A_val**3/6) + 500000
    northing = k0 * (M + N * math.tan(lat_rad) * (A_val**2/2))
    return easting, northing


def _fetch_wms_image(base_url: str, layers_to_try: list, bbox_25833: tuple,
                     width: int = 800, height: int = 600, auth=None, extra_params=None):
    """Try WMS GetMap with multiple layer name candidates."""
    xmin, ymin, xmax, ymax = bbox_25833
    for layer in layers_to_try:
        params = {
            "service": "WMS", "request": "GetMap", "version": "1.1.1",
            "layers": layer, "styles": "",
            "srs": "EPSG:25833",
            "bbox": f"{xmin},{ymin},{xmax},{ymax}",
            "width": str(width), "height": str(height),
            "format": "image/png", "transparent": "true",
        }
        if extra_params:
            params.update(extra_params)
        try:
            resp = requests.get(base_url, params=params, timeout=15, auth=auth)
            if resp.status_code == 200 and len(resp.content) > 2000:
                if b"ServiceException" not in resp.content[:1000]:
                    return Image.open(io.BytesIO(resp.content)).convert("RGBA"), layer
        except Exception:
            continue
    return None, None


def _fetch_arcgis_image(service_url: str, bbox_25833: tuple,
                        width: int = 800, height: int = 600, token: str = None):
    """Fetch image from ArcGIS REST MapServer export."""
    xmin, ymin, xmax, ymax = bbox_25833
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "25833", "imageSR": "25833",
        "size": f"{width},{height}",
        "format": "png", "transparent": "true", "f": "image",
    }
    if token:
        params["token"] = token
    try:
        resp = requests.get(f"{service_url}/export", params=params, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 1000:
            ctype = resp.headers.get("content-type", "")
            if "image" in ctype or resp.content[:4] in (b"\x89PNG", b"\xff\xd8\xff"):
                return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception:
        pass
    return None


def fetch_stoykart_image(lat: float, lon: float, kilde: str = "vei",
                         width: int = 800, height: int = 600, buffer_m: int = 300):
    """Hent støykart-bilde fra åpne WMS-tjenester og Geodata Online.
    
    Prøver kilder i rekkefølge:
    1. Geonorge WMS (åpen, T-1442 støyvarselkart)
    2. Geodata Online DOK Forurensning MapServer (token-autentisert)
    """
    if not HAS_REQUESTS:
        return None, "requests-biblioteket mangler"

    try:
        easting, northing = _latlon_to_utm33(lat, lon)
        bbox = (easting - buffer_m, northing - buffer_m,
                easting + buffer_m, northing + buffer_m)
    except Exception:
        return None, "Koordinatkonvertering feilet"

    errors = []

    # Get Geodata Online credentials for authenticated WMS access
    gdo_user = os.environ.get("GEODATA_ONLINE_USER", "")
    gdo_pass = os.environ.get("GEODATA_ONLINE_PASS", "")
    gdo_auth = (gdo_user, gdo_pass) if gdo_user and gdo_pass else None

    # --- Source 1: Geonorge WMS with Geodata Online auth (skwms1 = secure WMS) ---
    wms_sources = {
        "vei": [
            ("https://wms.geonorge.no/skwms1/wms.stoykartleggingveg",
             ["Støyvarselkart_Lden", "stoyvarselkart_lden", "Støyvarselkart", "0", "1", "2"]),
        ],
        "bane": [
            ("https://wms.geonorge.no/skwms1/wms.stoysonerjernbanenett",
             ["Støysoner", "stoysoner", "0", "1"]),
        ],
        "fly": [
            ("https://wms.geonorge.no/skwms1/wms.stoysonerlufthavn",
             ["Støysoner", "stoysoner", "0", "1"]),
        ],
        "industri": [
            ("https://wms.geonorge.no/skwms1/wms.stoykartleggingveg",
             ["Støyvarselkart_Lden", "0"]),
        ],
    }

    # Try with Geodata Online HTTP Basic Auth (skwms1 requires Norge digitalt credentials)
    for wms_url, layer_candidates in wms_sources.get(kilde.lower(), []):
        img, hit_layer = _fetch_wms_image(wms_url, layer_candidates, bbox, width, height, auth=gdo_auth)
        if img:
            return img, None
        # Also try without auth (some WMS services may be open)
        if gdo_auth:
            img, hit_layer = _fetch_wms_image(wms_url, layer_candidates, bbox, width, height)
            if img:
                return img, None
        errors.append(f"WMS {wms_url.split('/')[-1]}: ingen treff")

    # --- Source 2: Geodata Online DOK Forurensning MapServer (token-autentisert) ---
    try:
        from geodata_client import GeodataOnlineClient
        gdo = GeodataOnlineClient()
        if gdo.is_available():
            try:
                token = gdo.get_token()
                dok_url = "https://services.geodataonline.no/arcgis/rest/services/Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer"
                img = _fetch_arcgis_image(dok_url, bbox, width, height, token=token)
                if img:
                    return img, None
                errors.append("Geodata Online DOK Forurensning: ingen støydata i dette området")
            except Exception as e:
                errors.append(f"Geodata Online: {str(e)[:60]}")
    except ImportError:
        pass

    return None, f"Kunne ikke hente støykart: {'; '.join(errors) if errors else 'Ingen kilder tilgjengelig'}. Last opp manuelt under."


def fetch_stoykart_contours(lat: float, lon: float, kilde: str = "vei", buffer_m: int = 300):
    """Hent støykontur-data (feature query) fra Geodata Online DOK Forurensning."""
    if not HAS_REQUESTS:
        return []

    try:
        easting, northing = _latlon_to_utm33(lat, lon)
    except Exception:
        return []

    # Get Geodata Online token if available
    gdo_token = None
    try:
        from geodata_client import GeodataOnlineClient
        _gdo_client = GeodataOnlineClient()
        if _gdo_client.is_available():
            gdo_token = _gdo_client.get_token()
    except Exception:
        pass

    if not gdo_token:
        return []

    service_url = "https://services.geodataonline.no/arcgis/rest/services/Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer"
    params = {
        "geometry": f"{easting-buffer_m},{northing-buffer_m},{easting+buffer_m},{northing+buffer_m}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "25833",
        "outSR": "25833",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
        "token": gdo_token,
    }

    contours = []
    for layer_id in [0, 1, 2, 3, 4, 5]:
        try:
            resp = requests.get(f"{service_url}/{layer_id}/query", params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get("features", []):
                    attrs = feat.get("attributes", {})
                    db_val = attrs.get("DB_LOW", attrs.get("db_low", attrs.get("Lden", attrs.get("dB", ""))))
                    if db_val:
                        contours.append({
                            "layer": layer_id,
                            "db": str(db_val),
                            "name": attrs.get("NAVN", attrs.get("navn", "")),
                            "type": kilde,
                        })
        except Exception:
            continue

    return contours


# ── Interaktiv akustikk-editor ───────────────────────────────────────

def render_acoustic_editor(images_with_markers, bridge_label: str, component_key: str):
    """Render interaktiv editor for dB-sirkler og støysoner."""
    if not images_with_markers:
        return
    
    img_data = images_with_markers[0]
    image = img_data["image"]
    markers = img_data.get("markers", [])
    
    buf = io.BytesIO()
    thumb = image.copy()
    if max(thumb.size) > 1400:
        ratio = 1400 / max(thumb.size)
        thumb = thumb.resize((int(thumb.width * ratio), int(thumb.height * ratio)), Image.LANCZOS)
    thumb.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    img_uri = f"data:image/png;base64,{b64}"
    
    # Escape bridge_label into JS
    bl_escaped = bridge_label.replace("'", "\\'")
    
    marker_json = json.dumps(markers, ensure_ascii=False)
    
    html = f"""
    <div style="font-family:system-ui;color:#e2e8f0">
      <div style="display:flex;gap:3px;padding:6px 8px;background:#0a1929;border:1px solid #1a2a3a;border-radius:10px 10px 0 0;flex-wrap:wrap;align-items:center">
        <button onclick="AE.setTool('select')" id="at_select" class="at active" style="--tc:#38bdf8">Velg/Flytt</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="AE.setTool('db_red')" id="at_db_red" class="at" style="--tc:#ef4444">● &gt;65 dB</button>
        <button onclick="AE.setTool('db_yellow')" id="at_db_yellow" class="at" style="--tc:#f59e0b">● 55-65 dB</button>
        <button onclick="AE.setTool('db_green')" id="at_db_green" class="at" style="--tc:#22c55e">● &lt;55 dB</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="AE.setTool('zone')" id="at_zone" class="at" style="--tc:#a78bfa">▭ Støysone</button>
        <button onclick="AE.setTool('barrier')" id="at_barrier" class="at" style="--tc:#94a3b8">▬ Støyskjerm</button>
        <button onclick="AE.setTool('facade')" id="at_facade" class="at" style="--tc:#f59e0b">▬ Utsatt fasade</button>
        <span style="flex:1"></span>
        <button onclick="AE.deleteSelected()" style="background:rgba(239,68,68,0.15);color:#ef4444;border-color:rgba(239,68,68,0.3)">Slett</button>
        <button onclick="AE.save()" style="background:#38bdf8;color:#06111a;font-weight:700;border-color:#38bdf8">Lagre</button>
      </div>
      <canvas id="AC" style="width:100%;display:block;background:#0d1b2a;border:1px solid #1a2a3a;border-top:none;cursor:crosshair"></canvas>
      <div style="display:flex;gap:8px;padding:5px 10px;background:#0a1929;border:1px solid #1a2a3a;border-top:none;border-radius:0 0 10px 10px;align-items:center">
        <span id="AE_status" style="font-size:10px;color:#475569;font-family:monospace;flex:1"></span>
        <label style="font-size:10px;color:#64748b">dB-verdi:</label>
        <input id="AE_db" type="text" value="65" style="background:#1a2a3a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;padding:2px 8px;font-size:11px;width:80px" oninput="AE.updateDb(this.value)"/>
      </div>
      <textarea id="AE_export" style="display:none"></textarea>
    </div>
    <style>.at{{background:rgba(30,41,59,0.8);color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer;font-weight:500}}.at:hover{{background:rgba(56,189,248,0.1);color:#e2e8f0}}.at.active{{background:var(--tc,#38bdf8);color:#fff;font-weight:700;border-color:var(--tc)}}</style>
    <script>
    window.AE=(function(){{
      var BRIDGE_LABEL='{bl_escaped}';
      var cv=document.getElementById('AC'),ctx=cv.getContext('2d'),sts=document.getElementById('AE_status'),dbIn=document.getElementById('AE_db'),ex=document.getElementById('AE_export');
      var img=new Image();
      var els={marker_json};
      els=els.map(function(m,i){{m.id='m'+i;return m}});
      var tool='select',sel=-1,drag=null,sp=null,IW=0,IH=0;
      var TMAP={{db_red:['circle','#ef4444','70'],db_yellow:['circle','#f59e0b','60'],db_green:['circle','#22c55e','50'],zone:['rect','rgba(168,85,247,0.3)','Stoysone'],barrier:['line','#94a3b8','Stoyskjerm'],facade:['line','#f59e0b','Utsatt fasade']}};
      function uid(){{return Math.random().toString(36).slice(2,10)}}
      function dist(a,b,c,d){{return Math.sqrt((c-a)*(c-a)+(d-b)*(d-b))}}

      function render(){{
        if(!img.complete||IW<1)return;
        ctx.clearRect(0,0,cv.width,cv.height);
        ctx.drawImage(img,0,0,IW,IH);
        for(var i=0;i<els.length;i++){{
          var e=els[i],isSel=i===sel;
          var x=(e.x_pct/100)*IW,y=(e.y_pct/100)*IH;
          var c=e.color||'#ef4444';
          ctx.lineWidth=isSel?4:2.5;ctx.strokeStyle=c;
          if(e.type==='circle'||!e.type){{
            var r=Math.max(8,IW*0.015);
            ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.stroke();
            if(isSel){{ctx.fillStyle='rgba(255,255,255,0.15)';ctx.fill()}}
            var txt=(e.db||'??');
            ctx.font='bold '+Math.max(8,IW*0.01)+'px system-ui';
            var tw=ctx.measureText(txt).width;
            // Number inside circle
            ctx.fillStyle=c;ctx.fillRect(x-tw/2-2,y-5,tw+4,11);
            ctx.fillStyle='#fff';ctx.fillText(txt,x-tw/2,y+4);
            // Label below
            if(e.label){{ctx.font=Math.max(7,IW*0.008)+'px system-ui';ctx.fillStyle=c;ctx.fillText(e.label,x-r,y+r+10)}}
          }}else if(e.type==='rect'){{
            var w=(e.w_pct||10)/100*IW,h=(e.h_pct||8)/100*IH;
            ctx.fillStyle=c;ctx.fillRect(x,y,w,h);
            ctx.strokeStyle='#a78bfa';ctx.lineWidth=isSel?3:1.5;ctx.strokeRect(x,y,w,h);
            ctx.font='bold 11px system-ui';ctx.fillStyle='#a78bfa';ctx.fillText(e.label||'Sone',x+4,y+14);
            if(isSel){{ctx.fillStyle='#fff';ctx.strokeStyle='#a78bfa';ctx.lineWidth=2;ctx.beginPath();ctx.rect(x+w-5,y+h-5,10,10);ctx.fill();ctx.stroke()}}
          }}else if(e.type==='line'){{
            var x2=(e.x2_pct||e.x_pct+10)/100*IW,y2=(e.y2_pct||e.y_pct)/100*IH;
            ctx.strokeStyle=c;ctx.lineWidth=isSel?5:3;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.stroke();
            ctx.font='bold 10px system-ui';ctx.fillStyle=c;ctx.fillText(e.label||'',((x+x2)/2),((y+y2)/2)-6);
            if(isSel){{[{{x:x,y:y}},{{x:x2,y:y2}}].forEach(function(pt){{ctx.beginPath();ctx.arc(pt.x,pt.y,5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.stroke()}})}}
          }}
        }}
        sts.textContent=els.length+' markorer | '+(tool==='select'?'Velg/flytt':tool);
        ex.value=JSON.stringify(els,null,2);
      }}

      function resize(){{
        var mw=cv.parentElement?cv.parentElement.clientWidth:900;
        if(mw<100)mw=900;
        var r=Math.min(1,mw/Math.max(img.naturalWidth||img.width||900,1));
        IW=Math.max(100,Math.round((img.naturalWidth||img.width||900)*r));
        IH=Math.max(100,Math.round((img.naturalHeight||img.height||600)*r));
        cv.width=IW;cv.height=IH;
        render();
      }}

      function gP(e){{var r=cv.getBoundingClientRect();return{{x:(e.clientX-r.left)*(cv.width/r.width),y:(e.clientY-r.top)*(cv.height/r.height)}}}}

      function hitTest(x,y){{
        for(var i=els.length-1;i>=0;i--){{
          var e=els[i],ex=(e.x_pct/100)*IW,ey=(e.y_pct/100)*IH;
          if(dist(x,y,ex,ey)<IW*0.025)return i;
          if(e.type==='rect'){{var w=(e.w_pct||10)/100*IW,h=(e.h_pct||8)/100*IH;if(x>=ex&&x<=ex+w&&y>=ey&&y<=ey+h)return i}}
        }}
        return -1;
      }}

      cv.addEventListener('mousedown',function(ev){{
        var p=gP(ev);sp=p;
        if(tool==='select'){{
          sel=hitTest(p.x,p.y);
          if(sel>=0){{drag='move';dbIn.value=els[sel].db||els[sel].label||''}}
          else{{dbIn.value=''}}
        }}else{{
          var tm=TMAP[tool];if(!tm)return;
          var xp=p.x/IW*100,yp=p.y/IH*100;
          if(tm[0]==='circle'){{
            els.push({{id:uid(),type:'circle',x_pct:xp,y_pct:yp,db:dbIn.value||tm[2],color:tm[1],label:''}});
          }}else if(tm[0]==='rect'){{
            els.push({{id:uid(),type:'rect',x_pct:xp,y_pct:yp,w_pct:10,h_pct:8,color:tm[1],label:tm[2]}});drag='drawRect';
          }}else if(tm[0]==='line'){{
            els.push({{id:uid(),type:'line',x_pct:xp,y_pct:yp,x2_pct:xp,y2_pct:yp,color:tm[1],label:tm[2]}});drag='drawLine';
          }}
          sel=els.length-1;
        }}
        render();
      }});

      cv.addEventListener('mousemove',function(ev){{
        if(sel<0||!drag||!sp)return;
        var p=gP(ev),el=els[sel];
        if(drag==='move'){{
          var dx=(p.x-sp.x)/IW*100,dy=(p.y-sp.y)/IH*100;
          el.x_pct=Math.max(0,Math.min(100,(el.x_pct||0)+dx));
          el.y_pct=Math.max(0,Math.min(100,(el.y_pct||0)+dy));
          sp=p;
        }}else if(drag==='drawRect'&&el.type==='rect'){{
          el.w_pct=(p.x/IW*100)-(el.x_pct||0);
          el.h_pct=(p.y/IH*100)-(el.y_pct||0);
        }}else if(drag==='drawLine'&&el.type==='line'){{
          el.x2_pct=p.x/IW*100;el.y2_pct=p.y/IH*100;
        }}
        render();
      }});

      window.addEventListener('mouseup',function(){{drag=null;render()}});

      document.addEventListener('keydown',function(ev){{
        if(ev.target.tagName==='INPUT'||ev.target.tagName==='TEXTAREA')return;
        if((ev.key==='Delete'||ev.key==='Backspace')&&sel>=0){{els.splice(sel,1);sel=-1;render()}}
      }});

      // Init: load image THEN resize
      img.onload=function(){{resize()}};
      img.src='{img_uri}';
      window.addEventListener('resize',function(){{resize()}});
      // Fallback timer in case onload already fired
      setTimeout(function(){{if(img.complete&&IW<1)resize()}},200);

      return {{
        setTool:function(t){{tool=t;sel=-1;document.querySelectorAll('.at').forEach(function(b){{b.classList.remove('active')}});var btn=document.getElementById('at_'+t);if(btn)btn.classList.add('active');cv.style.cursor=t==='select'?'default':'crosshair'}},
        deleteSelected:function(){{if(sel>=0){{els.splice(sel,1);sel=-1;render()}}}},
        updateDb:function(v){{if(sel>=0&&els[sel]){{els[sel].db=v;render()}}}},
        save:function(){{
          ex.value=JSON.stringify(els,null,2);
          try{{
            var ta=window.parent.document.querySelector('textarea[aria-label="'+BRIDGE_LABEL+'"]');
            if(!ta)throw new Error('bridge not found');
            var setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
            setter.call(ta,ex.value);
            ta.dispatchEvent(new Event('input',{{bubbles:true}}));
            ta.dispatchEvent(new Event('change',{{bubbles:true}}));
            sts.textContent='Lagret! Klikk Bruk endringer under.';
          }}catch(err){{
            sts.textContent='Kopier JSON fra feltet under manuelt.';
            ex.style.display='block';
          }}
        }}
      }};
    }})();
    </script>
    """
    components.html(f"<!-- {component_key} -->\n" + html, height=750, scrolling=False)


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
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
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
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --radius-xl: 24px; --radius-lg: 16px; }
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
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
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

# --- 3. SESSION STATE & HARDDISK ---
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

# --- 5. DYNAMISK PDF MOTOR FOR AKUSTIKK ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIAku-001"), 0, 1, 'R')
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
    
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("AKUSTIKKRAPPORT (RIAku)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIAku AI Engine"), ("REGELVERK:", pd_state.get('land', 'Norge (NS 8175)'))]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. VURDERING AV DATAGRUNNLAG", 
        "3. KARTLEGGING OG PINPOINTING AV STØY", 
        "4. LYDFORHOLD INNENDØRS OG PLANLØSNING", 
        "5. KRAV TIL FASADEISOLASJON", 
        "6. TILTAK OG VIDERE PROSJEKTERING", 
        "VEDLEGG: VURDERT DATAGRUNNLAG"
    ]
    for t in toc:
        pdf.set_x(25); pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
        if line.startswith('# ') or re.match(r'^\d+\.\s[A-Z]', line):
            pdf.check_space(30); pdf.ln(8); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.ln(2); pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        elif line.startswith('##'):
            pdf.check_space(20); pdf.ln(6); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 12); pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            if safe_text.strip() == "": continue
            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30); pdf.multi_cell(145, 5, safe_text); pdf.set_x(25)
                else:
                    pdf.set_x(25); pdf.multi_cell(150, 5, safe_text)
            except Exception: pdf.ln(2)

    if maps and len(maps) > 0:
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT DATAGRUNNLAG", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG", quality=90)
                img_w = 160
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                x_pos = 105 - (img_w / 2)
                pdf.image(tmp.name, x=x_pos, y=pdf.get_y(), w=img_w)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-analysert kartutsnitt med estimerte støysoner."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))


# --- 6. UI FOR AKUSTIKK MODUL ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🔊 Lyd & Akustikk (RIAku)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for støyvurdering, fasadeisolasjon og romakustikk.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er automatisk synkronisert.")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"], disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)

with st.expander("2. Bygningsdata & Lydklasse", expanded=True):
    c3, c4, c5 = st.columns(3)
    b_type = c3.text_input("Formål", value=pd_state["b_type"], disabled=True)
    etasjer = c4.number_input("Antall etasjer", value=int(pd_state["etasjer"]), disabled=True)
    bta = c5.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]), disabled=True)
    
    st.markdown("##### Akustisk Klassifisering (NS 8175)")
    c6, c7 = st.columns(2)
    lydklasse = c6.selectbox("Lydklasse (NS 8175)", ["Klasse A (Spesielt gode)", "Klasse B (Gode)", "Klasse C (Minimumskrav i TEK)", "Klasse D (Eldre bygg)"], index=2)
    stoykilde = c7.selectbox("Dominerende Støykilde", ["Veitrafikk", "Bane/Tog", "Flystøy", "Industri/Næring", "Lite støy (Stille område)"], index=0)

with st.expander("3. Visuelt Grunnlag & Støykart", expanded=True):
    st.info("Viktig: For at AI-en skal kunne pinpointe fasadene, kreves støykart lagt over eller sammenholdt med situasjonsplanen.")
    
    # ── Geodata støykart API ──
    st.markdown("##### Automatisk støykart fra Geodata Online")
    stoy_col1, stoy_col2, stoy_col3 = st.columns(3)
    stoy_lat = stoy_col1.number_input("Breddegrad (lat)", value=63.43, format="%.4f", key="stoy_lat")
    stoy_lon = stoy_col2.number_input("Lengdegrad (lon)", value=10.40, format="%.4f", key="stoy_lon")
    stoy_buffer = stoy_col3.number_input("Buffer (m)", value=300, min_value=100, max_value=1000, key="stoy_buffer")
    
    stoy_kilde_map = {"Veitrafikk": "vei", "Bane/Tog": "bane", "Flystøy": "fly", "Industri/Næring": "industri"}
    stoy_api_kilde = stoy_kilde_map.get(stoykilde, "vei")
    
    if st.button("📡 Hent støykart fra Geodata", key="fetch_stoykart", use_container_width=True):
        with st.spinner(f"Henter støykart ({stoy_api_kilde}) fra Geodata Online..."):
            stoy_img, stoy_err = fetch_stoykart_image(stoy_lat, stoy_lon, stoy_api_kilde, buffer_m=stoy_buffer)
            if stoy_img:
                st.session_state["stoykart_image"] = stoy_img
                st.success("Støykart hentet fra Geodata Online!")
            else:
                st.warning(f"Kunne ikke hente støykart: {stoy_err}. Last opp manuelt under.")
            
            contours = fetch_stoykart_contours(stoy_lat, stoy_lon, stoy_api_kilde, buffer_m=stoy_buffer)
            if contours:
                st.session_state["stoykart_contours"] = contours
                st.caption(f"Fant {len(contours)} støykontur-lag i området.")
    
    if "stoykart_image" in st.session_state:
        st.image(st.session_state["stoykart_image"], caption="Støykart fra Geodata Online (Geonorge)", use_container_width=True)
    
    st.markdown("##### Tegninger fra prosjektet")
    saved_images = []
    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.glob("*.jpg")):
            saved_images.append(Image.open(p).convert("RGB"))
            
    if len(saved_images) > 0:
        st.success(f"📎 Fant {len(saved_images)} felles arkitekttegninger/kart fra Project Setup. Disse inkluderes automatisk i analysen!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp plan og støykart under.")
        
    st.markdown("##### Last opp spesifikke Akustikk-vedlegg")
    files = st.file_uploader("Last opp Støykart, Trafikkdata eller Planløsninger (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Kjør Akustisk Analyse (RIAku)", type="primary", use_container_width=True):
    
    images_for_ai = saved_images.copy()
    
    # Inkluder støykart fra Geodata API
    if "stoykart_image" in st.session_state:
        stoy_rgb = st.session_state["stoykart_image"].convert("RGB")
        stoy_rgb.thumbnail((1200, 1200))
        images_for_ai.insert(0, stoy_rgb)
        st.caption("Støykart fra Geodata er inkludert som første bilde i analysen.")
        
    if files:
        with st.spinner("📐 Leser ut støykart og supplerende filer..."):
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
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til AI-en for vurdering.")
    
    # Lagre rene kopier FØR AI tegner på dem
    saved_images_clean = [img.copy() for img in images_for_ai]
                
    with st.spinner(f"🤖 Pinpointer støysoner og tegner fysiske sirkler på bildene..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # --- DEN MAGISKE "TEGNE"-PROMPTEN ---
        prompt_text = f"""
        Du er Builtly RIAku AI, en streng og nøyaktig senior akustiker.
        
        PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer). 
        LOKASJON: {adresse}.
        MÅL-LYDKLASSE: {lydklasse}.
        DOMINERENDE STØYKILDE: {stoykilde}.
        {"STØYKART FRA GEODATA: Første bilde er et offisielt støykart fra Geonorge/Geodata Online. Bruk dette som primærkilde for støynivåer." if "stoykart_image" in st.session_state else "STØYKART: Ikke hentet automatisk. Estimer støynivåer fra tilgjengelige bilder og lokasjon."}
        {"STØYKONTURER: " + json.dumps(st.session_state.get("stoykart_contours", []), ensure_ascii=False) if st.session_state.get("stoykart_contours") else ""}
        
        VIKTIGE REGLER FOR NØYAKTIGHET:
        1. Les støykartet nøye. Fargekoder på offisielle støykart: rød=Lden>65dB, oransje=60-65, gul=55-60, grønn=<55.
        2. Plasser dB-markører PRESIST på hver bygningsfasade som treffes av støy — ett punkt per fasaderetning per bygg.
        3. Bruk FASADENIVÅER fra støykartet, ikke generelle estimater. Eksempel: "Bygg A1, Nord-øst fasade: Lden 64 dB".
        4. Ikke bruk store sirkler — plasser markører som PUNKTER rett på fasadelinjen der støyen treffer.
        5. Merk stille sider (Lden < 55 dB) med grønn markør.
        6. Henvis til NS 8175 lydklassekrav og TEK17 §13-6 for fasadeisolasjon.
        7. Vurder tiltak: støyskjerm, tett rekkverk, absorbenter, skjermvegg, lavt luftevindu.
        
        EKSTREMT VIKTIG FOR TEGNING AV STØY-MARKØRER:
        Returner en maskinlesbar JSON-blokk HELT NEDERST i teksten din.
        Plasser MANGE presise punkter — ett per fasadeside per bygg, som i en profesjonell støyrapport.
        Bruk EKSAKTE dB-verdier (heltall), ikke intervaller.
        
        JSON-format:
        ```json
        [
          {{"image_index": 0, "x_pct": 72, "y_pct": 35, "db": "64", "color": "red", "label": "A1 NØ-fasade"}},
          {{"image_index": 0, "x_pct": 65, "y_pct": 45, "db": "58", "color": "yellow", "label": "A1 SV-fasade"}},
          {{"image_index": 0, "x_pct": 60, "y_pct": 40, "db": "43", "color": "green", "label": "A1 stille side V"}}
        ]
        ```
        Regler for JSON:
        - `image_index`: Bilde nr (0=støykart/situasjonsplan, 1=plantegning osv).
        - `x_pct` og `y_pct`: Prosent (0-100) — plasser PRESIST på fasadelinjen, ikke midt i bygget.
        - `db`: Eksakt Lden-verdi som heltall. Bruk "??" KUN hvis helt umulig å estimere.
        - `color`: "red" (>65 dB), "yellow" (55-65 dB), eller "green" (<55 dB).
        - `label`: Kort: "Bygg [X], [retning] fasade" — f.eks. "A2 NV-fasade", "B stille side S".
        
        Tekstlig vurdering (skriv OVER JSON-blokken):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. VURDERING AV DATAGRUNNLAG (henvis til støykart fra Geodata hvis tilgjengelig)
        # 3. KARTLEGGING OG PINPOINTING AV STØY
        # 4. LYDFORHOLD INNENDØRS OG PLANLØSNING (NS 8175 {lydklasse})
        # 5. KRAV TIL FASADEISOLASJON (TEK17 §13-6, beregn R'w-krav per fasade)
        # 6. TILTAK OG VIDERE PROSJEKTERING
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            ai_raw_text = res.text
            
            # --- PYTHON TEGNE-ROBOTEN ---
            clean_text = ai_raw_text
            json_match = re.search(r'```json\s*(.*?)\s*```', ai_raw_text, re.DOTALL)
            
            if json_match:
                try:
                    markers = json.loads(json_match.group(1))
                    clean_text = re.sub(r'```json\s*.*?\s*```', '', ai_raw_text, flags=re.DOTALL) # Fjerner JSON fra rapporten
                    
                    for marker in markers:
                        idx = int(marker.get("image_index", 0))
                        if idx < len(images_for_ai):
                            img = images_for_ai[idx]
                            draw = ImageDraw.Draw(img)
                            w, h = img.size
                            
                            # Regn ut piksel-posisjon ut fra prosent
                            x = int((marker.get("x_pct", 50) / 100.0) * w)
                            y = int((marker.get("y_pct", 50) / 100.0) * h)
                            db_str = str(marker.get("db", "??"))
                            label = str(marker.get("label", ""))
                            
                            color_name = marker.get("color", "red").lower()
                            if "green" in color_name:
                                color_rgb = (46, 204, 113)
                            elif "yellow" in color_name:
                                color_rgb = (241, 196, 15)
                            else:
                                color_rgb = (231, 76, 60)
                            
                            # Liten, presis sirkel som i profesjonell rapport
                            radius = int(w * 0.014)
                            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color_rgb, width=max(2, int(w*0.003)))
                            
                            try:
                                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(w * 0.013))
                                font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(w * 0.009))
                            except:
                                font = ImageFont.load_default()
                                font_small = font
                            
                            # dB-tall inne i sirkelen
                            try:
                                bbox = draw.textbbox((0,0), db_str, font=font)
                                tw = bbox[2] - bbox[0]
                                th = bbox[3] - bbox[1]
                            except:
                                tw, th = 20, 12
                                
                            # Tall inne i sirkel med halvtransparent bakgrunn
                            draw.rectangle((x - tw/2 - 2, y - th/2 - 1, x + tw/2 + 2, y + th/2 + 1), fill=color_rgb)
                            draw.text((x - tw/2, y - th/2), db_str, fill=(255, 255, 255), font=font)
                            
                            # Label under sirkelen (bygg + fasaderetning)
                            if label:
                                try:
                                    lbbox = draw.textbbox((0,0), label, font=font_small)
                                    lw = lbbox[2] - lbbox[0]
                                except:
                                    lw = len(label) * 6
                                draw.text((x - lw/2, y + radius + 3), label, fill=color_rgb, font=font_small)
                            
                            images_for_ai[idx] = img

                except Exception as e:
                    print(f"Feil under tegning: {e}")
            
            # ── Lagre markører for interaktiv redigering ──
            st.session_state["aku_markers"] = markers if json_match else []
            st.session_state["aku_images"] = images_for_ai  # påtegnet versjon for PDF
            # Lagre RENE originaler for re-rendering
            st.session_state["aku_images_original"] = [img.copy() for img in saved_images_clean]
            st.session_state["aku_ai_text"] = clean_text
            
            with st.spinner("Kompilerer Akustikk-PDF og fletter inn tegninger med tegnede sirkler..."):
                pdf_data = create_full_report_pdf(p_name, pd_state['c_name'], clean_text, images_for_ai)
                
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-AKU{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                ai_text_lower = clean_text.lower()
                if "for svakt" in ai_text_lower or "avvist" in ai_text_lower:
                    status = "Rejected - Needs Data"
                    badge = "badge-early"
                elif "indikativ" in ai_text_lower or "delvis" in ai_text_lower:
                    status = "Indicative Assessment"
                    badge = "badge-roadmap"
                else:
                    status = "Pending Senior Review"
                    badge = "badge-pending"
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIAku (Akustikk)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Akustiker",
                    "status": status,
                    "class": badge,
                    "pdf_bytes": pdf_data
                }
                
                st.session_state.generated_aku_pdf = pdf_data
                st.session_state.generated_aku_filename = f"Builtly_RIAku_{p_name}.pdf"

                # Lagre til Supabase dashboard
                if _HAS_AUTH:

                    try:

                        builtly_auth.save_report(
                        project_name=pd_state.get("p_name", p_name),
                        report_name=f"Akustikk — Builtly_RIAku_{p_name}.pdf",
                        module="RIAku (Akustikk)",
                        file_path=f"Builtly_RIAku_{p_name}.pdf",

                        )

                    except Exception:

                        pass
                try:
                    report_dir = DB_DIR / "reports"
                    report_dir.mkdir(exist_ok=True)
                    
                    # Lagre PDF
                    pdf_path = report_dir / f"Builtly_RIAku_{p_name}.pdf"
                    pdf_path.write_bytes(pdf_data)
                    
                    # Lagre review-metadata til JSON
                    reviews_file = DB_DIR / "pending_reviews.json"
                    existing_reviews = {}
                    if reviews_file.exists():
                        try:
                            existing_reviews = json.loads(reviews_file.read_text(encoding="utf-8"))
                        except Exception:
                            existing_reviews = {}
                    
                    existing_reviews[doc_id] = {
                        "title": pd_state['p_name'],
                        "module": "RIAku (Akustikk)",
                        "drafter": "Builtly AI",
                        "reviewer": "Senior Akustiker",
                        "status": status,
                        "class": badge,
                        "pdf_file": str(pdf_path),
                        "timestamp": datetime.now().isoformat(),
                    }
                    reviews_file.write_text(json.dumps(existing_reviews, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as save_exc:
                    st.caption(f"Rapport generert men disk-lagring feilet: {save_exc}")

                st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_aku_pdf" in st.session_state:
    st.success("✅ RIAku Rapport er generert og lagt i QA-køen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Akustikkrapport", st.session_state.generated_aku_pdf, st.session_state.generated_aku_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å vurdere", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))

    # ── Interaktiv redigering av støymarkører ──
    if st.session_state.get("aku_markers") is not None and st.session_state.get("aku_images"):
        with st.expander("Rediger støymarkører (dB-sirkler)", expanded=False):
            st.caption("Flytt, legg til eller slett dB-sirkler på alle bilder. Klikk Lagre i editoren, deretter **Bruk endringer** for å oppdatere PDF-en.")
            
            all_images = st.session_state.get("aku_images", [])
            num_images = len(all_images)
            
            # Vis tabs for hvert bilde
            if num_images > 1:
                img_tabs = st.tabs([f"Bilde {i+1}" for i in range(min(num_images, 6))])
            else:
                img_tabs = [st.container()]
            
            for img_idx, tab in enumerate(img_tabs):
                if img_idx >= num_images:
                    break
                with tab:
                    all_markers = st.session_state.get("aku_markers", [])
                    img_markers = [m for m in all_markers if int(m.get("image_index", 0)) == img_idx]
                    bridge_label = f"AKU_MARKER_BRIDGE_{img_idx}"
                    images_data = [{"image": all_images[img_idx], "markers": img_markers}]
                    render_acoustic_editor(images_data, bridge_label=bridge_label, component_key=f"aku_editor_{img_idx}")
            
            marker_buffer_key = "aku_marker_buffer"
            if marker_buffer_key not in st.session_state:
                st.session_state[marker_buffer_key] = json.dumps(st.session_state.get("aku_markers", []), ensure_ascii=False, indent=2)
            
            st.text_area("Markør-data (alle bilder)", key=marker_buffer_key, height=100, label_visibility="collapsed")
            
            edit_cols = st.columns(3)
            if edit_cols[0].button("Bruk endringer og oppdater PDF", key="aku_apply_markers", use_container_width=True, type="primary"):
                try:
                    new_markers = json.loads(st.session_state.get(marker_buffer_key, "[]") or "[]")
                    if not isinstance(new_markers, list):
                        raise ValueError("Må være en liste")
                    
                    # Bruk RENE originaler
                    originals = st.session_state.get("aku_images_original", [])
                    if not originals:
                        originals = st.session_state.get("aku_images", [])
                    fresh_images = [img.copy() for img in originals]
                    
                    for marker in new_markers:
                        idx = int(marker.get("image_index", 0))
                        if idx < len(fresh_images):
                            img = fresh_images[idx]
                            draw = ImageDraw.Draw(img)
                            w, h = img.size
                            mx = int((marker.get("x_pct", 50) / 100.0) * w)
                            my = int((marker.get("y_pct", 50) / 100.0) * h)
                            db_str = str(marker.get("db", "??"))
                            label = str(marker.get("label", ""))
                            color_name = marker.get("color", "red").lower()
                            color_rgb = (46,204,113) if "green" in color_name else (241,196,15) if "yellow" in color_name else (231,76,60)
                            radius = int(w * 0.014)
                            draw.ellipse((mx-radius, my-radius, mx+radius, my+radius), outline=color_rgb, width=max(2, int(w*0.003)))
                            try:
                                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(w * 0.013))
                                font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(w * 0.009))
                            except:
                                font = ImageFont.load_default(); font_s = font
                            try:
                                bb = draw.textbbox((0,0), db_str, font=font); tw=bb[2]-bb[0]; th=bb[3]-bb[1]
                            except:
                                tw, th = 20, 12
                            draw.rectangle((mx-tw/2-2, my-th/2-1, mx+tw/2+2, my+th/2+1), fill=color_rgb)
                            draw.text((mx-tw/2, my-th/2), db_str, fill=(255,255,255), font=font)
                            if label:
                                try: lw = draw.textbbox((0,0), label, font=font_s)[2]
                                except: lw = len(label)*6
                                draw.text((mx-lw/2, my+radius+3), label, fill=color_rgb, font=font_s)
                    
                    st.session_state["aku_markers"] = new_markers
                    st.session_state["aku_images"] = fresh_images
                    
                    # Regenerer PDF
                    clean_text = st.session_state.get("aku_ai_text", "")
                    pdf_data = create_full_report_pdf(p_name, pd_state["c_name"], clean_text, fresh_images)
                    st.session_state.generated_aku_pdf = pdf_data
                    
                    if _HAS_AUTH:
                        try:
                            builtly_auth.save_report(project_name=pd_state.get("p_name", p_name), report_name=f"Akustikk — Builtly_RIAku_{p_name}.pdf (revidert)", module="RIAku (Akustikk)", file_path=f"Builtly_RIAku_{p_name}.pdf")
                        except Exception:
                            pass
                    
                    st.success("Markører oppdatert, bilder re-rendret og PDF regenerert!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Feil: {exc}")
            
            if edit_cols[1].button("Nullstill markører", key="aku_reset_markers", use_container_width=True):
                st.session_state[marker_buffer_key] = json.dumps(st.session_state.get("aku_markers", []), ensure_ascii=False, indent=2)
                st.rerun()
            
            if edit_cols[2].button("Tøm alle markører", key="aku_clear_markers", use_container_width=True):
                st.session_state["aku_markers"] = []
                st.session_state[marker_buffer_key] = "[]"
                st.rerun()
