"""
AI-drevet tomteplanlegger for Builtly — v2 (hybrid).

ARKITEKTUR:
  1. Python analyserer tomten og beregner et grid av GYLDIGE plasseringsposisjoner
  2. For hver posisjon beregnes sol-score, naboavstand, terrengkote
  3. Claude faar dette som et "menykort" og velger HVILKE posisjoner som brukes,
     med hvilken orientering, bredde/dybde og etasjetall
  4. Python validerer, bygger Shapely-polygoner og sjekker overlapp/BYA
"""

from __future__ import annotations
import json, math, os
from typing import Any, Dict, List, Optional, Tuple
import requests

try:
    from shapely.geometry import Polygon, box as shapely_box, Point
    from shapely import affinity
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-4-6"

def _get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")

def is_available():
    return bool(_get_api_key())


def _compute_placement_grid(buildable_polygon, site_polygon, neighbors, terrain, latitude_deg, grid_spacing_m=20.0):
    if not HAS_SHAPELY or buildable_polygon is None:
        return []
    bp = buildable_polygon
    minx, miny, maxx, maxy = bp.bounds

    neighbor_polys = []
    for nb in (neighbors or []):
        p = nb.get('polygon')
        if p is not None and not p.is_empty:
            if site_polygon and p.intersection(site_polygon).area / max(float(p.area), 1.0) < 0.3:
                neighbor_polys.append({'polygon': p, 'height_m': float(nb.get('height_m', 9.0)),
                                       'centroid': (p.centroid.x, p.centroid.y)})

    positions = []
    pos_id = 0
    x = minx + grid_spacing_m / 2
    while x < maxx:
        y = miny + grid_spacing_m / 2
        while y < maxy:
            pt = Point(x, y)
            if bp.contains(pt):
                test_rect = shapely_box(x - 10, y - 7, x + 10, y + 7)
                containment = bp.intersection(test_rect).area / max(test_rect.area, 1.0)

                min_nb_dist, closest_nb_h = 999.0, 0.0
                sol_score = 70.0
                for nb in neighbor_polys:
                    d = pt.distance(nb['polygon'])
                    if d < min_nb_dist:
                        min_nb_dist = d
                        closest_nb_h = nb['height_m']
                    ncx, ncy = nb['centroid']
                    dx, dy = ncx - x, ncy - y
                    nb_angle = math.degrees(math.atan2(dy, dx)) % 360
                    if 150 < nb_angle < 210:
                        dist = math.hypot(dx, dy)
                        sol_score -= min(30.0, (nb['height_m'] / max(dist, 1.0)) * 80.0)
                sol_score = max(10.0, min(100.0, sol_score))

                elev = 0.0
                if terrain and terrain.get('a') is not None:
                    elev = float(terrain['a']) * x + float(terrain['b']) * y + float(terrain['c'])
                    elev -= float(terrain.get('min_elev_m', elev))

                edge_dist = bp.exterior.distance(pt)
                rel_x = (x - minx) / max(maxx - minx, 1.0)
                rel_y = (y - miny) / max(maxy - miny, 1.0)

                positions.append({
                    'id': f'P{pos_id:03d}', 'x': round(x, 1), 'y': round(y, 1),
                    'rel_x': round(rel_x, 2), 'rel_y': round(rel_y, 2),
                    'containment': round(containment, 2),
                    'sol_score': round(sol_score, 0),
                    'nearest_neighbor_m': round(min_nb_dist, 0),
                    'nearest_neighbor_height_m': round(closest_nb_h, 0),
                    'edge_distance_m': round(edge_dist, 0),
                    'elevation_m': round(elev, 1),
                    'usable': containment > 0.5 and edge_dist > 4.0,
                })
                pos_id += 1
            y += grid_spacing_m
        x += grid_spacing_m
    return positions


def _rank_positions(positions):
    usable = [p for p in positions if p.get('usable')]
    for p in usable:
        p['rank_score'] = p['sol_score'] * 0.35 + min(p['nearest_neighbor_m'], 50) * 0.8 + min(p['edge_distance_m'], 30) * 0.6 + p['containment'] * 20
    usable.sort(key=lambda p: p['rank_score'], reverse=True)
    return usable


def _build_prompt(positions, typology, target_bta_m2, max_floors, max_height_m, max_bya_pct,
                  floor_to_floor_m, site_area_m2, buildable_area_m2, latitude_deg,
                  terrain, site_intelligence, neighbor_summary):
    max_footprint = buildable_area_m2 * (max_bya_pct / 100.0)
    fl_est = min(max_floors, max(2, int(target_bta_m2 / max(max_footprint, 1.0)) + 1))

    # Sorter posisjoner etter plassering for aa gi Claude romlig oversikt
    # Grupper i NORD/SOR/OST/VEST soner
    if positions:
        xs = [p['x'] for p in positions]
        ys = [p['y'] for p in positions]
        mid_x = (min(xs) + max(xs)) / 2
        mid_y = (min(ys) + max(ys)) / 2
        site_w = max(xs) - min(xs)
        site_h = max(ys) - min(ys)

        zones = {"nordvest": [], "nordost": [], "sorvest": [], "sorost": [], "sentrum": []}
        for p in positions:
            dx = p['x'] - mid_x
            dy = p['y'] - mid_y
            if abs(dx) < site_w * 0.15 and abs(dy) < site_h * 0.15:
                zones["sentrum"].append(p)
            elif dy > 0 and dx < 0:
                zones["nordvest"].append(p)
            elif dy > 0 and dx >= 0:
                zones["nordost"].append(p)
            elif dy <= 0 and dx < 0:
                zones["sorvest"].append(p)
            else:
                zones["sorost"].append(p)
    else:
        zones = {}

    zone_summary = []
    for name, pts in zones.items():
        if pts:
            best = max(pts, key=lambda p: p.get('rank_score', 0))
            zone_summary.append(f"  {name.upper()}: {len(pts)} pos, beste={best['id']} sol={best['sol_score']:.0f} ({best['x']:.0f},{best['y']:.0f})")

    pos_lines = []
    for p in positions[:60]:
        pos_lines.append(f"  {p['id']}: ({p['x']:.0f},{p['y']:.0f}) sol={p['sol_score']:.0f} nabo={p['nearest_neighbor_m']:.0f}m kant={p['edge_distance_m']:.0f}m h={p['elevation_m']:.1f}m")

    terrain_text = "Flatt"
    if terrain and terrain.get('point_count', 0) > 0:
        terrain_text = f"Fall {terrain.get('slope_pct', 0):.1f}%, relieff {terrain.get('relief_m', 0):.1f}m"

    courtyard_json = ',"courtyard":true,"ring_depth":11' if typology == "Karré" else ''

    # DETALJERTE BEBYGGELSESMONSTER per typologi
    typo_master = {
        "Punkthus": f"""
BEBYGGELSESKONSEPT: PUNKTHUS-PARK
Tenk Fornebu/Loeren: frittstaende punkthus i et parkmessig landskap.

ORGANISERING:
1. Plasser punkthusene langs tomtens YTTERKANT, 10-15m fra tomtegrensen
   — dette gjor naboer minst mulig usjenert og frigjor tomtens INDRE til park
2. Hvert punkthus: 22-25m x 22-25m, 8-12m avstand mellom paa sidene
3. SENTRUM av tomten holdes HELT AAPENT som stort felles parkdrag/lekeomraade
4. Hovedtorg/motepass der flest siktlinjer moetes — gjerne litt forskjevet fra sentrum mot sor
5. Varier hoyde: lavest mot eksisterende smaabebyggelse (3-4 et), hoyest der det er mest aapent ({fl_est} et)
6. Roter annethvert punkthus 10-20 grader for aa bryte opp og skape diagonale siktlinjer
7. La det vaere 25-35m AAPENT mellom husgrupper paa motsatte sider — dette er parkdraget
8. Plasser {max(4, min(9, int(target_bta_m2 / (22*22*fl_est))))} punkthus

KVALITETSSJEKK: Hvis du tegner en rett linje fra hvert hus, skal den treffe park/uterom — IKKE en annen bygning.""",

        "Karré": f"""
BEBYGGELSESKONSEPT: KVARTALSBY
Tenk Grunerloekka/Nydalen: tydelige kvartaler med gaardrom som private uterom.

ORGANISERING:
1. Del tomten i 2-4 KVARTALER, hvert 40-50m x 40-50m ytre maal
2. Hvert kvartal har et GAARDROM paa 20-28m x 20-28m i midten (courtyard=true, ring_depth=11)
3. Gaardrommet aapnes mot SORVEST (klipp bort sorvest-floyen eller gjor den lavere)
4. Mellom kvartalene: 18-22m gate/allmenning
5. Kvartals-aksene boer foelge tomtens hovedretning
6. Varier kvartalene: noen 42x42m, noen 48x46m — IKKE identiske kopier
7. Hoyeste punkt (gesims) paa nordost-floyen i hvert kvartal, lavest paa sorvest
8. Sett courtyard=true og ring_depth=11 paa ALLE kvartalene
9. Plasser {max(2, min(4, int(target_bta_m2 / (1500*fl_est)) + 1))} kvartaler

KVALITETSSJEKK: Hvert kvartal skal ha et tydelig gaardrom. Mellomrommene mellom kvartaler skal fungere som gater/allmenninger.""",

        "Lamell": f"""
BEBYGGELSESKONSEPT: LAMELLBY
Tenk Romsaas/klassisk skandinavisk: parallelle lameller med sol og luft.

ORGANISERING:
1. Plasser lamellene i PARALLELLE RADER orientert sor-sorvest (langfasade ~195 grader)
2. 20-25m mellom parallelle rader (TEK17 dagslys + privatliv)
3. Hver lamell: 35-50m lang, 12-14m dyp — VARIER lengden
4. FORSKYV annenhver rad 10-15m sidelengs for aa bryte monotoni og skape L-formede uterom
5. Radene skal dekke HELE tomtens utstrekning fra nord til sor
6. Mellom to lameller i samme rad: 8-12m aapning (portrom/gjennomgang)
7. Skap et hierarki: to-tre lameller rammer inn et "naabolagstorg" (30x40m aapent felt)
8. Lavest mot eksisterende smaabebyggelse, hoyest der tomten er mest aapen
9. Plasser {max(4, min(10, int(target_bta_m2 / (45*13*fl_est)) + 1))} lameller

KVALITETSSJEKK: Alle leiligheter skal ha solfasade. Mellomrommene skal vaere brede nok til at solen naar bakken kl 12 vaarjevndoegn.""",

        "Rekke": f"""
BEBYGGELSESKONSEPT: REKKEHUS-LANDSBY
Tenk Svartlamoen/Lillestroem: tette rader med private hager og felles groentdrag.

ORGANISERING:
1. Parallelle rader orientert OST-VEST slik at alle faar sorfasade
2. Hver rad: 40-55m lang, 9-11m dyp
3. 16-20m mellom rader (plass til privat hage + gangsti)
4. Spre radene over HELE tomtens nord-sor-utstrekning
5. Felles groentdrag/lekeomraade sentralt — bryt opp en rad i midten for aa lage en "landsbyplass"
6. Laveste rader (2 et) naermest naboer, hoyere (3-4 et) mot midten
7. Plasser {max(5, min(12, int(target_bta_m2 / (50*10*3)) + 1))} rader

KVALITETSSJEKK: Enhver rad skal ha direkte soltilgang uten aa bli skyggelagt av raden foran.""",

        "Tun": f"""
BEBYGGELSESKONSEPT: TUNBEBYGGELSE
Tenk tradisjonelt norsk tun: bygninger rundt et skjermet uterom.

ORGANISERING:
1. Del tomten i 2-3 TUN-GRUPPER, hver bestaaende av:
   - 1 HOVEDBYGG (35-45m langt, 10-12m dypt, role="main") som "rygg" mot nord/ost
   - 1-2 SIDEFLOYER (20-30m lange, role="wing") vinkelrett, danner U-form
2. Tunet (det innrammede uterommet) skal AAPNE MOT SOR/SORVEST for maks sol
3. Hvert tun-rom: ca 25-35m x 25-35m — stort nok for lek og opphold
4. Mellom tun-gruppene: 20-30m med felles groentstruktur
5. Hovedbygget typisk {fl_est} etasjer, sidefloyer {max(2, fl_est-1)}-{fl_est-1} etasjer (trapper ned mot solen)
6. Plasser {max(2, min(4, int(target_bta_m2 / (3000*fl_est/4)) + 1))} tungrupper

KVALITETSSJEKK: Hvert tun skal ha en tydelig "innside" (skjermet, solrikt) og "utside" (mot gate/nabo).""",

        "Tårn": f"""
BEBYGGELSESKONSEPT: TAARNLANDSKAP
Tenk Barcode/Bjoervika: vertikale volumer med maksimal avstand og sikt.

ORGANISERING:
1. Plasser taarnene i et AAPENT GRID med minimum 30-40m mellom hvert taarn
2. Hvert taarn: 20-25m x 20-25m fotavtrykk
3. Spread taarnene langs tomtens ytterkant for aa frigjore sentrum til torg/park
4. VARIER HOYDE DRAMATISK: fra {max(4, fl_est-4)} til {fl_est} etasjer
5. Hoyeste taarn i sorvest (minst skygge), laveste i nordost
6. Mellom taarnene: aapent terreng med siktlinjer mot omgivelsene
7. Sentralt felles torg/park der flest taarn har "adresse" mot
8. Plasser {max(3, min(6, int(target_bta_m2 / (22*22*fl_est)) + 1))} taarn

KVALITETSSJEKK: Fra hvert taarn skal du kunne se minst 2 andre taarn OG parken/torget.""",

        "Podium + Tårn": f"""
BEBYGGELSESKONSEPT: URBANT PODIUM MED TAARN
Tenk Bjoervika/Vulkan: lavt, aktivt sokkelplan med vertikale aksentbygg.

ORGANISERING:
1. 1-2 PODIER (40-55m x 18-25m, 2-3 etasjer, role="main") danner et sammenhengende gategrep
2. 1-2 TAARN (16-20m x 16-20m, {fl_est} etasjer, role="tower") plassert OPPAA podiet
3. Podiet skaper urban fasade mot gate/adkomst
4. Taarnet plasseres i podiumets mest eksponerte hjorne (sorvest for sikt, nordost for aa unngaa skygge)
5. Mellom podier: offentlig passasje/smug (8-12m) med gjennomgang
6. Takhage paa podiet rundt taarnet
7. Plasser {max(1, min(3, int(target_bta_m2 / (4000*fl_est/5)) + 1))} podium+taarn-kombinasjoner

KVALITETSSJEKK: Podiet skal skape et "gaterom", taarnet skal vaere et landemerke synlig fra avstand.""",
    }

    concept = typo_master.get(typology, typo_master["Lamell"])

    return f"""Du er Norges fremste arkitekt for boligutvikling og volumstudier. Du har vunnet Statens byggeskikkpris
og er kjent for aa skape bebyggelse der bygninger og uterom er LIKE VIKTIGE.

TOMT: {site_area_m2:.0f}m2 totalt, {buildable_area_m2:.0f}m2 byggbart
REGULERING: BYA {max_bya_pct:.0f}% (maks {max_footprint:.0f}m2 samlet fotavtrykk), {max_floors} et, {max_height_m:.0f}m
MAL: {target_bta_m2:.0f}m2 BTA | Etasjehoyde: {floor_to_floor_m}m | Estimert ~{fl_est} etasjer
TERRENG: {terrain_text} | BREDDEGRAD: {latitude_deg:.1f} (lav sol, sorfasade er livsnoedvendig)
{neighbor_summary}
Bygg PÅ tomten rives. Nabobygg utenfor tomtegrensen er UROERLIGE.

SONEOVERSIKT (for aa forstaa tomtens romlige struktur):
{chr(10).join(zone_summary) if zone_summary else "  Ingen soner beregnet"}
{concept}

TILGJENGELIGE POSISJONER (velg fra disse, juster +/- 15m):
{chr(10).join(pos_lines)}

ABSOLUTTE REGLER:
1. cx/cy SKAL vaere innenfor det byggbare omraadet (bruk posisjonslisten)
2. Maks samlet fotavtrykk: {max_footprint:.0f}m2
3. Maks {max_floors} etasjer / {max_height_m:.0f}m per bygg
4. Nabobygg utenfor tomten SKAL respekteres — plasser nye bygg min 8m fra tomtegrense
5. UTEROMMET ER LIKE VIKTIG SOM BYGNINGENE — vis at du har tenkt paa det i "notes"

Returner BARE JSON-array. Ingen annen tekst.
[{{"pos_id":"P005","name":"Lamell A","cx":0.0,"cy":0.0,"width":48,"depth":14,"angle_deg":195,"floors":{fl_est},"role":"main"{courtyard_json},"notes":"Rammer inn nordkant av naabolagstorget"}}]
"""


def _call_claude(prompt, api_key, model=DEFAULT_MODEL):
    try:
        resp = requests.post(ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4},
            timeout=90)
        resp.raise_for_status()
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception as exc:
        print(f"[ai_site_planner] API feilet: {exc}")
    return None


def _parse_buildings_json(text):
    if not text: return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.split("\n") if not l.strip().startswith("```"))
    s, e = cleaned.find("["), cleaned.rfind("]")
    if s < 0 or e < 0: return []
    try: return json.loads(cleaned[s:e+1])
    except: return []


def _make_building_polygon(cx, cy, width, depth, angle_deg):
    if not HAS_SHAPELY: return None
    rad = math.radians(angle_deg)
    hw, hd = width/2, depth/2
    ca, sa = math.cos(rad), math.sin(rad)
    corners = [(cx+hw*ca-hd*sa, cy+hw*sa+hd*ca), (cx-hw*ca-hd*sa, cy-hw*sa+hd*ca),
               (cx-hw*ca+hd*sa, cy-hw*sa-hd*ca), (cx+hw*ca+hd*sa, cy+hw*sa-hd*ca)]
    p = Polygon(corners)
    return p if p.is_valid and not p.is_empty else None


def _validate_and_build(raw_buildings, buildable_polygon, max_bya_m2, floor_to_floor_m, max_height_m, max_floors):
    if not HAS_SHAPELY or not raw_buildings: return []
    results, placed_union, total_fp = [], None, 0.0

    for bld in raw_buildings:
        try:
            cx, cy = float(bld["cx"]), float(bld["cy"])
            width = max(6, min(65, float(bld.get("width", 14))))
            depth = max(6, min(55, float(bld.get("depth", 14))))
            angle = float(bld.get("angle_deg", 0))
            floors = max(1, min(max_floors, int(bld.get("floors", 4))))
            name = str(bld.get("name", f"Bygg {len(results)+1}"))
            role = str(bld.get("role", "main"))
            notes = str(bld.get("notes", ""))
            height_m = min(max_height_m, floors * floor_to_floor_m)

            poly = _make_building_polygon(cx, cy, width, depth, angle)
            if not poly or poly.area < 20: continue

            clipped = poly.intersection(buildable_polygon).buffer(0)
            if clipped.is_empty or clipped.area < 20:
                bp_cx, bp_cy = buildable_polygon.centroid.x, buildable_polygon.centroid.y
                for t in [0.2, 0.4, 0.6]:
                    p2 = _make_building_polygon(cx+(bp_cx-cx)*t, cy+(bp_cy-cy)*t, width, depth, angle)
                    if p2:
                        c2 = p2.intersection(buildable_polygon).buffer(0)
                        if not c2.is_empty and c2.area > poly.area * 0.5:
                            clipped = c2; break
                if clipped.is_empty or clipped.area < 20: continue

            if placed_union and clipped.intersection(placed_union).area > clipped.area * 0.1: continue
            if total_fp + clipped.area > max_bya_m2 * 1.05: continue

            is_karre = bld.get("courtyard") or "karre" in name.lower() or "kvartal" in name.lower()
            if is_karre and clipped.area > 250:
                rd = max(8, min(float(bld.get("ring_depth", 11)), math.sqrt(clipped.area)*0.22))
                inner = clipped.buffer(-rd)
                if inner and not inner.is_empty and inner.area > 30:
                    clipped = clipped.difference(inner).buffer(0)

            placed_union = clipped if placed_union is None else placed_union.union(clipped).buffer(0)
            total_fp += clipped.area

            results.append({"polygon": clipped, "name": name, "role": role, "floors": floors,
                          "height_m": round(height_m, 1), "width_m": round(width, 1),
                          "depth_m": round(depth, 1), "angle_deg": round(angle, 1),
                          "area_m2": round(float(clipped.area), 1), "notes": notes,
                          "pos_id": bld.get("pos_id", "")})
        except (KeyError, ValueError, TypeError): continue
    return results


def plan_site(site_polygon, buildable_polygon, typology, *, neighbors=None, terrain=None,
              site_intelligence=None, site_inputs=None, target_bta_m2=5000.0, max_floors=5,
              max_height_m=16.0, max_bya_pct=35.0, floor_to_floor_m=3.2, model=DEFAULT_MODEL):
    api_key = _get_api_key()
    if not api_key:
        return {"buildings": [], "footprint": None, "error": "ANTHROPIC_API_KEY ikke satt"}
    if not HAS_SHAPELY or buildable_polygon is None:
        return {"buildings": [], "footprint": None, "error": "Shapely/polygon mangler"}

    latitude = float((site_inputs or {}).get('latitude_deg', 63.4))
    site_area = float(site_polygon.area) if site_polygon else 1000
    bp_area = float(buildable_polygon.area)
    max_fp = bp_area * (max_bya_pct / 100.0)

    grid_sp = max(15, min(30, math.sqrt(bp_area) / 8))
    all_pos = _compute_placement_grid(buildable_polygon, site_polygon, neighbors or [], terrain, latitude, grid_sp)
    ranked = _rank_positions(all_pos)
    if not ranked:
        return {"buildings": [], "footprint": None, "error": "Ingen gyldige posisjoner"}

    nb_lines = []
    for nb in (neighbors or [])[:12]:
        p = nb.get('polygon')
        if p and site_polygon:
            if p.intersection(site_polygon).area / max(float(p.area), 1) >= 0.3: continue
            nc, sc = p.centroid, site_polygon.centroid
            ang = math.degrees(math.atan2(nc.y-sc.y, nc.x-sc.x)) % 360
            compass = ["O","NO","N","NV","V","SV","S","SO"][int((ang+22.5)/45)%8]
            nb_lines.append(f"  {compass}: {nb.get('height_m',9):.0f}m, {nb.get('distance_m',0):.0f}m unna")
    nb_text = "NABOER (uroerlige):\n" + "\n".join(nb_lines[:10]) if nb_lines else "NABOER: Ingen naere"

    prompt = _build_prompt(ranked[:50], typology, target_bta_m2, max_floors, max_height_m,
                           max_bya_pct, floor_to_floor_m, site_area, bp_area, latitude,
                           terrain, site_intelligence, nb_text)

    raw = _call_claude(prompt, api_key, model)
    if not raw:
        return {"buildings": [], "footprint": None, "prompt": prompt, "error": "Ingen svar fra Claude"}

    parsed = _parse_buildings_json(raw)
    if not parsed:
        return {"buildings": [], "footprint": None, "prompt": prompt, "raw_response": raw, "error": "Parse feilet"}

    buildings = _validate_and_build(parsed, buildable_polygon, max_fp, floor_to_floor_m, max_height_m, max_floors)

    footprint = None
    if buildings:
        polys = [b["polygon"] for b in buildings if b.get("polygon")]
        if polys: footprint = unary_union(polys).buffer(0)

    return {"buildings": buildings, "footprint": footprint, "building_count": len(buildings),
            "total_footprint_m2": round(sum(b.get("area_m2",0) for b in buildings), 1),
            "total_bta_m2": round(sum(b.get("area_m2",0)*b.get("floors",1) for b in buildings), 1),
            "source": f"AI (Claude {model})", "positions_evaluated": len(all_pos),
            "positions_usable": len(ranked), "prompt": prompt, "raw_response": raw, "raw_parsed": parsed}
