import os
import hashlib
import hmac
import base64
import html
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import streamlit as st

# Auth & payment integration (Supabase + Stripe)
try:
    import builtly_auth
    _HAS_AUTH = True
except ImportError:
    _HAS_AUTH = False

# -------------------------------------------------
# 1) PAGE CONFIG
# -------------------------------------------------
st.set_page_config(
    page_title="Builtly | Engineering Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -------------------------------------------------
# 2) LANGUAGE NORMALIZATION & SESSION STATE
# -------------------------------------------------
LANG_ALIASES = {
    "🇬🇧 English": "🇬🇧 English (UK)",
    "🇸🇪 Svensk": "🇸🇪 Svenska",
}

if "app_lang" not in st.session_state:
    st.session_state.app_lang = "🇳🇴 Norsk"

st.session_state.app_lang = LANG_ALIASES.get(st.session_state.app_lang, st.session_state.app_lang)

if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / plan- og bygningsloven)",
        "p_name": "",
        "c_name": "",
        "p_desc": "",
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "Næring / Kontor",
        "etasjer": 4,
        "bta": 2500,
        "last_sync": "Ikke synket enda",
    }

if "assistant_history" not in st.session_state:
    st.session_state.assistant_history = []

if "assistant_input_nonce" not in st.session_state:
    st.session_state.assistant_input_nonce = 0

if "assistant_discipline_codes" not in st.session_state:
    st.session_state.assistant_discipline_codes = ['geo', 'rib', 'fire', 'sha', 'breeam']

if "assistant_dialog_open" not in st.session_state:
    st.session_state.assistant_dialog_open = False

if "site_access_granted" not in st.session_state:
    st.session_state.site_access_granted = False

if "site_access_error" not in st.session_state:
    st.session_state.site_access_error = ""

if "site_access_input_nonce" not in st.session_state:
    st.session_state.site_access_input_nonce = 0

# -- User auth & subscription state --
if "user_authenticated" not in st.session_state:
    st.session_state.user_authenticated = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "user_name" not in st.session_state:
    st.session_state.user_name = ""
if "user_plan" not in st.session_state:
    st.session_state.user_plan = ""  # "modul", "team", "enterprise"
if "user_company" not in st.session_state:
    st.session_state.user_company = ""
if "user_countries" not in st.session_state:
    st.session_state.user_countries = []  # list of country codes
if "user_payment_method" not in st.session_state:
    st.session_state.user_payment_method = ""  # "card" or "invoice"
if "user_account_status" not in st.session_state:
    st.session_state.user_account_status = ""  # "active", "pending_invoice", "inactive"
if "user_reports" not in st.session_state:
    st.session_state.user_reports = []  # list of dicts: {project, name, module, created, expires, download_url}
if "auth_page" not in st.session_state:
    st.session_state.auth_page = ""  # "login", "register", "plans", "dashboard"

ASSISTANT_END_MARKER = "[[BUILTLY_DONE]]"

ACCESS_GATE_COPY = {
    "🇬🇧 English (UK)": {
        "eyebrow": "Restricted access",
        "title": "Enter code to open Builtly",
        "subtitle": "This front page is protected. Enter the access code to continue.",
        "label": "Access code",
        "placeholder": "Enter code",
        "button": "Open portal",
        "error_invalid": "That code is not correct. Please try again.",
        "info": "Language selection is kept when the portal opens.",
        "admin_missing": "Access control is enabled, but no code is configured. Set BUILTLY_ACCESS_CODE or BUILTLY_ACCESS_CODES in Render.",
        "admin_help": "Optional: use BUILTLY_ACCESS_CODE_SHA256 to store a SHA-256 hash instead of plain text.",
    },
    "🇺🇸 English (US)": {
        "eyebrow": "Restricted access",
        "title": "Enter code to open Builtly",
        "subtitle": "This front page is protected. Enter the access code to continue.",
        "label": "Access code",
        "placeholder": "Enter code",
        "button": "Open portal",
        "error_invalid": "That code is not correct. Please try again.",
        "info": "Language selection is kept when the portal opens.",
        "admin_missing": "Access control is enabled, but no code is configured. Set BUILTLY_ACCESS_CODE or BUILTLY_ACCESS_CODES in Render.",
        "admin_help": "Optional: use BUILTLY_ACCESS_CODE_SHA256 to store a SHA-256 hash instead of plain text.",
    },
    "🇳🇴 Norsk": {
        "eyebrow": "Begrenset tilgang",
        "title": "Angi kode for å åpne Builtly",
        "subtitle": "Forsiden er låst. Skriv inn tilgangskoden for å åpne portalen.",
        "label": "Tilgangskode",
        "placeholder": "Skriv inn kode",
        "button": "Åpne portal",
        "error_invalid": "Koden er ikke riktig. Prøv igjen.",
        "info": "Språkvalget beholdes når portalen åpnes.",
        "admin_missing": "Tilgangskontroll er slått på, men ingen kode er konfigurert. Sett BUILTLY_ACCESS_CODE eller BUILTLY_ACCESS_CODES i Render.",
        "admin_help": "Valgfritt: bruk BUILTLY_ACCESS_CODE_SHA256 hvis du vil lagre hash i stedet for klartekst.",
    },
    "🇸🇪 Svenska": {
        "eyebrow": "Begränsad åtkomst",
        "title": "Ange kod för att öppna Builtly",
        "subtitle": "Startsidan är låst. Skriv in åtkomstkoden för att fortsätta.",
        "label": "Åtkomstkod",
        "placeholder": "Skriv in kod",
        "button": "Öppna portalen",
        "error_invalid": "Koden är inte korrekt. Försök igen.",
        "info": "Språkvalet behålls när portalen öppnas.",
        "admin_missing": "Åtkomstkontroll är aktiverad, men ingen kod är konfigurerad. Sätt BUILTLY_ACCESS_CODE eller BUILTLY_ACCESS_CODES i Render.",
        "admin_help": "Valfritt: använd BUILTLY_ACCESS_CODE_SHA256 om du vill lagra hash i stället för klartext.",
    },
    "🇩🇰 Dansk": {
        "eyebrow": "Begrænset adgang",
        "title": "Indtast kode for at åbne Builtly",
        "subtitle": "Forsiden er låst. Skriv adgangskoden for at fortsætte.",
        "label": "Adgangskode",
        "placeholder": "Skriv kode",
        "button": "Åbn portal",
        "error_invalid": "Koden er ikke korrekt. Prøv igen.",
        "info": "Sprogvalget bevares, når portalen åbnes.",
        "admin_missing": "Adgangskontrol er slået til, men ingen kode er konfigureret. Sæt BUILTLY_ACCESS_CODE eller BUILTLY_ACCESS_CODES i Render.",
        "admin_help": "Valgfrit: brug BUILTLY_ACCESS_CODE_SHA256, hvis du vil gemme hash i stedet for klartekst.",
    },
    "🇫🇮 Suomi": {
        "eyebrow": "Rajoitettu käyttö",
        "title": "Anna koodi avataksesi Builtlyn",
        "subtitle": "Etusivu on suojattu. Syötä pääsykoodi jatkaaksesi.",
        "label": "Pääsykoodi",
        "placeholder": "Anna koodi",
        "button": "Avaa portaali",
        "error_invalid": "Koodi ei ole oikein. Yritä uudelleen.",
        "info": "Kielivalinta säilyy, kun portaali avataan.",
        "admin_missing": "Pääsynhallinta on käytössä, mutta koodia ei ole määritetty. Aseta BUILTLY_ACCESS_CODE tai BUILTLY_ACCESS_CODES Renderissä.",
        "admin_help": "Valinnainen: käytä BUILTLY_ACCESS_CODE_SHA256, jos haluat tallentaa SHA-256-tiivisteen selväkielisen koodin sijaan.",
    },
    "🇩🇪 Deutsch": {
        "eyebrow": "Geschützter Zugang",
        "title": "Code eingeben, um Builtly zu öffnen",
        "subtitle": "Die Startseite ist geschützt. Bitte den Zugangscode eingeben, um fortzufahren.",
        "label": "Zugangscode",
        "placeholder": "Code eingeben",
        "button": "Portal öffnen",
        "error_invalid": "Der Code ist nicht korrekt. Bitte erneut versuchen.",
        "info": "Die Sprachauswahl bleibt beim Öffnen des Portals erhalten.",
        "admin_missing": "Der Zugangsschutz ist aktiv, aber kein Code ist konfiguriert. Setzen Sie BUILTLY_ACCESS_CODE oder BUILTLY_ACCESS_CODES in Render.",
        "admin_help": "Optional: Verwenden Sie BUILTLY_ACCESS_CODE_SHA256, um statt Klartext einen SHA-256-Hash zu speichern.",
    },
}


def get_access_copy(lang_key: str) -> Dict:
    return ACCESS_GATE_COPY.get(lang_key, ACCESS_GATE_COPY["🇬🇧 English (UK)"])

# -------------------------------------------------
# 3) LANGUAGE TEXTS & REGULATORY PROFILES
# -------------------------------------------------
TEXTS = {'🇬🇧 English (UK)': {'rule_set': 'United Kingdom (Building Regulations / Approved Documents)',
                     'eyebrow': 'Reimagining engineering in construction and property',
                     'title': "Engineering meets AI.",
                     'subtitle': 'Builtly automates the standardised technical deliverables in construction, civil and property. '
                                 'Upload raw data – AI analyses, calculates and produces the report. '
                                 'Qualified professionals review and sign off where the law requires it. '
                                 'You get more time for the work that actually needs an engineer.',
                     'btn_setup': 'Open project setup',
                     'btn_qa': 'Open QA and sign-off',
                     'proofs': ['Rules-first AI', 'Human-in-the-loop', 'PDF + DOCX output', 'Digital sign-off', 'Full audit trail'],
                     'why_kicker': 'Why Builtly?',
                     'stat1_v': 'Time back',
                     'stat1_t': '80–90% less manual writing',
                     'stat1_d': 'AI handles the repetitive. You handle the judgement.',
                     'stat2_v': 'Verified',
                     'stat2_t': 'Professional sign-off where the law requires it',
                     'stat2_d': 'Qualified responsible engineers certify digitally – where building regulations demand it.',
                     'stat3_v': 'PDF + DOCX',
                     'stat3_t': 'Submission-ready',
                     'stat3_d': 'Complete packages with appendices and digital signatures.',
                     'stat4_v': 'Full Traceability',
                     'stat4_t': 'Auditable end-to-end',
                     'stat4_d': 'Every input, source and decision is logged and verifiable.',
                     'sec_val_kicker': 'The platform',
                     'sec_val_title': 'One platform. Every discipline. One source of truth.',
                     'sec_val_sub': 'Builtly is not a tool – it is the infrastructure that connects every stakeholder in the value chain. '
                                    'Developer, engineer, contractor, bank and insurer on one traceable foundation.',
                     'val_1_t': 'Every discipline. One flow.',
                     'val_1_d': 'GEO, Fire, Structural, Acoustics, SHA, TDD, Climate risk and more – from the same project data, in one portal.',
                     'val_2_t': 'Rules-first AI',
                     'val_2_d': 'AI operates inside building regulations, national standards and EU Taxonomy – not free-form text. Grounded in the rules.',
                     'val_3_t': 'Human-in-the-loop where it counts',
                     'val_3_d': 'Most modules deliver directly. Where the law requires a responsible engineer, qualified professionals are in the sign-off.',
                     'val_4_t': 'Scales without growing proportionally',
                     'val_4_d': 'New disciplines, new markets and new partners plug into the same infrastructure.',
                     'sec_loop_kicker': 'The Builtly Loop',
                     'sec_loop_title': 'From raw data to finished deliverable – in four steps.',
                     'sec_loop_sub': 'A structured workflow that eliminates manual drafting, ensures regulatory compliance and delivers '
                                     'traceable document packages ready for submission or execution.',
                     'loop_1_t': 'Input',
                     'loop_1_d': 'Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place.',
                     'loop_2_t': 'AI analyses and generates',
                     'loop_2_d': 'The platform validates, checks regulations, performs calculations and writes the report – automatically.',
                     'loop_3_t': 'Human-in-the-loop',
                     'loop_3_d': 'Where the law requires it: a qualified responsible engineer reviews and signs off digitally. Most modules deliver directly.',
                     'loop_4_t': 'Finished deliverable',
                     'loop_4_d': 'Complete documentation package in standard formats – ready for municipal submission or use on site.',
                     'mod_sec_kicker': 'Modules',
                     'mod_sec_title': 'Modules for analysis, documentation and decisions',
                     'mod_sec_sub': 'Choose the workflow that fits your project. Every module uses the same project data, '
                                    'traceability and quality-controlled delivery flow inside Builtly.',
                     'mod_sec1': 'Ground conditions, acoustics & fire',
                     'mod_sec2': 'Early phase, structure & mobility',
                     'mod_sec3': 'Sustainability, safety & certification',
                     'mod_sec3_sub': 'Modules for environmental follow-up, safety planning and certification support in one workflow.',
                     'm_geo_t': 'GEO / ENV - Ground Conditions',
                     'm_geo_d': 'Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts '
                                'environmental action plans.',
                     'm_geo_in': 'XLSX / CSV / PDF + plans',
                     'm_geo_out': 'Environmental action plan, logs',
                     'm_geo_btn': 'Open Geo & Env',
                     'm_aku_t': 'ACOUSTICS - Noise & Sound',
                     'm_aku_d': 'Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation '
                                'strategies.',
                     'm_aku_in': 'Noise map + floor plan',
                     'm_aku_out': 'Acoustics report, facade evaluation',
                     'm_aku_btn': 'Open Acoustics',
                     'm_brann_t': 'FIRE - Safety Strategy',
                     'm_brann_d': 'Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, '
                                  'and fire strategy.',
                     'm_brann_in': 'Architectural drawings + class',
                     'm_brann_out': 'Fire strategy concept, deviations',
                     'm_brann_btn': 'Open Fire Strategy',
                     'm_ark_t': 'ARK - Feasibility Study',
                     'm_ark_d': 'Site screening, volume analysis, and early-phase decision support before full engineering design.',
                     'm_ark_in': 'Site data, zoning plans',
                     'm_ark_out': 'Feasibility report, utilization metrics',
                     'm_ark_btn': 'Open Feasibility',
                     'm_rib_t': 'STRUC - Structural Concept',
                     'm_rib_d': 'Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.',
                     'm_rib_in': 'Models, load parameters',
                     'm_rib_out': 'Concept memo, grid layouts',
                     'm_rib_btn': 'Open Structural',
                     'm_tra_t': 'TRAFFIC - Mobility',
                     'm_tra_d': 'Traffic generation, parking requirements, access logic, and soft-mobility planning for early project '
                                'phases.',
                     'm_tra_in': 'Site plans, local norms',
                     'm_tra_out': 'Traffic memo, mobility plan',
                     'm_tra_btn': 'Open Traffic & Mobility',
                     'm_sha_t': 'SHA - Safety & Health Plan',
                     'm_sha_d': 'Safety, health, and working environment. Generates routines for site logistics and high-risk operations.',
                     'm_sha_in': 'Project data + Risk factors',
                     'm_sha_out': 'Complete SHA plan',
                     'm_sha_btn': 'Open SHA Module',
                     'm_breeam_t': 'BREEAM Assistant',
                     'm_breeam_d': 'Early-phase assessment of BREEAM potential, credit requirements, and material strategies.',
                     'm_breeam_in': 'Building data + Ambitions',
                     'm_breeam_out': 'BREEAM Pre-assessment',
                     'm_breeam_btn': 'Open BREEAM Assistant',
                     'm_mop_t': 'MOP - Environment Plan',
                     'm_mop_d': 'Environmental follow-up plan. Assesses waste management, reuse, emissions, and nature preservation.',
                     'm_mop_in': 'Project data + Eco goals',
                     'm_mop_out': 'MOP Document',
                     'm_mop_btn': 'Open MOP Module',
                     'btn_dev': 'In development',
                     'cta_title': 'Ready to reimagine how engineering gets done?',
                     'cta_desc': 'Start with one project. See what AI can deliver in minutes – not days. '
                                 'Builtly is built for those who want to lead, not follow.',
                     'cta_btn1': 'Start in project setup',
                     'cta_btn2': 'Go to review queue',
                     'footer_copy': 'AI-assisted engineering. Human-verified. Compliance-grade.',
                     'footer_meta': '© 2026 Builtly Engineering AS. All rights reserved.',
                     'label_input': 'Input',
                     'label_output': 'Output',
                     'assistant_kicker': 'Builtly Assistant',
                     'assistant_title': 'Ask across every engineering discipline.',
                     'assistant_subtitle': 'A front-page question surface for GEO, structural, demolition, acoustics, fire, environment, '
                                           'SHA, BREEAM and property. The assistant follows the selected language and defaults to the '
                                           'relevant national rule set.',
                     'assistant_label_country': 'Country',
                     'assistant_label_rules': 'Rule set',
                     'assistant_label_status': 'Status',
                     'assistant_disciplines_label': 'Disciplines',
                     'assistant_question_label': 'Your question',
                     'assistant_placeholder': 'Example: What must be clarified for a six-storey apartment project with a basement in early '
                                              'phase?',
                     'assistant_btn': 'Ask Builtly',
                     'assistant_clear': 'Clear conversation',
                     'assistant_loading': 'Builtly is analysing your question...',
                     'assistant_examples_label': 'Example prompts',
                     'assistant_examples': ['What should we clarify early for a residential block with a basement near a busy road?',
                                            'Which BREEAM topics should be prioritised for an office project in early phase?',
                                            'What should a demolition and SHA strategy cover next to a school?'],
                     'assistant_disclaimer': 'Guidance is AI-assisted and must be quality-assured by the responsible discipline lead '
                                             'before design decisions or sign-off.',
                     'assistant_history_label': 'Recent dialogue',
                     'assistant_empty_title': 'Ready for live questions',
                     'assistant_empty_body': 'Visitors can ask questions about building technology and property right on the front page, '
                                             'and the answer can be steered by discipline, language and national regulations.',
                     'assistant_latest_answer': 'Latest answer',
                     'assistant_status_live': 'AI ready',
                     'assistant_status_setup': 'UI ready',
                     'assistant_error_prefix': 'Could not generate an answer',
                     'assistant_note_prefix': 'Setup note',
                     'assistant_scope_value': 'GEO · Structural · Demolition · Acoustics · Fire · Environment · SHA · BREEAM · Property · '
                                              'Traffic'},
 '🇳🇴 Norsk': {'rule_set': 'Norge (TEK17 / plan- og bygningsloven)',
              'eyebrow': 'Vi revolusjonerer prosjekteringen i bygg og eiendom',
              'title': "Ingeniørfaget møter AI.",
              'subtitle': 'Builtly automatiserer de standardiserte faglige leveransene i bygg, anlegg og eiendom. '
                          'Last opp rådata – AI analyserer, beregner og utarbeider rapporten. '
                          'Fagpersoner kvalitetssikrer og signerer der loven krever det. '
                          'Du får mer tid til det som faktisk krever ingeniørhodet ditt.',
              'btn_setup': 'Åpne Project Setup',
              'btn_qa': 'Åpne QA & Sign-off',
              'proofs': ['Regelstyrt AI', 'Human-in-the-loop', 'PDF + DOCX', 'Digital signering', 'Full revisjonsspor'],
              'why_kicker': 'Hvorfor Builtly?',
              'stat1_v': 'Tid tilbake',
              'stat1_t': '80–90% mindre skrivearbeid',
              'stat1_d': 'AI tar det repetitive. Du tar det faglige.',
              'stat2_v': 'Verifisert',
              'stat2_t': 'Faglig ansvar der loven krever det',
              'stat2_d': 'Godkjente fagpersoner signerer digitalt – der PBL og TEK17 stiller krav.',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Klar til innsending',
              'stat3_d': 'Ferdig dokumentpakke med vedlegg og digital signatur.',
              'stat4_v': 'Full Sporbarhet',
              'stat4_t': 'Etterprøvbart i alt',
              'stat4_d': 'Hvert input, kilde og beslutning er logget.',
              'sec_val_kicker': 'Plattformen',
              'sec_val_title': 'Én plattform. Alle fag. Én sannhet.',
              'sec_val_sub': 'Builtly er ikke et verktøy – det er infrastrukturen som binder alle aktører i verdikjeden. '
                             'Utbygger, rådgiver, entreprenør, bank og forsikring på ett sporbart grunnlag.',
              'val_1_t': 'Alle fag. Én flyt.',
              'val_1_d': 'GEO, Brann, RIB, Akustikk, SHA, TDD, Klimarisiko og mer – fra samme prosjektgrunnlag, i én portal.',
              'val_2_t': 'Regelstyrt AI',
              'val_2_d': 'AI opererer innenfor TEK17, NS-standarder og EU Taxonomy – ikke fri tekst. Forankret i regelverket.',
              'val_3_t': 'Human-in-the-loop der det teller',
              'val_3_d': 'De fleste moduler leveres direkte. Der loven krever faglig ansvarsrett er godkjente fagpersoner i sign-off.',
              'val_4_t': 'Skalerer uten å vokse proporsjonalt',
              'val_4_d': 'Nye fag, nye markeder og nye partnere plugges inn i samme infrastruktur.',
              'sec_loop_kicker': 'Builtly Loop',
              'sec_loop_title': 'Fra rådata til ferdig leveranse – i fire steg.',
              'sec_loop_sub': 'En strukturert arbeidsflyt som eliminerer manuelt skrivearbeid, sikrer regelverksetterlevelse '
                              'og leverer sporbare dokumentpakker klare for byggesak og utførelse.',
              'loop_1_t': 'Last opp rådata',
              'loop_1_d': 'PDF, IFC-modeller, labfiler, tegninger og prosjektdata samles på ett sted.',
              'loop_2_t': 'AI analyserer og genererer',
              'loop_2_d': 'Plattformen validerer, sjekker regelverk, gjør beregninger og skriver rapporten – automatisk.',
              'loop_3_t': 'Human-in-the-loop',
              'loop_3_d': 'Der loven krever det: fagperson med ansvarsrett kvalitetssikrer og signerer digitalt. De fleste moduler leveres direkte.',
              'loop_4_t': 'Ferdig leveranse',
              'loop_4_d': 'Komplett dokumentpakke i standardformater – klar for innsending til kommunen eller bruk på byggeplassen.',
              'mod_sec_kicker': 'Moduler',
              'mod_sec_title': 'Moduler for analyse, dokumentasjon og beslutningsstøtte',
              'mod_sec_sub': 'Velg arbeidsflaten som passer prosjektet ditt. Alle modulene bruker samme prosjektdata, '
                             'sporbarhet og kvalitetssikrede leveranseflyt i Builtly.',
              'mod_sec1': 'Grunnforhold, lyd & brann',
              'mod_sec2': 'Tidligfase, konstruksjon & mobilitet',
              'mod_sec3': 'Bærekraft, sikkerhet & sertifisering',
              'mod_sec3_sub': 'Moduler for miljøoppfølging, sikkerhetsplaner og sertifiseringsstøtte samlet i én arbeidsflyt.',
              'm_geo_t': 'GEO / MILJØ - Grunnforhold',
              'm_geo_d': 'Analyserer lab-filer og graveceller. Klassifiserer masser og utarbeider tiltaksplaner.',
              'm_geo_in': 'XLSX / CSV / PDF + Kart',
              'm_geo_out': 'Tiltaksplan, logg',
              'm_geo_btn': 'Åpne Geo & Miljø',
              'm_aku_t': 'AKUSTIKK - Støy & Lyd',
              'm_aku_d': 'Leser støykart og plantegninger. Genererer krav til fasade, vinduer og skjerming.',
              'm_aku_in': 'Støykart + Plan',
              'm_aku_out': 'Akustikkrapport',
              'm_aku_btn': 'Åpne Akustikk',
              'm_brann_t': 'BRANN - Sikkerhetskonsept',
              'm_brann_d': 'Vurderer arkitektur mot forskrifter. Definerer rømning og brannceller.',
              'm_brann_in': 'Tegninger + Klasse',
              'm_brann_out': 'Brannkonsept (RIBr)',
              'm_brann_btn': 'Åpne Brannkonsept',
              'm_ark_t': 'ARK - Mulighetsstudie',
              'm_ark_d': 'Tomteanalyse, volumvurdering og beslutningsgrunnlag for tidligfase.',
              'm_ark_in': 'Regulering + Tomt',
              'm_ark_out': 'Mulighetsstudie',
              'm_ark_btn': 'Åpne Feasibility',
              'm_rib_t': 'RIB - Konstruksjon',
              'm_rib_d': 'Konseptuelle struktursjekker, spennvidder og integrasjon med klimagass.',
              'm_rib_in': 'Modeller, Laster',
              'm_rib_out': 'Konseptnotat RIB',
              'm_rib_btn': 'Åpne Konstruksjon',
              'm_tra_t': 'TRAFIKK - Mobilitet',
              'm_tra_d': 'Trafikkgenerering, parkering, adkomstlogikk og myke trafikanter for tidligfase.',
              'm_tra_in': 'Situasjonsplan',
              'm_tra_out': 'Trafikknotat',
              'm_tra_btn': 'Åpne Trafikk & Mobilitet',
              'm_sha_t': 'SHA-Plan (Sikkerhet)',
              'm_sha_d': 'Sikkerhet, helse og arbeidsmiljø. Genererer rutiner for rigg, logistikk og risikofylte operasjoner.',
              'm_sha_in': 'Prosjektdata + Risiko',
              'm_sha_out': 'Komplett SHA-plan',
              'm_sha_btn': 'Åpne SHA',
              'm_breeam_t': 'BREEAM Assistent',
              'm_breeam_d': 'Tidligfase vurdering av BREEAM-NOR potensial, poengkrav og materialstrategi.',
              'm_breeam_in': 'Byggdata + Ambisjon',
              'm_breeam_out': 'BREEAM Pre-assessment',
              'm_breeam_btn': 'Åpne BREEAM',
              'm_mop_t': 'MOP (Miljøoppfølging)',
              'm_mop_d': 'Miljøoppfølgingsplan for byggeplass. Vurderer avfall, ombruk, utslipp og natur.',
              'm_mop_in': 'Prosjektdata + Miljømål',
              'm_mop_out': 'MOP Dokument',
              'm_mop_btn': 'Åpne MOP',
              'btn_dev': 'Under utvikling',
              'cta_title': 'Klar til å revolusjonere prosjekteringen?',
              'cta_desc': 'Start med ett prosjekt. Se hva Builtly kan levere på minutter – ikke dager og uker. '
                          'Builtly er bygget for de som vil ligge foran, ikke henge etter.',
              'cta_btn1': 'Start i Project Setup',
              'cta_btn2': 'Gå til kontroll-kø',
              'footer_copy': 'AI-assisted engineering. Human-verified. Compliance-grade.',
              'footer_meta': '© 2026 Builtly Engineering AS. All rights reserved.',
              'label_input': 'Input',
              'label_output': 'Output',
              'assistant_kicker': 'Builtly Assistent',
              'assistant_title': 'Still spørsmål på tvers av alle byggfag.',
              'assistant_subtitle': 'En integrert spørreflate på forsiden for GEO, RIB, rive, RIAku, RIBr, miljø, SHA, BREEAM og eiendom. '
                                    'Assistenten følger valgt språk og bruker riktig nasjonalt regelverk som utgangspunkt.',
              'assistant_label_country': 'Land',
              'assistant_label_rules': 'Regelverk',
              'assistant_label_status': 'Status',
              'assistant_disciplines_label': 'Fagområder',
              'assistant_question_label': 'Spørsmål',
              'assistant_placeholder': 'Eksempel: Hva må avklares i tidligfase for et boligprosjekt på seks etasjer med kjeller?',
              'assistant_btn': 'Spør Builtly',
              'assistant_clear': 'Tøm samtale',
              'assistant_loading': 'Builtly analyserer spørsmålet ditt...',
              'assistant_examples_label': 'Eksempler på spørsmål',
              'assistant_examples': ['Hva må vi avklare tidlig for en boligblokk med parkeringskjeller ved trafikkert vei?',
                                     'Hvilke BREEAM-tema bør prioriteres i tidligfase for et kontorprosjekt?',
                                     'Hva bør en rive- og SHA-strategi dekke ved arbeid nær skole?'],
              'assistant_disclaimer': 'Svarene er AI-assisterte og må kvalitetssikres av ansvarlig fagperson før prosjekteringsvalg eller '
                                      'signering.',
              'assistant_history_label': 'Nylig dialog',
              'assistant_empty_title': 'Klar for spørsmål fra besøkende',
              'assistant_empty_body': 'Besøkende kan stille spørsmål om bygningsteknikk og eiendom direkte på forsiden, og svarene styres '
                                      'av fagvalg, språk og nasjonalt regelverk.',
              'assistant_latest_answer': 'Siste svar',
              'assistant_status_live': 'AI klar',
              'assistant_status_setup': 'UI klart',
              'assistant_error_prefix': 'Kunne ikke generere svar',
              'assistant_note_prefix': 'Oppsett',
              'assistant_scope_value': 'GEO · RIB · Rive · RIAku · RIBr · Miljø · SHA · BREEAM · Eiendom · Trafikk'},
 '🇸🇪 Svenska': {'rule_set': 'Sverige (Boverkets regler / övergång 2025–2026)',
                'eyebrow': 'Vi revolutionerar projekteringen inom bygg och fastighet',
                'title': "Ingenjörsyrket möter AI.",
                'subtitle': 'Builtly automatiserar de standardiserade tekniska leveranserna inom bygg, anläggning och fastighet. '
                             'Ladda upp rådata – AI analyserar, beräknar och upprättar rapporten. '
                             'Ansvariga experter granskar och signerar där lagen kräver det. '
                             'Du får mer tid till det som faktiskt kräver ingenjörshuvudet.',
                'btn_setup': 'Starta i Project Setup',
                'btn_qa': 'Öppna QA & Sign-off',
                'proofs': ['Regelbaserad AI', 'Human-in-the-loop', 'PDF + DOCX', 'Digital signering', 'Fullständigt revisionsspår'],
                'why_kicker': 'Varför Builtly?',
                'stat1_v': 'Tid tillbaka',
                'stat1_t': '80–90% mindre skrivarbete',
                'stat1_d': 'AI hanterar det repetitiva. Du hanterar det tekniska.',
                'stat2_v': 'Verifierat',
                'stat2_t': 'Yrkesansvar där lagen kräver det',
                'stat2_d': 'Godkända ansvariga experter signerar digitalt – där plan- och bygglagen ställer krav.',
                'stat3_v': 'PDF + DOCX',
                'stat3_t': 'Kompletta rapporter',
                'stat3_d': 'Med bilagor och spårbarhet',
                'stat4_v': 'Spårbarhet',
                'stat4_t': 'Dokumentation',
                'stat4_d': 'Versionshantering från input till PDF',
                'sec_val_kicker': 'Plattformen',
                'sec_val_title': 'En plattform. Alla discipliner. En källa till sanning.',
                'sec_val_sub': 'Builtly är inte ett verktyg – det är infrastrukturen som förbinder alla aktörer i värdekedjan. '
                               'Byggherre, ingenjör, entreprenör, bank och försäkring på ett spårbart underlag.',
                'val_1_t': 'Alla discipliner. Ett flöde.',
                'val_1_d': 'GEO, Brand, Konstruktion, Akustik, SHA, TDD, Klimatrisk och mer – från samma projektdata, i en portal.',
                'val_2_t': 'Regelbaserad AI',
                'val_2_d': 'AI arbetar inom plan- och bygglagen, svenska standarder och EU Taxonomy – inte fri text. Förankrad i regelverket.',
                'val_3_t': 'Human-in-the-loop där det räknas',
                'val_3_d': 'De flesta moduler levereras direkt. Där lagen kräver ansvarig expert är de med i sign-off.',
                'val_4_t': 'Skalerar utan att växa proportionellt',
                'val_4_d': 'Nya discipliner, nya marknader och nya partners ansluts till samma infrastruktur.',
                'sec_loop_kicker': 'Builtly Loop',
                'sec_loop_title': 'Från rådata till färdig leverans – i fyra steg.',
                'sec_loop_sub': 'Ett strukturerat arbetsflöde som eliminerar manuellt skrivarbete, säkerställer regelefterlevnad '
                               'och levererar spårbara dokumentpaket klara för ansökan eller utförande.',
                'loop_1_t': 'Ladda upp rådata',
                'loop_1_d': 'PDF:er, IFC-modeller, labfiler, ritningar och projektdata samlas på ett ställe.',
                'loop_2_t': 'AI analyserar och genererar',
                'loop_2_d': 'Plattformen validerar, kontrollerar regelverk, beräknar och skriver rapporten – automatiskt.',
                'loop_3_t': 'Human-in-the-loop',
                'loop_3_d': 'Där lagen kräver det: ansvarig expert granskar och signerar digitalt. De flesta moduler levereras direkt.',
                'loop_4_t': 'Färdig leverans',
                'loop_4_d': 'Färdigt dokument för bygglov.',
                'mod_sec_kicker': 'Moduler',
                'mod_sec_title': 'Specialiserade agenter',
                'mod_sec_sub': 'Varje modul delar samma portal och kvalitetskontroll.',
                'mod_sec1': 'Tillgängligt nu',
                'mod_sec2': 'Roadmap och tidiga skeden',
                'mod_sec3': 'Hållbarhet & Säkerhet',
                'mod_sec3_sub': 'Integrerade tjänster för miljöuppföljning, säkerhet och certifiering, anpassade för att skapa '
                                'ansvarsfulla projekt.',
                'm_geo_t': 'GEO / MILJÖ',
                'm_geo_d': 'Analyserar labbfiler. Klassificerar massor och åtgärdsplaner.',
                'm_geo_in': 'XLSX / CSV + Karta',
                'm_geo_out': 'Åtgärdsplan',
                'm_geo_btn': 'Öppna Geo',
                'm_aku_t': 'AKUSTIK',
                'm_aku_d': 'Läser bullerkartor och planritningar. Genererar fasadkrav.',
                'm_aku_in': 'Bullerkarta + Plan',
                'm_aku_out': 'Akustikrapport',
                'm_aku_btn': 'Öppna Akustik',
                'm_brann_t': 'BRAND - Koncept',
                'm_brann_d': 'Utvärderar arkitektur mot BBR. Definierar brandceller.',
                'm_brann_in': 'Ritningar + Klass',
                'm_brann_out': 'Brandkoncept',
                'm_brann_btn': 'Öppna Brand',
                'm_ark_t': 'ARK - Förstudie',
                'm_ark_d': 'Tomtanalys och volymbedömning för tidiga skeden.',
                'm_ark_in': 'Detaljplan + Tomt',
                'm_ark_out': 'Förstudie',
                'm_ark_btn': 'Öppna ARK',
                'm_rib_t': 'Konstruktion',
                'm_rib_d': 'Konceptuella strukturkontroller och byggfysik.',
                'm_rib_in': 'Sektion + Laster',
                'm_rib_out': 'Koncept-PM',
                'm_rib_btn': 'Öppna Konstruktion',
                'm_tra_t': 'TRAFIK',
                'm_tra_d': 'Trafikalstring, parkering och logistik.',
                'm_tra_in': 'Situationsplan',
                'm_tra_out': 'Trafik-PM',
                'm_tra_btn': 'Öppna Trafik',
                'm_sha_t': 'SHA-Plan (Säkerhet)',
                'm_sha_d': 'Säkerhet, hälsa och arbetsmiljö. Genererar rutiner för byggarbetsplatsen.',
                'm_sha_in': 'Projektdata + Risker',
                'm_sha_out': 'Komplett SHA-plan',
                'm_sha_btn': 'Öppna SHA',
                'm_breeam_t': 'BREEAM Assistent',
                'm_breeam_d': 'Tidig bedömning av BREEAM-krav och materialstrategi.',
                'm_breeam_in': 'Byggdata + Ambition',
                'm_breeam_out': 'BREEAM Pre-assessment',
                'm_breeam_btn': 'Öppna BREEAM',
                'm_mop_t': 'MOP (Miljöplan)',
                'm_mop_d': 'Miljöuppföljningsplan för avfall, återbruk och utsläpp.',
                'm_mop_in': 'Projektdata + Miljömål',
                'm_mop_out': 'MOP Dokument',
                'm_mop_btn': 'Öppna MOP',
                'mod_sec6': 'Bank & Finansiering',
                'mod_sec6_sub': 'Moduler för byggnadskreditskontroll, kreditunderlag och bankrapportering. Automatiserad datainsamling och strukturerat beslutsstöd för långivare.',
                'badge_byggelanskontroll': 'Byggnadslån',
                'badge_kredittgrunnlag': 'Kredit',
                'm_byggelanskontroll_t': 'BYGGNADSKREDITSKONTROLL – Utbetalningskontroll & verifiering',
                'm_byggelanskontroll_d': 'Verifierar utbetalningsförfrågningar mot byggbudget, tidsplan och kontraktsunderlag. Genererar bankens kontrollrapport med avvikelser och godkännandeunderlag.',
                'm_byggelanskontroll_in': 'Utbetalningsförfrågan + budget + tidsplan',
                'm_byggelanskontroll_out': 'Kontrollrapport, avvikelselogg, godkännandeunderlag',
                'm_byggelanskontroll_btn': 'Öppna Kreditskontroll',
                'm_kredittgrunnlag_t': 'KREDITUNDERLAG – Beslutsstöd för kreditkommitté',
                'm_kredittgrunnlag_d': 'Sammanställer tekniska, regulatoriska och finansiella data till ett strukturerat kreditunderlag för tomtlån, byggnadslån och hyresrättslån.',
                'm_kredittgrunnlag_in': 'Projektdata + fastighetsinformation + finansstruktur',
                'm_kredittgrunnlag_out': 'Kreditpromemoria, riskmatris, beslutsunderlag',
                'm_kredittgrunnlag_btn': 'Öppna Kreditunderlag',
                'btn_dev': 'Under utveckling',
                'cta_title': 'Starta ett projekt. Ladda upp data.',
                'cta_desc': 'Builtly kombinerar insamling, AI och professionell signering i en portal.',
                'cta_btn1': 'Starta i Project Setup',
                'cta_btn2': 'Gå till QA-kö',
                'footer_copy': 'AI-assisted engineering. Human-verified. Compliance-grade.',
                'footer_meta': '© 2026 Builtly Engineering AS. Alla rättigheter förbehållna.',
                'label_input': 'Input',
                'label_output': 'Output',
                'assistant_kicker': 'Builtly Assistent',
                'assistant_title': 'Ställ frågor inom hela byggtekniken.',
                'assistant_subtitle': 'En integrerad frågeyta på startsidan för GEO, konstruktion, rivning, akustik, brand, miljö, SHA, '
                                      'BREEAM och fastighet. Assistenten följer valt språk och utgår från rätt nationellt regelverk.',
                'assistant_label_country': 'Land',
                'assistant_label_rules': 'Regelverk',
                'assistant_label_status': 'Status',
                'assistant_disciplines_label': 'Discipliner',
                'assistant_question_label': 'Din fråga',
                'assistant_placeholder': 'Exempel: Vad måste klarläggas i tidigt skede för ett bostadsprojekt i sex våningar med källare?',
                'assistant_btn': 'Fråga Builtly',
                'assistant_clear': 'Rensa dialog',
                'assistant_loading': 'Builtly analyserar din fråga...',
                'assistant_examples_label': 'Exempelfrågor',
                'assistant_examples': ['Vad bör vi klargöra tidigt för ett bostadshus med garage intill en trafikerad väg?',
                                       'Vilka BREEAM-frågor bör prioriteras tidigt för ett kontorsprojekt?',
                                       'Vad bör en rivnings- och SHA-strategi omfatta nära en skola?'],
                'assistant_disclaimer': 'Svaren är AI-assisterade och måste kvalitetssäkras av ansvarig specialist innan '
                                        'projekteringsbeslut eller signering.',
                'assistant_history_label': 'Senaste dialog',
                'assistant_empty_title': 'Redo för frågor från besökare',
                'assistant_empty_body': 'Besökare kan ställa frågor om byggteknik och fastighet direkt på startsidan, och svaren styrs av '
                                        'disciplin, språk och nationella regler.',
                'assistant_latest_answer': 'Senaste svar',
                'assistant_status_live': 'AI klar',
                'assistant_status_setup': 'UI klart',
                'assistant_error_prefix': 'Kunde inte generera svar',
                'assistant_note_prefix': 'Konfiguration',
                'assistant_scope_value': 'GEO · Konstruktion · Rivning · Akustik · Brand · Miljö · SHA · BREEAM · Fastighet · Trafik'},
 '🇩🇰 Dansk': {'rule_set': 'Danmark (BR18 / nationale annekser)',
              'eyebrow': 'Vi revolutionerer projekteringen inden for byg og ejendom',
              'title': "Ingeniørfaget møder AI.",
              'subtitle': 'Builtly automatiserer de standardiserede faglige leverancer inden for byggeri, anlæg og ejendom. '
                          'Upload rådata – AI analyserer, beregner og udarbejder rapporten. '
                          'Fagpersoner kvalitetssikrer og underskriver, hvor loven kræver det. '
                          'Du får mere tid til det, der faktisk kræver ingeniørhodet.',
              'btn_setup': 'Start i Project Setup',
              'btn_qa': 'Åbn QA & Sign-off',
              'proofs': ['Regelbaseret AI', 'Human-in-the-loop', 'PDF + DOCX', 'Digital signering', 'Fuldt revisionsspor'],
              'why_kicker': 'Hvorfor Builtly?',
              'stat1_v': 'Tid tilbage',
              'stat1_t': '80–90% mindre skrivearbejde',
              'stat1_d': 'AI tager det repetitive. Du tager det faglige.',
              'stat2_v': 'Verificeret',
              'stat2_t': 'Fagligt ansvar der, hvor loven kræver det',
              'stat2_d': 'Godkendte fagpersoner signerer digitalt – hvor byggeloven stiller krav.',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Komplette rapporter',
              'stat3_d': 'Med bilag og sporbarhed',
              'stat4_v': 'Sporbarhed',
              'stat4_t': 'Dokumentation',
              'stat4_d': 'Versionskontrol fra input til PDF',
              'sec_val_kicker': 'Platformen',
              'sec_val_title': 'Én platform. Alle fag. Én sandhed.',
              'sec_val_sub': 'Builtly er ikke et værktøj – det er infrastrukturen der forbinder alle aktører i værdikæden. '
                             'Bygherre, rådgiver, entreprenør, bank og forsikring på ét sporbart grundlag.',
              'val_1_t': 'Alle fag. Ét flow.',
              'val_1_d': 'GEO, Brand, Konstruktion, Akustik, SHA, TDD, Klimarisiko og mere – fra samme projektdata, i én portal.',
              'val_2_t': 'Regelbaseret AI',
              'val_2_d': 'AI opererer inden for bygningsreglementet, danske standarder og EU Taxonomy – ikke fri tekst. Forankret i reglerne.',
              'val_3_t': 'Human-in-the-loop der, hvor det tæller',
              'val_3_d': 'De fleste moduler leveres direkte. Hvor loven kræver fagligt ansvar, er godkendte fagpersoner i sign-off.',
              'val_4_t': 'Skalerer uden at vokse proportionelt',
              'val_4_d': 'Nye fag, nye markeder og nye partnere tilsluttes samme infrastruktur.',
              'sec_loop_kicker': 'Builtly Loop',
              'sec_loop_title': 'Fra rådata til færdig leverance – i fire trin.',
              'sec_loop_sub': 'Et struktureret workflow der eliminerer manuelt skrivearbejde, sikrer regeloverholdelse '
                             'og leverer sporbare dokumentpakker klar til sagsbehandling eller udførelse.',
              'loop_1_t': 'Upload rådata',
              'loop_1_d': 'PDF-er, IFC-modeller, labfiler, tegninger og projektdata samles ét sted.',
              'loop_2_t': 'AI analyserer og genererer',
              'loop_2_d': 'Platformen validerer, tjekker regler, laver beregninger og skriver rapporten – automatisk.',
              'loop_3_t': 'Human-in-the-loop',
              'loop_3_d': 'Hvor loven kræver det: fagperson med ansvar gennemgår og signerer digitalt. De fleste moduler leveres direkte.',
              'loop_4_t': 'Færdig leverance',
              'mod_sec_kicker': 'Moduler',
              'mod_sec_title': 'Specialiserede agenter',
              'mod_sec_sub': 'Hvert modul deler samme portal og kvalitetskontrol.',
              'mod_sec1': 'Tilgængelig nu',
              'mod_sec2': 'Roadmap',
              'mod_sec3': 'Bæredygtighed & Sikkerhed',
              'mod_sec3_sub': 'Integrerede tjenester til miljøopfølgning, sikkerhed og certificering, skræddersyet til ansvarlige '
                              'projekter.',
              'm_geo_t': 'GEO / MILJØ',
              'm_geo_d': 'Analyserer lab-filer og udarbejder miljøhandlingsplaner.',
              'm_geo_in': 'XLSX / CSV + Kort',
              'm_geo_out': 'Handlingsplan',
              'm_geo_btn': 'Åbn Geo',
              'm_aku_t': 'AKUSTIK',
              'm_aku_d': 'Læser støjkort. Genererer krav til facade.',
              'm_aku_in': 'Støjkort + Plan',
              'm_aku_out': 'Akustikrapport',
              'm_aku_btn': 'Åbn Akustik',
              'm_brann_t': 'BRAND',
              'm_brann_d': 'Vurderer arkitektur mod BR18. Definerer brandceller.',
              'm_brann_in': 'Tegninger + Klasse',
              'm_brann_out': 'Brandstrategi',
              'm_brann_btn': 'Åbn Brand',
              'm_ark_t': 'ARK - Studie',
              'm_ark_d': 'Grundanlyse og volumen for tidlige faser.',
              'm_ark_in': 'Lokalplan + Grund',
              'm_ark_out': 'Mulighedsstudie',
              'm_ark_btn': 'Åbn ARK',
              'm_rib_t': 'Konstruktion',
              'm_rib_d': 'Konceptuelle strukturtjek og bygningsfysik.',
              'm_rib_in': 'Snit + Laster',
              'm_rib_out': 'Konceptnotat',
              'm_rib_btn': 'Åbn Konstruktion',
              'm_tra_t': 'TRAFIK',
              'm_tra_d': 'Trafikgenerering og parkering.',
              'm_tra_in': 'Situationsplan',
              'm_tra_out': 'Trafiknotat',
              'm_tra_btn': 'Åbn Trafik',
              'm_sha_t': 'SHA-Plan (Sikkerhed)',
              'm_sha_d': 'Sikkerhed, sundhed og arbejdsmiljø. Genererer rutiner for byggepladsen.',
              'm_sha_in': 'Projektdata + Risici',
              'm_sha_out': 'Komplet SHA-plan',
              'm_sha_btn': 'Åbn SHA',
              'm_breeam_t': 'BREEAM Assistent',
              'm_breeam_d': 'Tidlig vurdering af BREEAM potentiale og materialestrategi.',
              'm_breeam_in': 'Byggedata + Ambition',
              'm_breeam_out': 'BREEAM Pre-assessment',
              'm_breeam_btn': 'Åbn BREEAM',
              'm_mop_t': 'MOP (Miljøplan)',
              'm_mop_d': 'Miljøopfølgningsplan for affald, genbrug og udledning.',
              'm_mop_in': 'Projektdata + Miljømål',
              'm_mop_out': 'MOP Dokument',
              'm_mop_btn': 'Åbn MOP',
              'mod_sec6': 'Bank & Finans',
              'mod_sec6_sub': 'Moduler til byggelånskontrol, kreditgrundlag og bankrapportering. Automatiseret dataindsamling og struktureret beslutningsstøtte til banker og kreditgivere.',
              'badge_byggelanskontroll': 'Byggelån',
              'badge_kredittgrunnlag': 'Kredit',
              'm_byggelanskontroll_t': 'BYGGELÅNSKONTROL – Udbetalingskontrol & verifikation',
              'm_byggelanskontroll_d': 'Verificerer trækningsanmodninger mod byggebudget, tidsplan og kontraktsgrundlag. Genererer bankens kontrolrapport med afvigelser og godkendelsesgrundlag.',
              'm_byggelanskontroll_in': 'Trækningsanmodning + budget + tidsplan',
              'm_byggelanskontroll_out': 'Kontrolrapport, afvigelseslog, godkendelsesgrundlag',
              'm_byggelanskontroll_btn': 'Åbn Byggelånskontrol',
              'm_kredittgrunnlag_t': 'KREDITGRUNDLAG – Beslutningsstøtte til kreditkomité',
              'm_kredittgrunnlag_d': 'Sammenstiller tekniske, regulatoriske og finansielle data til et struktureret kreditgrundlag for grund-, bygge- og udlejningslån.',
              'm_kredittgrunnlag_in': 'Projektdata + ejendomsinfo + finansieringsstruktur',
              'm_kredittgrunnlag_out': 'Kreditmemo, risikomatrix, beslutningsgrundlag',
              'm_kredittgrunnlag_btn': 'Åbn Kreditgrundlag',
              'btn_dev': 'Under udvikling',
              'cta_title': 'Start et projekt. Upload data.',
              'cta_desc': 'Builtly kombinerer dataindsamling, AI og faglig signering i én portal.',
              'cta_btn1': 'Start i Project Setup',
              'cta_btn2': 'Gå til QA',
              'footer_copy': 'AI-assisted engineering. Human-verified. Compliance-grade.',
              'footer_meta': '© 2026 Builtly Engineering AS. Alle rettigheder forbeholdes.',
              'label_input': 'Input',
              'label_output': 'Output',
              'assistant_kicker': 'Builtly Assistent',
              'assistant_title': 'Stil spørgsmål på tværs af alle byggediscipliner.',
              'assistant_subtitle': 'En integreret spørgeflade på forsiden for GEO, konstruktion, nedrivning, akustik, brand, miljø, SHA, '
                                    'BREEAM og ejendom. Assistenten følger valgt sprog og tager udgangspunkt i det relevante nationale '
                                    'regelsæt.',
              'assistant_label_country': 'Land',
              'assistant_label_rules': 'Regelsæt',
              'assistant_label_status': 'Status',
              'assistant_disciplines_label': 'Fagområder',
              'assistant_question_label': 'Dit spørgsmål',
              'assistant_placeholder': 'Eksempel: Hvad skal afklares tidligt for et boligprojekt i seks etager med kælder?',
              'assistant_btn': 'Spørg Builtly',
              'assistant_clear': 'Ryd dialog',
              'assistant_loading': 'Builtly analyserer dit spørgsmål...',
              'assistant_examples_label': 'Eksempelspørgsmål',
              'assistant_examples': ['Hvad skal vi afklare tidligt for en boligblok med parkeringskælder ved en trafikeret vej?',
                                     'Hvilke BREEAM-emner bør prioriteres tidligt i et kontorprojekt?',
                                     'Hvad bør en nedrivnings- og SHA-strategi dække tæt på en skole?'],
              'assistant_disclaimer': 'Svarene er AI-assisterede og skal kvalitetssikres af ansvarlig fagperson før projekteringsvalg '
                                      'eller signering.',
              'assistant_history_label': 'Seneste dialog',
              'assistant_empty_title': 'Klar til spørgsmål fra besøgende',
              'assistant_empty_body': 'Besøgende kan stille spørgsmål om byggeteknik og ejendom direkte på forsiden, og svarene styres af '
                                      'fagvalg, sprog og nationale regler.',
              'assistant_latest_answer': 'Seneste svar',
              'assistant_status_live': 'AI klar',
              'assistant_status_setup': 'UI klar',
              'assistant_error_prefix': 'Kunne ikke generere svar',
              'assistant_note_prefix': 'Opsætning',
              'assistant_scope_value': 'GEO · Konstruktion · Nedrivning · Akustik · Brand · Miljø · SHA · BREEAM · Ejendom · Trafik'},
 '🇺🇸 English (US)': {'rule_set': 'United States (IBC / IRC / local amendments)',
                     'eyebrow': 'Reimagining engineering in construction and property',
                     'title': "Engineering meets AI.",
                     'subtitle': 'Builtly automates the standardized technical deliverables in construction, civil and property. '
                                 'Upload raw data – AI analyses, calculates and produces the report. '
                                 'Qualified professionals review and sign off where the law requires it. '
                                 'You get more time for the work that actually needs an engineer.',
                     'btn_setup': 'Open project setup',
                     'btn_qa': 'Open QA and sign-off',
                     'proofs': ['Rules-first AI', 'Human-in-the-loop', 'PDF + DOCX output', 'Digital sign-off', 'Full audit trail'],
                     'why_kicker': 'Why Builtly?',
                     'stat1_v': 'Time back',
                     'stat1_t': '80–90% less manual writing',
                     'stat1_d': 'AI handles the repetitive. You handle the judgement.',
                     'stat2_v': 'Verified',
                     'stat2_t': 'Professional sign-off where the law requires it',
                     'stat2_d': 'Qualified responsible engineers certify digitally – where building regulations demand it.',
                     'stat3_v': 'PDF + DOCX',
                     'stat3_t': 'Complete report packages',
                     'stat3_d': 'with appendices and traceability',
                     'stat4_v': 'Full Traceability',
                     'stat4_t': 'End-to-end logging',
                     'stat4_d': 'Inputs, versions, compliance checks logged',
                     'sec_val_kicker': 'The platform',
                     'sec_val_title': 'One platform. Every discipline. One source of truth.',
                     'sec_val_sub': 'Builtly is not a tool – it is the infrastructure that connects every stakeholder in the value chain. '
                                    'Developer, engineer, contractor, bank and insurer on one traceable foundation.',
                     'val_1_t': 'Every discipline. One flow.',
                     'val_1_d': 'GEO, Fire, Structural, Acoustics, SHA, TDD, Climate risk and more – from the same project data, in one portal.',
                     'val_2_t': 'Rules-first AI',
                     'val_2_d': 'AI operates inside building regulations, national standards and EU Taxonomy – not free-form text. Grounded in the rules.',
                     'val_3_t': 'Human-in-the-loop where it counts',
                     'val_3_d': 'Most modules deliver directly. Where the law requires a responsible engineer, qualified professionals are in the sign-off.',
                     'val_4_t': 'Scales without growing proportionally',
                     'val_4_d': 'New disciplines, new markets and new partners plug into the same infrastructure.',
                     'sec_loop_kicker': 'The Builtly Loop',
                     'sec_loop_title': 'From raw data to finished deliverable – in four steps.',
                     'sec_loop_sub': 'A structured workflow that eliminates manual drafting, ensures regulatory compliance and delivers '
                                     'traceable document packages ready for submission or execution.',
                     'loop_1_t': 'Upload raw data',
                     'loop_1_d': 'PDFs, IFC models, lab files, drawings and project data in one place.',
                     'loop_2_t': 'AI analyses and generates',
                     'loop_2_d': 'The platform parses, validates, applies local rule checks, performs calculations, and drafts the '
                                 'deliverable.',
                     'loop_3_t': 'QA and sign-off',
                     'loop_3_d': 'Junior review, senior technical assessment, and digital sign-off - with version control throughout.',
                     'loop_4_t': 'Output',
                     'loop_4_d': 'Final documentation package in standard formats, ready for permit submission or construction use.',
                     'mod_sec_kicker': 'Modules and roadmap',
                     'mod_sec_title': 'Specialized agents in one platform',
                     'mod_sec_sub': 'Each module has dedicated ingestion logic, discipline-specific rules, and output templates while '
                                    'sharing the same portal, validation, QA, and sign-off backbone.',
                     'mod_sec1': 'Available now and pilot-ready',
                     'mod_sec2': 'Roadmap and early-phase tools',
                     'mod_sec3': 'Sustainability & Compliance',
                     'mod_sec3_sub': 'Integrated services for environmental follow-up, safety, and certification, tailored to create '
                                     'responsible and value-driven developments.',
                     'm_geo_t': 'GEO / ENV - Ground Conditions',
                     'm_geo_d': 'Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts '
                                'environmental action plans.',
                     'm_geo_in': 'XLSX / CSV / PDF + plans',
                     'm_geo_out': 'Environmental action plan, logs',
                     'm_geo_btn': 'Open Geo & Env',
                     'm_aku_t': 'ACOUSTICS - Noise & Sound',
                     'm_aku_d': 'Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation '
                                'strategies.',
                     'm_aku_in': 'Noise map + floor plan',
                     'm_aku_out': 'Acoustics report, facade evaluation',
                     'm_aku_btn': 'Open Acoustics',
                     'm_brann_t': 'FIRE - Safety Strategy',
                     'm_brann_d': 'Evaluate architectural drawings against applicable building codes. Generates egress logic, fire '
                                  'compartmentation, and fire strategy.',
                     'm_brann_in': 'Architectural drawings + class',
                     'm_brann_out': 'Fire strategy concept, deviations',
                     'm_brann_btn': 'Open Fire Strategy',
                     'm_ark_t': 'ARK - Feasibility Study',
                     'm_ark_d': 'Site screening, volume analysis, and early-phase decision support before full engineering design.',
                     'm_ark_in': 'Site data, zoning plans',
                     'm_ark_out': 'Feasibility report, utilization metrics',
                     'm_ark_btn': 'Open Feasibility',
                     'm_rib_t': 'STRUC - Structural Concept',
                     'm_rib_d': 'Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.',
                     'm_rib_in': 'Models, load parameters',
                     'm_rib_out': 'Concept memo, grid layouts',
                     'm_rib_btn': 'Open Structural',
                     'm_tra_t': 'TRAFFIC - Mobility',
                     'm_tra_d': 'Traffic generation, parking requirements, access logic, and soft-mobility planning for early project '
                                'phases.',
                     'm_tra_in': 'Site plans, local norms',
                     'm_tra_out': 'Traffic memo, mobility plan',
                     'm_tra_btn': 'Open Traffic & Mobility',
                     'm_sha_t': 'SHA - Safety & Health Plan',
                     'm_sha_d': 'Safety, health, and working environment. Generates routines for site logistics and high-risk operations.',
                     'm_sha_in': 'Project data + Risk factors',
                     'm_sha_out': 'Complete SHA plan',
                     'm_sha_btn': 'Open SHA Module',
                     'm_breeam_t': 'BREEAM Assistant',
                     'm_breeam_d': 'Early-phase assessment of BREEAM potential, credit requirements, and material strategies.',
                     'm_breeam_in': 'Building data + Ambitions',
                     'm_breeam_out': 'BREEAM Pre-assessment',
                     'm_breeam_btn': 'Open BREEAM Assistant',
                     'm_mop_t': 'MOP - Environment Plan',
                     'm_mop_d': 'Environmental follow-up plan. Assesses waste management, reuse, emissions, and nature preservation.',
                     'm_mop_in': 'Project data + Eco goals',
                     'm_mop_out': 'MOP Document',
                     'm_mop_btn': 'Open MOP Module',
                     'btn_dev': 'In development',
                     'cta_title': 'Ready to reimagine how engineering gets done?',
                     'cta_desc': 'Start with one project. See what AI can deliver in minutes – not days. '
                                 'Builtly is built for those who want to lead, not follow.',
                     'cta_btn1': 'Start in project setup',
                     'cta_btn2': 'Go to review queue',
                     'footer_copy': 'AI-assisted engineering. Human-verified. Compliance-grade.',
                     'footer_meta': '© 2026 Builtly Engineering AS. All rights reserved.',
                     'label_input': 'Input',
                     'label_output': 'Output',
                     'assistant_kicker': 'Builtly Assistant',
                     'assistant_title': 'Ask across every engineering discipline.',
                     'assistant_subtitle': 'A front-page question surface for GEO, structural, demolition, acoustics, fire, environment, '
                                           'SHA, BREEAM, and real estate. The assistant follows the selected language and defaults to the '
                                           'relevant national code framework.',
                     'assistant_label_country': 'Country',
                     'assistant_label_rules': 'Rule set',
                     'assistant_label_status': 'Status',
                     'assistant_disciplines_label': 'Disciplines',
                     'assistant_question_label': 'Your question',
                     'assistant_placeholder': 'Example: What should be checked early for a six-story multifamily project with one basement '
                                              'level?',
                     'assistant_btn': 'Ask Builtly',
                     'assistant_clear': 'Clear conversation',
                     'assistant_loading': 'Builtly is analysing your question...',
                     'assistant_examples_label': 'Example prompts',
                     'assistant_examples': ['What needs early review for a multifamily project with one basement level next to a busy '
                                            'street?',
                                            'Which BREEAM themes should be prioritized early for an office project?',
                                            'What should a demolition and site safety strategy cover next to a school?'],
                     'assistant_disclaimer': 'Guidance is AI-assisted and must be quality-assured by the responsible discipline lead '
                                             'before design decisions or sign-off.',
                     'assistant_history_label': 'Recent dialogue',
                     'assistant_empty_title': 'Ready for live questions',
                     'assistant_empty_body': 'Visitors can ask questions about building technology and property right on the front page, '
                                             'and the answer can be steered by discipline, language and national regulations.',
                     'assistant_latest_answer': 'Latest answer',
                     'assistant_status_live': 'AI ready',
                     'assistant_status_setup': 'UI ready',
                     'assistant_error_prefix': 'Could not generate an answer',
                     'assistant_note_prefix': 'Setup note',
                     'assistant_scope_value': 'GEO · Structural · Demolition · Acoustics · Fire · Environment · Safety · BREEAM · Real '
                                              'Estate · Traffic'},
 '🇫🇮 Suomi': {'rule_set': 'Suomi (rakentamislaki / Suomen rakentamismääräykset)',
              'eyebrow': 'Vallankumoamme suunnitteluprosessin rakentamisessa ja kiinteistöalalla',
              'title': "Insinöörityö kohtaa tekoälyn.",
              'subtitle': 'Builtly automatisoi standardoidut tekniset toimitukset rakentamisessa, infrassa ja kiinteistöalalla. '
                          'Lataa raakadata – tekoäly analysoi, laskee ja tuottaa raportin. '
                          'Asiantuntijat tarkistavat ja allekirjoittavat, missä laki sitä edellyttää. '
                          'Sinulle jää enemmän aikaa työhön, joka todella vaatii insinööriälyä.',
              'btn_setup': 'Avaa projektin aloitus',
              'btn_qa': 'Avaa QA ja hyväksyntä',
              'proofs': ['Sääntöpohjainen AI', 'Audit trail', 'PDF + DOCX', 'Digitaalinen hyväksyntä', 'Jäsennelty QA'],
              'why_kicker': 'Miksi Builtly?',
              'stat1_v': 'Aikaa takaisin',
              'stat1_t': '80–90% vähemmän manuaalista kirjoitustyötä',
              'stat1_d': 'Tekoäly hoitaa toistuvat. Sinä hoidat ammatillisen.',
              'stat2_v': 'Varmennettu',
              'stat2_t': 'Ammatillinen vastuu siellä, missä laki vaatii',
              'stat2_d': 'Hyväksytyt vastuuhenkilöt allekirjoittavat digitaalisesti – missä rakentamismääräykset edellyttävät sitä.',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Valmiit raporttipaketit',
              'stat3_d': 'liitteineen ja jäljitettävyyksineen',
              'stat4_v': 'Täysi jäljitettävyys',
              'stat4_t': 'Lokitus alusta loppuun',
              'stat4_d': 'syötteet, versiot ja määräystarkistukset tallennetaan',
              'sec_val_kicker': 'Alusta',
              'sec_val_title': 'Yksi alusta. Kaikki alat. Yksi totuus.',
              'sec_val_sub': 'Builtly ei ole irrallisten työkalujen kokoelma. Se on yksi turvallinen portaali projektin aloitukseen, '
                             'tiedonkeruuseen, validointiin, AI-käsittelyyn, tarkastukseen, hyväksyntään ja lopulliseen toimitukseen.',
              'val_1_t': 'Asiakasportaali',
              'val_1_d': 'Projektin perustaminen, aineistojen lataus, puuttuvien tietojen seuranta, dokumenttien tuotanto ja audit trail '
                         'samassa työnkulussa.',
              'val_2_t': 'Sääntöpohjainen AI',
              'val_2_d': 'AI toimii selkeiden määräysten, tarkistuslistojen ja standardimallien sisällä - ei vapaana arvailuna.',
              'val_3_t': 'Human-in-the-loop siellä, missä se merkitsee',
              'val_3_d': 'Useimmat moduulit toimitetaan suoraan. Missä laki edellyttää vastuuhenkilöä, he ovat mukana hyväksynnässä.',
              'val_4_t': 'Skaalautuva toimitus',
              'val_4_d': 'Uudet suunnittelualat voidaan liittää samaan validointi-, dokumentointi- ja hyväksyntärunkoon.',
              'sec_loop_kicker': 'Builtly Loop',
              'sec_loop_title': 'Raakadatasta valmiiseen toimitukseen – neljässä vaiheessa.',
              'sec_loop_sub': 'Jäsennelty työnkulku, joka poistaa manuaalisen kirjoitustyön, varmistaa säädöstenmukaisuuden '
                             'ja tuottaa jäljitettäviä dokumenttipaketteja valmiina lupakäsittelyyn tai toteutukseen.',
              'loop_1_t': 'Syöte',
              'loop_1_d': 'Lataa PDF:t, IFC-mallit, Excel-tiedostot, piirustukset ja projektikohtaiset tiedot yhteen paikkaan.',
              'loop_2_t': 'Tekoäly analysoi ja luo',
              'loop_2_d': 'Alusta validoi, tarkistaa säädökset, tekee laskelmat ja kirjoittaa raportin – automaattisesti.',
              'loop_3_t': 'Human-in-the-loop',
              'loop_3_d': 'Missä laki vaatii: vastuuhenkilö tarkistaa ja allekirjoittaa digitaalisesti. Useimmat moduulit toimitetaan suoraan.',
              'loop_4_t': 'Valmis toimitus',
              'loop_4_d': 'Valmis dokumenttipaketti vakioformaateissa, valmis lupakäsittelyyn tai toteutukseen.',
              'mod_sec_kicker': 'Moduulit ja tiekartta',
              'mod_sec_title': 'Erikoistuneet agentit yhdellä alustalla',
              'mod_sec_sub': 'Jokaisella moduulilla on oma syöttölogiikka, alakohtaiset säännöt ja tuotemallit, mutta sama portaali-, '
                             'validointi-, QA- ja hyväksyntärunko.',
              'mod_sec1': 'Saatavilla nyt',
              'mod_sec2': 'Tiekartta ja varhaisen vaiheen työkalut',
              'mod_sec3': 'Kestävyys ja turvallisuus',
              'mod_sec3_sub': 'Integroituja palveluja ympäristöseurantaan, turvallisuuteen ja sertifiointiin vastuullisten hankkeiden '
                              'tueksi.',
              'm_geo_t': 'GEO / YMPÄRISTÖ - Maaperä ja olosuhteet',
              'm_geo_d': 'Analysoi laboratoriotiedot ja kaivusuunnitelmat. Luokittelee massat ja laatii ympäristötoimenpiteitä.',
              'm_geo_in': 'XLSX / CSV / PDF + suunnitelmat',
              'm_geo_out': 'Ympäristötoimenpidesuunnitelma, lokit',
              'm_geo_btn': 'Avaa Geo & ympäristö',
              'm_aku_t': 'AKUSTIIKKA - Melu ja ääni',
              'm_aku_d': 'Lukee melukarttoja ja pohjakuvia. Tuottaa julkisivuvaatimukset, ikkunaerittelyt ja torjuntaratkaisut.',
              'm_aku_in': 'Melukartta + pohja',
              'm_aku_out': 'Akustiikkaraportti',
              'm_aku_btn': 'Avaa akustiikka',
              'm_brann_t': 'PALO - Turvallisuusstrategia',
              'm_brann_d': 'Arvioi arkkitehtipiirustukset määräysten näkökulmasta. Tuottaa poistumislogiikan, palo-osastoinnin ja '
                           'palostrategian.',
              'm_brann_in': 'Piirustukset + luokka',
              'm_brann_out': 'Palostrategia',
              'm_brann_btn': 'Avaa palostrategia',
              'm_ark_t': 'ARK - Toteutettavuustutkimus',
              'm_ark_d': 'Tontin seulonta, volyymianalyysi ja varhaisen vaiheen päätöstuki ennen täyttä suunnittelua.',
              'm_ark_in': 'Tonttidata, kaavat',
              'm_ark_out': 'Toteutettavuusraportti',
              'm_ark_btn': 'Avaa feasibility',
              'm_rib_t': 'RAKENNE - Konseptitarkastelu',
              'm_rib_d': 'Konseptitason rakennecheckit, mitoitusperiaatteet ja yhteys hiilijalanjälkilaskentaan.',
              'm_rib_in': 'Mallit, kuormat',
              'm_rib_out': 'Konseptimuistio',
              'm_rib_btn': 'Avaa rakenne',
              'm_tra_t': 'LIIKENNE - Liikkuminen',
              'm_tra_d': 'Liikennetuotos, pysäköinti, saavutettavuus ja pehmeän liikenteen tarkastelut varhaisessa vaiheessa.',
              'm_tra_in': 'Asemapiirros, paikalliset normit',
              'm_tra_out': 'Liikennemuistio',
              'm_tra_btn': 'Avaa liikenne',
              'm_sha_t': 'SHA - Turvallisuus ja terveys',
              'm_sha_d': 'Turvallisuus, terveys ja työympäristö. Tuottaa käytännöt työmaajärjestelyille ja riskialttiille töille.',
              'm_sha_in': 'Projektidata + riskit',
              'm_sha_out': 'Täydellinen SHA-suunnitelma',
              'm_sha_btn': 'Avaa SHA',
              'm_breeam_t': 'BREEAM-avustaja',
              'm_breeam_d': 'Varhaisen vaiheen arvio BREEAM-potentiaalista, krediiteistä ja materiaalistrategioista.',
              'm_breeam_in': 'Rakennusdata + tavoitteet',
              'm_breeam_out': 'BREEAM-esiarvio',
              'm_breeam_btn': 'Avaa BREEAM',
              'm_mop_t': 'MOP - Ympäristösuunnitelma',
              'm_mop_d': 'Ympäristön seuranta- ja toteutussuunnitelma jätteille, uudelleenkäytölle, päästöille ja luonnolle.',
              'm_mop_in': 'Projektidata + ympäristötavoitteet',
              'm_mop_out': 'MOP-dokumentti',
              'm_mop_btn': 'Avaa MOP',
              'mod_sec6': 'Pankki & Rahoitus',
              'mod_sec6_sub': 'Moduulit rakennuslainan valvontaan, luottoperusteisiin ja pankkiraportointiin. Automatisoitu tiedonkeruu ja jäsennelty päätöksentuki pankeille ja luotonantajille.',
              'badge_byggelanskontroll': 'Rakennuslaina',
              'badge_kredittgrunnlag': 'Luotto',
              'm_byggelanskontroll_t': 'RAKENNUSLAINAN VALVONTA – Maksatuksen valvonta & todentaminen',
              'm_byggelanskontroll_d': 'Tarkistaa nostopyynnöt rakennusbudjetin, aikataulun ja sopimusperustan suhteen. Tuottaa pankin valvontaraportin poikkeamineen ja hyväksymisperustan.',
              'm_byggelanskontroll_in': 'Nostopyyntö + budjetti + aikataulu',
              'm_byggelanskontroll_out': 'Valvontaraportti, poikkeamaloki, hyväksymisperusta',
              'm_byggelanskontroll_btn': 'Avaa Lainavalvonta',
              'm_kredittgrunnlag_t': 'LUOTTOPERUSTE – Päätöstuki luottovaliokunnalle',
              'm_kredittgrunnlag_d': 'Kokoaa tekniset, sääntelyyn liittyvät ja taloudelliset tiedot jäsennellyksi luottoperusteeksi tontti-, rakennus- ja vuokralainoja varten.',
              'm_kredittgrunnlag_in': 'Projektidata + kiinteistötiedot + rahoitusrakenne',
              'm_kredittgrunnlag_out': 'Luottomuistio, riskimatriisi, päätösperuste',
              'm_kredittgrunnlag_btn': 'Avaa Luottoperuste',
              'btn_dev': 'Kehityksessä',
              'cta_title': 'Aloita yhdellä projektilla. Lataa raaka-aineisto.',
              'cta_desc': 'Builtly yhdistää asiakkaan itsepalvelun, deterministiset tarkistukset, AI-luonnokset ja ammatillisen '
                          'hyväksynnän yhteen portaaliin.',
              'cta_btn1': 'Aloita projektin aloituksessa',
              'cta_btn2': 'Siirry tarkastusjonoon',
              'footer_copy': 'AI-avusteinen suunnittelu. Ihmisen varmistama. Compliance-grade.',
              'footer_meta': '© 2026 Builtly Engineering AS. Kaikki oikeudet pidätetään.',
              'label_input': 'Syöte',
              'label_output': 'Tuloste',
              'assistant_kicker': 'Builtly-assistentti',
              'assistant_title': 'Kysy kaikista rakennustekniikan osa-alueista.',
              'assistant_subtitle': 'Integroitu kysymysnäkymä etusivulla GEO:lle, rakenteille, purulle, akustiikalle, palolle, '
                                    'ympäristölle, SHA:lle, BREEAMille ja kiinteistöille. Assistentti seuraa valittua kieltä ja soveltaa '
                                    'oikeaa kansallista sääntöpohjaa.',
              'assistant_label_country': 'Maa',
              'assistant_label_rules': 'Sääntöpohja',
              'assistant_label_status': 'Tila',
              'assistant_disciplines_label': 'Aihealueet',
              'assistant_question_label': 'Kysymyksesi',
              'assistant_placeholder': 'Esimerkki: Mitä pitää selvittää varhaisessa vaiheessa kuusikerroksisessa asuinhankkeessa, jossa on '
                                       'kellari?',
              'assistant_btn': 'Kysy Builtlyltä',
              'assistant_clear': 'Tyhjennä keskustelu',
              'assistant_loading': 'Builtly analysoi kysymystäsi...',
              'assistant_examples_label': 'Esimerkkikysymyksiä',
              'assistant_examples': ['Mitä pitää selvittää varhain asuinkerrostalossa, jossa on pysäköintikellari vilkkaan tien vieressä?',
                                     'Mitkä BREEAM-teemat kannattaa priorisoida toimistohankkeen alkuvaiheessa?',
                                     'Mitä purku- ja SHA-strategian pitäisi kattaa koulun vieressä?'],
              'assistant_disclaimer': 'Vastaukset ovat AI-avusteisia ja vastuullisen asiantuntijan on tarkistettava ne ennen '
                                      'suunnittelupäätöksiä tai hyväksyntää.',
              'assistant_history_label': 'Viimeaikainen dialogi',
              'assistant_empty_title': 'Valmis live-kysymyksille',
              'assistant_empty_body': 'Vierailijat voivat kysyä rakennustekniikasta ja kiinteistökehityksestä suoraan etusivulla. '
                                      'Vastaukset ohjautuvat alan, kielen ja kansallisten määräysten mukaan.',
              'assistant_latest_answer': 'Viimeisin vastaus',
              'assistant_status_live': 'AI valmis',
              'assistant_status_setup': 'Käyttöliittymä valmis',
              'assistant_error_prefix': 'Vastauksen luonti epäonnistui',
              'assistant_note_prefix': 'Huomio',
              'assistant_scope_value': 'GEO · Rakenteet · Purku · Akustiikka · Palo · Ympäristö · SHA · BREEAM · Kiinteistö · Liikenne'},
 '🇩🇪 Deutsch': {'rule_set': 'Deutschland (Landesbauordnungen / MBO / MVV TB)',
                'eyebrow': 'Wir revolutionieren das Bauwesen und die Immobilienbranche',
                'title': "Das Ingenieurwesen trifft KI.",
                'subtitle': 'Builtly automatisiert die standardisierten Fachleistungen im Bau-, Ingenieur- und Immobilienwesen. '
                             'Rohdaten hochladen – KI analysiert, berechnet und erstellt den Bericht. '
                             'Fachleute prüfen und geben frei, wo das Gesetz es vorschreibt. '
                             'Sie gewinnen mehr Zeit für die Arbeit, die wirklich Ingenieurverstand erfordert.',
                'btn_setup': 'Projekt-Setup öffnen',
                'btn_qa': 'QA & Freigabe öffnen',
                'proofs': ['Regelbasierte KI', 'Human-in-the-loop', 'PDF + DOCX', 'Digitale Freigabe', 'Vollständiger Prüfpfad'],
                'why_kicker': 'Warum Builtly?',
                'stat1_v': 'Zeit zurück',
                'stat1_t': '80–90% weniger Schreibarbeit',
                'stat1_d': 'Die KI übernimmt das Repetitive. Sie übernehmen das Fachliche.',
                'stat2_v': 'Verifiziert',
                'stat2_t': 'Fachverantwortung wo das Gesetz es fordert',
                'stat2_d': 'Zugelassene Fachleute unterschreiben digital – wo die Landesbauordnung es verlangt.',
                'stat3_v': 'PDF + DOCX',
                'stat3_t': 'Komplette Berichtspakete',
                'stat3_d': 'mit Anhängen und Nachvollziehbarkeit',
                'stat4_v': 'Volle Nachvollziehbarkeit',
                'stat4_t': 'End-to-end Logging',
                'stat4_d': 'Eingaben, Versionen und Regelprüfungen werden protokolliert',
                'sec_val_kicker': 'Die Plattform',
                'sec_val_title': 'Eine Plattform. Alle Disziplinen. Eine Wahrheit.',
                'sec_val_sub': 'Builtly ist kein Werkzeug – es ist die Infrastruktur, die alle Akteure der Wertschöpfungskette verbindet. '
                               'Bauherr, Ingenieur, Unternehmer, Bank und Versicherung auf einer nachvollziehbaren Grundlage.',
                'val_1_t': 'Alle Disziplinen. Ein Workflow.',
                'val_1_d': 'GEO, Brand, Tragwerk, Akustik, SHA, TDD, Klimarisiko und mehr – aus denselben Projektdaten, in einem Portal.',
                'val_2_t': 'Regelbasierte KI',
                'val_2_d': 'Die KI arbeitet innerhalb klarer regulatorischer Leitplanken, Checklisten und Standardvorlagen - nicht als '
                           'freies Rätselraten.',
                'val_3_t': 'Human-in-the-loop wo es zählt',
                'val_3_d': 'Die meisten Module liefern direkt. Wo das Gesetz einen verantwortlichen Ingenieur verlangt, sind Fachleute im Sign-off.',
                'val_4_t': 'Skaliert ohne proportionales Wachstum',
                'val_4_d': 'Neue Disziplinen, neue Märkte und neue Partner werden an dieselbe Infrastruktur angebunden.',
                'sec_loop_kicker': 'Builtly Loop',
                'sec_loop_title': 'Von Rohdaten zur fertigen Lieferung – in vier Schritten.',
                'sec_loop_sub': 'Ein strukturierter Workflow, der manuelle Schreibarbeit eliminiert, Regelkonformität sicherstellt '
                               'und nachvollziehbare Dokumentationspakete liefert, bereit für Genehmigung oder Ausführung.',
                'loop_1_t': 'Input',
                'loop_1_d': 'PDFs, IFC-Modelle, Excel-Dateien, Zeichnungen und projektspezifische Daten an einem Ort hochladen.',
                'loop_2_t': 'KI analysiert und generiert',
                'loop_2_d': 'Die Plattform validiert, prüft Vorschriften, führt Berechnungen aus und erstellt den Bericht – automatisch.',
                'loop_3_t': 'Human-in-the-loop',
                'loop_3_d': 'Wo das Gesetz es verlangt: zugelassene Fachleute prüfen und unterzeichnen digital. Die meisten Module liefern direkt.',
                'loop_4_t': 'Fertige Lieferung',
                'loop_4_d': 'Vollständiges Dokumentationspaket in Standardformaten – bereit für die Baugenehmigung oder Ausführung.',
                'mod_sec_kicker': 'Module und Roadmap',
                'mod_sec_title': 'Spezialisierte Agenten auf einer Plattform',
                'mod_sec_sub': 'Jedes Modul hat eigene Ingestionslogik, fachliche Regelwerke und Ausgabevorlagen, teilt sich aber Portal, '
                               'Validierung, QA und Freigabe.',
                'mod_sec1': 'Jetzt verfügbar',
                'mod_sec2': 'Roadmap und Frühphase',
                'mod_sec3': 'Nachhaltigkeit & Sicherheit',
                'mod_sec3_sub': 'Integrierte Leistungen für Umweltbegleitung, Sicherheit und Zertifizierung für verantwortungsvolle '
                                'Projekte.',
                'm_geo_t': 'GEO / UMWELT - Baugrund und Rahmenbedingungen',
                'm_geo_d': 'Analysiert Laborwerte und Aushubpläne, klassifiziert Massen und erstellt Umweltmaßnahmen.',
                'm_geo_in': 'XLSX / CSV / PDF + Pläne',
                'm_geo_out': 'Umweltmaßnahmenplan, Logs',
                'm_geo_btn': 'Geo & Umwelt öffnen',
                'm_aku_t': 'AKUSTIK - Lärm & Schall',
                'm_aku_d': 'Liest Lärmkarten und Grundrisse ein. Erzeugt Fassadenanforderungen, Fensterspezifikationen und '
                           'Minderungsstrategien.',
                'm_aku_in': 'Lärmkarte + Grundriss',
                'm_aku_out': 'Akustikbericht',
                'm_aku_btn': 'Akustik öffnen',
                'm_brann_t': 'BRAND - Sicherheitskonzept',
                'm_brann_d': 'Prüft Architekturunterlagen gegen baurechtliche Anforderungen und erzeugt Fluchtweglogik, Brandabschnitte '
                             'und Brandschutzstrategie.',
                'm_brann_in': 'Pläne + Klasse',
                'm_brann_out': 'Brandschutzkonzept',
                'm_brann_btn': 'Brandschutz öffnen',
                'm_ark_t': 'ARK - Machbarkeitsstudie',
                'm_ark_d': 'Grundstücksscreening, Volumenanalyse und Entscheidungsgrundlage für frühe Projektphasen.',
                'm_ark_in': 'Standortdaten, Planungsrecht',
                'm_ark_out': 'Machbarkeitsbericht',
                'm_ark_btn': 'Machbarkeit öffnen',
                'm_rib_t': 'TRAGWERK - Strukturkonzept',
                'm_rib_d': 'Konzeptionelle Tragwerkschecks, erste Dimensionierung und Anbindung an CO2-Betrachtungen.',
                'm_rib_in': 'Modelle, Lastannahmen',
                'm_rib_out': 'Konzeptmemo',
                'm_rib_btn': 'Tragwerk öffnen',
                'm_tra_t': 'VERKEHR - Mobilität',
                'm_tra_d': 'Verkehrserzeugung, Stellplätze, Erschließungslogik und Mobilitätskonzepte in frühen Phasen.',
                'm_tra_in': 'Lageplan, lokale Normen',
                'm_tra_out': 'Verkehrsmemo',
                'm_tra_btn': 'Verkehr öffnen',
                'm_sha_t': 'SHA - Sicherheit & Gesundheit',
                'm_sha_d': 'Sicherheit, Gesundheit und Arbeitsumfeld. Erzeugt Routinen für Baustellenlogistik und risikoreiche Arbeiten.',
                'm_sha_in': 'Projektdaten + Risiken',
                'm_sha_out': 'Vollständiger SHA-Plan',
                'm_sha_btn': 'SHA öffnen',
                'm_breeam_t': 'BREEAM-Assistent',
                'm_breeam_d': 'Frühe Bewertung von BREEAM-Potenzial, Credits und Materialstrategien.',
                'm_breeam_in': 'Gebäudedaten + Ambition',
                'm_breeam_out': 'BREEAM Pre-Assessment',
                'm_breeam_btn': 'BREEAM öffnen',
                'm_mop_t': 'MOP - Umweltplan',
                'm_mop_d': 'Umweltbegleitplan für Abfall, Wiederverwendung, Emissionen und Naturbelange.',
                'm_mop_in': 'Projektdaten + Umweltziele',
                'm_mop_out': 'MOP-Dokument',
                'm_mop_btn': 'MOP öffnen',
                'mod_sec6': 'Bank & Finanzierung',
                'mod_sec6_sub': 'Module für Baufinanzierungskontrolle, Kreditgrundlagen und Bankberichterstattung. Automatisierte Datenerfassung und strukturierte Entscheidungsunterstützung für Banken und Kreditgeber.',
                'badge_byggelanskontroll': 'Baufinanzierung',
                'badge_kredittgrunnlag': 'Kredit',
                'm_byggelanskontroll_t': 'BAUFINANZIERUNGSKONTROLLE – Auszahlungsprüfung & Verifizierung',
                'm_byggelanskontroll_d': 'Prüft Auszahlungsanforderungen gegen Baubudget, Terminplan und Vertragsgrundlage. Erstellt den Kontrollbericht der Bank mit Abweichungen und Genehmigungsgrundlage.',
                'm_byggelanskontroll_in': 'Auszahlungsanforderung + Budget + Terminplan',
                'm_byggelanskontroll_out': 'Kontrollbericht, Abweichungslog, Genehmigungsgrundlage',
                'm_byggelanskontroll_btn': 'Finanzierungskontrolle öffnen',
                'm_kredittgrunnlag_t': 'KREDITGRUNDLAGE – Entscheidungsunterstützung für Kreditkomitee',
                'm_kredittgrunnlag_d': 'Konsolidiert technische, regulatorische und finanzielle Daten zu einer strukturierten Kreditgrundlage für Grundstücks-, Bau- und Mietkredite.',
                'm_kredittgrunnlag_in': 'Projektdaten + Immobilieninfo + Finanzstruktur',
                'm_kredittgrunnlag_out': 'Kreditmemorendum, Risikomat, Entscheidungsgrundlage',
                'm_kredittgrunnlag_btn': 'Kreditgrundlage öffnen',
                'btn_dev': 'In Entwicklung',
                'cta_title': 'Mit einem Projekt starten. Rohdaten hochladen.',
                'cta_desc': 'Builtly verbindet Self-Service für Kunden, deterministische Prüfungen, KI-Entwürfe und professionelle '
                            'Freigabe in einem Portal.',
                'cta_btn1': 'Im Projekt-Setup starten',
                'cta_btn2': 'Zur Review-Warteschlange',
                'footer_copy': 'KI-gestützte Planung. Menschlich verifiziert. Compliance-grade.',
                'footer_meta': '© 2026 Builtly Engineering AS. Alle Rechte vorbehalten.',
                'label_input': 'Input',
                'label_output': 'Output',
                'assistant_kicker': 'Builtly Assistent',
                'assistant_title': 'Fragen über alle Baufächer hinweg stellen.',
                'assistant_subtitle': 'Eine integrierte Fragefläche auf der Startseite für GEO, Tragwerk, Rückbau, Akustik, Brandschutz, '
                                      'Umwelt, SHA, BREEAM und Immobilie. Der Assistent folgt der gewählten Sprache und orientiert sich am '
                                      'passenden nationalen Regelwerk.',
                'assistant_label_country': 'Land',
                'assistant_label_rules': 'Regelwerk',
                'assistant_label_status': 'Status',
                'assistant_disciplines_label': 'Fachbereiche',
                'assistant_question_label': 'Ihre Frage',
                'assistant_placeholder': 'Beispiel: Was muss in der Frühphase für ein sechsgeschossiges Wohnprojekt mit Untergeschoss '
                                         'geklärt werden?',
                'assistant_btn': 'Builtly fragen',
                'assistant_clear': 'Dialog leeren',
                'assistant_loading': 'Builtly analysiert Ihre Frage...',
                'assistant_examples_label': 'Beispielfragen',
                'assistant_examples': ['Was sollten wir früh für einen Wohnblock mit Tiefgarage an einer stark befahrenen Straße klären?',
                                       'Welche BREEAM-Themen sollten bei einem Büroprojekt früh priorisiert werden?',
                                       'Was sollte eine Rückbau- und SHA-Strategie neben einer Schule abdecken?'],
                'assistant_disclaimer': 'Die Antworten sind KI-gestützt und müssen vor Planungsentscheidungen oder Freigaben durch '
                                        'verantwortliche Fachpersonen geprüft werden.',
                'assistant_history_label': 'Letzte Dialoge',
                'assistant_empty_title': 'Bereit für Live-Fragen',
                'assistant_empty_body': 'Besucher können direkt auf der Startseite Fragen zu Gebäudetechnik und Immobilien stellen. Die '
                                        'Antworten folgen Fachgebiet, Sprache und nationalem Regelwerk.',
                'assistant_latest_answer': 'Letzte Antwort',
                'assistant_status_live': 'KI bereit',
                'assistant_status_setup': 'UI bereit',
                'assistant_error_prefix': 'Antwort konnte nicht erzeugt werden',
                'assistant_note_prefix': 'Hinweis',
                'assistant_scope_value': 'GEO · Tragwerk · Rückbau · Akustik · Brandschutz · Umwelt · SHA · BREEAM · Immobilie · Verkehr'}}

LANGUAGE_PROFILES = {'🇬🇧 English (UK)': {'country': 'United Kingdom',
                     'rule_set': 'Building Regulations / Approved Documents (England default)',
                     'language_name': 'British English',
                     'variation_note': 'Default to England and flag where Scotland, Wales, Northern Ireland or local authority practice '
                                       'may differ.',
                     'project_land_label': 'United Kingdom (Building Regulations / Approved Documents)',
                     'jurisdiction_short': 'England default; UK variations flagged'},
 '🇺🇸 English (US)': {'country': 'United States',
                     'rule_set': 'IBC / IRC / state and local amendments',
                     'language_name': 'American English',
                     'variation_note': 'State, county and city amendments can materially change requirements, so the answer must flag '
                                       'local code adoption where relevant.',
                     'project_land_label': 'United States (IBC / IRC / local amendments)',
                     'jurisdiction_short': 'Model codes + local adoption'},
 '🇳🇴 Norsk': {'country': 'Norge',
              'rule_set': 'TEK17 / plan- og bygningsloven',
              'language_name': 'Norsk bokmål',
              'variation_note': 'Bruk TEK17 som hovedramme og flagg kommunale krav, veiledning og prosesskrav der det er relevant.',
              'project_land_label': 'Norge (TEK17 / plan- og bygningsloven)',
              'jurisdiction_short': 'TEK17 som standard'},
 '🇸🇪 Svenska': {'country': 'Sverige',
                'rule_set': 'Boverkets regler / övergång 2025–2026',
                'language_name': 'Svenska',
                'variation_note': 'Förklara när svaret påverkas av övergången mellan äldre BBR-regler och de nya regler som trädde i kraft '
                                  '1 juli 2025.',
                'project_land_label': 'Sverige (Boverkets regler / övergång 2025–2026)',
                'jurisdiction_short': 'BBR + nya regler i övergång'},
 '🇩🇰 Dansk': {'country': 'Danmark',
              'rule_set': 'BR18 / nationale annekser',
              'language_name': 'Dansk',
              'variation_note': 'Brug BR18 som grundlag og flag konstruktionsklasse, brandklasse og nationale annekser når det er '
                                'relevant.',
              'project_land_label': 'Danmark (BR18 / nationale annekser)',
              'jurisdiction_short': 'BR18 som standard'},
 '🇫🇮 Suomi': {'country': 'Suomi',
              'rule_set': 'Construction Act / National Building Code of Finland',
              'language_name': 'Finnish',
              'variation_note': "Use Finland's Construction Act and the National Building Code as the baseline, and call out "
                                'municipality-specific permit practice when relevant.',
              'project_land_label': 'Suomi (rakentamislaki / Suomen rakentamismääräykset)',
              'jurisdiction_short': 'Construction Act + code'},
 '🇩🇪 Deutsch': {'country': 'Deutschland',
                'rule_set': 'Landesbauordnungen / MBO / MVV TB',
                'language_name': 'Deutsch',
                'variation_note': 'Use MBO and MVV TB only as a common baseline and explicitly flag that the applicable Landesbauordnung '
                                  'and local authority practice must be confirmed.',
                'project_land_label': 'Deutschland (Landesbauordnungen / MBO / MVV TB)',
                'jurisdiction_short': 'LBO + MBO baseline'}}

MODULE_EXPANSION_TEXTS = {
    "🇬🇧 English (UK)": {
        "mod_sec_title": "Specialized agents, commercial engines and scale layers in one platform",
        "mod_sec_sub": "Builtly should combine vertical specialist modules with horizontal engines for tender, quantity, yield, climate risk and partner distribution. That is how the product scales without scaling like a consultancy.",
        "mod_sec4": "Commercial & delivery intelligence",
        "mod_sec4_sub": "Horizontal modules that reduce tender risk, quantify scope and improve project yield.",
        "mod_sec5": "Climate, portfolio & partner scale",
        "mod_sec5_sub": "Portfolio screening, climate risk and white-label/API distribution for enterprise growth.",
        "m_tender_t": "TENDER CONTROL - Bid Package QA",
        "m_tender_d": "Compare tender documents, drawings and bid inputs. Generates deviation matrix, missing-item log, ambiguity log and RFI suggestions.",
        "m_tender_in": "Tender docs + drawings + IFC/PDF",
        "m_tender_out": "Deviation matrix, scope log, RFIs",
        "m_tender_btn": "Open Tender Control",
        "m_quantity_t": "QUANTITY & SCOPE - Revision Intelligence",
        "m_quantity_d": "Track quantities, areas, revision deltas and traceability between model, drawing and description.",
        "m_quantity_in": "IFC / PDF / BOQ / room data",
        "m_quantity_out": "Quantity set, area log, delta report",
        "m_quantity_btn": "Open Quantity & Scope",
        "m_yield_t": "AREA & YIELD - Development Optimizer",
        "m_yield_d": "Analyze gross/net, saleable and lettable area, core ratio, technical rooms and scenario-based yield improvements.",
        "m_yield_in": "Plan basis + area program",
        "m_yield_out": "Yield note, scenarios, value uplift",
        "m_yield_btn": "Open Yield Optimizer",
        "m_climate_t": "CLIMATE RISK - Asset & Portfolio Screening",
        "m_climate_d": "Scores flood, landslide, sea-level and heat stress risk and maps outputs to Taxonomy, SFDR and banking workflows.",
        "m_climate_in": "Address / coordinates + exposure",
        "m_climate_out": "Climate risk score, taxonomy mapping",
        "m_climate_btn": "Open Climate Risk",
        "m_partner_t": "WHITE-LABEL API - Partner Program",
        "m_partner_d": "Tenant architecture, API access, webhooks and branded report delivery so partners can run Builtly inside their own systems.",
        "m_partner_in": "Partner config + API setup",
        "m_partner_out": "Tenant blueprint, API package",
        "m_partner_btn": "Open Partner API"
    },
    "🇳🇴 Norsk": {
        "mod_sec_title": "Spesialiserte moduler, kommersielle motorer og skaleringslag i én plattform",
        "mod_sec_sub": "Builtly bør kombinere vertikale fagmoduler med horisontale motorer for anbud, mengder, yield, klimarisiko og partnerdistribusjon. Det er slik plattformen kan skalere uten å vokse som et konsulentselskap.",
        "mod_sec4": "Kommersiell & leveranseintelligens",
        "mod_sec4_sub": "Horisontale moduler som reduserer anbudsrisiko, kvantifiserer scope og forbedrer areal/yield.",
        "mod_sec5": "Klima, portefølje & partnerskala",
        "mod_sec5_sub": "Porteføljescreening, klimarisiko og white-label/API-distribusjon for enterprise-vekst.",
        "m_tender_t": "ANBUDSKONTROLL - Tilbudsgrunnlag & QA",
        "m_tender_d": "Sammenligner konkurransegrunnlag, tegninger og tilbudsinput. Genererer avviksmatrise, mangelliste, uklarhetslogg og forslag til spørsmål.",
        "m_tender_in": "Anbudsgrunnlag + tegninger + IFC/PDF",
        "m_tender_out": "Avviksmatrise, scope-logg, RFIs",
        "m_tender_btn": "Åpne Tender Control",
        "m_quantity_t": "MENGDE & SCOPE - Revisjon og sporbarhet",
        "m_quantity_d": "Fanger mengder, arealer, revisjonsendringer og sporbarhet mellom modell, tegning og beskrivelse.",
        "m_quantity_in": "IFC / PDF / BOQ / romdata",
        "m_quantity_out": "Mengdeliste, areallogg, deltarapport",
        "m_quantity_btn": "Åpne Mengde & Scope",
        "m_yield_t": "AREAL & YIELD - Utvikleroptimalisering",
        "m_yield_d": "Analyserer brutto/netto, salgbart og utleibart areal, kjerneandel, tekniske rom og scenarioer for mer verdiskaping.",
        "m_yield_in": "Plangrunnlag + arealprogram",
        "m_yield_out": "Yield-notat, scenarioer, verdiøkning",
        "m_yield_btn": "Åpne Yield Optimizer",
        "m_climate_t": "KLIMARISIKO - Eiendom & portefølje",
        "m_climate_d": "Skårer flom, skred, havnivå og varmestress og mapper output mot Taxonomy, SFDR og bankrapportering.",
        "m_climate_in": "Adresse / koordinater + eksponering",
        "m_climate_out": "Klimarisikoscore, taxonomy-mapping",
        "m_climate_btn": "Åpne Klimarisiko",
        "m_partner_t": "WHITE-LABEL API - Partnerprogram",
        "m_partner_d": "Tenant-arkitektur, API-tilgang, webhooks og brandede rapporter slik at partnere kan kjøre Builtly i egne systemer.",
        "m_partner_in": "Partneroppsett + API-konfig",
        "m_partner_out": "Tenant-blueprint, API-pakke",
        "m_partner_btn": "Åpne Partner API"
    }
}

for _lang_key, _payload in MODULE_EXPANSION_TEXTS.items():
    TEXTS.setdefault(_lang_key, {}).update(_payload)

DISCIPLINE_CATALOG = [{'code': 'geo',
  'labels': {'🇬🇧 English (UK)': 'GEO / Ground',
             '🇺🇸 English (US)': 'GEO / Ground',
             '🇳🇴 Norsk': 'GEO / grunnforhold',
             '🇸🇪 Svenska': 'GEO / mark',
             '🇩🇰 Dansk': 'GEO / jordbund',
             '🇫🇮 Suomi': 'GEO / maaperä',
             '🇩🇪 Deutsch': 'GEO / Baugrund'}},
 {'code': 'rib',
  'labels': {'🇬🇧 English (UK)': 'Structural / RIB',
             '🇺🇸 English (US)': 'Structural / RIB',
             '🇳🇴 Norsk': 'RIB / konstruksjon',
             '🇸🇪 Svenska': 'Konstruktion / RIB',
             '🇩🇰 Dansk': 'Konstruktion / RIB',
             '🇫🇮 Suomi': 'Rakenteet / RIB',
             '🇩🇪 Deutsch': 'Tragwerk / RIB'}},
 {'code': 'demolition',
  'labels': {'🇬🇧 English (UK)': 'Demolition / Reuse',
             '🇺🇸 English (US)': 'Demolition / Reuse',
             '🇳🇴 Norsk': 'Rive / ombruk',
             '🇸🇪 Svenska': 'Rivning / återbruk',
             '🇩🇰 Dansk': 'Nedrivning / genbrug',
             '🇫🇮 Suomi': 'Purku / uudelleenkäyttö',
             '🇩🇪 Deutsch': 'Rückbau / Wiederverwendung'}},
 {'code': 'acoustics',
  'labels': {'🇬🇧 English (UK)': 'Acoustics / RIAku',
             '🇺🇸 English (US)': 'Acoustics / RIAku',
             '🇳🇴 Norsk': 'RIAku / akustikk',
             '🇸🇪 Svenska': 'Akustik / RIAku',
             '🇩🇰 Dansk': 'Akustik / RIAku',
             '🇫🇮 Suomi': 'Akustiikka / RIAku',
             '🇩🇪 Deutsch': 'Akustik / RIAku'}},
 {'code': 'fire',
  'labels': {'🇬🇧 English (UK)': 'Fire / RIBr',
             '🇺🇸 English (US)': 'Fire / RIBr',
             '🇳🇴 Norsk': 'RIBr / brann',
             '🇸🇪 Svenska': 'Brand / RIBr',
             '🇩🇰 Dansk': 'Brand / RIBr',
             '🇫🇮 Suomi': 'Palo / RIBr',
             '🇩🇪 Deutsch': 'Brandschutz / RIBr'}},
 {'code': 'environment',
  'labels': {'🇬🇧 English (UK)': 'Environment',
             '🇺🇸 English (US)': 'Environment',
             '🇳🇴 Norsk': 'Miljø',
             '🇸🇪 Svenska': 'Miljö',
             '🇩🇰 Dansk': 'Miljø',
             '🇫🇮 Suomi': 'Ympäristö',
             '🇩🇪 Deutsch': 'Umwelt'}},
 {'code': 'sha',
  'labels': {'🇬🇧 English (UK)': 'SHA / H&S',
             '🇺🇸 English (US)': 'Site Safety / H&S',
             '🇳🇴 Norsk': 'SHA',
             '🇸🇪 Svenska': 'SHA',
             '🇩🇰 Dansk': 'SHA',
             '🇫🇮 Suomi': 'SHA',
             '🇩🇪 Deutsch': 'SHA'}},
 {'code': 'breeam',
  'labels': {'🇬🇧 English (UK)': 'BREEAM',
             '🇺🇸 English (US)': 'BREEAM',
             '🇳🇴 Norsk': 'BREEAM',
             '🇸🇪 Svenska': 'BREEAM',
             '🇩🇰 Dansk': 'BREEAM',
             '🇫🇮 Suomi': 'BREEAM',
             '🇩🇪 Deutsch': 'BREEAM'}},
 {'code': 'property',
  'labels': {'🇬🇧 English (UK)': 'Property / Feasibility',
             '🇺🇸 English (US)': 'Real Estate / Feasibility',
             '🇳🇴 Norsk': 'Eiendom / mulighetsstudie',
             '🇸🇪 Svenska': 'Fastighet / förstudie',
             '🇩🇰 Dansk': 'Ejendom / forstudie',
             '🇫🇮 Suomi': 'Kiinteistö / feasibility',
             '🇩🇪 Deutsch': 'Immobilie / Machbarkeit'}},
 {'code': 'traffic',
  'labels': {'🇬🇧 English (UK)': 'Traffic / Mobility',
             '🇺🇸 English (US)': 'Traffic / Mobility',
             '🇳🇴 Norsk': 'Trafikk / mobilitet',
             '🇸🇪 Svenska': 'Trafik / mobilitet',
             '🇩🇰 Dansk': 'Trafik / mobilitet',
             '🇫🇮 Suomi': 'Liikenne / liikkuvuus',
             '🇩🇪 Deutsch': 'Verkehr / Mobilität'}}]

DEFAULT_DISCIPLINES = ['geo', 'rib', 'fire', 'sha', 'breeam']
DISCIPLINE_LABELS = {item["code"]: item["labels"] for item in DISCIPLINE_CATALOG}

DISCIPLINE_CATALOG.extend([
    {
        "code": "tender",
        "labels": {
            "🇬🇧 English (UK)": "Tender control",
            "🇺🇸 English (US)": "Tender control",
            "🇳🇴 Norsk": "Anbudskontroll",
            "🇸🇪 Svenska": "Anbudskontroll",
            "🇩🇰 Dansk": "Anbudskontrol",
            "🇫🇮 Suomi": "Tarjouskontrolli",
            "🇩🇪 Deutsch": "Ausschreibungskontrolle",
        },
    },
    {
        "code": "quantity",
        "labels": {
            "🇬🇧 English (UK)": "Quantity & scope",
            "🇺🇸 English (US)": "Quantity & scope",
            "🇳🇴 Norsk": "Mengde & scope",
            "🇸🇪 Svenska": "Mängd & scope",
            "🇩🇰 Dansk": "Mængde & scope",
            "🇫🇮 Suomi": "Määrä & scope",
            "🇩🇪 Deutsch": "Mengen & Scope",
        },
    },
    {
        "code": "yield",
        "labels": {
            "🇬🇧 English (UK)": "Area & yield",
            "🇺🇸 English (US)": "Area & yield",
            "🇳🇴 Norsk": "Areal & yield",
            "🇸🇪 Svenska": "Area & yield",
            "🇩🇰 Dansk": "Areal & yield",
            "🇫🇮 Suomi": "Alue & yield",
            "🇩🇪 Deutsch": "Fläche & Yield",
        },
    },
    {
        "code": "climate",
        "labels": {
            "🇬🇧 English (UK)": "Climate risk",
            "🇺🇸 English (US)": "Climate risk",
            "🇳🇴 Norsk": "Klimarisiko",
            "🇸🇪 Svenska": "Klimatrisk",
            "🇩🇰 Dansk": "Klimarisiko",
            "🇫🇮 Suomi": "Ilmastoriski",
            "🇩🇪 Deutsch": "Klimarisiko",
        },
    },
    {
        "code": "partner_api",
        "labels": {
            "🇬🇧 English (UK)": "White-label API",
            "🇺🇸 English (US)": "White-label API",
            "🇳🇴 Norsk": "White-label API",
            "🇸🇪 Svenska": "White-label API",
            "🇩🇰 Dansk": "White-label API",
            "🇫🇮 Suomi": "White-label API",
            "🇩🇪 Deutsch": "White-label API",
        },
    },
])
DISCIPLINE_LABELS.update({item["code"]: item["labels"] for item in DISCIPLINE_CATALOG})


def get_text_bundle(lang_key: str) -> Dict:
    base = dict(TEXTS["🇬🇧 English (UK)"])
    base.update(TEXTS.get(lang_key, {}))
    return base


def get_locale_profile(lang_key: str) -> Dict:
    return LANGUAGE_PROFILES.get(lang_key, LANGUAGE_PROFILES["🇳🇴 Norsk"])


lang = get_text_bundle(st.session_state.app_lang)
locale_profile = get_locale_profile(st.session_state.app_lang)

MODULE_COPY_OVERRIDES = {
    "default": {
        "mod_sec_title": "Modules for analysis, documentation and decisions",
        "mod_sec_sub": "Choose the workflow that fits your project. Every module uses the same project data, traceability and quality-controlled delivery flow inside Builtly.",
        "mod_sec1": "Ground, sound & fire",
        "mod_sec2": "Early phase, structure & mobility",
        "mod_sec3": "Sustainability, safety & certification",
        "mod_sec3_sub": "Modules for environmental follow-up, safety planning and certification support in one workflow.",
        "mod_sec4": "Tender, quantities & area",
        "mod_sec4_sub": "Workflows that help you control tender material, track quantities and improve area efficiency before decisions are locked.",
        "mod_sec5": "Property & portfolio",
        "mod_sec5_sub": "Workflows for climate screening and technical due diligence across single assets and portfolios.",
        "m_tdd_t": "TDD - Technical Due Diligence",
        "m_tdd_d": "Turn drawings, certificates and condition data into a structured TDD draft for transactions, financing and portfolio reviews.",
        "m_tdd_in": "Drawings + certificates + condition docs",
        "m_tdd_out": "TDD draft, risk matrix, remediation overview",
        "m_tdd_btn": "Open TDD",
        "mod_sec6": "Bank & Finance",
        "mod_sec6_sub": "Modules for construction loan control, credit assessment and bank reporting. Automated data collection and structured decision support for lenders.",
        "badge_byggelanskontroll": "Construction loan",
        "badge_kredittgrunnlag": "Credit",
        "m_byggelanskontroll_t": "CONSTRUCTION LOAN CONTROL – Draw verification & control",
        "m_byggelanskontroll_d": "Verifies draw requests against construction budget, progress schedule and contract basis. Generates the bank control report with deviations and approval documentation.",
        "m_byggelanskontroll_in": "Draw request + budget + progress plan",
        "m_byggelanskontroll_out": "Control report, deviation log, approval basis",
        "m_byggelanskontroll_btn": "Open Loan Control",
        "m_kredittgrunnlag_t": "CREDIT ASSESSMENT – Decision support for credit committee",
        "m_kredittgrunnlag_d": "Consolidates technical, regulatory and financial data into a structured credit memorandum for land loans, construction loans and rental loans.",
        "m_kredittgrunnlag_in": "Project data + property info + financial structure",
        "m_kredittgrunnlag_out": "Credit memo, risk matrix, decision basis",
        "m_kredittgrunnlag_btn": "Open Credit Assessment",
        "partner_line": "Are you a consulting engineering firm or system supplier? Contact us about integration.",
        "contact_form_title": "Contact us about integration",
        "contact_form_sub": "Tell us briefly what you want to connect, automate or deliver through Builtly. We will route your request to the right team.",
        "contact_name": "Name",
        "contact_email": "Work email",
        "contact_company": "Company",
        "contact_message": "How can we help?",
        "contact_send": "Send request",
        "contact_close": "Close form",
        "contact_missing_fields": "Please complete name, work email and message before sending.",
        "contact_invalid_email": "Please enter a valid work email address.",
        "contact_success": "Thanks — your message has been sent to Builtly.",
        "contact_fallback": "The server is not set up to send email directly yet. Open the prefilled email below and send it to continue.",
        "contact_fallback_button": "Open prefilled email",
        "contact_direct_email": "Or contact us directly at {email}.",
        "contact_subject_prefix": "Builtly integration inquiry",
        "badge_geo": "Ground",
        "badge_acoustics": "Sound",
        "badge_fire": "Fire",
        "badge_feasibility": "Early phase",
        "badge_structural": "Structure",
        "badge_traffic": "Mobility",
        "badge_sha": "Safety",
        "badge_breeam": "Certification",
        "badge_mop": "Environment",
        "badge_tender": "Bid",
        "badge_quantity": "Quantities",
        "badge_yield": "Area",
        "badge_climate": "Portfolio",
        "badge_tdd": "Due diligence",
    },
    "🇳🇴 Norsk": {
        "mod_sec_title": "Moduler for analyse, dokumentasjon og beslutningsstøtte",
        "mod_sec_sub": "Velg arbeidsflaten som passer prosjektet ditt. Alle modulene bruker samme prosjektdata, sporbarhet og kvalitetssikrede leveranseflyt i Builtly.",
        "mod_sec1": "Grunnforhold, lyd & brann",
        "mod_sec2": "Tidligfase, konstruksjon & mobilitet",
        "mod_sec3": "Bærekraft, sikkerhet & sertifisering",
        "mod_sec3_sub": "Moduler for miljøoppfølging, sikkerhetsplaner og sertifiseringsstøtte samlet i én arbeidsflyt.",
        "mod_sec4": "Anbud, mengder & areal",
        "mod_sec4_sub": "Arbeidsflater som hjelper deg å kontrollere konkurransegrunnlag, følge mengder og forbedre arealeffektivitet før viktige beslutninger tas.",
        "mod_sec5": "Eiendom & portefølje",
        "mod_sec5_sub": "Arbeidsflater for klimarisiko og teknisk due diligence i eiendom, transaksjon og porteføljearbeid.",
        "m_tender_t": "ANBUD - Kontroll før innlevering",
        "m_tender_d": "Last opp konkurransegrunnlaget og få risikopunkter, mangelliste, spørsmål og tilbudsstruktur samlet i én arbeidsflate.",
        "m_tender_in": "Konkurransegrunnlag + tegninger + IFC/PDF",
        "m_tender_out": "Avviksmatrise, risikorapport, RFI-utkast",
        "m_tender_btn": "Åpne Anbudsmodul",
        "m_quantity_t": "MENGDE & SCOPE - Oversikt og sporbarhet",
        "m_quantity_d": "Komplett oversikt over mengder, arealer og endringer mellom revisjoner med sporbarhet tilbake til kildene.",
        "m_quantity_in": "IFC / tegninger / beskrivelser",
        "m_quantity_out": "Mengdeliste, areallogg, revisjonsdelta",
        "m_quantity_btn": "Åpne Mengde & Scope",
        "m_yield_t": "AREAL & YIELD - Arealoptimalisering",
        "m_yield_d": "Se hvor arealet går, hva som kan optimaliseres og hva det er verdt. Sammenlign scenarioer for mer salgbart eller utleibart areal.",
        "m_yield_in": "Planløsning + arealoppsett",
        "m_yield_out": "Yield-notat, scenarioer, forbedringsgrep",
        "m_yield_btn": "Åpne Areal & Yield",
        "m_climate_t": "KLIMARISIKO - Eiendom & portefølje",
        "m_climate_d": "Screen eiendommer og porteføljer for flom, skred, havnivå og varmestress med eksport til videre rapportering.",
        "m_climate_in": "Adresse / koordinat + eiendomsliste",
        "m_climate_out": "Klimarisikoscore, datapunkter, porteføljeuttrekk",
        "m_climate_btn": "Åpne Klimarisiko",
        "m_tdd_t": "TDD - Teknisk Due Diligence",
        "m_tdd_d": "Få oversikt over teknisk tilstand, risiko og vedlikeholdsbehov basert på tegninger, attester og tilstandsdata.",
        "m_tdd_in": "Tegninger + attester + tilstandsgrunnlag",
        "m_tdd_out": "TDD-utkast, risikomatrise, kostnadsoversikt",
        "m_tdd_btn": "Åpne TDD",
        "mod_sec6": "Bank & Finansiering",
        "mod_sec6_sub": "Moduler for byggelånskontroll, kredittgrunnlag og bankrapportering. Automatisert datainnhenting og strukturert beslutningsstøtte for banker og kredittgivere.",
        "badge_byggelanskontroll": "Byggelån",
        "badge_kredittgrunnlag": "Kreditt",
        "m_byggelanskontroll_t": "BYGGELÅNSKONTROLL – Utbetalingskontroll & verifisering",
        "m_byggelanskontroll_d": "Verifiserer trekkforespørsler mot byggebudsjett, fremdriftsplan og kontraktsgrunnlag. Genererer bankens kontrollrapport med avvik og godkjenningsgrunnlag.",
        "m_byggelanskontroll_in": "Trekkforespørsel + budsjett + fremdriftsplan",
        "m_byggelanskontroll_out": "Kontrollrapport, avvikslogg, godkjenningsgrunnlag",
        "m_byggelanskontroll_btn": "Åpne Byggelånskontroll",
        "m_kredittgrunnlag_t": "KREDITTGRUNNLAG – Beslutningsstøtte for kredittkomité",
        "m_kredittgrunnlag_d": "Sammenstiller tekniske, regulatoriske og finansielle data til et strukturert kredittgrunnlag for tomtelån, byggelån og utleielån.",
        "m_kredittgrunnlag_in": "Prosjektdata + eiendomsinfo + finansstruktur",
        "m_kredittgrunnlag_out": "Kredittmemo, risikomatrise, beslutningsgrunnlag",
        "m_kredittgrunnlag_btn": "Åpne Kredittgrunnlag",
        "partner_line": "Er du et rådgivende ingeniørfirma eller systemleverandør? Ta kontakt om integrering.",
        "contact_form_title": "Kontakt oss om integrering",
        "contact_form_sub": "Fortell kort hva du ønsker å koble på, automatisere eller levere gjennom Builtly, så sender vi henvendelsen til riktig team.",
        "contact_name": "Navn",
        "contact_email": "Jobb-e-post",
        "contact_company": "Firma",
        "contact_message": "Hva ønsker du hjelp med?",
        "contact_send": "Send henvendelse",
        "contact_close": "Lukk skjema",
        "contact_missing_fields": "Fyll ut navn, jobb-e-post og melding før du sender.",
        "contact_invalid_email": "Skriv inn en gyldig jobb-e-postadresse.",
        "contact_success": "Takk — meldingen din er sendt til Builtly.",
        "contact_fallback": "Serveren er ikke satt opp for direkte e-postsending ennå. Åpne e-posten under og send den videre derfra.",
        "contact_fallback_button": "Åpne ferdig utfylt e-post",
        "contact_direct_email": "Du kan også kontakte oss direkte på {email}.",
        "contact_subject_prefix": "Builtly integreringshenvendelse",
        "badge_geo": "Grunnlag",
        "badge_acoustics": "Lyd",
        "badge_fire": "Brann",
        "badge_feasibility": "Tidligfase",
        "badge_structural": "Konstruksjon",
        "badge_traffic": "Mobilitet",
        "badge_sha": "Sikkerhet",
        "badge_breeam": "Sertifisering",
        "badge_mop": "Miljø",
        "badge_tender": "Tilbud",
        "badge_quantity": "Mengder",
        "badge_yield": "Areal",
        "badge_climate": "Portefølje",
        "badge_tdd": "Due diligence",
    },
    "🇬🇧 English (UK)": {
        "mod_sec_title": "Modules for analysis, documentation and decision support",
        "mod_sec_sub": "Choose the workflow that fits your project. Every module uses the same project data, traceability and quality-controlled delivery flow inside Builtly.",
        "mod_sec1": "Ground, sound & fire",
        "mod_sec2": "Early phase, structure & mobility",
        "mod_sec3": "Sustainability, safety & certification",
        "mod_sec3_sub": "Modules for environmental follow-up, safety planning and certification support in one workflow.",
        "mod_sec4": "Tender, quantities & area",
        "mod_sec4_sub": "Workflows that help you control tender material, track quantities and improve area efficiency before key decisions are locked.",
        "mod_sec5": "Property & portfolio",
        "mod_sec5_sub": "Workflows for climate screening and technical due diligence across single assets and portfolios.",
        "m_tdd_t": "TDD - Technical Due Diligence",
        "m_tdd_d": "Turn drawings, certificates and condition data into a structured TDD draft for transactions, financing and portfolio reviews.",
        "m_tdd_in": "Drawings + certificates + condition docs",
        "m_tdd_out": "TDD draft, risk matrix, remediation overview",
        "m_tdd_btn": "Open TDD",
        "partner_line": "Are you a consulting engineering firm or system supplier? Contact us about integration.",
        "contact_form_title": "Contact us about integration",
        "contact_form_sub": "Tell us briefly what you want to connect, automate or deliver through Builtly. We will route your request to the right team.",
        "contact_name": "Name",
        "contact_email": "Work email",
        "contact_company": "Company",
        "contact_message": "How can we help?",
        "contact_send": "Send request",
        "contact_close": "Close form",
        "contact_missing_fields": "Please complete name, work email and message before sending.",
        "contact_invalid_email": "Please enter a valid work email address.",
        "contact_success": "Thanks — your message has been sent to Builtly.",
        "contact_fallback": "The server is not set up to send email directly yet. Open the prefilled email below and send it to continue.",
        "contact_fallback_button": "Open prefilled email",
        "contact_direct_email": "Or contact us directly at {email}.",
        "contact_subject_prefix": "Builtly integration inquiry",
        "badge_geo": "Ground",
        "badge_acoustics": "Sound",
        "badge_fire": "Fire",
        "badge_feasibility": "Early phase",
        "badge_structural": "Structure",
        "badge_traffic": "Mobility",
        "badge_sha": "Safety",
        "badge_breeam": "Certification",
        "badge_mop": "Environment",
        "badge_tender": "Bid",
        "badge_quantity": "Quantities",
        "badge_yield": "Area",
        "badge_climate": "Portfolio",
        "badge_tdd": "Due diligence",
    },
}
for key, value in MODULE_COPY_OVERRIDES.get("default", {}).items():
    lang.setdefault(key, value)
for key, value in MODULE_COPY_OVERRIDES.get(st.session_state.app_lang, {}).items():
    lang[key] = value
st.session_state.project_data["land"] = locale_profile["project_land_label"]


# -------------------------------------------------
# 4) PAGE MAP & SMART ROUTING
# -------------------------------------------------
def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists():
            return str(p)
    return f"pages/{base_name}.py"


PAGES = {
    "mulighetsstudie": find_page("Mulighetsstudie"),
    "geo": find_page("Geo"),
    "konstruksjon": find_page("Konstruksjon"),
    "brann": find_page("Brannkonsept"),
    "akustikk": find_page("Akustikk"),
    "trafikk": find_page("Trafikk"),
    "sha": find_page("SHA"),
    "breeam": find_page("BREEAM"),
    "mop": find_page("MOP"),
    "tender_control": find_page("TenderControl"),
    "quantity_scope": find_page("QuantityScope"),
    "yield_optimizer": find_page("YieldOptimizer"),
    "climate_risk": find_page("ClimateRisk"),
    "tdd": find_page("TDD"),
    "partner_api": find_page("PartnerAPI"),
    "byggelanskontroll": find_page("Byggelanskontroll"),
    "kredittgrunnlag": find_page("Kredittgrunnlag"),
    "project": find_page("Project"),
    "review": find_page("Review"),
}


# -------------------------------------------------
# 5) HELPERS
# -------------------------------------------------
def page_exists(page_path: str) -> bool:
    return Path(page_path).exists()


def page_route(page_key: str) -> Optional[str]:
    page_path = PAGES.get(page_key)
    if not page_path or not page_exists(page_path):
        return None
    return Path(page_path).stem


def href_or_none(page_key: str) -> Optional[str]:
    route = page_route(page_key)
    if route is None:
        return None
    # If gate is enabled and user is not yet authenticated, intercept the click
    if access_gate_enabled() and not st.session_state.get("site_access_granted"):
        return f"?gate={page_key}"
    return route


def hero_action(page_key: str, label: str, kind: str = "primary") -> str:
    href = href_or_none(page_key)
    if href:
        return f'<a href="{href}" target="_self" class="hero-action {kind}">{label}</a>'
    return f'<span class="hero-action {kind} disabled">{label}</span>'


def render_html(html_string: str):
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""


def discipline_label(code: str, lang_key: str) -> str:
    return DISCIPLINE_LABELS.get(code, {}).get(lang_key, DISCIPLINE_LABELS.get(code, {}).get("🇬🇧 English (UK)", code))


def discipline_labels(codes: List[str], lang_key: str) -> List[str]:
    return [discipline_label(code, lang_key) for code in codes]


def module_card(
    page_key: str,
    icon: str,
    badge: str,
    badge_class: str,
    title: str,
    description: str,
    input_text: str,
    output_text: str,
    cta_label: str,
) -> str:
    href = href_or_none(page_key)
    action_html = (
        f'<a href="{href}" target="_self" class="module-cta">{cta_label}</a>'
        if href
        else f'<span class="module-cta disabled">{lang["btn_dev"]}</span>'
    )

    return f"""
        <div class="module-card">
            <div class="module-header">
                <div class="module-icon">{icon}</div>
                <div class="module-badge {badge_class}">{badge}</div>
            </div>
            <div class="module-title">{title}</div>
            <div class="module-desc">{description}</div>
            <div class="module-spacer"></div>
            <div class="module-meta">
                <strong>{lang["label_input"]}:</strong> {input_text}<br/>
                <strong>{lang["label_output"]}:</strong> {output_text}
            </div>
            <div class="module-cta-wrap">
                {action_html}
            </div>
        </div>
    """


def gemini_api_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def gemini_ready() -> bool:
    return bool(gemini_api_key())


LANGUAGE_REFERENCE_SLUGS = {
    "🇬🇧 English (UK)": "uk",
    "🇺🇸 English (US)": "us",
    "🇳🇴 Norsk": "no",
    "🇸🇪 Svenska": "se",
    "🇩🇰 Dansk": "dk",
    "🇫🇮 Suomi": "fi",
    "🇩🇪 Deutsch": "de",
}

COUNTRY_GUIDANCE_PACKS = {
    "🇬🇧 English (UK)": [
        "Use the Building Regulations and the Approved Documents as the default baseline for England.",
        "Explicitly flag when Scotland, Wales or Northern Ireland may differ.",
        "Call out Building Control interpretation, planning conditions and project-specific fire strategy where relevant.",
    ],
    "🇺🇸 English (US)": [
        "Use IBC or IRC as the model-code baseline and always flag state, county and city adoption differences.",
        "Separate permit-path questions, zoning questions and technical-code questions when they are mixed together.",
        "When the answer depends on AHJ interpretation, say so clearly.",
    ],
    "🇳🇴 Norsk": [
        "Bruk plan- og bygningsloven og TEK17 som hovedrammeverk.",
        "Flagg når svaret også avhenger av regulering, kommuneplan, kommunal praksis eller byggesaksprosess.",
        "Skill tydelig mellom forskriftskrav, vanlig prosjekteringspraksis og forhold som må avklares i prosjektet.",
    ],
    "🇸🇪 Svenska": [
        "Utgå från Boverkets regler och förklara när övergången mellan äldre BBR/EKS och de nya reglerna från 1 juli 2025 påverkar svaret.",
        "Påminn om att ett projekt normalt måste hålla sig till ett av regelverken under övergången.",
        "Flagga kommunal tillämpning, detaljplan och bygglovsprocess när det är relevant.",
    ],
    "🇩🇰 Dansk": [
        "Brug BR18 som udgangspunkt og fremhæv konstruktionsklasse, brandklasse og nationale annekser når de styrer svaret.",
        "Skeln mellem krav i bygningsreglementet, almindelig rådgiverpraksis og emner der skal afklares med myndigheden.",
        "Flag kommunal sagsbehandling og lokale forhold hvor det kan ændre vurderingen.",
    ],
    "🇫🇮 Suomi": [
        "Use Finland's Construction Act and the National Building Code as the baseline.",
        "Call out municipality-specific permit practice when it can materially affect the answer.",
        "Separate mandatory compliance points from recommended early-phase risk reduction.",
    ],
    "🇩🇪 Deutsch": [
        "Nutze Landesbauordnung, MBO und MVV TB nur als Ausgangspunkt und sage ausdrücklich, dass die zuständige Landesbauordnung und örtliche Praxis bestätigt werden müssen.",
        "Trenne zwingende Anforderungen, übliche Fachpraxis und projektspezifische Annahmen klar voneinander.",
        "Weise auf Genehmigungsbehörde, Brandschutzkonzept und Nachweisführung hin, wenn diese den Inhalt steuern.",
    ],
}

DISCIPLINE_GUIDANCE_PACKS = {
    "geo": [
        "Focus on ground conditions, contamination, groundwater, excavation support, reuse or disposal routes, and impacts on neighbouring structures.",
        "When useful, distinguish investigation stage, concept stage, permit stage and construction stage.",
    ],
    "rib": [
        "Focus on load paths, spans, robustness, foundation strategy, temporary conditions and interfaces with architecture and geotechnics.",
        "State clearly when conceptual guidance is not enough and project-specific calculations or code checks are needed.",
    ],
    "demolition": [
        "Cover demolition sequencing, hazardous materials, waste streams, reuse potential, temporary stability and third-party impacts.",
        "Flag permit, notification or environmental follow-up items when they are likely to matter.",
    ],
    "acoustics": [
        "Address external noise, internal sound insulation, facade requirements, glazing, ventilation trade-offs and vibration where relevant.",
        "Separate early-phase screening from final façade or room-acoustics documentation.",
    ],
    "fire": [
        "Address use class, risk class, fire strategy, compartmentation, escape, fire resistance, smoke control and fire service access where relevant.",
        "Make it explicit when the final answer depends on a coordinated fire concept rather than a single clause.",
    ],
    "environment": [
        "Cover contamination, waste, mass handling, emissions, circularity, material choices, biodiversity and environmental follow-up where relevant.",
        "Highlight where the project needs documented assumptions, measurements or material declarations.",
    ],
    "sha": [
        "Address construction-phase risk, site logistics, interfaces between trades, high-risk work and responsibilities in the SHA or H&S setup.",
        "Differentiate client duties, designer duties and contractor duties when the jurisdiction makes that distinction.",
    ],
    "breeam": [
        "Treat the answer as early advisory guidance on certification strategy, credits, evidence planning and design consequences.",
        "Flag where the scheme version, assessor input or evidence requirements can materially change the recommendation.",
    ],
    "property": [
        "Focus on feasibility, permitting exposure, development risk, land-use constraints, phasing, value drivers and decision gates.",
        "Separate commercial assumptions from technical constraints when both are present.",
    ],
    "traffic": [
        "Cover access, servicing, traffic generation, parking, active mobility, road safety and local mobility requirements where relevant.",
        "Flag when transport modelling, junction analysis or municipality-specific parking policy is needed.",
    ],
}

ASSISTANT_CLOSE_LABELS = {
    "🇬🇧 English (UK)": "Close assistant",
    "🇺🇸 English (US)": "Close assistant",
    "🇳🇴 Norsk": "Lukk spørrevindu",
    "🇸🇪 Svenska": "Stäng frågefönstret",
    "🇩🇰 Dansk": "Luk spørgevindu",
    "🇫🇮 Suomi": "Sulje kysymysikkuna",
    "🇩🇪 Deutsch": "Fragefenster schließen",
}


def assistant_close_label(lang_key: str) -> str:
    return ASSISTANT_CLOSE_LABELS.get(lang_key, ASSISTANT_CLOSE_LABELS["🇬🇧 English (UK)"])


def get_query_params_dict() -> Dict[str, str]:
    if hasattr(st, "query_params"):
        try:
            return dict(st.query_params.to_dict())
        except Exception:
            try:
                return dict(st.query_params)
            except Exception:
                return {}

    if hasattr(st, "experimental_get_query_params"):
        raw = st.experimental_get_query_params()
        cleaned = {}
        for key, values in raw.items():
            if isinstance(values, list):
                cleaned[key] = values[-1] if values else ""
            else:
                cleaned[key] = str(values)
        return cleaned

    return {}


def set_query_params_dict(params: Dict[str, str]) -> None:
    if hasattr(st, "query_params"):
        try:
            st.query_params.clear()
            if params:
                st.query_params.from_dict(params)
            return
        except Exception:
            pass

    if hasattr(st, "experimental_set_query_params"):
        st.experimental_set_query_params(**params)


def assistant_query_requested() -> bool:
    value = str(get_query_params_dict().get("assistant", "")).strip().lower()
    return value in {"1", "true", "open", "yes"}


def clear_assistant_query_param() -> None:
    params = get_query_params_dict()
    if "assistant" in params:
        params.pop("assistant", None)
        set_query_params_dict(params)


def open_assistant() -> None:
    st.session_state.assistant_dialog_open = True


def close_assistant() -> None:
    st.session_state.assistant_dialog_open = False
    clear_assistant_query_param()


def bump_assistant_input_nonce() -> None:
    st.session_state.assistant_input_nonce = int(st.session_state.get("assistant_input_nonce", 0)) + 1


def reset_assistant_conversation() -> None:
    st.session_state.assistant_history = []
    bump_assistant_input_nonce()


LANGUAGE_QUERY_SLUGS = {
    "🇬🇧 English (UK)": "en-gb",
    "🇺🇸 English (US)": "en-us",
    "🇳🇴 Norsk": "no",
    "🇸🇪 Svenska": "sv",
    "🇩🇰 Dansk": "da",
    "🇫🇮 Suomi": "fi",
    "🇩🇪 Deutsch": "de",
}
QUERY_LANGUAGE_SLUGS = {value: key for key, value in LANGUAGE_QUERY_SLUGS.items()}


def language_slug(lang_key: str) -> str:
    return LANGUAGE_QUERY_SLUGS.get(lang_key, LANGUAGE_QUERY_SLUGS["🇳🇴 Norsk"])


def language_from_query_param() -> Optional[str]:
    raw = str(get_query_params_dict().get("lang", "")).strip().lower()
    return QUERY_LANGUAGE_SLUGS.get(raw)


def apply_language_from_query() -> None:
    requested_language = language_from_query_param()
    if requested_language and requested_language != st.session_state.get("app_lang"):
        st.session_state.app_lang = requested_language
        st.session_state.project_data["land"] = get_locale_profile(requested_language)["project_land_label"]


def assistant_href(lang_key: str) -> str:
    return "?" + urlparse.urlencode({"assistant": "open", "lang": language_slug(lang_key)})


def contact_query_requested() -> bool:
    value = str(get_query_params_dict().get("contact", "")).strip().lower()
    return value in {"1", "true", "open", "yes"}


def clear_contact_query_param() -> None:
    params = get_query_params_dict()
    if "contact" in params:
        params.pop("contact", None)
        set_query_params_dict(params)


def contact_href(lang_key: str) -> str:
    params = {"contact": "open", "lang": language_slug(lang_key)}
    if assistant_query_requested() or st.session_state.get("assistant_dialog_open"):
        params["assistant"] = "open"
    return "?" + urlparse.urlencode(params) + "#contact-form-anchor"


def contact_close_href(lang_key: str) -> str:
    params = {"lang": language_slug(lang_key)}
    if assistant_query_requested() or st.session_state.get("assistant_dialog_open"):
        params["assistant"] = "open"
    return "?" + urlparse.urlencode(params)


def sync_language_query_param(lang_key: str, keep_assistant: bool = False) -> None:
    params = get_query_params_dict()
    params["lang"] = language_slug(lang_key)
    if keep_assistant or st.session_state.get("assistant_dialog_open"):
        params["assistant"] = "open"
    else:
        params.pop("assistant", None)
    if contact_query_requested():
        params["contact"] = "open"
    else:
        params.pop("contact", None)
    set_query_params_dict(params)


def _safe_secret_get(name: str) -> Optional[str]:
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _env_or_secret(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    return _safe_secret_get(name)


def _truthy_env(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def contact_recipient() -> str:
    return _env_or_secret("BUILTLY_CONTACT_TO") or "kontakt@builtly.ai"


def contact_mailto_url(*, name: str, email: str, company: str, message: str, lang_bundle: Dict) -> str:
    subject = f"{lang_bundle['contact_subject_prefix']} — {company or name}".strip()
    body_lines = [
        f"Name: {name}",
        f"Company: {company}",
        f"Email: {email}",
        "",
        "Message:",
        message.strip(),
        "",
        "Source: Builtly front page integration form",
    ]
    query = urlparse.urlencode({
        "subject": subject,
        "body": "\n".join(body_lines).strip(),
    })
    recipient = contact_recipient()
    return f"mailto:{recipient}?{query}"


def send_contact_email(*, name: str, email: str, company: str, message: str, lang_bundle: Dict) -> tuple[bool, Optional[str]]:
    host = _env_or_secret("BUILTLY_SMTP_HOST") or _env_or_secret("SMTP_HOST")
    username = _env_or_secret("BUILTLY_SMTP_USER") or _env_or_secret("SMTP_USER")
    password = _env_or_secret("BUILTLY_SMTP_PASSWORD") or _env_or_secret("SMTP_PASSWORD")
    from_address = _env_or_secret("BUILTLY_CONTACT_FROM") or _env_or_secret("SMTP_FROM") or username or contact_recipient()
    port_raw = _env_or_secret("BUILTLY_SMTP_PORT") or _env_or_secret("SMTP_PORT") or "587"
    use_ssl = _truthy_env(_env_or_secret("BUILTLY_SMTP_USE_SSL") or _env_or_secret("SMTP_USE_SSL"), default=False)
    use_tls = _truthy_env(_env_or_secret("BUILTLY_SMTP_USE_TLS") or _env_or_secret("SMTP_USE_TLS"), default=not use_ssl)

    try:
        port = int(str(port_raw).strip())
    except Exception:
        port = 587

    mailto_url = contact_mailto_url(name=name, email=email, company=company, message=message, lang_bundle=lang_bundle)

    if not host:
        return False, mailto_url

    msg = EmailMessage()
    msg["To"] = contact_recipient()
    msg["From"] = from_address
    msg["Reply-To"] = email
    msg["Subject"] = f"{lang_bundle['contact_subject_prefix']} — {company or name}".strip()
    msg.set_content(
        "\n".join(
            [
                f"Name: {name}",
                f"Company: {company}",
                f"Email: {email}",
                "",
                "Message:",
                message.strip(),
                "",
                "Source: Builtly front page integration form",
            ]
        ).strip()
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context()) as server:
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=25) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
        return True, None
    except Exception:
        return False, mailto_url


def configured_access_codes() -> List[str]:
    values: List[str] = []
    for env_name in ("BUILTLY_ACCESS_CODES", "BUILTLY_ACCESS_CODE", "BUILTLY_ENTRY_CODE"):
        raw_value = _env_or_secret(env_name)
        if not raw_value:
            continue
        parts = [part.strip() for part in str(raw_value).split(",") if part.strip()]
        for part in parts:
            if part not in values:
                values.append(part)
    return values


def configured_access_hashes() -> List[str]:
    values: List[str] = []
    for env_name in ("BUILTLY_ACCESS_CODE_SHA256S", "BUILTLY_ACCESS_CODE_SHA256"):
        raw_value = _env_or_secret(env_name)
        if not raw_value:
            continue
        parts = [part.strip().lower() for part in str(raw_value).split(",") if part.strip()]
        for part in parts:
            if part not in values:
                values.append(part)
    return values


def access_gate_configured() -> bool:
    return bool(configured_access_codes() or configured_access_hashes())


def access_gate_enabled() -> bool:
    explicit_flag = _env_or_secret("BUILTLY_REQUIRE_ACCESS_CODE")
    if explicit_flag is not None:
        return explicit_flag.strip().lower() not in {"0", "false", "no", "off"}
    return access_gate_configured()


def bump_site_access_nonce() -> None:
    st.session_state.site_access_input_nonce = int(st.session_state.get("site_access_input_nonce", 0)) + 1


def verify_site_access_code(candidate: str) -> bool:
    value = (candidate or "").strip()
    if not value:
        return False

    for configured in configured_access_codes():
        if hmac.compare_digest(value, configured):
            return True

    candidate_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    for configured_hash in configured_access_hashes():
        if hmac.compare_digest(candidate_hash, configured_hash):
            return True

    return False


def _generate_session_token(code: str) -> str:
    """Genererer en kort token fra koden som lagres i URL for persistent innlogging."""
    raw = hashlib.sha256(f"builtly-session-{code.strip()}".encode("utf-8")).hexdigest()
    return raw[:16]


def _verify_session_token(token: str) -> bool:
    """Sjekker om en session-token matcher en av de konfigurerte kodene."""
    if not token or len(token) < 8:
        return False
    for code in configured_access_codes():
        if hmac.compare_digest(_generate_session_token(code), token):
            return True
    for code_hash in configured_access_hashes():
        pass
    return False


def _restore_session_from_url() -> bool:
    """Sjekker om URL-en inneholder en gyldig session-token og gjenoppretter tilgangen."""
    try:
        token = st.query_params.get("s", "")
    except Exception:
        return False
    if token and _verify_session_token(token):
        st.session_state.site_access_granted = True
        return True
    return False


def render_site_access_gate(lang_key: str) -> None:
    copy = get_access_copy(lang_key)
    lang_bundle = get_text_bundle(lang_key)
    locale_profile = get_locale_profile(lang_key)

    outer_left, outer_center, outer_right = st.columns([1.0, 1.3, 1.0], gap="large")
    with outer_center:
        render_html(
            f"""
            <div class="access-gate-head">
                <div class="assistant-kicker">{copy['eyebrow']}</div>
                <div class="access-gate-title">{copy['title']}</div>
                <div class="access-gate-subtitle">{copy['subtitle']}</div>
                <div class="context-chips compact">
                    <div class="context-chip"><span>{lang_bundle['assistant_label_country']}:</span> {locale_profile['country']}</div>
                    <div class="context-chip"><span>{lang_bundle['assistant_label_rules']}:</span> {locale_profile['jurisdiction_short']}</div>
                </div>
            </div>
            """
        )

        if not access_gate_configured():
            st.warning(copy["admin_missing"])
            st.caption(copy["admin_help"])
            return

        input_key = f"site_access_code_{st.session_state.get('site_access_input_nonce', 0)}"
        with st.form("builtly_site_access_gate"):
            access_code = st.text_input(
                copy["label"],
                key=input_key,
                type="password",
                placeholder=copy["placeholder"],
            )
            submitted = st.form_submit_button(copy["button"], use_container_width=True)

        if submitted:
            if verify_site_access_code(access_code):
                st.session_state.site_access_granted = True
                st.session_state.site_access_error = ""
                bump_site_access_nonce()
                st.query_params["s"] = _generate_session_token(access_code)
                st.rerun()
            else:
                st.session_state.site_access_error = copy["error_invalid"]
                bump_site_access_nonce()
                st.rerun()

        if st.session_state.get("site_access_error"):
            st.error(st.session_state.site_access_error)

        st.caption(copy["info"])


def ensure_frontpage_access(lang_key: str) -> None:
    if not access_gate_enabled():
        return
    if st.session_state.get("site_access_granted"):
        return
    if _restore_session_from_url():
        return
    render_site_access_gate(lang_key)
    st.stop()


@st.dialog("🔐 Tilgang kreves", width="small")
def _module_gate_dialog(lang_key: str, dest_key: str) -> None:
    """Shown when an unauthenticated user clicks a module card."""
    copy = get_access_copy(lang_key)
    lang_bundle = get_text_bundle(lang_key)

    st.markdown(
        f"<p style='color:var(--soft,#c8d3df);font-size:0.95rem;margin-bottom:1rem;'>{copy['subtitle']}</p>",
        unsafe_allow_html=True,
    )

    if not access_gate_configured():
        st.warning(copy["admin_missing"])
        return

    nonce = st.session_state.get("site_access_input_nonce", 0)
    with st.form("module_gate_form"):
        code = st.text_input(
            copy["label"],
            key=f"module_gate_code_{nonce}",
            type="password",
            placeholder=copy["placeholder"],
        )
        submitted = st.form_submit_button(copy["button"], use_container_width=True)

    if submitted:
        if verify_site_access_code(code):
            st.session_state.site_access_granted = True
            st.session_state.site_access_error = ""
            token = _generate_session_token(code)
            params = get_query_params_dict()
            params.pop("gate", None)
            params["s"] = token
            set_query_params_dict(params)
            dest_route = page_route(dest_key)
            if dest_route:
                st.switch_page(PAGES.get(dest_key, dest_route))
            else:
                st.rerun()
        else:
            st.error(copy["error_invalid"])
            bump_site_access_nonce()
            st.rerun()


# -------------------------------------------------
# 5b) SUBSCRIPTION TIERS & USER AUTH
# -------------------------------------------------

SUBSCRIPTION_PLANS = {
    "modul": {
        "name": "Modul",
        "price_label": "5 000 kr/modul/mnd",
        "price_detail": "+ 15 000–40 000 kr per rapport",
        "features": [
            "Tilgang til enkeltmoduler",
            "AI-genererte rapporter (Nivå 1 — Auto)",
            "30 dagers rapportlagring",
            "E-poststøtte",
        ],
        "badge": "STARTER",
    },
    "team": {
        "name": "Team",
        "price_label": "Fra 12 000 kr/mnd",
        "price_detail": "Volumrabatter tilgjengelig",
        "features": [
            "Alle moduler inkludert",
            "Nivå 1 & 2 rapporter (Auto + Reviewed)",
            "30 dagers rapportlagring",
            "Flerbrukertilgang",
            "Prioritert støtte",
        ],
        "badge": "POPULÆR",
    },
    "enterprise": {
        "name": "Enterprise",
        "price_label": "50 000–200 000 kr/mnd",
        "price_detail": "SSO, SLA, dedikert kontakt",
        "features": [
            "Alle moduler + Nivå 3 (Attestert)",
            "Ubegrenset rapportlagring",
            "Portefølje-API for banker",
            "White-label muligheter",
            "Dedikert rådgiver",
            "SSO / SAML",
        ],
        "badge": "ENTERPRISE",
    },
}

REPORT_RETENTION_DAYS = 30  # Reports auto-deleted after 30 days
REVISION_NOTICE = (
    "Eventuelle revideringer av rapport som følge av endring av forutsetninger "
    "fra kundens ståsted må avtales direkte med rådgiver tilknyttet Builtly Engineering AS."
)

AVAILABLE_COUNTRIES = [
    ("NO", "Norge (TEK17 / NS-standarder)"),
    ("SE", "Sverige (BBR / Boverket)"),
    ("DK", "Danmark (BR18 / SBi)"),
    ("FI", "Finland (Ympäristöministeriö)"),
    ("DE", "Deutschland (DIN / EnEV)"),
    ("GB", "United Kingdom (Building Regs)"),
    ("NL", "Nederland (Bouwbesluit)"),
]

CONTRACT_BINDING_MONTHS = 12

GDPR_CONSENT_TEXT = (
    "Jeg bekrefter at jeg har lest og aksepterer Builtly Engineering AS sine "
    "[vilkår for bruk](https://builtly.ai/terms) og "
    "[personvernerklæring](https://builtly.ai/privacy). "
    "Builtly behandler personopplysninger i henhold til GDPR / personopplysningsloven. "
    "Data lagres innenfor EØS og slettes ved oppsigelse av abonnement. "
    "Du kan når som helst be om innsyn, retting eller sletting av dine data ved å kontakte post@builtly.ai."
)

CONTRACT_TERMS_TEXT = (
    "Abonnementet har {months} måneders bindingstid fra aktivering. "
    "Etter bindingstiden fornyes abonnementet månedlig med 1 måneds oppsigelsestid. "
    "Priser gjelder per land — tilgang til flere land faktureres separat per land."
)

# Payment provider: Stripe recommended for card payments
# Env vars needed: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET
PAYMENT_PROVIDER = "stripe"  # "stripe" or "vipps"


def save_user_report(
    project_name: str,
    report_name: str,
    module: str,
    download_url: str = "",
    file_path: str = "",
) -> None:
    """
    Call this from ANY module when a report is generated.
    Adds the report to the user's dashboard with automatic 30-day expiry.

    Usage in modules (e.g. Mulighetsstudie, GEO, RIB, TDD, etc.):
        from frontpage import save_user_report
        save_user_report(
            project_name="Linås Ski",
            report_name="Mulighetsstudie — Alternativ B",
            module="Mulighetsstudie",
            file_path="/path/to/generated_report.pdf",
        )

    Parameters:
        project_name: The project this report belongs to (from st.session_state.project_data["p_name"])
        report_name:  Display name for the report
        module:       Module code/name (e.g. "Mulighetsstudie", "GEO", "RIB", "TDD", "Klimarisiko", etc.)
        download_url: URL to download the report (if hosted)
        file_path:    Local file path to the generated PDF/file
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    expires = now + timedelta(days=REPORT_RETENTION_DAYS)

    report_entry = {
        "project": project_name or st.session_state.get("project_data", {}).get("p_name", "Uten prosjekt"),
        "name": report_name,
        "module": module,
        "created": now.strftime("%Y-%m-%d %H:%M"),
        "expires": expires.strftime("%Y-%m-%d"),
        "download_url": download_url,
        "file_path": file_path,
    }

    if "user_reports" not in st.session_state:
        st.session_state.user_reports = []
    st.session_state.user_reports.append(report_entry)

    # TODO: In production, also persist to database (Supabase, etc.)
    # and set up a cron/scheduled task to delete expired reports.


def purge_expired_reports() -> None:
    """Remove reports older than REPORT_RETENTION_DAYS. Call on login or page load."""
    from datetime import datetime
    if "user_reports" not in st.session_state:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    st.session_state.user_reports = [
        r for r in st.session_state.user_reports
        if r.get("expires", "9999-99-99") >= today
    ]


def _is_user_logged_in() -> bool:
    return st.session_state.get("user_authenticated", False) and bool(st.session_state.get("user_email"))


def _user_has_plan() -> bool:
    return _is_user_logged_in() and bool(st.session_state.get("user_plan"))


def _get_auth_page() -> str:
    """Check query params for auth page routing."""
    try:
        return st.query_params.get("auth", "").strip()
    except Exception:
        return ""


def render_login_page(lang_key: str) -> None:
    """Render login form. Currently mock — connect to Supabase/Auth0 for production."""
    copy = get_access_copy(lang_key)

    outer_left, outer_center, outer_right = st.columns([1.0, 1.3, 1.0], gap="large")
    with outer_center:
        render_html("""
            <div class="access-gate-head">
                <div class="assistant-kicker">LOGG INN</div>
                <div class="access-gate-title">Velkommen tilbake</div>
                <div class="access-gate-subtitle">Logg inn for å se dine rapporter og administrere abonnement.</div>
            </div>
        """)

        with st.form("login_form"):
            email = st.text_input("E-post", placeholder="din@epost.no")
            password = st.text_input("Passord", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Logg inn", use_container_width=True)

        if submitted:
            if email.strip() and password.strip():
                if _HAS_AUTH:
                    ok, msg = builtly_auth.login(email.strip(), password.strip())
                    if ok:
                        try:
                            params = get_query_params_dict()
                            params.pop("auth", None)
                            set_query_params_dict(params)
                        except Exception:
                            pass
                        st.rerun()
                    else:
                        st.error(msg)
                        if "ikke bekreftet" in msg.lower():
                            if st.button("Send bekreftelseslenke på nytt"):
                                rok, rmsg = builtly_auth.resend_verification(email.strip())
                                if rok:
                                    st.success(rmsg)
                                else:
                                    st.error(rmsg)
                else:
                    st.error("Auth-modul (builtly_auth) er ikke installert. Kontakt administrator.")
            else:
                st.error("Vennligst fyll ut e-post og passord.")

        st.markdown("---")

        col_reg, col_demo = st.columns(2)
        with col_reg:
            render_html('<a href="?auth=register" target="_self" class="module-cta" style="text-align:center;display:block;">Opprett konto</a>')
        with col_demo:
            render_html('<a href="?auth=demo" target="_self" class="module-cta" style="text-align:center;display:block;">Demo-tilgang</a>')


def render_register_page(lang_key: str) -> None:
    """Render registration form with company, country, and GDPR consent."""
    outer_left, outer_center, outer_right = st.columns([1.0, 1.5, 1.0], gap="large")
    with outer_center:
        render_html("""
            <div class="access-gate-head">
                <div class="assistant-kicker">OPPRETT KONTO</div>
                <div class="access-gate-title">Kom i gang med Builtly</div>
                <div class="access-gate-subtitle">Opprett bedriftskonto for å få tilgang til AI-drevne prosjekteringsverktøy.</div>
            </div>
        """)

        with st.form("register_form"):
            st.markdown("##### Kontaktperson")
            r_col1, r_col2 = st.columns(2)
            with r_col1:
                reg_name = st.text_input("Fullt navn *", placeholder="Ola Nordmann")
            with r_col2:
                reg_phone = st.text_input("Telefon", placeholder="+47 900 00 000")

            reg_email = st.text_input("E-post *", placeholder="din@bedrift.no")

            st.markdown("##### Bedriftsinformasjon")
            b_col1, b_col2 = st.columns(2)
            with b_col1:
                reg_company = st.text_input("Selskapsnavn *", placeholder="Selskap AS")
            with b_col2:
                reg_org_nr = st.text_input("Org.nr.", placeholder="999 999 999")

            reg_countries = st.multiselect(
                "Land for prosjektering *",
                options=[c[1] for c in AVAILABLE_COUNTRIES],
                default=["Norge (TEK17 / NS-standarder)"],
                help="Priser gjelder per land. Velg alle land du ønsker tilgang til.",
            )

            n_countries = max(len(reg_countries), 1)
            if n_countries > 1:
                render_html(f"""
                    <div style="color:#22d3ee;font-size:0.85rem;margin:-0.5rem 0 0.5rem 0;">
                        ℹ️ {n_countries} land valgt — abonnementspris gjelder per land.
                    </div>
                """)

            st.markdown("##### Passord")
            p_col1, p_col2 = st.columns(2)
            with p_col1:
                reg_password = st.text_input("Passord *", type="password", placeholder="Minimum 8 tegn")
            with p_col2:
                reg_password2 = st.text_input("Bekreft passord *", type="password", placeholder="Gjenta passord")

            st.markdown("---")

            # GDPR consent
            reg_gdpr = st.checkbox(GDPR_CONSENT_TEXT, value=False)

            # Contract terms
            reg_terms = st.checkbox(
                CONTRACT_TERMS_TEXT.format(months=CONTRACT_BINDING_MONTHS)
                + " Jeg aksepterer kontraktsvilkårene.",
                value=False,
            )

            reg_submitted = st.form_submit_button("Opprett konto og velg abonnement", use_container_width=True)

        if reg_submitted:
            errors = []
            if not reg_name.strip():
                errors.append("Fullt navn er påkrevd.")
            if not reg_email.strip() or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", reg_email.strip()):
                errors.append("Gyldig e-postadresse er påkrevd.")
            if not reg_company.strip():
                errors.append("Selskapsnavn er påkrevd.")
            if not reg_countries:
                errors.append("Velg minst ett land.")
            if len(reg_password) < 8:
                errors.append("Passord må være minst 8 tegn.")
            if reg_password != reg_password2:
                errors.append("Passordene stemmer ikke overens.")
            if not reg_gdpr:
                errors.append("Du må akseptere personvernerklæringen.")
            if not reg_terms:
                errors.append("Du må akseptere kontraktsvilkårene.")

            if errors:
                for e in errors:
                    st.error(e)
            else:
                # Map display names back to country codes
                country_codes = []
                for sel in reg_countries:
                    for code, label in AVAILABLE_COUNTRIES:
                        if label == sel:
                            country_codes.append(code)
                            break

                if _HAS_AUTH:
                    ok, msg = builtly_auth.register(
                        email=reg_email.strip(),
                        password=reg_password,
                        name=reg_name.strip(),
                        company=reg_company.strip(),
                        org_nr=reg_org_nr.strip(),
                        phone=reg_phone.strip(),
                        countries=country_codes,
                    )
                    if ok:
                        st.success(msg)
                        st.info("Etter at du har bekreftet e-posten, kan du logge inn og velge abonnement.")
                    else:
                        st.error(msg)
                else:
                    st.error("Auth-modul (builtly_auth) er ikke installert. Kontakt administrator.")

        st.markdown("---")
        render_html('<div style="text-align:center;"><a href="?auth=login" target="_self" style="color:var(--cyan,#38bdf8);">Har du allerede konto? Logg inn</a></div>')

    # Plans preview below registration form
    st.markdown("<div style='margin-top:3rem;'></div>", unsafe_allow_html=True)
    render_html("""
        <div style="text-align:center;margin-bottom:1.5rem;">
            <div style="color:#22d3ee;font-weight:700;font-size:0.75rem;letter-spacing:0.08em;margin-bottom:0.5rem;">ABONNEMENTER</div>
            <div style="color:var(--bright,#f1f5f9);font-size:1.3rem;font-weight:700;">Velg plan etter registrering</div>
            <div style="color:var(--soft,#c8d3df);font-size:0.9rem;">Alle priser gjelder per land. 12 måneders bindingstid.</div>
        </div>
    """)
    plan_cols = st.columns(3, gap="medium")
    for idx, (plan_key, plan) in enumerate(SUBSCRIPTION_PLANS.items()):
        with plan_cols[idx]:
            is_pop = plan_key == "team"
            bc = "#38bdf8" if is_pop else "rgba(56,189,248,0.10)"
            feats = "".join(f'<div style="padding:0.2rem 0;color:var(--soft,#c8d3df);font-size:0.85rem;">✓ {f}</div>' for f in plan["features"])
            render_html(f"""
                <div style="border:1px solid {bc};border-radius:1rem;padding:1.5rem;
                            background:var(--card-bg,rgba(6,17,26,0.55));min-height:320px;">
                    <div style="color:#22d3ee;font-weight:700;font-size:0.7rem;letter-spacing:0.08em;margin-bottom:0.3rem;">{plan['badge']}</div>
                    <div style="color:var(--bright,#f1f5f9);font-size:1.3rem;font-weight:700;">{plan['name']}</div>
                    <div style="color:#22d3ee;font-size:1.2rem;font-weight:800;margin-bottom:0.2rem;">{plan['price_label']}</div>
                    <div style="color:var(--soft,#c8d3df);font-size:0.8rem;margin-bottom:1rem;">{plan['price_detail']}</div>
                    {feats}
                </div>
            """)


def render_demo_gate(lang_key: str) -> None:
    """Render the original demo access code gate."""
    copy = get_access_copy(lang_key)
    outer_left, outer_center, outer_right = st.columns([1.0, 1.3, 1.0], gap="large")
    with outer_center:
        render_html(f"""
            <div class="access-gate-head">
                <div class="assistant-kicker">DEMO-TILGANG</div>
                <div class="access-gate-title">{copy['title']}</div>
                <div class="access-gate-subtitle">{copy['subtitle']}</div>
            </div>
        """)

        if not access_gate_configured():
            st.warning(copy["admin_missing"])
            return

        nonce = st.session_state.get("site_access_input_nonce", 0)
        with st.form("demo_gate_form"):
            code = st.text_input(
                copy["label"],
                key=f"demo_gate_code_{nonce}",
                type="password",
                placeholder=copy["placeholder"],
            )
            submitted = st.form_submit_button(copy["button"], use_container_width=True)

        if submitted:
            if verify_site_access_code(code):
                st.session_state.site_access_granted = True
                st.session_state.site_access_error = ""
                token = _generate_session_token(code)
                params = get_query_params_dict()
                params.pop("auth", None)
                params.pop("gate", None)
                params["s"] = token
                set_query_params_dict(params)
                st.rerun()
            else:
                st.error(copy["error_invalid"])
                bump_site_access_nonce()
                st.rerun()

        st.markdown("---")
        render_html('<div style="text-align:center;"><a href="?auth=login" target="_self" style="color:var(--cyan,#38bdf8);">Logg inn med konto i stedet</a></div>')


def render_plans_page(lang_key: str) -> None:
    """Render subscription plan selection with payment method choice."""
    # Require registration before viewing plans
    if not _is_user_logged_in():
        render_html("""
            <div class="access-gate-head" style="text-align:center;">
                <div class="assistant-kicker">REGISTRERING PÅKREVD</div>
                <div class="access-gate-title">Opprett konto først</div>
                <div class="access-gate-subtitle">Du må registrere deg før du kan velge abonnement og betale.</div>
            </div>
        """)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Opprett konto", type="primary", use_container_width=True):
                try:
                    st.query_params["auth"] = "register"
                except Exception:
                    pass
                st.rerun()
        with col_b:
            if st.button("Logg inn", type="secondary", use_container_width=True):
                try:
                    st.query_params["auth"] = "login"
                except Exception:
                    pass
                st.rerun()
        return

    user_countries = st.session_state.get("user_countries", ["NO"])
    n_countries = max(len(user_countries), 1)
    country_names = []
    for code in user_countries:
        for c_code, c_label in AVAILABLE_COUNTRIES:
            if c_code == code:
                country_names.append(c_label.split(" (")[0])
                break

    render_html(f"""
        <div class="access-gate-head" style="text-align:center;">
            <div class="assistant-kicker">VELG ABONNEMENT</div>
            <div class="access-gate-title">Tre nivåer — fra fullt automatisert til attestert</div>
            <div class="access-gate-subtitle">
                {'Land: ' + ', '.join(country_names) + '.' if country_names else ''}
                {'Prisene nedenfor gjelder per land ×' + str(n_countries) + '.' if n_countries > 1 else 'Prisene gjelder for 1 land.'}
                Alle planer har {CONTRACT_BINDING_MONTHS} måneders bindingstid.
            </div>
        </div>
    """)

    cols = st.columns(3, gap="medium")
    for idx, (plan_key, plan) in enumerate(SUBSCRIPTION_PLANS.items()):
        with cols[idx]:
            is_popular = plan_key == "team"
            border_color = "#38bdf8" if is_popular else "var(--card-border, rgba(56,189,248,0.10))"
            badge_color = "#38bdf8" if is_popular else "#22d3ee"

            features_html = "".join(
                f'<div style="padding:0.3rem 0;color:var(--soft,#c8d3df);font-size:0.9rem;">✓ {f}</div>'
                for f in plan["features"]
            )

            multi_note = ""
            if n_countries > 1:
                multi_note = f'<div style="color:#f59e0b;font-size:0.8rem;margin-top:0.5rem;">× {n_countries} land</div>'

            render_html(f"""
                <div style="border:1px solid {border_color};border-radius:1rem;padding:1.8rem;
                            background:var(--card-bg,rgba(6,17,26,0.55));min-height:450px;
                            display:flex;flex-direction:column;position:relative;">
                    <div style="color:{badge_color};font-weight:700;font-size:0.75rem;
                                letter-spacing:0.08em;margin-bottom:0.5rem;">{plan['badge']}</div>
                    <div style="color:var(--bright,#f1f5f9);font-size:1.5rem;font-weight:700;
                                margin-bottom:0.25rem;">{plan['name']}</div>
                    <div style="color:#22d3ee;font-size:1.4rem;font-weight:800;
                                margin-bottom:0.25rem;">{plan['price_label']}</div>
                    <div style="color:var(--soft,#c8d3df);font-size:0.85rem;
                                margin-bottom:0.3rem;">{plan['price_detail']}</div>
                    {multi_note}
                    <div style="color:var(--soft,#c8d3df);font-size:0.75rem;
                                margin-bottom:1rem;">{CONTRACT_BINDING_MONTHS} mnd bindingstid</div>
                    <div style="flex:1;">{features_html}</div>
                </div>
            """)
            if st.button(f"Velg {plan['name']}", key=f"select_plan_{plan_key}", use_container_width=True):
                st.session_state.user_plan = plan_key
                st.rerun()

    # -- Payment method selection (shown after plan is selected) --
    selected_plan = st.session_state.get("user_plan", "")
    if selected_plan and selected_plan in SUBSCRIPTION_PLANS:
        plan_info = SUBSCRIPTION_PLANS[selected_plan]
        st.markdown("---")

        render_html(f"""
            <div style="text-align:center;margin-bottom:1.5rem;">
                <div style="color:var(--bright,#f1f5f9);font-size:1.2rem;font-weight:700;">
                    Valgt plan: {plan_info['name']} — {plan_info['price_label']}
                    {' × ' + str(n_countries) + ' land' if n_countries > 1 else ''}
                </div>
            </div>
        """)

        render_html("""
            <div style="text-align:center;margin-bottom:1rem;">
                <div style="color:var(--bright,#f1f5f9);font-size:1rem;font-weight:600;">Velg betalingsmetode</div>
            </div>
        """)

        pay_col1, pay_col2 = st.columns(2, gap="large")
        with pay_col1:
            render_html("""
                <div style="border:1px solid rgba(56,189,248,0.15);border-radius:1rem;padding:1.5rem;
                            background:var(--card-bg,rgba(6,17,26,0.55));text-align:center;">
                    <div style="font-size:2rem;margin-bottom:0.5rem;">💳</div>
                    <div style="color:var(--bright,#f1f5f9);font-weight:700;margin-bottom:0.3rem;">Kortbetaling</div>
                    <div style="color:var(--soft,#c8d3df);font-size:0.85rem;margin-bottom:0.5rem;">
                        Automatisk aktivering ved godkjent betaling. Stripe sikker betaling.
                    </div>
                    <div style="color:#22d3ee;font-size:0.8rem;">Aktiv umiddelbart</div>
                </div>
            """)
            if st.button("Betal med kort", key="pay_card", use_container_width=True):
                if _HAS_AUTH:
                    checkout_url, err = builtly_auth.create_checkout(selected_plan, n_countries)
                    if checkout_url:
                        st.markdown(f'<meta http-equiv="refresh" content="0;url={checkout_url}">', unsafe_allow_html=True)
                        st.info("Omdirigerer til Stripe...")
                    else:
                        st.error(err)
                else:
                    st.error("Betalingsmodul ikke installert.")

        with pay_col2:
            render_html("""
                <div style="border:1px solid rgba(56,189,248,0.15);border-radius:1rem;padding:1.5rem;
                            background:var(--card-bg,rgba(6,17,26,0.55));text-align:center;">
                    <div style="font-size:2rem;margin-bottom:0.5rem;">📄</div>
                    <div style="color:var(--bright,#f1f5f9);font-weight:700;margin-bottom:0.3rem;">Faktura</div>
                    <div style="color:var(--soft,#c8d3df);font-size:0.85rem;margin-bottom:0.5rem;">
                        Faktura sendes til oppgitt e-post. Konto aktiveres manuelt etter mottatt betaling.
                    </div>
                    <div style="color:#f59e0b;font-size:0.8rem;">Manuell godkjenning (1–3 virkedager)</div>
                </div>
            """)
            if st.button("Bestill på faktura", key="pay_invoice", use_container_width=True):
                if _HAS_AUTH:
                    ok, msg = builtly_auth.request_invoice(selected_plan, n_countries)
                    if ok:
                        st.info(msg)
                    else:
                        st.error(msg)
                else:
                    st.error("Betalingsmodul ikke installert.")

        # Contract summary
        render_html(f"""
            <div style="text-align:center;color:var(--soft,#c8d3df);font-size:0.8rem;
                        max-width:600px;margin:1.5rem auto 0 auto;padding:1rem;
                        border:1px solid rgba(56,189,248,0.08);border-radius:0.75rem;">
                <strong>Kontraktsvilkår:</strong> {CONTRACT_TERMS_TEXT.format(months=CONTRACT_BINDING_MONTHS)}<br><br>
                <strong>Personvern:</strong> Data behandles iht. GDPR/personopplysningsloven og lagres innenfor EØS.<br><br>
                <strong>Betalingsleverandør:</strong> Stripe (PCI DSS-sertifisert). Builtly lagrer ikke kortinformasjon.
            </div>
        """)

    # Platform pricing info
    st.markdown("---")
    render_html(f"""
        <div style="text-align:center;color:var(--soft,#c8d3df);font-size:0.85rem;
                    max-width:700px;margin:0 auto;padding:1rem 0;">
            <strong style="color:#22d3ee;">PLATTFORMPRISING (SaaS)</strong><br>
            <strong>Portefølje-API (bank):</strong> 150 000–500 000 kr/år · 10 banker = 2 MNOK ARR uten én fagpersontime<br>
            <strong>White-label partnerprogram:</strong> Lisens + revenue share · 25 000–2 000 000 kr/år<br><br>
            <em>{REVISION_NOTICE}</em>
        </div>
    """)


def render_user_dashboard(lang_key: str) -> None:
    """Render user dashboard with saved reports and account info."""
    user_name = st.session_state.get("user_name", "Bruker")
    user_email = st.session_state.get("user_email", "")
    user_company = st.session_state.get("user_company", "")
    user_plan = st.session_state.get("user_plan", "")
    user_countries = st.session_state.get("user_countries", [])
    user_status = st.session_state.get("user_account_status", "inactive")
    user_payment = st.session_state.get("user_payment_method", "")
    reports = st.session_state.get("user_reports", [])

    status_labels = {
        "active": ("✅ Aktiv", "#22c55e"),
        "pending_invoice": ("⏳ Venter på betaling", "#f59e0b"),
        "inactive": ("⚠️ Inaktiv", "#ef4444"),
    }
    status_text, status_color = status_labels.get(user_status, ("Ukjent", "#c8d3df"))

    country_names = []
    for code in user_countries:
        for c_code, c_label in AVAILABLE_COUNTRIES:
            if c_code == code:
                country_names.append(c_label.split(" (")[0])
                break

    render_html(f"""
        <div class="access-gate-head">
            <div class="assistant-kicker">MIN KONTO</div>
            <div class="access-gate-title">Hei, {html.escape(user_name)}</div>
        </div>
    """)

    # -- Account status banner --
    if user_status == "pending_invoice":
        render_html("""
            <div style="background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);
                        border-radius:0.75rem;padding:1rem 1.5rem;margin-bottom:1.5rem;
                        color:#f59e0b;font-size:0.9rem;">
                ⏳ <strong>Kontoen venter på betalingsbekreftelse.</strong>
                Faktura er sendt — kontoen aktiveres når betaling er registrert (1–3 virkedager).
                Kontakt post@builtly.ai ved spørsmål.
            </div>
        """)
    elif user_status == "inactive":
        render_html("""
            <div style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);
                        border-radius:0.75rem;padding:1rem 1.5rem;margin-bottom:1.5rem;
                        color:#ef4444;font-size:0.9rem;">
                ⚠️ <strong>Ingen aktiv plan.</strong>
                Velg et abonnement for å få tilgang til Builtly sine moduler.
            </div>
        """)

    # -- Account info --
    info_col, plan_col = st.columns([1, 1], gap="large")
    with info_col:
        render_html(f"""
            <div style="border:1px solid var(--card-border,rgba(56,189,248,0.10));border-radius:1rem;
                        padding:1.5rem;background:var(--card-bg,rgba(6,17,26,0.55));">
                <div style="color:var(--soft,#c8d3df);font-size:0.8rem;margin-bottom:0.5rem;">KONTOINFORMASJON</div>
                <div style="color:var(--bright,#f1f5f9);margin-bottom:0.3rem;"><strong>Navn:</strong> {html.escape(user_name)}</div>
                {'<div style="color:var(--bright,#f1f5f9);margin-bottom:0.3rem;"><strong>Selskap:</strong> ' + html.escape(user_company) + '</div>' if user_company else ''}
                <div style="color:var(--bright,#f1f5f9);margin-bottom:0.3rem;"><strong>E-post:</strong> {html.escape(user_email)}</div>
                <div style="color:var(--bright,#f1f5f9);margin-bottom:0.3rem;"><strong>Land:</strong> {html.escape(', '.join(country_names)) if country_names else 'Ikke valgt'}</div>
                <div style="color:var(--bright,#f1f5f9);margin-bottom:0.3rem;"><strong>Betaling:</strong> {'Kort (Stripe)' if user_payment == 'card' else 'Faktura' if user_payment == 'invoice' else 'Ikke satt opp'}</div>
                <div style="margin-top:0.5rem;"><strong style="color:{status_color};">{status_text}</strong></div>
            </div>
        """)
    with plan_col:
        plan_info = SUBSCRIPTION_PLANS.get(user_plan, {})
        if plan_info:
            render_html(f"""
                <div style="border:1px solid var(--card-border,rgba(56,189,248,0.10));border-radius:1rem;
                            padding:1.5rem;background:var(--card-bg,rgba(6,17,26,0.55));">
                    <div style="color:var(--soft,#c8d3df);font-size:0.8rem;margin-bottom:0.5rem;">DITT ABONNEMENT</div>
                    <div style="color:#22d3ee;font-size:1.2rem;font-weight:700;">{plan_info['name']} — {plan_info['price_label']}</div>
                    <div style="color:var(--soft,#c8d3df);font-size:0.85rem;margin-top:0.3rem;">{plan_info['price_detail']}</div>
                </div>
            """)
        else:
            render_html('<div style="border:1px solid rgba(56,189,248,0.10);border-radius:1rem;padding:1.5rem;background:rgba(6,17,26,0.55);">')
            render_html('<div style="color:var(--soft,#c8d3df);">Ingen aktiv plan.</div>')
            render_html('</div>')
            if st.button("Velg abonnement", use_container_width=True):
                try:
                    st.query_params["auth"] = "plans"
                except Exception:
                    pass
                st.rerun()

    # -- Saved reports --
    st.markdown("<div style='margin-top:2rem;'></div>", unsafe_allow_html=True)
    render_html(f"""
        <div style="color:var(--bright,#f1f5f9);font-size:1.2rem;font-weight:700;margin-bottom:0.5rem;">
            Mine rapporter
        </div>
        <div style="color:var(--soft,#c8d3df);font-size:0.85rem;margin-bottom:1rem;">
            Rapporter lagres i {REPORT_RETENTION_DAYS} dager. {REVISION_NOTICE}
        </div>
    """)

    if not reports:
        render_html("""
            <div style="border:1px dashed rgba(56,189,248,0.20);border-radius:1rem;padding:3rem;
                        text-align:center;color:var(--soft,#c8d3df);">
                <div style="font-size:2rem;margin-bottom:0.5rem;">📄</div>
                <div>Ingen rapporter ennå. Opprett ditt første prosjekt for å komme i gang.</div>
            </div>
        """)
    else:
        # Sort options
        sort_col, filter_col = st.columns([1, 1])
        with sort_col:
            sort_by = st.selectbox(
                "Sorter etter",
                ["Prosjekt (A–Å)", "Prosjekt (Å–A)", "Nyeste først", "Eldste først", "Modul"],
                index=0,
                key="report_sort",
            )
        with filter_col:
            # Collect unique project names
            all_projects = sorted(set(r.get("project", "Uten prosjekt") for r in reports))
            filter_project = st.selectbox(
                "Filtrer på prosjekt",
                ["Alle prosjekter"] + all_projects,
                index=0,
                key="report_filter_project",
            )

        # Filter
        filtered = reports
        if filter_project != "Alle prosjekter":
            filtered = [r for r in reports if r.get("project", "Uten prosjekt") == filter_project]

        # Sort
        if sort_by == "Prosjekt (A–Å)":
            filtered = sorted(filtered, key=lambda r: (r.get("project", "Uten prosjekt").lower(), r.get("created", "")))
        elif sort_by == "Prosjekt (Å–A)":
            filtered = sorted(filtered, key=lambda r: r.get("project", "Uten prosjekt").lower(), reverse=True)
        elif sort_by == "Nyeste først":
            filtered = sorted(filtered, key=lambda r: r.get("created", ""), reverse=True)
        elif sort_by == "Eldste først":
            filtered = sorted(filtered, key=lambda r: r.get("created", ""))
        elif sort_by == "Modul":
            filtered = sorted(filtered, key=lambda r: (r.get("module", ""), r.get("created", "")))

        # Group by project
        from collections import OrderedDict
        grouped: dict = OrderedDict()
        for r in filtered:
            proj = r.get("project", "Uten prosjekt")
            grouped.setdefault(proj, []).append(r)

        for proj_name, proj_reports in grouped.items():
            render_html(f"""
                <div style="color:#38bdf8;font-weight:700;font-size:0.95rem;margin-top:1.2rem;
                            margin-bottom:0.4rem;padding-bottom:0.3rem;
                            border-bottom:1px solid rgba(56,189,248,0.15);">
                    📁 {html.escape(proj_name)}
                    <span style="color:var(--soft,#c8d3df);font-weight:400;font-size:0.8rem;margin-left:0.5rem;">
                        {len(proj_reports)} rapport{'er' if len(proj_reports) != 1 else ''}
                    </span>
                </div>
            """)
            for report in proj_reports:
                exp_text = report.get("expires", "—")
                render_html(f"""
                    <div style="border:1px solid var(--card-border,rgba(56,189,248,0.10));border-radius:0.75rem;
                                padding:1rem 1.5rem;margin-bottom:0.4rem;margin-left:1rem;
                                background:var(--card-bg,rgba(6,17,26,0.55));
                                display:flex;align-items:center;justify-content:space-between;">
                        <div>
                            <div style="color:var(--bright,#f1f5f9);font-weight:600;">{html.escape(report.get('name', 'Rapport'))}</div>
                            <div style="color:var(--soft,#c8d3df);font-size:0.8rem;">
                                {html.escape(report.get('module', ''))} · Opprettet: {html.escape(report.get('created', '—'))} · Utløper: {html.escape(exp_text)}
                            </div>
                        </div>
                        <div style="color:#38bdf8;font-size:0.85rem;cursor:pointer;">Last ned ↓</div>
                    </div>
                """)

    # -- Actions --
    st.markdown("<div style='margin-top:2rem;'></div>", unsafe_allow_html=True)
    act_col1, act_col2, act_col3 = st.columns(3)
    with act_col1:
        if st.button("← Tilbake til forsiden", use_container_width=True):
            try:
                params = get_query_params_dict()
                params.pop("auth", None)
                set_query_params_dict(params)
            except Exception:
                pass
            st.rerun()
    with act_col2:
        if st.button("Endre abonnement", use_container_width=True):
            try:
                st.query_params["auth"] = "plans"
            except Exception:
                pass
            st.rerun()
    with act_col3:
        if st.button("Logg ut", use_container_width=True):
            if _HAS_AUTH:
                builtly_auth.logout()
            else:
                st.session_state.user_authenticated = False
                st.session_state.user_email = ""
                st.session_state.user_name = ""
                st.session_state.user_plan = ""
                st.session_state.user_reports = []
            try:
                params = get_query_params_dict()
                params.pop("auth", None)
                set_query_params_dict(params)
            except Exception:
                pass
            st.rerun()


def handle_auth_routing(lang_key: str) -> bool:
    """Check if we need to show an auth page. Returns True if an auth page was rendered (caller should st.stop)."""
    auth_page = _get_auth_page()
    if not auth_page:
        return False

    if auth_page == "login":
        render_login_page(lang_key)
        return True
    elif auth_page == "register":
        render_register_page(lang_key)
        return True
    elif auth_page == "demo":
        render_demo_gate(lang_key)
        return True
    elif auth_page == "plans":
        render_plans_page(lang_key)
        return True
    elif auth_page == "dashboard":
        if _is_user_logged_in():
            render_user_dashboard(lang_key)
        else:
            render_login_page(lang_key)
        return True
    elif auth_page == "payment_success":
        # Stripe redirects here after successful checkout
        if _HAS_AUTH:
            session_id = ""
            try:
                session_id = st.query_params.get("session_id", "")
            except Exception:
                pass
            if session_id:
                ok, msg = builtly_auth.verify_checkout(session_id)
                if ok:
                    st.success(msg)
                    render_html('<div style="text-align:center;margin-top:2rem;">')
                    render_html('<a href="/" target="_self" class="hero-action primary">Gå til Builtly</a>')
                    render_html('</div>')
                else:
                    st.error(msg)
            else:
                st.error("Mangler session-ID fra Stripe.")
        return True
    return False


def reference_base_dir() -> Path:
    return Path(os.getenv("BUILTLY_REFERENCE_DIR") or "knowledge_base")


def reference_file_candidates(lang_key: str, selected_codes: List[str]) -> List[Path]:
    base_dir = reference_base_dir()
    locale_slug = LANGUAGE_REFERENCE_SLUGS.get(lang_key, "global")
    candidates = [
        base_dir / "global" / "shared.md",
        base_dir / locale_slug / "shared.md",
    ]

    for code in selected_codes or []:
        candidates.append(base_dir / "global" / f"{code}.md")
        candidates.append(base_dir / locale_slug / f"{code}.md")

    unique_paths: List[Path] = []
    seen = set()
    for path in candidates:
        path_key = path.as_posix()
        if path_key not in seen:
            seen.add(path_key)
            unique_paths.append(path)
    return unique_paths


def load_reference_snippets(lang_key: str, selected_codes: List[str], char_limit: int = 2800) -> str:
    snippets: List[str] = []
    used = 0

    for path in reference_file_candidates(lang_key, selected_codes):
        if not path.exists() or not path.is_file():
            continue

        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue

        remaining = char_limit - used
        if remaining <= 0:
            break

        excerpt = text[:remaining].strip()
        if not excerpt:
            continue

        snippets.append(f"[{path.as_posix()}]\n{excerpt}")
        used += len(excerpt)

    return "\n\n".join(snippets).strip()


def loaded_reference_pack_names(lang_key: str, selected_codes: List[str]) -> List[str]:
    names: List[str] = []
    for path in reference_file_candidates(lang_key, selected_codes):
        if path.exists() and path.is_file():
            names.append(path.as_posix())
    return names


def build_builtly_prompt(question: str, selected_codes: List[str], lang_key: str, history: List[Dict]) -> str:
    profile = get_locale_profile(lang_key)
    selected_codes = selected_codes or list(DEFAULT_DISCIPLINES)
    selected_labels = ", ".join(discipline_labels(selected_codes, lang_key)) or get_text_bundle(lang_key)["assistant_scope_value"]

    history_block = ""
    if history:
        snippets = []
        for item in history[-4:]:
            snippets.append(f"User: {item['question']}\nBuiltly: {item['answer']}")
        history_block = "\n\nRecent conversation:\n" + "\n\n".join(snippets)

    country_guidance = COUNTRY_GUIDANCE_PACKS.get(lang_key, COUNTRY_GUIDANCE_PACKS["🇬🇧 English (UK)"])
    country_guidance_block = "\n".join([f"- {line}" for line in country_guidance])

    discipline_lines: List[str] = []
    for code in selected_codes:
        guidance_lines = DISCIPLINE_GUIDANCE_PACKS.get(code, [])
        label = discipline_label(code, lang_key)
        for line in guidance_lines:
            discipline_lines.append(f"- {label}: {line}")
    discipline_guidance_block = "\n".join(discipline_lines) or "- Use the user's question to infer the most relevant technical focus."

    reference_snippets = load_reference_snippets(lang_key, selected_codes)
    reference_block = ""
    if reference_snippets:
        reference_block = (
            "\n\nLocal reference pack excerpts (prioritise these when they are more specific than generic guidance):\n"
            + reference_snippets
        )

    return f"""
You are Builtly, the front-page engineering and property assistant for builtly.ai.

Respond in {profile['language_name']}.
Primary country context: {profile['country']}.
Primary regulatory baseline: {profile['rule_set']}.
Jurisdiction note: {profile['variation_note']}.
Active disciplines: {selected_labels}.

Country guidance pack:
{country_guidance_block}

Discipline guidance pack:
{discipline_guidance_block}

You can help with:
- geotechnics and ground conditions
- structural engineering
- demolition, reuse and waste handling
- acoustics and noise
- fire safety
- environment and sustainability
- SHA / health and safety
- BREEAM and certification strategy
- traffic and mobility in early phase
- property, feasibility and development

How to answer:
- Start with a direct practical answer.
- Separate mandatory requirements, common practice, and assumptions when useful.
- Be explicit about what depends on municipality, state, county, local authority or local permitting practice.
- Prefer a practical structure when relevant: Direct answer, What governs, Main risks/open points, Next to clarify.
- For early-phase questions, highlight the missing project facts that materially change the answer.
- If several disciplines are involved, organise the answer by discipline or by decision topic.
- For Sweden, explain when the 2025–2026 transition between older BBR rules and newer Boverket regulations matters.
- For Germany, state clearly that the applicable Landesbauordnung and local authority practice must be confirmed.
- For the United States, explain that state and local code adoption can differ from model codes.
- For the United Kingdom, default to England and flag if Scotland, Wales or Northern Ireland may differ.
- Do not pretend to certify, legally approve, or formally sign off.
- If the question is outside scope, politely say that Builtly focuses on building technology, development and property.

Formatting and length rules:
- Write clean Markdown only.
- Do not use tables.
- Do not use horizontal rules.
- Keep the answer compact and decision-oriented. In most cases stay under roughly 450-650 words.
- Use at most four short headings or sections.
- Finish the very last line with exactly {ASSISTANT_END_MARKER}

{history_block}{reference_block}

User question:
{question.strip()}
""".strip()


def answer_has_end_marker(text: str) -> bool:
    return ASSISTANT_END_MARKER in (text or "")


def clean_ai_answer_text(text: str) -> str:
    cleaned = (text or "").replace(ASSISTANT_END_MARKER, "")
    cleaned = cleaned.replace("END_OF_BUILTLY_ANSWER", "")
    cleaned = re.sub(r"(?m)^\s*---\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\*\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def trim_incomplete_tail(text: str) -> str:
    cleaned = clean_ai_answer_text(text)
    if not cleaned:
        return ""

    lines = cleaned.splitlines()
    if not lines:
        return cleaned

    last_line = lines[-1].strip()
    if last_line and not last_line.endswith((".", "!", "?", ")", "]", "»", "”", ":")):
        lines = lines[:-1]

    trimmed = "\n".join(lines).strip()
    if not trimmed:
        trimmed = cleaned

    sentence_end = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
    if sentence_end >= 0 and sentence_end >= int(len(trimmed) * 0.55):
        return trimmed[: sentence_end + 1].strip()
    return trimmed


def build_builtly_continuation_prompt(question: str, partial_answer: str, selected_codes: List[str], lang_key: str) -> str:
    profile = get_locale_profile(lang_key)
    selected_labels = ", ".join(discipline_labels(selected_codes, lang_key)) or get_text_bundle(lang_key)["assistant_scope_value"]
    return f"""
You are continuing the same Builtly answer.

Respond in {profile['language_name']}.
Country context: {profile['country']}.
Primary regulatory baseline: {profile['rule_set']}.
Active disciplines: {selected_labels}.

Rules:
- Continue exactly where the previous answer stopped.
- Do not restart or repeat the introduction.
- Finish any cut-off word, sentence, heading or bullet.
- Keep the remaining part compact and practical.
- Do not use tables or horizontal rules.
- Finish the very last line with exactly {ASSISTANT_END_MARKER}

Original user question:
{question.strip()}

Existing partial answer:
{clean_ai_answer_text(partial_answer)}
""".strip()


def build_builtly_repair_prompt(question: str, partial_answer: str, selected_codes: List[str], lang_key: str) -> str:
    profile = get_locale_profile(lang_key)
    selected_labels = ", ".join(discipline_labels(selected_codes, lang_key)) or get_text_bundle(lang_key)["assistant_scope_value"]
    return f"""
You are repairing a Builtly answer that stopped before it was completed.

Respond in {profile['language_name']}.
Country context: {profile['country']}.
Primary regulatory baseline: {profile['rule_set']}.
Active disciplines: {selected_labels}.

Rules:
- Rewrite the draft below into one complete, compact and practical answer.
- Preserve the useful substance from the draft, but remove broken Markdown, repeated lines and unfinished fragments.
- Do not use tables or horizontal rules.
- Keep the answer decision-oriented and normally under roughly 450-650 words.
- Finish the very last line with exactly {ASSISTANT_END_MARKER}

Original user question:
{question.strip()}

Draft answer to repair:
{clean_ai_answer_text(partial_answer)}
""".strip()


def parse_gemini_response(response_payload: Dict) -> tuple[str, bool]:
    candidates = response_payload.get("candidates", [])
    text_parts: List[str] = []
    was_truncated = False
    for candidate in candidates:
        finish_reason = str(candidate.get("finishReason", "")).upper()
        if finish_reason == "MAX_TOKENS":
            was_truncated = True
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                text_parts.append(part["text"])
    if text_parts:
        return "\n".join(text_parts).strip(), was_truncated

    block_reason = response_payload.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise RuntimeError(f"Response blocked by AI engine: {block_reason}")

    raise RuntimeError("No text returned from the AI engine.")


def call_gemini_generate_content(
    *,
    api_key: str,
    model_name: str,
    prompt_text: str,
    max_output_tokens: int = 2400,
) -> tuple[str, bool]:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt_text}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": max_output_tokens,
        },
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urlparse.quote(model_name, safe='')}:generateContent?key={urlparse.quote(api_key, safe='')}"
    )

    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlrequest.urlopen(req, timeout=70) as response:
            data = json.loads(response.read().decode("utf-8"))
        return parse_gemini_response(data)
    except urlerror.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        raise RuntimeError(f"AI engine HTTP {exc.code}: {details}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"AI engine connection error: {exc}") from exc


def merge_assistant_continuation(answer: str, continuation: str) -> str:
    primary = (answer or "").rstrip()
    extra = (continuation or "").lstrip()
    if not extra:
        return primary
    if extra in primary:
        return primary

    max_overlap = min(len(primary), len(extra), 160)
    overlap = 0
    for size in range(max_overlap, 11, -1):
        if primary[-size:].lower() == extra[:size].lower():
            overlap = size
            break
    if overlap:
        extra = extra[overlap:].lstrip()
        if not extra:
            return primary

    if primary and extra and primary[-1].isalnum() and extra[0].isalnum():
        if extra[0].islower():
            return f"{primary}{extra}".strip()
        return f"{primary} {extra}".strip()

    if primary.endswith(("-", "–", "/", ":")):
        separator = " "
    elif primary.endswith("\n") or extra.startswith(("-", "*", "•", "1.", "2.", "3.", "4.", "5.")):
        separator = "\n"
    else:
        separator = "\n\n"

    return f"{primary}{separator}{extra}".strip()


def request_builtly_answer(question: str, selected_codes: List[str], lang_key: str, history: List[Dict]) -> str:
    api_key = gemini_api_key()
    profile = get_locale_profile(lang_key)
    selected_labels = ", ".join(discipline_labels(selected_codes, lang_key))
    if not api_key:
        return (
            f"**{get_text_bundle(lang_key)['assistant_note_prefix']}:** "
            f"Set the AI API key in Render to activate live answers. "
            f"The front page is already wired to send the selected language ({profile['language_name']}), "
            f"country ({profile['country']}), rule set ({profile['rule_set']}) and disciplines ({selected_labels}) to the AI engine."
        )

    model_name = os.getenv("BUILTLY_GEMINI_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    primary_prompt = build_builtly_prompt(question, selected_codes, lang_key, history)
    answer, was_truncated = call_gemini_generate_content(
        api_key=api_key,
        model_name=model_name,
        prompt_text=primary_prompt,
        max_output_tokens=1800,
    )

    combined_answer = answer
    continuation_rounds = 0
    while continuation_rounds < 3 and (was_truncated or not answer_has_end_marker(combined_answer)):
        continuation_rounds += 1
        continuation_prompt = build_builtly_continuation_prompt(
            question=question,
            partial_answer=combined_answer,
            selected_codes=selected_codes,
            lang_key=lang_key,
        )
        try:
            continuation, continuation_truncated = call_gemini_generate_content(
                api_key=api_key,
                model_name=model_name,
                prompt_text=continuation_prompt,
                max_output_tokens=900,
            )
        except Exception:
            break

        merged_answer = merge_assistant_continuation(combined_answer, continuation)
        if merged_answer == combined_answer:
            break

        combined_answer = merged_answer
        was_truncated = continuation_truncated

    if not answer_has_end_marker(combined_answer):
        repair_prompt = build_builtly_repair_prompt(
            question=question,
            partial_answer=combined_answer,
            selected_codes=selected_codes,
            lang_key=lang_key,
        )
        try:
            repaired_answer, repaired_truncated = call_gemini_generate_content(
                api_key=api_key,
                model_name=model_name,
                prompt_text=repair_prompt,
                max_output_tokens=1400,
            )
            combined_answer = repaired_answer
            if repaired_truncated or not answer_has_end_marker(combined_answer):
                final_continuation_prompt = build_builtly_continuation_prompt(
                    question=question,
                    partial_answer=combined_answer,
                    selected_codes=selected_codes,
                    lang_key=lang_key,
                )
                try:
                    continuation, _ = call_gemini_generate_content(
                        api_key=api_key,
                        model_name=model_name,
                        prompt_text=final_continuation_prompt,
                        max_output_tokens=700,
                    )
                    combined_answer = merge_assistant_continuation(combined_answer, continuation)
                except Exception:
                    pass
        except Exception:
            combined_answer = trim_incomplete_tail(combined_answer)

    final_answer = clean_ai_answer_text(combined_answer)
    if not final_answer:
        raise RuntimeError("No text returned from the AI engine.")

    if not answer_has_end_marker(combined_answer):
        final_answer = trim_incomplete_tail(final_answer)

    return final_answer


def handle_assistant_submission(question: str, selected_codes: List[str], lang_key: str) -> None:
    lang = get_text_bundle(lang_key)

    try:
        with st.spinner(lang["assistant_loading"]):
            answer = request_builtly_answer(
                question=question,
                selected_codes=selected_codes,
                lang_key=lang_key,
                history=st.session_state.assistant_history,
            )

        st.session_state.assistant_history.append(
            {
                "question": question.strip(),
                "answer": answer,
                "disciplines": discipline_labels(selected_codes, lang_key),
                "lang": lang_key,
            }
        )
        st.session_state.assistant_history = st.session_state.assistant_history[-8:]
    except Exception as exc:
        st.session_state.assistant_history.append(
            {
                "question": question.strip(),
                "answer": f"**{lang['assistant_error_prefix']}:** {exc}",
                "disciplines": discipline_labels(selected_codes, lang_key),
                "lang": lang_key,
            }
        )
        st.session_state.assistant_history = st.session_state.assistant_history[-8:]


ASSISTANT_TEASER_TITLE_COPY = {
    "🇬🇧 English (UK)": "Ask about building technology and property.",
    "🇺🇸 English (US)": "Ask about building technology and property.",
    "🇳🇴 Norsk": "Still spørsmål om bygg, eiendom og regelverk.",
    "🇸🇪 Svenska": "Ställ frågor om bygg, fastighet och regelverk.",
    "🇩🇰 Dansk": "Stil spørgsmål om byggeri, ejendom og regelværk.",
    "🇫🇮 Suomi": "Kysy rakentamisesta, kiinteistöistä ja määräyksistä.",
    "🇩🇪 Deutsch": "Fragen zu Bau, Immobilie und Regelwerk stellen.",
}

ASSISTANT_TEASER_SUBTITLE_COPY = {
    "🇬🇧 English (UK)": "A practical Q&A surface adapted to the selected language and national baseline.",
    "🇺🇸 English (US)": "A practical Q&A surface adapted to the selected language and national baseline.",
    "🇳🇴 Norsk": "En spørreflate som følger valgt språk og nasjonalt rammeverk.",
    "🇸🇪 Svenska": "En frågeyta som följer valt språk och nationellt ramverk.",
    "🇩🇰 Dansk": "En spørgeflade som følger valgt sprog og nationalt regelsæt.",
    "🇫🇮 Suomi": "Kysymysikkuna, joka seuraa valittua kieltä ja kansallista viitekehystä.",
    "🇩🇪 Deutsch": "Ein Fragenfenster passend zu Sprache und nationalem Regelwerk.",
}

ASSISTANT_TEASER_FOOT_COPY = {
    "🇬🇧 English (UK)": "Covers GEO, structural, demolition, acoustics, fire, environment, SHA, BREEAM and property.",
    "🇺🇸 English (US)": "Covers GEO, structural, demolition, acoustics, fire, environment, SHA, BREEAM and property.",
    "🇳🇴 Norsk": "Dekker GEO, RIB, rive, RIAku, RIBr, miljø, SHA, BREEAM og eiendom.",
    "🇸🇪 Svenska": "Täcker GEO, konstruktion, rivning, akustik, brand, miljö, SHA, BREEAM och fastighet.",
    "🇩🇰 Dansk": "Dækker GEO, konstruktion, nedrivning, akustik, brand, miljø, SHA, BREEAM og ejendom.",
    "🇫🇮 Suomi": "Kattaa GEO, rakenteet, purun, akustiikan, palon, ympäristön, SHA:n, BREEAMin ja kiinteistöt.",
    "🇩🇪 Deutsch": "Deckt GEO, Tragwerk, Rückbau, Akustik, Brandschutz, Umwelt, SHA, BREEAM und Immobilie ab.",
}


def assistant_teaser_title(lang_key: str) -> str:
    return ASSISTANT_TEASER_TITLE_COPY.get(lang_key, ASSISTANT_TEASER_TITLE_COPY["🇬🇧 English (UK)"])


def assistant_teaser_subtitle(lang_key: str) -> str:
    return ASSISTANT_TEASER_SUBTITLE_COPY.get(lang_key, ASSISTANT_TEASER_SUBTITLE_COPY["🇬🇧 English (UK)"])


def assistant_teaser_foot(lang_key: str) -> str:
    return ASSISTANT_TEASER_FOOT_COPY.get(lang_key, ASSISTANT_TEASER_FOOT_COPY["🇬🇧 English (UK)"])


def render_assistant_surface(lang_key: str, surface_key: str = "dialog") -> None:
    lang = get_text_bundle(lang_key)
    locale_profile = get_locale_profile(lang_key)
    selected_codes = st.session_state.get("assistant_discipline_codes", list(DEFAULT_DISCIPLINES))

    render_html(
        f"""
        <div class="assistant-dialog-hero">
            <div class="assistant-kicker">{lang['assistant_kicker']}</div>
            <div class="assistant-title">{assistant_teaser_title(lang_key)}</div>
            <div class="assistant-subtitle">{assistant_teaser_subtitle(lang_key)}</div>
            <div class="context-chips compact">
                <div class="context-chip"><span>{lang['assistant_label_country']}:</span> {locale_profile['country']}</div>
                <div class="context-chip"><span>{lang['assistant_label_rules']}:</span> {locale_profile['jurisdiction_short']}</div>
            </div>
            <div class="assistant-dialog-note">{locale_profile['variation_note']}</div>
        </div>
        """
    )

    render_html(
        f"""
        <div class="example-label">{lang['assistant_examples_label']}</div>
        <div class="example-chip-wrap">
            {''.join([f'<div class="example-chip">{html.escape(example)}</div>' for example in lang['assistant_examples']])}
        </div>
        """
    )

    input_key = f"assistant_input_{surface_key}_{st.session_state.get('assistant_input_nonce', 0)}"

    with st.form(f"builtly_frontpage_assistant_{surface_key}"):
        selected_codes = st.multiselect(
            lang["assistant_disciplines_label"],
            options=[item["code"] for item in DISCIPLINE_CATALOG],
            format_func=lambda code: discipline_label(code, lang_key),
            key="assistant_discipline_codes",
        )
        question = st.text_area(
            lang["assistant_question_label"],
            key=input_key,
            placeholder=lang["assistant_placeholder"],
            height=170,
        )
        submitted = st.form_submit_button(lang["assistant_btn"], use_container_width=True)

    if submitted and question.strip():
        handle_assistant_submission(question, selected_codes, lang_key)
        bump_assistant_input_nonce()
        st.rerun()

    action_left, action_right = st.columns([0.35, 0.65], gap="small")
    with action_left:
        clear_clicked = st.button(lang["assistant_clear"], key=f"assistant_clear_{surface_key}", use_container_width=True)
    with action_right:
        st.caption(lang["assistant_disclaimer"])

    if clear_clicked:
        reset_assistant_conversation()
        st.rerun()

    if st.session_state.assistant_history:
        latest = st.session_state.assistant_history[-1]
        with st.container(border=True):
            st.markdown(f"**{lang['assistant_latest_answer']}**")
            if latest.get("disciplines"):
                st.caption(" · ".join(latest["disciplines"]))
            st.markdown(latest["answer"])

        if len(st.session_state.assistant_history) > 1:
            with st.expander(lang["assistant_history_label"], expanded=False):
                for item in reversed(st.session_state.assistant_history[-8:]):
                    st.markdown(f"**Q:** {item['question']}")
                    if item.get("disciplines"):
                        st.caption(" · ".join(item["disciplines"]))
                    st.markdown(item["answer"])
                    st.markdown("---")
    else:
        render_html(
            f"""
            <div class="assistant-note">
                <strong>{lang['assistant_empty_title']}</strong>
                {lang['assistant_empty_body']}
            </div>
            """
        )


def maybe_render_assistant_dialog(lang_key: str) -> None:
    if not st.session_state.get("assistant_dialog_open"):
        return

    if hasattr(st, "dialog"):
        title = get_text_bundle(lang_key)["assistant_kicker"]

        @st.dialog(
            title,
            width="large",
            dismissible=True,
            icon=":material/auto_awesome:",
            on_dismiss=close_assistant,
        )
        def _assistant_dialog() -> None:
            render_assistant_surface(lang_key, surface_key="dialog")

        _assistant_dialog()
        return

    st.markdown("<div style='margin-top: 1.25rem;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        render_assistant_surface(lang_key, surface_key="inline")
        if st.button(assistant_close_label(lang_key), key="assistant_inline_close", use_container_width=True):
            close_assistant()
            st.rerun()


# -------------------------------------------------
# 6) CSS
# -------------------------------------------------
st.markdown(
    """
<style>
    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18);
        --stroke-strong: rgba(120, 145, 170, 0.28);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38c2c9;
        --accent-2: #78dce1;
        --accent-3: #112c3f;
        --ok: #7ee081;
        --warn: #f4bf4f;
        --shadow: 0 24px 90px rgba(0,0,0,0.35);
        --radius-xl: 28px;
        --radius-lg: 22px;
        --radius-md: 14px;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .stApp {
        background:
            radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
            radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
            linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }

    /* Prevent Streamlit from injecting white backgrounds on inner containers */
    div[data-testid="stExpander"] [data-testid="stVerticalBlock"],
    div[data-testid="stExpander"] [data-testid="element-container"],
    div[data-testid="stExpander"] [class*="stElementContainer"],
    div[data-testid="stExpander"] section,
    div[data-testid="stExpander"] [class*="block-container"] {
        background: transparent !important;
        background-color: transparent !important;
    }

    header[data-testid="stHeader"] {
        visibility: hidden;
        height: 0;
    }

    [data-testid="stSidebar"] {
        background: rgba(7, 16, 24, 0.96);
        border-right: 1px solid var(--stroke);
    }

    .block-container {
        max-width: 1300px !important;
        padding-top: 1.35rem !important;
        padding-bottom: 2rem !important;
    }

    .top-shell {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1.25rem;
    }

    .brand-left {
        display: flex;
        align-items: center;
        gap: 0.9rem;
        min-width: 0;
    }

    .brand-logo {
        display: block;
        height: 85px;
        width: auto;
        flex-shrink: 0;
        filter: drop-shadow(0 0 18px rgba(120,220,225,0.08));
    }

    .brand-name {
        color: var(--text);
        font-weight: 750;
        font-size: 1.5rem;
        line-height: 1.1;
        letter-spacing: -0.02em;
    }

    [data-testid="stSelectbox"] {
        margin-bottom: 0 !important;
        width: 170px;
        float: right;
    }

    [data-testid="stSelectbox"] label {
        display: none !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        background: linear-gradient(180deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96)) !important;
        color: var(--text) !important;
        border: 1px solid rgba(120,145,170,0.28) !important;
        border-radius: 16px !important;
        min-height: 44px !important;
        padding-left: 10px !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
        cursor: pointer;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
        border-color: rgba(120,220,225,0.42) !important;
        background: linear-gradient(180deg, rgba(14,28,43,0.98), rgba(9,19,30,0.98)) !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] span,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] input,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] div {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    div[data-testid="stSelectbox"] svg {
        color: var(--muted) !important;
        fill: var(--muted) !important;
    }

    .hero-action.disabled,
    .module-cta.disabled {
        opacity: 0.45;
        pointer-events: none;
        cursor: default;
    }

    .hero {
        position: relative;
        overflow: hidden;
        background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: var(--radius-xl);
        padding: 2.5rem;
        box-shadow: var(--shadow);
        margin-bottom: 0;
        min-height: 560px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    .hero::before {
        content: "";
        position: absolute;
        inset: -80px -120px auto auto;
        width: 420px;
        height: 420px;
        background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%);
        pointer-events: none;
    }

    .eyebrow {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.78rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }

    .hero-title {
        font-size: clamp(2.55rem, 5vw, 4.35rem);
        line-height: 1.05;
        letter-spacing: -0.03em;
        font-weight: 800;
        margin: 0 0 1rem 0;
        color: var(--text);
        max-width: none;
    }

    .hero-title .accent {
        color: var(--accent-2);
    }

    .hero-subtitle {
        max-width: 58ch;
        font-size: 1.08rem;
        line-height: 1.8;
        color: var(--soft);
        margin-bottom: 1.5rem;
    }

    .hero-actions {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
        margin-bottom: 1.5rem;
    }

    .hero-action {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 46px;
        padding: 0.78rem 1rem;
        border-radius: 12px;
        text-decoration: none !important;
        font-weight: 650;
        font-size: 0.95rem;
        transition: all 0.2s ease;
        border: 1px solid transparent;
    }

    .hero-action.primary {
        color: #041018 !important;
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96));
        border-color: rgba(120,220,225,0.45);
    }

    .hero-action.secondary {
        color: #ffffff !important;
        background: rgba(255,255,255,0.05);
        border-color: rgba(120,145,170,0.22);
    }

    .hero-action:hover,
    .module-cta:hover {
        transform: translateY(-1px);
    }

    .proof-strip {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
    }

    .proof-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.42rem 0.7rem;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(120,145,170,0.16);
        color: var(--soft);
        font-size: 0.82rem;
    }

    .hero-panel {
        background: rgba(20, 35, 50, 0.4);
        border: 1px solid var(--stroke);
        border-radius: var(--radius-xl);
        padding: 1.35rem;
        height: 560px;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        gap: 1rem;
    }

    .panel-title {
        font-size: 0.86rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
        margin-bottom: 0.1rem;
    }

    .mini-stat-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        grid-template-rows: repeat(2, minmax(0, 1fr));
        gap: 0.85rem;
        flex: 1 1 auto;
        align-items: stretch;
    }

    .mini-stat {
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1.08rem 1.08rem;
        min-height: 0;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
    }

    .mini-stat-value {
        font-size: 1.35rem;
        font-weight: 760;
        color: var(--text);
        line-height: 1.1;
    }

    .mini-stat-label {
        margin-top: 0.28rem;
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
    }

    .assistant-teaser {
        margin-top: auto;
        background: linear-gradient(135deg, rgba(56,194,201,0.08), rgba(17,44,63,0.78));
        border: 1px solid rgba(56,194,201,0.22);
        border-radius: 22px;
        padding: 1rem;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
    }

    .assistant-kicker {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.74rem;
        font-weight: 700;
        margin-bottom: 0.55rem;
    }

    .assistant-title {
        font-size: 1.55rem;
        font-weight: 760;
        line-height: 1.15;
        letter-spacing: -0.02em;
        margin-bottom: 0.65rem;
        color: var(--text);
    }

    .assistant-subtitle {
        color: var(--soft);
        line-height: 1.72;
        font-size: 0.96rem;
        margin-bottom: 0.9rem;
    }

    .assistant-teaser .assistant-title {
        font-size: 1.08rem;
        margin-bottom: 0.45rem;
    }

    .assistant-teaser .assistant-subtitle {
        font-size: 0.88rem;
        line-height: 1.6;
        color: var(--muted);
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
        margin-bottom: 0.75rem;
    }

    .assistant-scope {
        color: var(--muted);
        line-height: 1.6;
        font-size: 0.84rem;
        margin-top: 0.55rem;
    }

    .context-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
    }

    .context-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.42rem;
        padding: 0.42rem 0.72rem;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(120,145,170,0.16);
        color: var(--soft);
        font-size: 0.82rem;
    }

    .context-chip span {
        color: var(--muted);
    }

    .context-chip.live {
        border-color: rgba(126,224,129,0.28);
        background: rgba(126,224,129,0.1);
        color: #d8f8de;
    }

    .context-chip.ready {
        border-color: rgba(244,191,79,0.22);
        background: rgba(244,191,79,0.1);
        color: #f6ddb0;
    }

    .assistant-teaser-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 44px;
        padding: 0.75rem 1rem;
        border-radius: 12px;
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96));
        border: 1px solid rgba(120,220,225,0.45);
        color: #041018 !important;
        text-decoration: none !important;
        font-weight: 700;
        margin-top: 0.85rem;
        transition: transform 0.2s ease;
    }

    .assistant-teaser-link:hover {
        transform: translateY(-1px);
    }

    .assistant-dialog-hero {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.25rem 1.2rem 1rem 1.2rem;
        margin-bottom: 1rem;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
    }

    .assistant-dialog-note {
        color: var(--muted);
        line-height: 1.62;
        font-size: 0.86rem;
        margin-top: 0.75rem;
    }

    .example-label {
        color: var(--muted);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        margin-bottom: 0.45rem;
    }

    .example-chip-wrap {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
        margin-bottom: 0.85rem;
    }

    .example-chip {
        display: inline-flex;
        align-items: center;
        padding: 0.46rem 0.72rem;
        border-radius: 999px;
        border: 1px solid rgba(120,145,170,0.16);
        background: rgba(255,255,255,0.03);
        color: var(--soft);
        font-size: 0.82rem;
        line-height: 1.4;
    }

    .assistant-rail {
        position: fixed;
        right: -48px;
        top: 48%;
        transform: translateY(-50%) rotate(-90deg);
        z-index: 999;
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        padding: 0.72rem 1rem;
        border-radius: 16px 16px 0 0;
        background: linear-gradient(135deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(56,194,201,0.26);
        box-shadow: 0 18px 42px rgba(0,0,0,0.32);
        color: #f5f7fb !important;
        text-decoration: none !important;
        font-weight: 650;
        letter-spacing: 0.01em;
        backdrop-filter: blur(14px);
    }

    .assistant-rail:hover {
        transform: translateY(-50%) rotate(-90deg) translateX(4px);
    }

    .assistant-rail-dot {
        width: 8px;
        height: 8px;
        border-radius: 999px;
        background: var(--accent-2);
        box-shadow: 0 0 0 6px rgba(56,194,201,0.12);
    }

    div[data-testid="stForm"] {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important;
        background-color: rgba(12,25,39,0.98) !important;
        border: 1px solid var(--stroke) !important;
        border-radius: 22px !important;
        padding: 1.2rem 1.15rem 1.15rem 1.15rem !important;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
    }

    /* All direct children of stForm */
    div[data-testid="stForm"] > div {
        background: transparent !important;
        background-color: transparent !important;
    }

    div[data-testid="stForm"] label {
        color: var(--soft) !important;
        font-weight: 650 !important;
    }

    div[data-testid="stMultiSelect"] > div,
    div[data-testid="stTextArea"] > div {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: 14px;
    }

    div[data-testid="stMultiSelect"] div[data-baseweb="tag"] {
        background: rgba(56,194,201,0.12) !important;
        border: 1px solid rgba(56,194,201,0.18) !important;
    }

    div[data-testid="stTextArea"] textarea {
        min-height: 130px !important;
        background: rgba(255,255,255,0.02) !important;
        color: var(--text) !important;
        border-radius: 14px !important;
    }

    div[data-testid="stTextArea"] textarea::placeholder {
        color: var(--muted) !important;
    }

    div[data-testid="stFormSubmitButton"] button,
    div.stButton > button {
        width: 100%;
        min-height: 46px;
        border-radius: 12px;
        background: rgba(56,194,201,0.12);
        border: 1px solid rgba(56,194,201,0.28);
        color: #f5f7fb !important;
        font-weight: 650;
        transition: all 0.2s ease;
    }

    div[data-testid="stFormSubmitButton"] button:hover,
    div.stButton > button:hover {
        border-color: rgba(120,220,225,0.42);
        transform: translateY(-1px);
    }

    .assistant-note {
        margin-top: 0.9rem;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: 18px;
        padding: 1rem 1rem 1rem 1rem;
        color: var(--soft);
        line-height: 1.65;
        font-size: 0.92rem;
    }

    .assistant-note strong {
        color: var(--text);
        display: block;
        margin-bottom: 0.25rem;
    }

    div[data-testid="stExpander"] {
        border: 1px solid rgba(120,145,170,0.16) !important;
        border-radius: 18px !important;
        background: rgba(12,24,38,0.98) !important;
        background-color: rgba(12,24,38,0.98) !important;
    }

    /* Expander clickable header row */
    div[data-testid="stExpander"] details > summary,
    div[data-testid="stExpander"] details summary {
        background: rgba(12,24,38,0.98) !important;
        background-color: rgba(12,24,38,0.98) !important;
        border-radius: 18px !important;
        color: #c8d3df !important;
        padding: 0.85rem 1.1rem !important;
    }

    div[data-testid="stExpander"] details[open] > summary {
        border-radius: 18px 18px 0 0 !important;
    }

    div[data-testid="stExpander"] details > summary span,
    div[data-testid="stExpander"] details > summary p,
    div[data-testid="stExpander"] details > summary svg {
        color: #c8d3df !important;
        fill: #9fb0c3 !important;
    }

    /* Expander content area – the inner div Streamlit wraps content in */
    div[data-testid="stExpander"] > div,
    div[data-testid="stExpander"] > div > div,
    div[data-testid="stExpander"] details,
    div[data-testid="stExpander"] details > div,
    div[data-testid="stExpander"] details summary ~ div {
        background: rgba(12,24,38,0.98) !important;
        background-color: rgba(12,24,38,0.98) !important;
    }

    /* Force all text inside expander dark-mode */
    div[data-testid="stExpander"] p,
    div[data-testid="stExpander"] span,
    div[data-testid="stExpander"] label,
    div[data-testid="stExpander"] .stMarkdown,
    div[data-testid="stExpander"] .stCaptionContainer {
        color: #c8d3df !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 18px !important;
        border: 1px solid rgba(120,145,170,0.18) !important;
        background: rgba(255,255,255,0.03);
    }

    .stats-row {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-top: 1.15rem;
    }

    .stat-card {
        background: var(--panel);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1.15rem;
        min-height: 132px;
    }

    .stat-value {
        font-size: 1.35rem;
        font-weight: 750;
        color: var(--text);
        line-height: 1.1;
    }

    .stat-title {
        margin-top: 0.3rem;
        font-size: 0.96rem;
        font-weight: 660;
        color: var(--text);
    }

    .stat-desc {
        margin-top: 0.2rem;
        color: var(--muted);
        font-size: 0.86rem;
        line-height: 1.55;
    }

    .section-head {
        margin-top: 2.25rem;
        margin-bottom: 1rem;
    }

    .section-kicker {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.74rem;
        font-weight: 700;
        margin-bottom: 0.4rem;
    }

    .section-title {
        font-size: 1.86rem;
        font-weight: 750;
        letter-spacing: -0.03em;
        color: var(--text);
        margin: 0;
    }

    .section-subtitle {
        margin-top: 0.35rem;
        color: var(--muted);
        line-height: 1.75;
        max-width: 74ch;
    }

    .trust-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-top: 0.8rem;
    }

    .trust-card {
        background: var(--panel);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1.2rem;
        min-height: 136px;
    }

    .trust-title {
        font-size: 1.05rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .trust-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .loop-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-top: 0.8rem;
    }

    .loop-card {
        background: var(--panel-2);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1.2rem;
        min-height: 172px;
    }

    .loop-number {
        width: 34px;
        height: 34px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        background: rgba(56,194,201,0.12);
        border: 1px solid rgba(56,194,201,0.22);
        color: var(--accent-2);
        font-weight: 700;
        font-size: 0.92rem;
        margin-bottom: 0.8rem;
    }

    .loop-title {
        font-size: 1.05rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .loop-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .subsection-title {
        margin-top: 1.5rem;
        margin-bottom: 0.9rem;
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--text);
    }

    .module-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 1.2rem;
        align-items: stretch;
        margin-top: 0.8rem;
    }

    .module-grid-two {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .module-card {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.25rem;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
        display: flex;
        flex-direction: column;
        min-height: 360px;
        height: 100%;
    }

    .module-card:hover {
        border-color: rgba(56,194,201,0.24);
        box-shadow: 0 16px 42px rgba(0,0,0,0.24);
    }

    .module-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 1rem;
    }

    .module-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0.32rem 0.62rem;
        border-radius: 999px;
        border: 1px solid rgba(120,145,170,0.18);
        background: rgba(255,255,255,0.03);
        color: var(--muted);
        font-size: 0.75rem;
        font-weight: 650;
        letter-spacing: 0.01em;
        white-space: nowrap;
    }

    .badge-priority { color: #8ef0c0; border-color: rgba(142,240,192,0.25); background: rgba(126,224,129,0.08); }
    .badge-phase2 { color: #9fe7ff; border-color: rgba(120,220,225,0.22); background: rgba(56,194,201,0.08); }
    .badge-early { color: #d7def7; border-color: rgba(215,222,247,0.18); background: rgba(255,255,255,0.03); }
    .badge-roadmap { color: #f4bf4f; border-color: rgba(244,191,79,0.22); background: rgba(244,191,79,0.08); }

    .module-icon {
        width: 46px;
        height: 46px;
        border-radius: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.18);
        color: var(--accent-2);
        font-size: 1.32rem;
        flex-shrink: 0;
    }

    .module-title {
        font-size: 1.08rem;
        font-weight: 720;
        line-height: 1.35;
        color: var(--text);
        margin-bottom: 0.5rem;
    }

    .module-desc {
        font-size: 0.95rem;
        line-height: 1.72;
        color: var(--muted);
        min-height: 80px;
    }

    .module-meta {
        font-size: 0.86rem;
        line-height: 1.75;
        color: var(--soft);
        padding-top: 0.95rem;
        border-top: 1px solid rgba(120,145,170,0.14);
        min-height: 65px;
    }

    .module-spacer {
        flex: 1;
        min-height: 1rem;
    }

    .module-cta-wrap {
        margin-top: 1rem;
    }

    .module-cta {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        min-height: 46px;
        padding: 0.78rem 1rem;
        border-radius: 12px;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.28);
        color: #f5f7fb !important;
        text-decoration: none !important;
        font-weight: 650;
        font-size: 0.94rem;
        transition: all 0.2s ease;
    }

    .cta-band {
        margin-top: 4rem;
        background: linear-gradient(135deg, rgba(56,194,201,0.08), rgba(18,49,76,0.3));
        border: 1px solid rgba(56,194,201,0.2);
        border-radius: 20px;
        padding: 2.5rem 3rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 2rem;
    }

    .cta-text-wrapper {
        max-width: 65ch;
    }

    .cta-title {
        font-size: 1.45rem;
        font-weight: 750;
        color: var(--text);
        margin-bottom: 0.5rem;
    }

    .cta-desc {
        color: var(--muted);
        line-height: 1.6;
        font-size: 1rem;
    }

    .cta-actions {
        display: flex;
        gap: 1rem;
        flex-shrink: 0;
    }

    .integration-footer-callout {
        margin-top: 1.35rem;
        text-align: center;
    }

    .integration-footer-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 0.35rem;
        color: rgba(192, 206, 223, 0.78) !important;
        font-size: 0.92rem;
        text-decoration: none !important;
        border-bottom: 1px solid rgba(112, 214, 220, 0.18);
        padding-bottom: 0.1rem;
        transition: color 0.18s ease, border-color 0.18s ease;
    }

    .integration-footer-link:hover {
        color: #f5f7fb !important;
        border-color: rgba(112, 214, 220, 0.36);
    }

    .integration-close-link {
        color: rgba(159,176,195,0.82) !important;
        font-size: 0.85rem;
        text-decoration: none !important;
    }

    .integration-close-link:hover {
        color: #f5f7fb !important;
    }

    .footer-block {
        text-align: center;
        margin-top: 2.1rem;
        padding-top: 1.5rem;
        padding-bottom: 1rem;
        border-top: 1px solid rgba(120,145,170,0.15);
    }

    .footer-copy {
        color: var(--muted);
        font-size: 0.9rem;
        margin-bottom: 0.35rem;
    }

    .footer-meta {
        color: rgba(159,176,195,0.55);
        font-size: 0.8rem;
    }

    @media (max-width: 1180px) {
        .top-shell {
            flex-direction: column;
            align-items: flex-start;
        }
        .mini-stat-grid,
        .stats-row,
        .trust-grid,
        .loop-grid,
        .module-grid,
        .module-grid-two {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 900px) {
        .cta-band {
            flex-direction: column;
            align-items: flex-start;
            padding: 2rem;
        }
        .cta-actions {
            margin-top: 1rem;
            flex-wrap: wrap;
        }
    }

    @media (max-width: 900px) {
        .assistant-rail {
            display: none;
        }
        .hero-panel {
            height: auto;
        }
    }

    @media (max-width: 760px) {
        .mini-stat-grid,
        .stats-row,
        .trust-grid,
        .loop-grid,
        .module-grid,
        .module-grid-two {
            grid-template-columns: 1fr;
        }
        .hero,
        .hero-panel {
            min-height: auto;
            height: auto;
        }
        .brand-logo {
            height: 60px;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<style>
    .assistant-teaser {
        margin-top: auto;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: 20px;
        padding: 1rem 1rem 0.95rem 1rem;
        box-shadow: none;
    }

    .assistant-teaser-row {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 1rem;
    }

    .assistant-teaser-copy {
        flex: 1 1 auto;
        min-width: 0;
    }

    .assistant-teaser .assistant-title {
        font-size: 1.02rem;
        margin-bottom: 0.32rem;
    }

    .assistant-teaser .assistant-subtitle {
        font-size: 0.88rem;
        line-height: 1.58;
        color: var(--muted);
        margin-bottom: 0;
        display: block;
    }

    .assistant-teaser-link.compact {
        margin-top: 0;
        min-height: 42px;
        padding: 0.7rem 0.95rem;
        border-radius: 12px;
        white-space: nowrap;
        background: rgba(56,194,201,0.12);
        border: 1px solid rgba(56,194,201,0.24);
        color: var(--text) !important;
        flex-shrink: 0;
    }

    .assistant-teaser-link.compact:hover {
        background: rgba(56,194,201,0.16);
    }

    .assistant-teaser-foot {
        margin-top: 0.72rem;
        padding-top: 0.72rem;
        border-top: 1px solid rgba(120,145,170,0.12);
        color: var(--muted);
        font-size: 0.82rem;
        line-height: 1.55;
    }

    .context-chips.compact {
        margin-top: 0.05rem;
    }

    div[data-testid="stDialog"] {
        backdrop-filter: blur(6px);
    }

    div[data-testid="stDialog"] [role="dialog"] {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important;
        border: 1px solid rgba(120,145,170,0.18) !important;
        border-radius: 26px !important;
        box-shadow: 0 32px 90px rgba(0,0,0,0.44) !important;
        color: var(--text) !important;
        overflow: hidden !important;
    }

    div[data-testid="stDialog"] [data-baseweb="modal"],
    div[data-testid="stDialog"] [data-baseweb="modal-header"],
    div[data-testid="stDialog"] [data-baseweb="modal-body"],
    div[data-testid="stDialog"] [data-testid="stDialogContent"] {
        background: transparent !important;
        color: var(--text) !important;
    }

    div[data-testid="stDialog"] [data-baseweb="modal-header"] {
        border-bottom: 1px solid rgba(120,145,170,0.12);
    }

    div[data-testid="stDialog"] div[data-testid="stForm"] {
        background: rgba(255,255,255,0.03) !important;
        border: 1px solid rgba(120,145,170,0.16) !important;
        border-radius: 22px !important;
        padding: 1.1rem 1.05rem 1.05rem 1.05rem !important;
        box-shadow: none !important;
    }

    div[data-testid="stDialog"] div[data-testid="stForm"] label,
    div[data-testid="stDialog"] div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stDialog"] div[data-testid="stMarkdownContainer"] li,
    div[data-testid="stDialog"] div[data-testid="stCaptionContainer"],
    div[data-testid="stDialog"] .stCaptionContainer {
        color: var(--soft) !important;
    }

    div[data-testid="stDialog"] div[data-testid="stMarkdownContainer"] strong,
    div[data-testid="stDialog"] h1,
    div[data-testid="stDialog"] h2,
    div[data-testid="stDialog"] h3,
    div[data-testid="stDialog"] div[data-testid="stMarkdownContainer"] p strong {
        color: var(--text) !important;
    }

    div[data-testid="stDialog"] div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 18px !important;
        border: 1px solid rgba(120,145,170,0.16) !important;
        background: rgba(255,255,255,0.03) !important;
    }

    div[data-testid="stDialog"] div[data-testid="stMultiSelect"] > div,
    div[data-testid="stDialog"] div[data-testid="stTextArea"] > div {
        background: transparent !important;
        border: 0 !important;
    }

    div[data-testid="stDialog"] div[data-baseweb="select"] > div,
    div[data-testid="stDialog"] div[data-baseweb="base-input"] > div,
    div[data-testid="stDialog"] div[data-baseweb="textarea"] > div,
    div[data-testid="stDialog"] div[data-testid="stTextArea"] textarea {
        background: rgba(255,255,255,0.03) !important;
        border: 1px solid rgba(120,145,170,0.16) !important;
        color: var(--text) !important;
    }

    div[data-testid="stDialog"] input,
    div[data-testid="stDialog"] textarea,
    div[data-testid="stDialog"] div[data-baseweb="select"] input {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        caret-color: var(--accent-2) !important;
    }

    div[data-testid="stDialog"] input::placeholder,
    div[data-testid="stDialog"] textarea::placeholder {
        color: var(--muted) !important;
    }

    div[data-testid="stDialog"] div[data-baseweb="select"] > div,
    div[data-testid="stDialog"] div[data-baseweb="base-input"] > div,
    div[data-testid="stDialog"] div[data-baseweb="textarea"] {
        background: linear-gradient(180deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96)) !important;
        border-radius: 14px !important;
    }

    div[data-testid="stDialog"] div[data-baseweb="textarea"] textarea {
        background: transparent !important;
    }

    div[data-testid="stDialog"] div[data-baseweb="tag"] {
        background: rgba(56,194,201,0.12) !important;
        border: 1px solid rgba(56,194,201,0.2) !important;
    }

    div[data-testid="stDialog"] div[data-baseweb="tag"] span {
        color: var(--text) !important;
    }

    div[data-testid="stDialog"] div.stButton > button,
    div[data-testid="stDialog"] div[data-testid="stFormSubmitButton"] button {
        background: rgba(56,194,201,0.12) !important;
        border: 1px solid rgba(56,194,201,0.28) !important;
        color: var(--text) !important;
    }

    div[data-testid="stDialog"] div.stButton > button:hover,
    div[data-testid="stDialog"] div[data-testid="stFormSubmitButton"] button:hover {
        background: rgba(56,194,201,0.16) !important;
        border-color: rgba(120,220,225,0.42) !important;
        transform: translateY(-1px);
    }

    div[data-baseweb="popover"],
    div[data-baseweb="menu"] {
        background: transparent !important;
        border: 0 !important;
        color: var(--text) !important;
        box-shadow: none !important;
    }

    div[data-baseweb="popover"] > div,
    div[data-baseweb="menu"] > div,
    div[data-baseweb="popover"] ul,
    div[data-baseweb="menu"] ul,
    div[data-baseweb="popover"] [role="listbox"],
    div[data-baseweb="menu"] [role="listbox"] {
        background: linear-gradient(180deg, rgba(10,22,35,0.99), rgba(7,16,24,0.99)) !important;
        border: 1px solid rgba(120,145,170,0.2) !important;
        color: var(--text) !important;
        border-radius: 18px !important;
        box-shadow: 0 20px 40px rgba(0,0,0,0.34) !important;
        overflow: hidden !important;
    }

    div[data-baseweb="popover"] *,
    div[data-baseweb="menu"] *,
    ul[role="listbox"] *,
    li[role="option"] * {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    div[data-baseweb="popover"] li,
    div[data-baseweb="menu"] li,
    li[role="option"] {
        background: transparent !important;
        border-radius: 12px !important;
        margin: 0.15rem 0 !important;
    }

    div[data-baseweb="popover"] li > div,
    div[data-baseweb="menu"] li > div,
    li[role="option"] > div {
        background: transparent !important;
    }

    div[data-baseweb="popover"] li[aria-selected="true"],
    div[data-baseweb="popover"] li:hover,
    div[data-baseweb="menu"] li[aria-selected="true"],
    div[data-baseweb="menu"] li:hover,
    li[role="option"][aria-selected="true"],
    li[role="option"]:hover {
        background: rgba(56,194,201,0.14) !important;
    }

    div[data-testid="stTextInput"] > div {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: 14px;
    }

    div[data-testid="stTextInput"] input {
        background: transparent !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border-radius: 14px !important;
    }

    div[data-testid="stTextInput"] input::placeholder {
        color: var(--muted) !important;
        -webkit-text-fill-color: var(--muted) !important;
    }

    div[data-testid="stTextInput"] div[data-baseweb="base-input"],
    div[data-testid="stTextInput"] div[data-baseweb="input"] {
        background-color: transparent !important;
        background: transparent !important;
    }

    div[data-testid="stExpander"] div[data-testid="stTextInput"] > div,
    div[data-testid="stForm"] div[data-testid="stTextInput"] > div {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(120,145,170,0.22) !important;
    }

    div[data-testid="stExpander"] div[data-testid="stTextInput"] input,
    div[data-testid="stForm"] div[data-testid="stTextInput"] input {
        background: transparent !important;
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
    }

    div[data-testid="stExpander"] div[data-testid="stTextArea"] textarea,
    div[data-testid="stForm"] div[data-testid="stTextArea"] textarea {
        background: transparent !important;
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
    }

    div[data-testid="stExpander"] div[data-baseweb="base-input"],
    div[data-testid="stExpander"] div[data-baseweb="input"],
    div[data-testid="stForm"] div[data-baseweb="base-input"],
    div[data-testid="stForm"] div[data-baseweb="input"] {
        background-color: transparent !important;
        background: transparent !important;
    }

    /* Contact form – nuclear fix for white-on-white inputs */
    div[data-testid="stExpander"] input,
    div[data-testid="stExpander"] textarea,
    div[data-testid="stForm"] input,
    div[data-testid="stForm"] textarea {
        background: rgba(255,255,255,0.03) !important;
        background-color: rgba(255,255,255,0.03) !important;
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
        caret-color: #38c2c9 !important;
    }

    div[data-testid="stExpander"] input:-webkit-autofill,
    div[data-testid="stExpander"] textarea:-webkit-autofill,
    div[data-testid="stForm"] input:-webkit-autofill,
    div[data-testid="stForm"] textarea:-webkit-autofill {
        -webkit-box-shadow: 0 0 0px 1000px rgba(12,25,39,0.98) inset !important;
        -webkit-text-fill-color: #f5f7fb !important;
        caret-color: #38c2c9 !important;
    }

    div[data-testid="stExpander"] input::placeholder,
    div[data-testid="stExpander"] textarea::placeholder,
    div[data-testid="stForm"] input::placeholder,
    div[data-testid="stForm"] textarea::placeholder {
        color: rgba(159,176,195,0.7) !important;
        -webkit-text-fill-color: rgba(159,176,195,0.7) !important;
    }

    .access-gate-head {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.3rem 1.2rem 1.05rem 1.2rem;
        margin-bottom: 1rem;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
    }

    .access-gate-title {
        font-size: 1.7rem;
        font-weight: 760;
        line-height: 1.12;
        letter-spacing: -0.02em;
        margin-bottom: 0.55rem;
        color: var(--text);
    }

    .access-gate-subtitle {
        color: var(--soft);
        line-height: 1.72;
        font-size: 0.98rem;
        margin-bottom: 0.85rem;
    }

    @media (max-width: 1100px) {
        .assistant-teaser-row {
            flex-direction: column;
            align-items: stretch;
        }

        .assistant-teaser-link.compact {
            width: 100%;
            justify-content: center;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 7) TOP BAR
# -------------------------------------------------
apply_language_from_query()

top_l, top_m, top_r = st.columns([4, 1, 1], gap="small")

with top_l:
    logo_uri = logo_data_uri()
    logo_html = f'<img src="{logo_uri}" class="brand-logo" alt="Builtly logo" />' if logo_uri else '<div class="brand-name">Builtly</div>'
    render_html(f'<div class="top-shell" style="margin-bottom: 0;"><div class="brand-left"><a href="/" target="_self" style="text-decoration:none;">{logo_html}</a></div></div>')

with top_m:
    st.markdown("<div style='margin-top: 1.25rem;'></div>", unsafe_allow_html=True)
    if _is_user_logged_in():
        _acct_options = ["👤 Konto", "Min side", "Logg ut"]
        _acct_choice = st.selectbox("Konto", _acct_options, index=0, label_visibility="collapsed", key="acct_menu")
        if _acct_choice == "Min side":
            try:
                st.query_params["auth"] = "dashboard"
            except Exception:
                pass
            st.rerun()
        elif _acct_choice == "Logg ut":
            if _HAS_AUTH:
                builtly_auth.logout()
            else:
                st.session_state.user_authenticated = False
                st.session_state.user_email = ""
                st.session_state.user_name = ""
                st.session_state.user_plan = ""
                st.session_state.user_reports = []
            try:
                params = get_query_params_dict()
                params.pop("auth", None)
                set_query_params_dict(params)
            except Exception:
                pass
            st.rerun()
    else:
        _acct_options = ["👤 Konto", "Logg inn", "Opprett konto"]
        _acct_choice = st.selectbox("Konto", _acct_options, index=0, label_visibility="collapsed", key="acct_menu")
        if _acct_choice == "Logg inn":
            try:
                st.query_params["auth"] = "login"
            except Exception:
                pass
            st.rerun()
        elif _acct_choice == "Opprett konto":
            try:
                st.query_params["auth"] = "register"
            except Exception:
                pass
            st.rerun()

with top_r:
    st.markdown("<div style='margin-top: 1.25rem;'></div>", unsafe_allow_html=True)
    chosen_language = st.selectbox(
        "Språk",
        list(TEXTS.keys()),
        index=list(TEXTS.keys()).index(st.session_state.app_lang) if st.session_state.app_lang in TEXTS else 0,
    )
    if chosen_language != st.session_state.app_lang:
        st.session_state.app_lang = chosen_language
        st.session_state.project_data["land"] = get_locale_profile(chosen_language)["project_land_label"]
        reset_assistant_conversation()
        st.session_state.assistant_discipline_codes = list(DEFAULT_DISCIPLINES)
        sync_language_query_param(chosen_language)
        st.rerun()

lang = get_text_bundle(st.session_state.app_lang)
locale_profile = get_locale_profile(st.session_state.app_lang)
for key, value in MODULE_COPY_OVERRIDES.get("default", {}).items():
    lang.setdefault(key, value)
for key, value in MODULE_COPY_OVERRIDES.get(st.session_state.app_lang, {}).items():
    lang[key] = value

st.markdown("<div style='margin-bottom: 2rem;'></div>", unsafe_allow_html=True)

# Restore session from URL token if present (silent, no st.stop)
if not st.session_state.get("site_access_granted"):
    _restore_session_from_url()

# If user clicked a module card while unauthenticated, show gate dialog
_gate_dest = get_query_params_dict().get("gate", "").strip()
if _gate_dest and access_gate_enabled() and not st.session_state.get("site_access_granted"):
    _module_gate_dialog(st.session_state.app_lang, _gate_dest)

# Auth page routing (login, register, plans, dashboard, demo)
if handle_auth_routing(st.session_state.app_lang):
    st.stop()

# -------------------------------------------------
# 8) HERO + ASSISTANT ENTRYPOINT
# -------------------------------------------------
if assistant_query_requested():
    open_assistant()
    clear_assistant_query_param()

render_html(
    f"""
    <a href="{assistant_href(st.session_state.app_lang)}" target="_self" class="assistant-rail">
        <span class="assistant-rail-dot"></span>{lang['assistant_btn']}
    </a>
    """
)

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    render_html(
        f"""
        <div class="hero">
            <div class="eyebrow">{lang['eyebrow']}</div>
            <h1 class="hero-title">{lang['title']}</h1>
            <div class="hero-subtitle">{lang['subtitle']}</div>
            <div class="hero-actions">
                {hero_action('project', lang['btn_setup'], 'primary')}
                {hero_action('review', lang['btn_qa'], 'secondary')}
            </div>
            <div class="proof-strip">
                {''.join([f'<div class="proof-chip">{proof}</div>' for proof in lang['proofs']])}
            </div>
        </div>
        """
    )

with right:
    render_html(
        f"""
        <div class="hero-panel">
            <div class="panel-title">{lang['why_kicker']}</div>
            <div class="mini-stat-grid">
                <div class="mini-stat">
                    <div class="mini-stat-value">{lang['stat1_v']}</div>
                    <div class="mini-stat-label"><b>{lang['stat1_t']}</b><br>{lang['stat1_d']}</div>
                </div>
                <div class="mini-stat">
                    <div class="mini-stat-value">{lang['stat2_v']}</div>
                    <div class="mini-stat-label"><b>{lang['stat2_t']}</b><br>{lang['stat2_d']}</div>
                </div>
                <div class="mini-stat">
                    <div class="mini-stat-value">{lang['stat3_v']}</div>
                    <div class="mini-stat-label"><b>{lang['stat3_t']}</b><br>{lang['stat3_d']}</div>
                </div>
                <div class="mini-stat">
                    <div class="mini-stat-value">{lang['stat4_v']}</div>
                    <div class="mini-stat-label"><b>{lang['stat4_t']}</b><br>{lang['stat4_d']}</div>
                </div>
            </div>
        </div>
        """
    )

maybe_render_assistant_dialog(st.session_state.app_lang)

# -------------------------------------------------
# 10) CORE VALUE PROPOSITION & WORKFLOW
# -------------------------------------------------
render_html(
    f"""
    <div class="section-head">
        <div class="section-kicker">{lang['sec_val_kicker']}</div>
        <h2 class="section-title">{lang['sec_val_title']}</h2>
        <div class="section-subtitle">{lang['sec_val_sub']}</div>
    </div>
    <div class="trust-grid">
        <div class="trust-card"><div class="trust-title">{lang['val_1_t']}</div><div class="trust-desc">{lang['val_1_d']}</div></div>
        <div class="trust-card"><div class="trust-title">{lang['val_2_t']}</div><div class="trust-desc">{lang['val_2_d']}</div></div>
        <div class="trust-card"><div class="trust-title">{lang['val_3_t']}</div><div class="trust-desc">{lang['val_3_d']}</div></div>
        <div class="trust-card"><div class="trust-title">{lang['val_4_t']}</div><div class="trust-desc">{lang['val_4_d']}</div></div>
    </div>

    <div class="section-head">
        <div class="section-kicker">{lang['sec_loop_kicker']}</div>
        <h2 class="section-title">{lang['sec_loop_title']}</h2>
        <div class="section-subtitle">{lang['sec_loop_sub']}</div>
    </div>
    <div class="loop-grid">
        <div class="loop-card"><div class="loop-number">1</div><div class="loop-title">{lang['loop_1_t']}</div><div class="loop-desc">{lang['loop_1_d']}</div></div>
        <div class="loop-card"><div class="loop-number">2</div><div class="loop-title">{lang['loop_2_t']}</div><div class="loop-desc">{lang['loop_2_d']}</div></div>
        <div class="loop-card"><div class="loop-number">3</div><div class="loop-title">{lang['loop_3_t']}</div><div class="loop-desc">{lang['loop_3_d']}</div></div>
        <div class="loop-card"><div class="loop-number">4</div><div class="loop-title">{lang['loop_4_t']}</div><div class="loop-desc">{lang['loop_4_d']}</div></div>
    </div>
    """
)

# -------------------------------------------------
# 11) MODULES
# -------------------------------------------------
available_cards = [
    module_card("geo", "🌍", lang["badge_geo"], "badge-priority", lang["m_geo_t"], lang["m_geo_d"], lang["m_geo_in"], lang["m_geo_out"], lang["m_geo_btn"]),
    module_card("akustikk", "🔊", lang["badge_acoustics"], "badge-phase2", lang["m_aku_t"], lang["m_aku_d"], lang["m_aku_in"], lang["m_aku_out"], lang["m_aku_btn"]),
    module_card("brann", "🔥", lang["badge_fire"], "badge-phase2", lang["m_brann_t"], lang["m_brann_d"], lang["m_brann_in"], lang["m_brann_out"], lang["m_brann_btn"]),
]

roadmap_cards = [
    module_card("mulighetsstudie", "📐", lang["badge_feasibility"], "badge-early", lang["m_ark_t"], lang["m_ark_d"], lang["m_ark_in"], lang["m_ark_out"], lang["m_ark_btn"]),
    module_card("konstruksjon", "🏢", lang["badge_structural"], "badge-roadmap", lang["m_rib_t"], lang["m_rib_d"], lang["m_rib_in"], lang["m_rib_out"], lang["m_rib_btn"]),
    module_card("trafikk", "🚦", lang["badge_traffic"], "badge-roadmap", lang["m_tra_t"], lang["m_tra_d"], lang["m_tra_in"], lang["m_tra_out"], lang["m_tra_btn"]),
]

sustainability_cards = [
    module_card("sha", "🦺", lang["badge_sha"], "badge-priority", lang["m_sha_t"], lang["m_sha_d"], lang["m_sha_in"], lang["m_sha_out"], lang["m_sha_btn"]),
    module_card("breeam", "🌿", lang["badge_breeam"], "badge-phase2", lang["m_breeam_t"], lang["m_breeam_d"], lang["m_breeam_in"], lang["m_breeam_out"], lang["m_breeam_btn"]),
    module_card("mop", "♻️", lang["badge_mop"], "badge-roadmap", lang["m_mop_t"], lang["m_mop_d"], lang["m_mop_in"], lang["m_mop_out"], lang["m_mop_btn"]),
]

commercial_cards = [
    module_card("tender_control", "📑", lang["badge_tender"], "badge-priority", lang["m_tender_t"], lang["m_tender_d"], lang["m_tender_in"], lang["m_tender_out"], lang["m_tender_btn"]),
    module_card("quantity_scope", "📏", lang["badge_quantity"], "badge-phase2", lang["m_quantity_t"], lang["m_quantity_d"], lang["m_quantity_in"], lang["m_quantity_out"], lang["m_quantity_btn"]),
    module_card("yield_optimizer", "🏙️", lang["badge_yield"], "badge-early", lang["m_yield_t"], lang["m_yield_d"], lang["m_yield_in"], lang["m_yield_out"], lang["m_yield_btn"]),
]

platform_cards = [
    module_card("climate_risk", "🌊", lang["badge_climate"], "badge-phase2", lang["m_climate_t"], lang["m_climate_d"], lang["m_climate_in"], lang["m_climate_out"], lang["m_climate_btn"]),
    module_card("tdd", "🏦", lang["badge_tdd"], "badge-phase2", lang["m_tdd_t"], lang["m_tdd_d"], lang["m_tdd_in"], lang["m_tdd_out"], lang["m_tdd_btn"]),
]

bank_cards = [
    module_card("byggelanskontroll", "🏗️", lang["badge_byggelanskontroll"], "badge-phase2", lang["m_byggelanskontroll_t"], lang["m_byggelanskontroll_d"], lang["m_byggelanskontroll_in"], lang["m_byggelanskontroll_out"], lang["m_byggelanskontroll_btn"]),
    module_card("kredittgrunnlag", "📋", lang["badge_kredittgrunnlag"], "badge-phase2", lang["m_kredittgrunnlag_t"], lang["m_kredittgrunnlag_d"], lang["m_kredittgrunnlag_in"], lang["m_kredittgrunnlag_out"], lang["m_kredittgrunnlag_btn"]),
]

render_html(
    f"""
    <div class="section-head">
        <div class="section-kicker">{lang['mod_sec_kicker']}</div>
        <h2 class="section-title">{lang['mod_sec_title']}</h2>
        <div class="section-subtitle">{lang['mod_sec_sub']}</div>
    </div>

    <div class="subsection-title">{lang['mod_sec1']}</div>
    <div class="module-grid">{''.join(available_cards)}</div>

    <div class="subsection-title">{lang['mod_sec2']}</div>
    <div class="module-grid">{''.join(roadmap_cards)}</div>

    <div class="subsection-title" style="margin-top: 2.5rem;">{lang['mod_sec3']}</div>
    <div class="section-subtitle" style="margin-top: -0.5rem; margin-bottom: 1rem;">{lang['mod_sec3_sub']}</div>
    <div class="module-grid">{''.join(sustainability_cards)}</div>

    <div class="subsection-title" style="margin-top: 2.5rem;">{lang['mod_sec4']}</div>
    <div class="section-subtitle" style="margin-top: -0.5rem; margin-bottom: 1rem;">{lang['mod_sec4_sub']}</div>
    <div class="module-grid">{''.join(commercial_cards)}</div>

    <div class="subsection-title" style="margin-top: 2.5rem;">{lang['mod_sec5']}</div>
    <div class="section-subtitle" style="margin-top: -0.5rem; margin-bottom: 1rem;">{lang['mod_sec5_sub']}</div>
    <div class="module-grid module-grid-two">{''.join(platform_cards)}</div>

    <div class="subsection-title" style="margin-top: 2.5rem;">{lang['mod_sec6']}</div>
    <div class="section-subtitle" style="margin-top: -0.5rem; margin-bottom: 1rem;">{lang['mod_sec6_sub']}</div>
    <div class="module-grid module-grid-two">{''.join(bank_cards)}</div>
    """
)

# -------------------------------------------------
# 12) CTA BAND
# -------------------------------------------------
render_html(
    f"""
    <div class="cta-band">
        <div class="cta-text-wrapper">
            <div class="cta-title">{lang['cta_title']}</div>
            <div class="cta-desc">{lang['cta_desc']}</div>
        </div>
        <div class="cta-actions">
            {hero_action('project', lang['cta_btn1'], 'primary')}
            {hero_action('review', lang['cta_btn2'], 'secondary')}
            <a href="?auth=plans" target="_self" class="hero-action secondary">Se priser</a>
        </div>
    </div>
    """
)

# -------------------------------------------------
# 13) CONTACT LINK + CONTACT FORM
# -------------------------------------------------

# Anchor element that the scroll targets
render_html('<div id="contact-form-anchor" style="position:relative;top:-100px;height:1px;pointer-events:none;"></div>')

# Render the partner link visually as normal HTML
render_html(
    f"""
    <div class="integration-footer-callout">
        <a href="{contact_href(st.session_state.app_lang)}" target="_self" class="integration-footer-link">{lang['partner_line']}</a>
    </div>
    """
)

# Hidden component: attaches onclick to the link (sets sessionStorage flag)
# and on page load checks if flag is set and scrolls to contact form
st.components.v1.html(
    """<script>
    (function() {
        // Check sessionStorage on every load
        if (sessionStorage.getItem('builtly_scroll_contact') === '1') {
            sessionStorage.removeItem('builtly_scroll_contact');
            function scrollToContact(n) {
                try {
                    var doc = window.parent.document;
                    var el = doc.getElementById('contact-form-anchor');
                    if (!el) {
                        var exps = doc.querySelectorAll('[data-testid="stExpander"]');
                        if (exps.length) el = exps[exps.length - 1];
                    }
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    } else if (n > 0) {
                        setTimeout(function() { scrollToContact(n - 1); }, 250);
                    }
                } catch(e) {
                    if (n > 0) setTimeout(function() { scrollToContact(n - 1); }, 250);
                }
            }
            setTimeout(function() { scrollToContact(12); }, 400);
        }

        // Attach onclick to the partner link in parent document
        function attachClickHandler(n) {
            try {
                var doc = window.parent.document;
                var link = doc.querySelector('.integration-footer-link');
                if (link) {
                    link.addEventListener('click', function() {
                        sessionStorage.setItem('builtly_scroll_contact', '1');
                    });
                } else if (n > 0) {
                    setTimeout(function() { attachClickHandler(n - 1); }, 200);
                }
            } catch(e) {}
        }
        setTimeout(function() { attachClickHandler(10); }, 300);
    })();
    </script>""",
    height=0,
)

if contact_query_requested():
    with st.expander(lang["contact_form_title"], expanded=True):
        col_info, col_close = st.columns([0.82, 0.18], gap="small")
        with col_info:
            st.caption(lang["contact_form_sub"])
            st.caption(lang["contact_direct_email"].format(email=contact_recipient()))
        with col_close:
            render_html(
                f'<div style="text-align:right; padding-top: 0.4rem;"><a href="{contact_close_href(st.session_state.app_lang)}" target="_self" class="integration-close-link">{lang["contact_close"]}</a></div>'
            )

        fallback_mailto = None
        with st.form("builtly_integration_contact_form", clear_on_submit=True):
            col_a, col_b = st.columns(2, gap="medium")
            with col_a:
                contact_name = st.text_input(lang["contact_name"])
                contact_company = st.text_input(lang["contact_company"])
            with col_b:
                contact_email = st.text_input(lang["contact_email"])
            contact_message = st.text_area(lang["contact_message"], height=160)
            contact_submit = st.form_submit_button(lang["contact_send"], use_container_width=True)

        if contact_submit:
            stripped_name = contact_name.strip()
            stripped_company = contact_company.strip()
            stripped_email = contact_email.strip()
            stripped_message = contact_message.strip()
            email_ok = bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", stripped_email))

            if not stripped_name or not stripped_email or not stripped_message:
                st.error(lang["contact_missing_fields"])
            elif not email_ok:
                st.error(lang["contact_invalid_email"])
            else:
                sent, fallback_mailto = send_contact_email(
                    name=stripped_name,
                    email=stripped_email,
                    company=stripped_company,
                    message=stripped_message,
                    lang_bundle=lang,
                )
                if sent:
                    st.success(lang["contact_success"])
                else:
                    st.info(lang["contact_fallback"])
                    if fallback_mailto:
                        render_html(
                            f'<div style="margin-top:0.5rem; margin-bottom:0.25rem;"><a href="{html.escape(fallback_mailto, quote=True)}" class="module-cta">{lang["contact_fallback_button"]}</a></div>'
                        )

# -------------------------------------------------
# 14) FOOTER
# -------------------------------------------------
render_html(
    f"""
    <div class="footer-block">
        <div class="footer-copy">{lang['footer_copy']}</div>
        <div class="footer-meta">{lang['footer_meta']}</div>
    </div>
    """
)
