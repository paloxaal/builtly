"""
builtly_drofus_integration.py
─────────────────────────────
dRofus romprogrammering-integrasjon for Builtly.

Henter romprogram (romtyper, arealkrav, funksjonskrav, lydklasser,
brannkrav) fra dRofus REST API og verifiserer mot IFC-analysens
IfcSpace-data eller manuelt registrerte rom.

Funksjoner:
  1. Hent romprogram fra dRofus API
  2. Match programmerte rom mot faktiske rom (IFC / manuell)
  3. Generer avviksrapport (areal, funksjon, akustikk, brann, tilgjengelighet)
  4. Eksporter avvik som BCF-issues

dRofus API docs: https://www.drofus.com/api
API base URL: https://{tenant}.drofus.com/api/v1

Bruk:
    from builtly_drofus_integration import DrofusClient, verify_room_program

    client = DrofusClient(base_url="https://firma.drofus.com/api/v1", api_key="...")
    program = client.get_room_program(project_id="abc123")
    
    # Fra IFC-analyse:
    from builtly_ifc_analyzer import analyze_ifc
    ifc = analyze_ifc("model.ifc")
    
    report = verify_room_program(program, ifc["rooms"])
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("builtly.drofus")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ─── Dataklasser ────────────────────────────────────────────────────

@dataclass
class DrofusRoomRequirement:
    """Krav knyttet til et rom fra dRofus."""
    category: str          # areal, akustikk, brann, klima, tilgjengelighet, funksjon
    parameter: str         # f.eks. "Minimum areal", "Lydklasse", "Brannmotstand"
    required_value: str    # f.eks. "15.0", "B", "EI 60"
    unit: str = ""         # f.eks. "m²", "dB", "min"
    tek17_ref: str = ""    # f.eks. "§13-6", "§11-8"
    ns_ref: str = ""       # f.eks. "NS 8175:2019"


@dataclass
class DrofusRoom:
    """Et programmert rom fra dRofus."""
    drofus_id: str
    room_number: str       # Romnummer (f.eks. "101", "2.04")
    room_name: str         # Romnavn (f.eks. "Stue", "Bad hovedsoverom")
    room_type: str         # Romtype-kode (f.eks. "A1.1", "C2.3")
    room_type_name: str    # Romtype-navn (f.eks. "Soverom", "Bad/WC")
    department: str = ""   # Avdeling/sone
    floor: str = ""        # Etasje
    area_program: float = 0.0      # Programmert nettoareal (m²)
    area_tolerance: float = 0.10   # Akseptabel avvik (default 10%)
    height_min: float = 0.0        # Minimum romhøyde (m)
    persons: int = 0               # Dimensjonerende personantall
    requirements: List[DrofusRoomRequirement] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)

    # Normalisert romtype for matching
    @property
    def normalized_type(self) -> str:
        return _normalize_room_type(self.room_type_name or self.room_name)


@dataclass
class RoomMatch:
    """Resultat av matching mellom dRofus-rom og faktisk rom."""
    drofus_room: DrofusRoom
    matched_room: Optional[Dict[str, Any]]  # Fra IFC/manuell
    match_score: float                       # 0-1
    match_method: str                        # "number", "name", "type+area", "unmatched"
    deviations: List[RoomDeviation] = field(default_factory=list)


@dataclass
class RoomDeviation:
    """Avvik mellom programmert og faktisk rom."""
    category: str           # areal, akustikk, brann, tilgjengelighet, funksjon
    parameter: str
    required: str
    actual: str
    severity: str           # critical, warning, info
    description: str
    tek17_ref: str = ""
    ns_ref: str = ""


# ─── Romtype-normalisering ──────────────────────────────────────────

_ROOM_TYPE_MAP = {
    # Bad/WC
    "bad": "bad", "wc": "bad", "toalett": "bad", "bathroom": "bad",
    "dusj": "bad", "baderom": "bad", "do": "bad",
    # Soverom
    "soverom": "soverom", "bedroom": "soverom", "sov": "soverom",
    "hovedsoverom": "soverom", "gjesterom": "soverom",
    # Stue/opphold
    "stue": "stue", "oppholdsrom": "stue", "living": "stue",
    "dagligstue": "stue", "tv-stue": "stue", "allrom": "stue",
    # Kjøkken
    "kjøkken": "kjoekken", "kjøkken": "kjoekken", "kitchen": "kjoekken",
    "kokken": "kjoekken", "kjk": "kjoekken",
    # Gang/korridor
    "gang": "gang", "korridor": "gang", "hall": "gang", "corridor": "gang",
    # Entré
    "entre": "entre", "entré": "entre", "entrance": "entre", "inngang": "entre",
    # Bod/lager
    "bod": "bod", "lager": "bod", "storage": "bod", "garderobe": "bod",
    # Vaskerom
    "vaskerom": "vaskerom", "laundry": "vaskerom", "vask": "vaskerom",
    # Trapp
    "trapp": "trapp", "trapperom": "trapp", "stair": "trapp",
    # Teknisk
    "teknisk": "teknisk", "technical": "teknisk", "tekn": "teknisk",
    # Kontor
    "kontor": "kontor", "office": "kontor",
    # Møterom
    "møterom": "moeterom", "meeting": "moeterom",
}


def _normalize_room_type(name: str) -> str:
    """Normaliser romnavn til standard type."""
    low = name.lower().strip()
    for key, val in _ROOM_TYPE_MAP.items():
        if key in low:
            return val
    return "annet"


# ─── Lydklasse-krav (NS 8175:2019) ─────────────────────────────────

NS8175_REQUIREMENTS = {
    "soverom": {
        "lydklasse_C": {"Lp_A_eq": 30, "Lp_A_max": 45, "R_w": 55, "L_n_w": 53},
        "lydklasse_B": {"Lp_A_eq": 26, "Lp_A_max": 40, "R_w": 58, "L_n_w": 48},
        "lydklasse_A": {"Lp_A_eq": 22, "Lp_A_max": 35, "R_w": 61, "L_n_w": 43},
    },
    "stue": {
        "lydklasse_C": {"Lp_A_eq": 35, "Lp_A_max": 45, "R_w": 55, "L_n_w": 53},
        "lydklasse_B": {"Lp_A_eq": 30, "Lp_A_max": 40, "R_w": 58, "L_n_w": 48},
    },
    "kjoekken": {
        "lydklasse_C": {"Lp_A_eq": 40, "Lp_A_max": 50, "R_w": 50, "L_n_w": 58},
    },
    "bad": {
        "lydklasse_C": {"Lp_A_eq": 40, "Lp_A_max": 50, "R_w": 52, "L_n_w": 58},
    },
    "kontor": {
        "lydklasse_C": {"Lp_A_eq": 38, "Lp_A_max": 45, "R_w": 40, "L_n_w": 58},
        "lydklasse_B": {"Lp_A_eq": 33, "Lp_A_max": 40, "R_w": 44, "L_n_w": 53},
    },
}


# ─── TEK17 minimumskrav per romtype ────────────────────────────────

TEK17_ROOM_REQUIREMENTS = {
    "bad": {
        "min_area": 3.3,    # §12-9
        "min_width": 1.5,   # tilgjengelighet
        "min_door_width": 0.9,
        "tek17_ref": "§12-9",
    },
    "soverom": {
        "min_area": 5.7,    # §12-7 (anbefalt)
        "min_width": 2.0,
        "min_door_width": 0.9,
        "tek17_ref": "§12-7",
    },
    "stue": {
        "min_area": 7.0,    # anbefalt
        "min_width": 2.4,
        "tek17_ref": "§12-7",
    },
    "kjoekken": {
        "min_area": 5.0,
        "min_width": 1.8,
        "tek17_ref": "§12-7",
    },
    "gang": {
        "min_width": 1.2,   # §12-6 (tilgjengelig bolig: 1.5m)
        "tek17_ref": "§12-6",
    },
    "entre": {
        "min_area": 3.0,
        "min_width": 1.5,   # snusirkel rullestol
        "tek17_ref": "§12-6",
    },
}


# ─── dRofus API-klient ──────────────────────────────────────────────

class DrofusClient:
    """
    Klient for dRofus REST API.
    
    Args:
        base_url: API base URL, f.eks. "https://firma.drofus.com/api/v1"
        api_key: API-nøkkel (Bearer token)
        timeout: Request timeout i sekunder
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            })

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """HTTP GET med feilhåndtering."""
        if not self.session:
            raise RuntimeError("requests-biblioteket er ikke installert")
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            logger.error(f"dRofus API HTTP-feil: {exc}")
            raise
        except requests.exceptions.ConnectionError as exc:
            logger.error(f"dRofus API tilkoblingsfeil: {exc}")
            raise

    def get_projects(self) -> List[Dict[str, Any]]:
        """Hent alle tilgjengelige prosjekter."""
        return self._get("projects")

    def get_rooms(self, project_id: str) -> List[Dict[str, Any]]:
        """Hent alle rom i et prosjekt."""
        return self._get(f"projects/{project_id}/rooms")

    def get_room_types(self, project_id: str) -> List[Dict[str, Any]]:
        """Hent alle romtyper i et prosjekt."""
        return self._get(f"projects/{project_id}/room-types")

    def get_room_requirements(self, project_id: str, room_id: str) -> List[Dict[str, Any]]:
        """Hent krav for et spesifikt rom."""
        return self._get(f"projects/{project_id}/rooms/{room_id}/requirements")

    def get_room_program(self, project_id: str) -> List[DrofusRoom]:
        """
        Hent komplett romprogram som DrofusRoom-objekter.
        
        Mapper dRofus API-respons til Builtly-intern modell.
        """
        raw_rooms = self.get_rooms(project_id)
        rooms = []

        for raw in raw_rooms:
            requirements = []

            # Ekstraher krav fra dRofus-attributter
            attrs = raw.get("attributes", raw.get("properties", {}))

            # Arealkrav
            area = _safe_float(attrs.get("area_program", attrs.get("net_area", attrs.get("planned_area", 0))))
            if area > 0:
                requirements.append(DrofusRoomRequirement(
                    category="areal",
                    parameter="Programmert nettoareal",
                    required_value=str(area),
                    unit="m²",
                ))

            # Høydekrav
            height = _safe_float(attrs.get("room_height", attrs.get("clear_height", 0)))
            if height > 0:
                requirements.append(DrofusRoomRequirement(
                    category="areal",
                    parameter="Minimum romhøyde",
                    required_value=str(height),
                    unit="m",
                ))

            # Lydklasse
            sound_class = attrs.get("sound_class", attrs.get("lydklasse", ""))
            if sound_class:
                requirements.append(DrofusRoomRequirement(
                    category="akustikk",
                    parameter="Lydklasse",
                    required_value=str(sound_class),
                    ns_ref="NS 8175:2019",
                ))

            # Brannmotstand
            fire_rating = attrs.get("fire_rating", attrs.get("brannmotstand", ""))
            if fire_rating:
                requirements.append(DrofusRoomRequirement(
                    category="brann",
                    parameter="Brannmotstand vegger",
                    required_value=str(fire_rating),
                    tek17_ref="§11-8",
                ))

            # Personbelastning
            persons = int(_safe_float(attrs.get("persons", attrs.get("personbelastning", 0))))

            room = DrofusRoom(
                drofus_id=str(raw.get("id", "")),
                room_number=str(raw.get("room_number", raw.get("number", ""))),
                room_name=str(raw.get("name", raw.get("room_name", ""))),
                room_type=str(raw.get("room_type_code", raw.get("type_code", ""))),
                room_type_name=str(raw.get("room_type_name", raw.get("type_name", ""))),
                department=str(raw.get("department", raw.get("zone", ""))),
                floor=str(raw.get("floor", raw.get("storey", ""))),
                area_program=area,
                height_min=height,
                persons=persons,
                requirements=requirements,
                properties=attrs,
            )
            rooms.append(room)

        return rooms


# ─── Offline / manuell romprogram-input ─────────────────────────────

def parse_room_program_from_excel(filepath: str) -> List[DrofusRoom]:
    """
    Parse romprogram fra Excel-fil (vanlig norsk format).
    
    Forventer kolonner: Romnr, Romnavn, Romtype, Areal (m²), Etasje,
                        Lydklasse, Brannmotstand, Personantall
    """
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl trengs for Excel-import: pip install openpyxl")
        return []

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    if not rows:
        return []

    # Finn header-rad
    header = [str(c or "").lower().strip() for c in rows[0]]
    col_map = {}
    for i, h in enumerate(header):
        if any(k in h for k in ["romnr", "rom nr", "room number", "nummer"]):
            col_map["number"] = i
        elif any(k in h for k in ["romnavn", "rom navn", "room name", "navn"]):
            col_map["name"] = i
        elif any(k in h for k in ["romtype", "type"]):
            col_map["type"] = i
        elif any(k in h for k in ["areal", "area", "m²", "m2"]):
            col_map["area"] = i
        elif any(k in h for k in ["etasje", "floor", "plan"]):
            col_map["floor"] = i
        elif any(k in h for k in ["lydklasse", "lyd", "sound"]):
            col_map["sound"] = i
        elif any(k in h for k in ["brann", "fire", "ei "]):
            col_map["fire"] = i
        elif any(k in h for k in ["person", "pers", "belegg"]):
            col_map["persons"] = i
        elif any(k in h for k in ["høyde", "height"]):
            col_map["height"] = i

    rooms = []
    for row in rows[1:]:
        if not row or not any(row):
            continue

        def get_col(key, default=""):
            idx = col_map.get(key)
            if idx is not None and idx < len(row) and row[idx] is not None:
                return row[idx]
            return default

        number = str(get_col("number", ""))
        name = str(get_col("name", ""))
        if not number and not name:
            continue

        area = _safe_float(get_col("area", 0))
        requirements = []

        if area > 0:
            requirements.append(DrofusRoomRequirement(
                category="areal", parameter="Programmert nettoareal",
                required_value=str(area), unit="m²",
            ))

        sound = str(get_col("sound", ""))
        if sound:
            requirements.append(DrofusRoomRequirement(
                category="akustikk", parameter="Lydklasse",
                required_value=sound, ns_ref="NS 8175:2019",
            ))

        fire = str(get_col("fire", ""))
        if fire:
            requirements.append(DrofusRoomRequirement(
                category="brann", parameter="Brannmotstand",
                required_value=fire, tek17_ref="§11-8",
            ))

        height = _safe_float(get_col("height", 0))

        room = DrofusRoom(
            drofus_id=f"excel_{len(rooms)}",
            room_number=number,
            room_name=name,
            room_type="",
            room_type_name=str(get_col("type", "")),
            floor=str(get_col("floor", "")),
            area_program=area,
            height_min=height,
            persons=int(_safe_float(get_col("persons", 0))),
            requirements=requirements,
        )
        rooms.append(room)

    wb.close()
    return rooms


# ─── Rommatching ────────────────────────────────────────────────────

def _safe_float(val: Any) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _similarity(a: str, b: str) -> float:
    """Streng-likhet mellom to navn."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def match_rooms(
    program_rooms: List[DrofusRoom],
    actual_rooms: List[Dict[str, Any]],
) -> List[RoomMatch]:
    """
    Match programmerte rom (dRofus) mot faktiske rom (IFC/manuell).
    
    actual_rooms format (fra builtly_ifc_analyzer.py):
        [{"name": "Bad 1", "type": "bad", "storey": "1. etasje",
          "area_m2": 4.2, "height_m": 2.4, "width_m": 2.1, "depth_m": 2.0}]
    
    Matching-strategi:
        1. Eksakt romnummer-match
        2. Navnelikhet (>0.7)
        3. Type + nærmeste areal
        4. Unmatched
    """
    matched_actual = set()
    results = []

    # Pass 1: Eksakt romnummer
    for prog in program_rooms:
        if not prog.room_number:
            continue
        for i, actual in enumerate(actual_rooms):
            if i in matched_actual:
                continue
            actual_number = str(actual.get("room_number", actual.get("name", "")))
            if prog.room_number == actual_number:
                results.append(RoomMatch(
                    drofus_room=prog, matched_room=actual,
                    match_score=1.0, match_method="number",
                ))
                matched_actual.add(i)
                break

    # Pass 2: Navnelikhet
    unmatched_prog = [p for p in program_rooms if not any(r.drofus_room == p for r in results)]
    for prog in unmatched_prog:
        best_score = 0.0
        best_idx = -1
        for i, actual in enumerate(actual_rooms):
            if i in matched_actual:
                continue
            score = _similarity(prog.room_name, actual.get("name", ""))
            if score > best_score and score > 0.6:
                best_score = score
                best_idx = i

        if best_idx >= 0:
            results.append(RoomMatch(
                drofus_room=prog, matched_room=actual_rooms[best_idx],
                match_score=best_score, match_method="name",
            ))
            matched_actual.add(best_idx)

    # Pass 3: Type + areal
    unmatched_prog = [p for p in program_rooms if not any(r.drofus_room == p for r in results)]
    for prog in unmatched_prog:
        norm_type = prog.normalized_type
        candidates = [
            (i, a) for i, a in enumerate(actual_rooms)
            if i not in matched_actual and _normalize_room_type(a.get("type", a.get("name", ""))) == norm_type
        ]
        if candidates:
            # Velg nærmeste areal
            if prog.area_program > 0:
                candidates.sort(key=lambda c: abs(_safe_float(c[1].get("area_m2", 0)) - prog.area_program))
            best_i, best_a = candidates[0]
            area_diff = abs(_safe_float(best_a.get("area_m2", 0)) - prog.area_program)
            score = max(0.3, 1.0 - area_diff / max(prog.area_program, 1))
            results.append(RoomMatch(
                drofus_room=prog, matched_room=best_a,
                match_score=score, match_method="type+area",
            ))
            matched_actual.add(best_i)

    # Unmatched
    unmatched_prog = [p for p in program_rooms if not any(r.drofus_room == p for r in results)]
    for prog in unmatched_prog:
        results.append(RoomMatch(
            drofus_room=prog, matched_room=None,
            match_score=0.0, match_method="unmatched",
        ))

    return results


# ─── Avviksanalyse ──────────────────────────────────────────────────

def analyze_deviations(matches: List[RoomMatch]) -> List[RoomMatch]:
    """
    Analyser avvik mellom programmerte og faktiske rom.
    Sjekker: areal, høyde, tilgjengelighet (bredde), TEK17-minimumskrav,
    akustikk-krav (NS 8175), brannkrav.
    """
    for match in matches:
        deviations = []
        prog = match.drofus_room
        actual = match.matched_room

        if actual is None:
            deviations.append(RoomDeviation(
                category="funksjon",
                parameter="Rom eksisterer ikke",
                required=f"{prog.room_name} ({prog.room_number})",
                actual="Ikke funnet i modell",
                severity="critical",
                description=f"Programmert rom '{prog.room_name}' ({prog.room_number}) finnes ikke i modellen.",
            ))
            match.deviations = deviations
            continue

        actual_area = _safe_float(actual.get("area_m2", 0))
        actual_height = _safe_float(actual.get("height_m", 0))
        actual_width = _safe_float(actual.get("width_m", 0))
        actual_depth = _safe_float(actual.get("depth_m", 0))
        min_dim = min(actual_width, actual_depth) if actual_width > 0 and actual_depth > 0 else 0
        norm_type = prog.normalized_type

        # ── Arealsjekk mot programmet ──
        if prog.area_program > 0 and actual_area > 0:
            diff = actual_area - prog.area_program
            diff_pct = diff / prog.area_program
            tolerance = prog.area_tolerance

            if diff_pct < -tolerance:
                deviations.append(RoomDeviation(
                    category="areal",
                    parameter="Nettoareal under program",
                    required=f"{prog.area_program:.1f} m² (±{tolerance*100:.0f}%)",
                    actual=f"{actual_area:.1f} m² ({diff_pct*100:+.1f}%)",
                    severity="critical" if diff_pct < -0.2 else "warning",
                    description=f"{prog.room_name}: {actual_area:.1f} m² er {abs(diff):.1f} m² under programmert {prog.area_program:.1f} m².",
                ))
            elif diff_pct > tolerance * 2:
                deviations.append(RoomDeviation(
                    category="areal",
                    parameter="Nettoareal vesentlig over program",
                    required=f"{prog.area_program:.1f} m²",
                    actual=f"{actual_area:.1f} m² ({diff_pct*100:+.1f}%)",
                    severity="info",
                    description=f"{prog.room_name}: {actual_area:.1f} m² er {diff:.1f} m² over programmert. Vurder arealeffektivitet.",
                ))

        # ── TEK17 minimumskrav ──
        tek_req = TEK17_ROOM_REQUIREMENTS.get(norm_type)
        if tek_req:
            if actual_area > 0 and actual_area < tek_req.get("min_area", 0):
                deviations.append(RoomDeviation(
                    category="tilgjengelighet",
                    parameter="Under TEK17 minsteareal",
                    required=f"{tek_req['min_area']:.1f} m²",
                    actual=f"{actual_area:.1f} m²",
                    severity="critical",
                    description=f"{prog.room_name}: {actual_area:.1f} m² < {tek_req['min_area']:.1f} m² (TEK17 {tek_req['tek17_ref']}).",
                    tek17_ref=tek_req["tek17_ref"],
                ))

            if min_dim > 0 and min_dim < tek_req.get("min_width", 0):
                deviations.append(RoomDeviation(
                    category="tilgjengelighet",
                    parameter="For smal",
                    required=f"{tek_req['min_width']:.1f} m bredde",
                    actual=f"{min_dim:.1f} m",
                    severity="critical",
                    description=f"{prog.room_name}: bredde {min_dim:.1f}m < {tek_req['min_width']:.1f}m (TEK17 {tek_req['tek17_ref']}).",
                    tek17_ref=tek_req["tek17_ref"],
                ))

        # ── Romhøyde ──
        if prog.height_min > 0 and actual_height > 0 and actual_height < prog.height_min:
            deviations.append(RoomDeviation(
                category="areal",
                parameter="Romhøyde under krav",
                required=f"{prog.height_min:.2f} m",
                actual=f"{actual_height:.2f} m",
                severity="warning",
                description=f"{prog.room_name}: romhøyde {actual_height:.2f}m < programmert {prog.height_min:.2f}m.",
            ))

        # ── Akustikk (lydklasse-krav fra dRofus) ──
        for req in prog.requirements:
            if req.category == "akustikk" and req.parameter == "Lydklasse":
                ns_req = NS8175_REQUIREMENTS.get(norm_type, {})
                class_key = f"lydklasse_{req.required_value}"
                if class_key in ns_req:
                    vals = ns_req[class_key]
                    deviations.append(RoomDeviation(
                        category="akustikk",
                        parameter=f"Lydklasse {req.required_value} krav",
                        required=f"R'w ≥ {vals.get('R_w', '?')} dB, L'n,w ≤ {vals.get('L_n_w', '?')} dB",
                        actual="Verifiser mot akustikkprosjektering",
                        severity="info",
                        description=(
                            f"{prog.room_name} krever lydklasse {req.required_value} (NS 8175:2019): "
                            f"R'w ≥ {vals.get('R_w', '?')} dB, L'n,w ≤ {vals.get('L_n_w', '?')} dB, "
                            f"Lp,A,eq ≤ {vals.get('Lp_A_eq', '?')} dB."
                        ),
                        ns_ref="NS 8175:2019",
                    ))

            # Brannkrav
            if req.category == "brann":
                deviations.append(RoomDeviation(
                    category="brann",
                    parameter=req.parameter,
                    required=req.required_value,
                    actual="Verifiser mot brannprosjektering",
                    severity="info",
                    description=f"{prog.room_name}: krav til {req.parameter} = {req.required_value}.",
                    tek17_ref=req.tek17_ref,
                ))

        match.deviations = deviations

    return matches


# ─── Hovedfunksjon ──────────────────────────────────────────────────

def verify_room_program(
    program_rooms: List[DrofusRoom],
    actual_rooms: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Verifiser romprogram mot faktiske rom.
    
    Args:
        program_rooms: Fra DrofusClient.get_room_program() eller parse_room_program_from_excel()
        actual_rooms: Fra builtly_ifc_analyzer.analyze_ifc()["rooms"] eller manuelt
    
    Returns:
        {
            "matches": [RoomMatch as dict],
            "summary": {
                "total_programmed": int,
                "matched": int, "unmatched": int,
                "critical_deviations": int, "warnings": int,
                "total_area_programmed": float,
                "total_area_actual": float,
                "area_deviation_pct": float,
            },
            "deviations_by_category": {
                "areal": [...], "akustikk": [...], "brann": [...], etc.
            },
        }
    """
    matches = match_rooms(program_rooms, actual_rooms)
    matches = analyze_deviations(matches)

    # Sammendrag
    total_prog = len(program_rooms)
    matched = sum(1 for m in matches if m.matched_room is not None)
    unmatched = total_prog - matched

    all_devs = [d for m in matches for d in m.deviations]
    critical = sum(1 for d in all_devs if d.severity == "critical")
    warnings = sum(1 for d in all_devs if d.severity == "warning")

    total_area_prog = sum(r.area_program for r in program_rooms)
    total_area_actual = sum(
        _safe_float(m.matched_room.get("area_m2", 0))
        for m in matches if m.matched_room
    )
    area_dev_pct = (
        (total_area_actual - total_area_prog) / total_area_prog * 100
        if total_area_prog > 0 else 0
    )

    # Avvik per kategori
    devs_by_cat: Dict[str, List[Dict]] = {}
    for d in all_devs:
        devs_by_cat.setdefault(d.category, []).append(asdict(d))

    return {
        "matches": [
            {
                "drofus_room": asdict(m.drofus_room),
                "matched_room": m.matched_room,
                "match_score": m.match_score,
                "match_method": m.match_method,
                "deviations": [asdict(d) for d in m.deviations],
                "deviation_count": len(m.deviations),
                "has_critical": any(d.severity == "critical" for d in m.deviations),
            }
            for m in matches
        ],
        "summary": {
            "total_programmed": total_prog,
            "matched": matched,
            "unmatched": unmatched,
            "match_rate_pct": round(matched / max(total_prog, 1) * 100, 1),
            "critical_deviations": critical,
            "warnings": warnings,
            "info": sum(1 for d in all_devs if d.severity == "info"),
            "total_area_programmed_m2": round(total_area_prog, 1),
            "total_area_actual_m2": round(total_area_actual, 1),
            "area_deviation_pct": round(area_dev_pct, 1),
        },
        "deviations_by_category": devs_by_cat,
    }


def deviations_to_bcf_issues(report: Dict[str, Any]) -> List:
    """Konverter avviksrapport til BCF-issues (brukes med builtly_bcf_exporter)."""
    try:
        from builtly_bcf_exporter import BcfIssue
    except ImportError:
        return []

    issues = []
    for match in report.get("matches", []):
        for dev in match.get("deviations", []):
            severity_map = {"critical": "Error", "warning": "Warning", "info": "Info"}
            priority_map = {"critical": "High", "warning": "Normal", "info": "Low"}

            issues.append(BcfIssue(
                title=f"{dev['parameter']}: {dev.get('description', '')[:80]}",
                description=dev.get("description", ""),
                topic_type=severity_map.get(dev["severity"], "Info"),
                priority=priority_map.get(dev["severity"], "Normal"),
                module=dev["category"].capitalize(),
                tek17_ref=dev.get("tek17_ref", ""),
                labels=[dev["category"], "Romprogram", "dRofus"],
            ))

    return issues
