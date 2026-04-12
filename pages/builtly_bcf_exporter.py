"""
builtly_bcf_exporter.py
───────────────────────
BCF 2.1 eksport for Builtly.

Genererer BCF-filer (BIM Collaboration Format) fra analyseresultater.
Kompatibel med Solibri, BIMcollab, Navisworks, Revit, og andre
BCF-kompatible verktøy.

BCF 2.1 spesifikasjon: buildingSMART International
Filformat: ZIP-arkiv med XML + PNG viewpoints

Bruk:
    from builtly_bcf_exporter import create_bcf, BcfIssue

    issues = [
        BcfIssue(
            title="Bærevegg mangler i 2. etasje",
            description="Ingen vertikal lastoverføring mellom akse B og C.",
            topic_type="Error",
            priority="High",
            component_ifcguids=["2O2Fr$t4X7Zf8NOew3FLOH"],
        ),
    ]
    bcf_path = create_bcf(issues, "rib_analyse.bcf", project_name="Mitt prosjekt")
"""

from __future__ import annotations

import os
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element, SubElement, tostring

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── Dataklasser ────────────────────────────────────────────────────

@dataclass
class BcfViewpoint:
    """Kameraposisjon og synlighetsinformasjon."""
    camera_position: Optional[Dict[str, float]] = None    # {x, y, z}
    camera_direction: Optional[Dict[str, float]] = None   # {x, y, z}
    camera_up: Optional[Dict[str, float]] = None          # {x, y, z}
    field_of_view: float = 60.0
    snapshot_png: Optional[bytes] = None                   # PNG bytes for viewpoint
    component_ifcguids: List[str] = field(default_factory=list)  # Synlige/valgte IFC GUIDs


@dataclass
class BcfIssue:
    """En BCF-issue (topic) med valgfri viewpoint."""
    title: str
    description: str = ""
    topic_type: str = "Error"           # Error, Warning, Info, Request
    priority: str = "Normal"            # Critical, High, Normal, Low
    status: str = "Open"                # Open, In Progress, Closed
    assigned_to: str = ""
    due_date: str = ""                  # ISO format YYYY-MM-DD
    stage: str = ""                     # Prosjektfase
    labels: List[str] = field(default_factory=list)
    component_ifcguids: List[str] = field(default_factory=list)
    viewpoint: Optional[BcfViewpoint] = None
    reference_links: List[str] = field(default_factory=list)

    # Builtly-spesifikke felter (lagres som BIM Snippet)
    tek17_ref: str = ""                 # F.eks. "§12-9", "§10-2"
    module: str = "RIB"                 # RIB, Akustikk, Brann, TEK17
    severity_score: Optional[float] = None  # 0-1


# ─── BCF XML-generering ────────────────────────────────────────────

BCF_VERSION = "2.1"
BCF_NAMESPACE = "http://www.buildingsmart-tech.org/bcf/markup/2"
BCF_VP_NAMESPACE = "http://www.buildingsmart-tech.org/bcf/visinfo/2"

BUILTLY_AUTHOR = "Builtly RIB AI"
BUILTLY_TOOL_ID = "Builtly"


def _new_guid() -> str:
    """Generer BCF-kompatibel GUID."""
    return str(uuid.uuid4())


def _iso_now() -> str:
    """Nåtidsstempel i ISO 8601."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_placeholder_snapshot(
    title: str,
    topic_type: str = "Error",
    width: int = 800,
    height: int = 600,
) -> bytes:
    """Generer en enkel PNG-snapshot som placeholder for viewpoint."""
    if not PIL_AVAILABLE:
        # Minimal 1x1 transparent PNG
        import struct
        import zlib
        def _png_chunk(chunk_type, data):
            c = chunk_type + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        sig = b'\x89PNG\r\n\x1a\n'
        ihdr = _png_chunk(b'IHDR', struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        idat = _png_chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
        iend = _png_chunk(b'IEND', b'')
        return sig + ihdr + idat + iend

    # Builtly-styled snapshot
    type_colors = {
        "Error": "#ef4444",
        "Warning": "#f59e0b",
        "Info": "#38bdf8",
        "Request": "#a78bfa",
    }
    color = type_colors.get(topic_type, "#64748b")

    img = Image.new("RGB", (width, height), "#0d1b2a")
    draw = ImageDraw.Draw(img)

    # Background gradient effect
    for y in range(height):
        alpha = int(y / height * 30)
        draw.line([(0, y), (width, y)], fill=(13 + alpha, 27 + alpha, 42 + alpha))

    # Border
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(r, g, b), width=3)

    # Type badge
    draw.rounded_rectangle([20, 20, 160, 55], radius=6, fill=(r, g, b))

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_badge = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_badge = ImageFont.load_default()

    draw.text((30, 24), topic_type.upper(), fill="white", font=font_badge)

    # Builtly logo
    draw.text((width - 120, 25), "Builtly RIB", fill=(56, 189, 248), font=font_badge)

    # Title
    y_pos = 80
    # Word wrap
    words = title.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > 50:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)

    for line in lines[:4]:
        draw.text((30, y_pos), line, fill="white", font=font_large)
        y_pos += 28

    # Footer
    draw.text((30, height - 40), f"Generert av Builtly · {datetime.now().strftime('%Y-%m-%d')}", fill=(100, 116, 139), font=font_small)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _build_version_xml() -> bytes:
    """bcf.version XML."""
    root = Element("Version", VersionId=BCF_VERSION)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _build_project_xml(project_name: str, project_id: str) -> bytes:
    """project.bcfp XML."""
    root = Element("ProjectInfo")
    project = SubElement(root, "Project", ProjectId=project_id)
    name = SubElement(project, "Name")
    name.text = project_name
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _build_extensions_xml() -> bytes:
    """extensions.xml — definerer lovlige verdier."""
    root = Element("Extensions")

    topic_types = SubElement(root, "TopicTypes")
    for tt in ["Error", "Warning", "Info", "Request"]:
        t = SubElement(topic_types, "TopicType")
        t.text = tt

    priorities = SubElement(root, "Priorities")
    for p in ["Critical", "High", "Normal", "Low"]:
        pr = SubElement(priorities, "Priority")
        pr.text = p

    statuses = SubElement(root, "TopicStatuses")
    for s in ["Open", "In Progress", "Closed", "Resolved"]:
        st = SubElement(statuses, "TopicStatus")
        st.text = s

    labels = SubElement(root, "Labels")
    for l in ["RIB", "Akustikk", "Brann", "TEK17", "Tilgjengelighet", "Energi", "Bæresystem", "Stabilitet"]:
        lb = SubElement(labels, "Label")
        lb.text = l

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _build_markup_xml(issue: BcfIssue, topic_guid: str, viewpoint_guid: Optional[str] = None) -> bytes:
    """markup.bcf XML for en enkelt issue/topic."""
    root = Element("Markup")
    now = _iso_now()

    # Header
    header = SubElement(root, "Header")

    # Topic
    topic = SubElement(root, "Topic", Guid=topic_guid, TopicType=issue.topic_type, TopicStatus=issue.status)

    # Reference links
    for link in issue.reference_links:
        ref = SubElement(topic, "ReferenceLink")
        ref.text = link

    title_el = SubElement(topic, "Title")
    title_el.text = issue.title

    if issue.priority:
        prio = SubElement(topic, "Priority")
        prio.text = issue.priority

    creation = SubElement(topic, "CreationDate")
    creation.text = now

    author = SubElement(topic, "CreationAuthor")
    author.text = BUILTLY_AUTHOR

    modified = SubElement(topic, "ModifiedDate")
    modified.text = now

    mod_author = SubElement(topic, "ModifiedAuthor")
    mod_author.text = BUILTLY_AUTHOR

    if issue.assigned_to:
        assigned = SubElement(topic, "AssignedTo")
        assigned.text = issue.assigned_to

    if issue.stage:
        stage = SubElement(topic, "Stage")
        stage.text = issue.stage

    if issue.description:
        desc = SubElement(topic, "Description")
        desc.text = issue.description

    if issue.due_date:
        due = SubElement(topic, "DueDate")
        due.text = issue.due_date

    # Labels
    for label_text in issue.labels:
        label = SubElement(topic, "Labels")
        label.text = label_text

    # Module label
    if issue.module and issue.module not in issue.labels:
        label = SubElement(topic, "Labels")
        label.text = issue.module

    # TEK17 reference as label
    if issue.tek17_ref:
        label = SubElement(topic, "Labels")
        label.text = f"TEK17 {issue.tek17_ref}"

    # BIM Snippet for Builtly metadata
    if issue.severity_score is not None or issue.tek17_ref:
        snippet = SubElement(topic, "BimSnippet", SnippetType="JSON")
        ref = SubElement(snippet, "Reference")
        ref.text = "builtly_metadata.json"
        schema = SubElement(snippet, "ReferenceSchema")
        schema.text = "https://builtly.ai/schemas/bcf-metadata/v1"

    # Viewpoints reference
    if viewpoint_guid:
        vp_ref = SubElement(root, "Viewpoints", Guid=viewpoint_guid)
        vp_file = SubElement(vp_ref, "Viewpoint")
        vp_file.text = "viewpoint.bcfv"
        snapshot_file = SubElement(vp_ref, "Snapshot")
        snapshot_file.text = "snapshot.png"

    # Comment
    comment = SubElement(root, "Comment", Guid=_new_guid())
    comment_date = SubElement(comment, "Date")
    comment_date.text = now
    comment_author = SubElement(comment, "Author")
    comment_author.text = BUILTLY_AUTHOR
    comment_text = SubElement(comment, "Comment")
    comment_text.text = issue.description or issue.title
    if viewpoint_guid:
        comment_vp = SubElement(comment, "Viewpoint", Guid=viewpoint_guid)

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _build_viewpoint_xml(
    viewpoint: Optional[BcfViewpoint],
    issue: BcfIssue,
    vp_guid: str,
) -> bytes:
    """viewpoint.bcfv XML."""
    root = Element("VisualizationInfo", Guid=vp_guid)

    # Components
    all_guids = list(set(
        (viewpoint.component_ifcguids if viewpoint else []) +
        issue.component_ifcguids
    ))

    if all_guids:
        components = SubElement(root, "Components")
        selection = SubElement(components, "Selection")
        for guid in all_guids:
            comp = SubElement(selection, "Component", IfcGuid=guid)

    # Camera
    if viewpoint and viewpoint.camera_position:
        pp = SubElement(root, "PerspectiveCamera")
        pos = viewpoint.camera_position
        cam_pos = SubElement(pp, "CameraViewPoint")
        SubElement(cam_pos, "X").text = str(pos.get("x", 0))
        SubElement(cam_pos, "Y").text = str(pos.get("y", 0))
        SubElement(cam_pos, "Z").text = str(pos.get("z", 0))

        if viewpoint.camera_direction:
            d = viewpoint.camera_direction
            cam_dir = SubElement(pp, "CameraDirection")
            SubElement(cam_dir, "X").text = str(d.get("x", 0))
            SubElement(cam_dir, "Y").text = str(d.get("y", -1))
            SubElement(cam_dir, "Z").text = str(d.get("z", 0))

        if viewpoint.camera_up:
            u = viewpoint.camera_up
            cam_up = SubElement(pp, "CameraUpVector")
            SubElement(cam_up, "X").text = str(u.get("x", 0))
            SubElement(cam_up, "Y").text = str(u.get("y", 0))
            SubElement(cam_up, "Z").text = str(u.get("z", 1))

        fov = SubElement(pp, "FieldOfView")
        fov.text = str(viewpoint.field_of_view)

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _build_builtly_metadata(issue: BcfIssue) -> bytes:
    """Builtly-spesifikk metadata JSON for BIM Snippet."""
    import json
    data = {
        "builtly_version": "1.0",
        "module": issue.module,
        "severity_score": issue.severity_score,
        "tek17_ref": issue.tek17_ref,
        "generated_by": BUILTLY_TOOL_ID,
        "timestamp": _iso_now(),
    }
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


# ─── Hovedfunksjon ──────────────────────────────────────────────────

def create_bcf(
    issues: List[BcfIssue],
    output_path: str = "builtly_rapport.bcf",
    project_name: str = "Builtly Prosjekt",
) -> str:
    """
    Generer BCF 2.1-fil fra liste av issues.

    Args:
        issues: Liste av BcfIssue-objekter
        output_path: Sti for output .bcf-fil
        project_name: Prosjektnavn i BCF-header

    Returns:
        Absolutt sti til generert .bcf-fil
    """
    output_path = str(output_path)
    project_id = _new_guid()

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Versjon
        zf.writestr("bcf.version", _build_version_xml())

        # Prosjekt
        zf.writestr("project.bcfp", _build_project_xml(project_name, project_id))

        # Extensions
        zf.writestr("extensions.xml", _build_extensions_xml())

        # Topics
        for issue in issues:
            topic_guid = _new_guid()
            topic_dir = f"{topic_guid}/"

            # Viewpoint
            vp_guid = None
            if issue.component_ifcguids or (issue.viewpoint and issue.viewpoint.camera_position):
                vp_guid = _new_guid()

                # Viewpoint XML
                zf.writestr(
                    f"{topic_dir}viewpoint.bcfv",
                    _build_viewpoint_xml(issue.viewpoint, issue, vp_guid),
                )

                # Snapshot
                snapshot = None
                if issue.viewpoint and issue.viewpoint.snapshot_png:
                    snapshot = issue.viewpoint.snapshot_png
                else:
                    snapshot = _generate_placeholder_snapshot(issue.title, issue.topic_type)

                if snapshot:
                    zf.writestr(f"{topic_dir}snapshot.png", snapshot)

            # Markup
            zf.writestr(
                f"{topic_dir}markup.bcf",
                _build_markup_xml(issue, topic_guid, vp_guid),
            )

            # Builtly metadata
            if issue.severity_score is not None or issue.tek17_ref:
                zf.writestr(
                    f"{topic_dir}builtly_metadata.json",
                    _build_builtly_metadata(issue),
                )

    return os.path.abspath(output_path)


# ─── Hjelpefunksjoner for Builtly-moduler ───────────────────────────

def rib_analysis_to_bcf_issues(analysis_result: Dict[str, Any]) -> List[BcfIssue]:
    """
    Konverter RIB-analyseresultat fra Konstruksjon.py til BCF-issues.

    Forventer analysis_result med nøkler:
        - observasjoner: List[str]
        - mangler: List[str]
        - load_assumptions: List[str]
        - foundation_assumptions: List[str]
        - next_steps: List[str]
        - safety_reason: str
        - recommended_system: Dict
    """
    issues = []

    # Mangler → Error
    for mangel in analysis_result.get("mangler", []):
        issues.append(BcfIssue(
            title=f"Mangel: {mangel[:80]}",
            description=mangel,
            topic_type="Error",
            priority="High",
            module="RIB",
            labels=["Bæresystem", "Mangel"],
        ))

    # Observasjoner → Warning
    for obs in analysis_result.get("observasjoner", []):
        issues.append(BcfIssue(
            title=f"Observasjon: {obs[:80]}",
            description=obs,
            topic_type="Warning",
            priority="Normal",
            module="RIB",
            labels=["Bæresystem"],
        ))

    # Lastantagelser → Info
    for assumption in analysis_result.get("load_assumptions", []):
        issues.append(BcfIssue(
            title=f"Lastantagelse: {assumption[:80]}",
            description=assumption,
            topic_type="Info",
            priority="Low",
            module="RIB",
            labels=["Last"],
        ))

    # Fundamentering → Info
    for assumption in analysis_result.get("foundation_assumptions", []):
        issues.append(BcfIssue(
            title=f"Fundamentering: {assumption[:80]}",
            description=assumption,
            topic_type="Info",
            priority="Normal",
            module="RIB",
            labels=["Fundamentering"],
        ))

    # Neste steg → Request
    for step in analysis_result.get("next_steps", []):
        issues.append(BcfIssue(
            title=f"Neste steg: {step[:80]}",
            description=step,
            topic_type="Request",
            priority="Normal",
            module="RIB",
            labels=["Oppfølging"],
        ))

    return issues


def tek17_checks_to_bcf_issues(
    checks: List[Dict[str, Any]],
    module: str = "TEK17",
) -> List[BcfIssue]:
    """
    Konverter TEK17/tilgjengelighetsfunn til BCF-issues.

    Forventer checks med format:
        [{"room": "Bad 1", "issues": ["Bad: 2.8 m² < 3.3 m² (TEK17 §12-9)"]}]
    """
    issues = []
    for check in checks:
        for issue_text in check.get("issues", []):
            # Ekstraher TEK17-referanse
            tek_ref = ""
            if "§" in issue_text:
                import re
                match = re.search(r"§[\d\-]+", issue_text)
                if match:
                    tek_ref = match.group()

            issues.append(BcfIssue(
                title=f"{check.get('room', 'Rom')}: {issue_text[:80]}",
                description=issue_text,
                topic_type="Error",
                priority="High",
                module=module,
                tek17_ref=tek_ref,
                labels=["TEK17", "Tilgjengelighet"],
            ))

    return issues


def u_value_checks_to_bcf_issues(
    u_values: List[Dict[str, Any]],
    max_u_wall: float = 0.18,       # TEK17 yttervegg
    max_u_roof: float = 0.13,       # TEK17 tak
    max_u_floor: float = 0.10,      # TEK17 gulv mot grunn
) -> List[BcfIssue]:
    """Sjekk U-verdier mot TEK17 §14 og generer BCF-issues for overskridelser."""
    issues = []
    for uv in u_values:
        if not uv.get("is_external"):
            continue

        u = uv.get("u_value", 0)
        el_type = uv.get("type", "")
        name = uv.get("element_name", "Element")

        max_u = max_u_wall
        if "roof" in el_type.lower() or "tak" in name.lower():
            max_u = max_u_roof
        elif "floor" in el_type.lower() or "gulv" in name.lower():
            max_u = max_u_floor

        if u > max_u:
            issues.append(BcfIssue(
                title=f"U-verdi overskridelse: {name} ({u:.3f} > {max_u})",
                description=(
                    f"{name} har U-verdi {u:.3f} W/(m²·K) som overskrider "
                    f"TEK17-kravet på {max_u} W/(m²·K)."
                ),
                topic_type="Error",
                priority="High",
                module="TEK17",
                tek17_ref="§14-2",
                labels=["Energi", "TEK17", "U-verdi"],
                component_ifcguids=[uv["global_id"]] if uv.get("global_id") else [],
            ))

    return issues


# ─── Streamlit-integrasjon ──────────────────────────────────────────

def render_bcf_export_button(
    issues: List[BcfIssue],
    project_name: str = "Builtly Prosjekt",
    filename: str = "builtly_rapport.bcf",
) -> Optional[str]:
    """
    Vis BCF-eksport-knapp i Streamlit og returner filsti ved eksport.

    Bruk i Konstruksjon.py:
        issues = rib_analysis_to_bcf_issues(analysis_result)
        bcf_path = render_bcf_export_button(issues, project_name="Mitt prosjekt")
        if bcf_path:
            st.success(f"BCF eksportert: {bcf_path}")
    """
    try:
        import streamlit as st
    except ImportError:
        return None

    if not issues:
        st.caption("Ingen BCF-issues å eksportere.")
        return None

    error_count = sum(1 for i in issues if i.topic_type == "Error")
    warning_count = sum(1 for i in issues if i.topic_type == "Warning")
    info_count = sum(1 for i in issues if i.topic_type in ("Info", "Request"))

    st.markdown(
        f"**BCF-eksport:** {len(issues)} issues "
        f"({error_count} feil, {warning_count} advarsler, {info_count} info)"
    )

    if st.button("📦 Eksporter BCF 2.1", key="bcf_export_btn", use_container_width=True):
        output_dir = Path("qa_database") / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / filename)

        bcf_path = create_bcf(issues, output_path, project_name)

        # Tilby nedlasting
        with open(bcf_path, "rb") as f:
            st.download_button(
                label="⬇️ Last ned BCF-fil",
                data=f.read(),
                file_name=filename,
                mime="application/octet-stream",
                key="bcf_download",
            )

        return bcf_path

    return None
