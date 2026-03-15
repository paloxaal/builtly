# Builtly RIB — Arkitektur for faglig korrekt konseptvurdering
## Versjon: Arkitekturbeskrivelse v1 / Mars 2026

---

## Problemet med nåværende løsning (v16–v21)

Hele pipelinen er bygget rundt én feil antagelse: at en LLM kan se et
rasterbilde av en tegning og returnere presise koordinater for bærende
elementer. Det fungerer ikke fordi:

1. **All strukturell informasjon kastes bort** — DWG-filene inneholder
   aksesystem, mål, lag-navn og tekstannotasjoner som forteller nøyaktig
   hva bygget er. Når filen rasteriseres til et PNG-bilde, forsvinner alt
   dette og erstattes med piksler.

2. **LLM-er gjetter, de måler ikke** — selv GPT-4o og Claude kan ikke
   presist plassere (x,y)-koordinater fra et bilde. De er gode på å
   *beskrive* hva de ser, svake på *numerisk presisjon*.

3. **Systemvalg uten data** — uten å vite at tegningen viser "T=250"
   (betongdekke 250mm) og "ø12 c250" (armering), anbefaler LLM-en
   massivtre for et bygg som åpenbart er plasstøpt betong.

---

## Ny arkitektur: Tre steg

### Steg 1: Ekstraher strukturdata fra kildefiler (deterministisk)

Ingen AI. Ren parsing av hva som faktisk står i filene.

#### 1a. DWG/DXF-parser (ezdxf)

```python
import ezdxf

def parse_dwg_structure(filepath: str) -> dict:
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    
    result = {
        "axes": [],
        "dimensions": [],
        "texts": [],
        "lines_by_layer": {},
        "inserts": [],  # blokk-referanser (søyler, utstyr)
    }
    
    for entity in msp:
        layer = entity.dxf.layer.upper()
        etype = entity.dxftype()
        
        # Dimensjoner → aksemål
        if etype == "DIMENSION":
            result["dimensions"].append({
                "layer": layer,
                "measurement": entity.dxf.actual_measurement,
                "defpoint": (entity.dxf.defpoint.x, entity.dxf.defpoint.y),
                "defpoint4": (entity.dxf.defpoint4.x, entity.dxf.defpoint4.y),
            })
        
        # Tekst → strukturelle annotasjoner
        elif etype in ("TEXT", "MTEXT"):
            text = entity.dxf.text if etype == "TEXT" else entity.text
            pos = entity.dxf.insert if hasattr(entity.dxf, 'insert') else (0,0)
            result["texts"].append({
                "text": text,
                "layer": layer,
                "position": (pos[0], pos[1]),
            })
        
        # Linjer → vegger, akselinjer
        elif etype in ("LINE", "LWPOLYLINE", "POLYLINE"):
            if layer not in result["lines_by_layer"]:
                result["lines_by_layer"][layer] = []
            if etype == "LINE":
                result["lines_by_layer"][layer].append({
                    "start": (entity.dxf.start.x, entity.dxf.start.y),
                    "end": (entity.dxf.end.x, entity.dxf.end.y),
                })
            elif etype == "LWPOLYLINE":
                points = list(entity.get_points(format="xy"))
                result["lines_by_layer"][layer].append({"points": points})
        
        # INSERT → blokk-referanser (søyler, utstyr)
        elif etype == "INSERT":
            result["inserts"].append({
                "block_name": entity.dxf.name,
                "layer": layer,
                "position": (entity.dxf.insert.x, entity.dxf.insert.y),
                "scale": (entity.dxf.xscale, entity.dxf.yscale),
            })
    
    return result
```

#### 1b. Strukturell tekstgjenkjenning (regex)

```python
import re

STRUCTURAL_PATTERNS = {
    # Dekketykkelse: "T=250 UK +115,475"
    "slab": re.compile(
        r"T\s*=\s*(\d{2,4})\s*(?:UK|OK)?\s*\+?\s*([\d,\.]+)?",
        re.IGNORECASE
    ),
    # Armering: "ø12 c250 B18" eller "24ø12 c250 B18"
    "reinforcement": re.compile(
        r"(\d+)?ø(\d+)\s*c(\d+)\s*(B\d+)?",
    ),
    # Søyleprofil: "SØYLE: KF HUP 100x100x6"
    "column_profile": re.compile(
        r"(?:SØYLE|COLUMN|PILLAR)[:\s]+(?:KF\s+)?(\w+\s+[\dx\.]+)",
        re.IGNORECASE
    ),
    # Isokorb: "v/ISOKORB"
    "thermal_break": re.compile(
        r"(?:v/)?ISOKORB",
        re.IGNORECASE
    ),
    # Akselabel: "B-01", "B-A", "1", "A"
    "axis_label": re.compile(
        r"^[A-Z]-?\d{1,3}$|^[A-Z]$|^\d{1,3}$"
    ),
    # Kote: "UK +118,725" eller "OK FG +119,40"
    "level": re.compile(
        r"(?:UK|OK)\s*(?:FG)?\s*\+?\s*([\d,\.]+)",
        re.IGNORECASE
    ),
    # Sjaktvegg: "SJAKTVEGG", "HEISSJAKT"
    "core_element": re.compile(
        r"(?:SJAKT|HEIS|TRAPP|CORE)",
        re.IGNORECASE
    ),
}


def classify_structural_texts(texts: list[dict]) -> dict:
    """Klassifiserer tekst-annotasjoner fra tegningen."""
    classified = {
        "slabs": [],
        "reinforcement": [],
        "columns": [],
        "levels": [],
        "core_elements": [],
        "thermal_breaks": [],
        "axis_labels": [],
        "unclassified": [],
    }
    
    for item in texts:
        text = item["text"].strip()
        matched = False
        
        for category, pattern in STRUCTURAL_PATTERNS.items():
            match = pattern.search(text)
            if match:
                classified_item = {
                    **item,
                    "match": match.groups(),
                    "category": category,
                }
                if category == "slab":
                    classified["slabs"].append(classified_item)
                elif category == "reinforcement":
                    classified["reinforcement"].append(classified_item)
                elif category == "column_profile":
                    classified["columns"].append(classified_item)
                elif category == "level":
                    classified["levels"].append(classified_item)
                elif category == "core_element":
                    classified["core_elements"].append(classified_item)
                elif category == "thermal_break":
                    classified["thermal_breaks"].append(classified_item)
                elif category == "axis_label":
                    classified["axis_labels"].append(classified_item)
                matched = True
                break
        
        if not matched:
            classified["unclassified"].append(item)
    
    return classified
```

#### 1c. Aksesystem-rekonstruksjon

```python
def reconstruct_axis_system(
    dimensions: list[dict],
    axis_labels: list[dict],
    lines_by_layer: dict,
) -> dict:
    """
    Bygger aksesystemet fra dimensjoner og akselabels.
    
    Fra Hus B-tegningen:
      B-01 --|-- 5911 --|-- B-02 --|-- 9000 --|-- B-03
      B-A  --|-- 6911 --|-- B-B  --|-- 6531 --|-- B-C
    """
    # Finn akselinjer fra lag (typisk "S-GRID", "AXIS", "AKSE")
    axis_layers = [
        layer for layer in lines_by_layer 
        if any(kw in layer.upper() for kw in ["GRID", "AXIS", "AKSE"])
    ]
    
    # Grupper vertikale og horisontale akselinjer
    x_axes = []  # vertikale linjer (konstant x)
    y_axes = []  # horisontale linjer (konstant y)
    
    for layer in axis_layers:
        for line in lines_by_layer[layer]:
            if "start" in line:
                dx = abs(line["end"][0] - line["start"][0])
                dy = abs(line["end"][1] - line["start"][1])
                if dx < dy * 0.1:  # vertikal
                    x_axes.append({"x": line["start"][0], "layer": layer})
                elif dy < dx * 0.1:  # horisontal
                    y_axes.append({"y": line["start"][1], "layer": layer})
    
    # Sorter og fjern duplikater
    x_axes = sorted(set(round(a["x"]) for a in x_axes))
    y_axes = sorted(set(round(a["y"]) for a in y_axes))
    
    # Match med dimensjoner for å få avstander i mm
    x_positions = []
    for i, x in enumerate(x_axes):
        label = _find_nearest_axis_label(x, None, axis_labels, "x")
        distance = (x - x_axes[i-1]) if i > 0 else 0
        x_positions.append({"label": label, "pos_mm": x, "distance_mm": round(distance)})
    
    y_positions = []
    for i, y in enumerate(y_axes):
        label = _find_nearest_axis_label(None, y, axis_labels, "y")
        distance = (y - y_axes[i-1]) if i > 0 else 0
        y_positions.append({"label": label, "pos_mm": y, "distance_mm": round(distance)})
    
    return {
        "x_axes": x_positions,
        "y_axes": y_positions,
        "total_width_mm": x_axes[-1] - x_axes[0] if x_axes else 0,
        "total_depth_mm": y_axes[-1] - y_axes[0] if y_axes else 0,
    }
```

#### 1d. Bygg den strukturelle datamodellen

```python
def build_structural_model(
    raw_parse: dict,
    classified_texts: dict,
    axis_system: dict,
) -> dict:
    """
    Setter sammen all ekstrahert info til én strukturell modell.
    Dette er det LLM-en mottar i steg 2.
    """
    
    # Utled materiale fra hva som er funnet
    has_reinforcement = len(classified_texts["reinforcement"]) > 0
    has_slab_thickness = len(classified_texts["slabs"]) > 0
    has_steel_columns = any("HUP" in c["text"] or "HEB" in c["text"] 
                           for c in classified_texts["columns"])
    
    if has_reinforcement and has_slab_thickness:
        primary_material = "plasstøpt betong"
    elif has_steel_columns and not has_reinforcement:
        primary_material = "stål"
    else:
        primary_material = "ukjent — trenger mer data"
    
    # Utled bæresystem fra elementer
    n_walls = len([l for layer, lines in raw_parse["lines_by_layer"].items() 
                   for l in lines if "WALL" in layer.upper() or "VEGG" in layer.upper()])
    n_columns = len(classified_texts["columns"]) + len(
        [i for i in raw_parse["inserts"] if "COL" in i["block_name"].upper() or "SØYLE" in i["block_name"].upper()]
    )
    n_cores = len(classified_texts["core_elements"])
    
    if n_walls > n_columns * 2:
        bearing_system = "vegger_og_kjerner"
    elif n_columns > n_walls:
        bearing_system = "søyle_bjelke"
    else:
        bearing_system = "hybrid"
    
    # Hent spennvidder fra aksesystemet
    spans = []
    for i in range(1, len(axis_system["x_axes"])):
        spans.append({
            "from": axis_system["x_axes"][i-1]["label"],
            "to": axis_system["x_axes"][i]["label"],
            "length_mm": axis_system["x_axes"][i]["distance_mm"],
            "direction": "x",
        })
    
    # Dekkeinfo
    slabs = []
    for s in classified_texts["slabs"]:
        thickness = int(s["match"][0]) if s["match"][0] else None
        level = s["match"][1] if len(s["match"]) > 1 else None
        slabs.append({
            "thickness_mm": thickness,
            "level": level,
            "position": s["position"],
        })
    
    return {
        "project_type": "detected",
        "primary_material": primary_material,
        "bearing_system": bearing_system,
        "axis_system": axis_system,
        "slabs": slabs,
        "reinforcement_count": len(classified_texts["reinforcement"]),
        "columns": classified_texts["columns"],
        "core_elements": classified_texts["core_elements"],
        "thermal_breaks": classified_texts["thermal_breaks"],
        "spans": spans,
        "levels": classified_texts["levels"],
        "confidence": {
            "material": "høy" if has_reinforcement else "lav",
            "axes": "høy" if len(axis_system["x_axes"]) >= 2 else "lav",
            "bearing": "middels" if n_walls + n_columns > 3 else "lav",
        },
    }
```

---

### Steg 2: LLM vurderer faglig (mottar JSON, ikke bilder)

LLM-en ser aldri tegningen som bilde. Den mottar den strukturelle
datamodellen fra steg 1 og gjør en faglig vurdering.

```python
def build_rib_assessment_prompt(structural_model: dict, project_data: dict) -> str:
    """Bygger prompt for faglig vurdering basert på strukturert data."""
    
    return f"""
Du er en senior rådgivende ingeniør konstruksjon (RIB).

Du har mottatt en maskinelt ekstrahert strukturell modell fra tegningene.
Vurder bæresystem, stabilitet, rasjonalitet og risiko.

PROSJEKT:
- Navn: {project_data.get('p_name')}
- Type: {project_data.get('b_type')}
- Etasjer: {project_data.get('etasjer')}
- BTA: {project_data.get('bta')} m²

EKSTRAHERT STRUKTURELL MODELL:
- Primærmateriale: {structural_model['primary_material']}
- Bæresystem: {structural_model['bearing_system']}
- Aksesystem: {json.dumps(structural_model['axis_system'], indent=2)}
- Dekker: {json.dumps(structural_model['slabs'], indent=2)}
- Søyler funnet: {len(structural_model['columns'])}
- Kjerneelementer: {len(structural_model['core_elements'])}
- Spennvidder: {json.dumps(structural_model['spans'], indent=2)}
- Armering funnet: {structural_model['reinforcement_count']} annotasjoner
- Konfidensgrad: {json.dumps(structural_model['confidence'], indent=2)}

OPPGAVE:
Basert på denne strukturelle modellen, vurder:

1. Er det utledede bæresystemet ({structural_model['bearing_system']}) 
   korrekt basert på dataene?
2. Er materialet ({structural_model['primary_material']}) konsistent 
   med funnene?
3. Er spennviddene rasjonelle for dette systemet?
4. Hvilke risikoer ser du?
5. Hva mangler for å gå videre til detaljprosjektering?

VIKTIG: Du trenger IKKE returnere koordinater. 
Referer til aksesystemet: "mellom akse B-01 og B-02", 
"langs akse B-B", "i kjerneområdet ved akse B-02/B-B".

Returner JSON med:
- assessment (tekstlig vurdering)
- system_confirmed (bool)  
- material_confirmed (bool)
- risks (liste)
- missing_info (liste)
- recommendations (liste)
- rationality_score (1-100)
"""
```

---

### Steg 3: Generer rapport og konseptskisse

Bruker den strukturelle datamodellen (steg 1) og den faglige
vurderingen (steg 2) til å generere rapport og visuell skisse.

Konseptskissen plasserer elementer basert på **aksesystemet**,
ikke basert på LLM-gjetting. Hvert element refererer til en akse.

```python
def generate_concept_sketch_from_model(
    structural_model: dict,
    assessment: dict,
) -> list[dict]:
    """Genererer konseptskisse-elementer fra strukturell modell."""
    
    elements = []
    axes = structural_model["axis_system"]
    
    # Søyler på aksekryss (hvis søyle-bjelke-system)
    if structural_model["bearing_system"] in ("søyle_bjelke", "hybrid"):
        col_nr = 1
        for x_axis in axes["x_axes"]:
            for y_axis in axes["y_axes"]:
                elements.append({
                    "type": "column",
                    "x_mm": x_axis["pos_mm"],
                    "y_mm": y_axis["pos_mm"],
                    "axis_ref": f"{x_axis['label']}/{y_axis['label']}",
                    "label": f"S{col_nr}",
                })
                col_nr += 1
    
    # Bærevegger langs akser (hvis vegg-system)
    if structural_model["bearing_system"] in ("vegger_og_kjerner", "hybrid"):
        for x_axis in axes["x_axes"][1:-1]:  # indre akser
            elements.append({
                "type": "wall",
                "x1_mm": x_axis["pos_mm"],
                "y1_mm": axes["y_axes"][0]["pos_mm"],
                "x2_mm": x_axis["pos_mm"],
                "y2_mm": axes["y_axes"][-1]["pos_mm"],
                "axis_ref": x_axis["label"],
                "label": f"Bærevegg {x_axis['label']}",
            })
    
    # Kjerner
    for i, core in enumerate(structural_model["core_elements"]):
        elements.append({
            "type": "core",
            "x_mm": core["position"][0],
            "y_mm": core["position"][1],
            "label": f"Kjerne {i+1}",
        })
    
    # Spennpiler
    for span in structural_model["spans"]:
        elements.append({
            "type": "span_arrow",
            "label": f"{span['length_mm']} mm",
            "from_axis": span["from"],
            "to_axis": span["to"],
        })
    
    return elements
```

---

## Hva dette betyr i praksis

### For DWG-filer (det mest vanlige for norske RIB-prosjekter):
- `ezdxf` leser filen direkte — ingen rasterisering
- Aksesystem, mål, lag-navn og tekst ekstraheres programmatisk
- Armering, dekketykkelse, søyleprofiler og kotehenvisninger gjenkjennes
- Bæresystem og materiale utledes fra data, ikke gjettet fra bilde

### For PDF-filer (når DWG ikke er tilgjengelig):
- PyMuPDF ekstraherer tekst og posisjon
- Samme regex-mønstre brukes for å finne strukturelle annotasjoner
- Aksesystem kan rekonstrueres fra dimensjonstekst
- Lavere konfidensgrad enn DWG, men vesentlig bedre enn bildegjetting

### For IFC-filer (beste kilde):
- ifcopenshell gir direkte tilgang til alle bygningselementer
- Vegger, søyler, dekker, trapper med eksakte koordinater
- Materialer, lag-info, bærende egenskaper er metadata
- Aksesystem finnes som IfcGrid-entiteter

---

## Utviklingsrekkefølge (anbefalt)

### Fase 1 (2-3 uker): DWG tekst-ekstraksjon
- Bygg `parse_dwg_structure()` med ezdxf
- Bygg `classify_structural_texts()` med regex
- Test på 5-10 ekte RIB-tegninger
- Resultat: strukturell JSON fra DWG

### Fase 2 (1-2 uker): LLM faglig vurdering
- Bygg prompt som sender JSON (ikke bilder) til LLM
- Test at LLM korrekt identifiserer betong vs stål vs tre
- Test at LLM refererer til akser, ikke koordinater
- Resultat: faglig vurdering i JSON

### Fase 3 (1-2 uker): Rapport og skisse
- Bygg aksegrid-basert overlay
- Generer konseptskisse fra strukturell modell
- Integrer i eksisterende PDF-rapport
- Resultat: rapport med korrekt bæresystem

### Fase 4 (løpende): Utvidelse
- Flere regex-mønstre for spesialtilfeller
- Bedre IFC-støtte med Dockerfile
- Snitt-tolkning (vertikal lastnedføring)
- Multi-etasje-sammenligning

---

## Nøkkelforskjell oppsummert

| Aspekt | Nåværende (v16-v21) | Ny arkitektur |
|--------|---------------------|---------------|
| Input til AI | Rasterbilde (piksel) | Strukturert JSON |
| Aksesystem | Gjettet fra bilde | Lest fra DWG |
| Mål | Gjettet fra bilde | Lest fra dimensjoner |
| Materiale | Gjettet fra utseende | Utledet fra armering/profiler |
| Søyler | Random plassering | På aksekryss |
| Vegger | Random plassering | Langs akselinjer |
| LLM-rolle | Gjetter alt | Vurderer det som er funnet |
| Treffsikkerhet | ~10-20% | ~80-90% (avhenger av filkvalitet) |
