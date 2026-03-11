import os
import base64
import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import streamlit as st

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

ASSISTANT_END_MARKER = "[[BUILTLY_DONE]]"

# -------------------------------------------------
# 3) LANGUAGE TEXTS & REGULATORY PROFILES
# -------------------------------------------------
TEXTS = {'🇬🇧 English (UK)': {'rule_set': 'United Kingdom (Building Regulations / Approved Documents)',
                     'eyebrow': 'The Builtly Loop',
                     'title': "From <span class='accent'>raw data</span> to signed deliverables.",
                     'subtitle': 'Builtly is the customer portal for compliance-grade engineering delivery. Upload project inputs, let the '
                                 'platform validate, calculate, check rules, and draft the report - before junior QA and senior sign-off '
                                 'turn it into a consistent, traceable, submission-ready package.',
                     'btn_setup': 'Open project setup',
                     'btn_qa': 'Open QA and sign-off',
                     'proofs': ['Rules-first', 'Audit trail', 'PDF + DOCX output', 'Digital sign-off', 'Structured QA workflow'],
                     'why_kicker': 'Why Builtly?',
                     'stat1_v': '80-90%',
                     'stat1_t': 'Reduction in manual drafting',
                     'stat1_d': 'and repetitive report production',
                     'stat2_v': 'Junior + Senior',
                     'stat2_t': 'Human-in-the-loop QA',
                     'stat2_d': 'technical control, and digital sign-off',
                     'stat3_v': 'PDF + DOCX',
                     'stat3_t': 'Complete report packages',
                     'stat3_d': 'with appendices and traceability',
                     'stat4_v': 'Full Traceability',
                     'stat4_t': 'End-to-end logging',
                     'stat4_d': 'Inputs, versions, compliance checks logged',
                     'sec_val_kicker': 'Core value proposition',
                     'sec_val_title': 'Portal first. Modules under.',
                     'sec_val_sub': 'Builtly is not a collection of disconnected tools. It is one secure portal for project setup, data '
                                    'ingestion, validation, AI processing, review, sign-off, and final delivery.',
                     'val_1_t': 'Client portal',
                     'val_1_d': 'Project creation, input uploads, missing-data follow-up, document generation, and audit trails in one '
                                'workflow.',
                     'val_2_t': 'Rules-first AI',
                     'val_2_d': 'AI operates inside explicit regulatory guardrails, checklists, and standard templates - not as free-form '
                                'guesswork.',
                     'val_3_t': 'QA and sign-off',
                     'val_3_d': 'Junior engineers validate plausibility and structure. Senior engineers provide final review and '
                                'certification.',
                     'val_4_t': 'Scalable delivery',
                     'val_4_d': 'Each new engineering discipline plugs into the same validation, documentation, and sign-off backbone.',
                     'sec_loop_kicker': 'Workflow',
                     'sec_loop_title': 'The Builtly Loop',
                     'sec_loop_sub': 'A deterministic four-step workflow that takes you from fragmented project data to a reviewable, '
                                     'compliant engineering package.',
                     'loop_1_t': 'Input',
                     'loop_1_d': 'Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place.',
                     'loop_2_t': 'Validate and analyze',
                     'loop_2_d': 'The platform parses, validates, applies local rule checks, performs calculations, and drafts the '
                                 'deliverable.',
                     'loop_3_t': 'QA and sign-off',
                     'loop_3_d': 'Junior review, senior technical assessment, and digital sign-off - with version control throughout.',
                     'loop_4_t': 'Output',
                     'loop_4_d': 'Finalized documentation package in standard formats, ready for municipal submission or execution.',
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
                     'cta_title': 'Start with one project. Upload raw data.',
                     'cta_desc': 'Builtly combines customer self-service, deterministic checks, AI-generated drafts, and professional '
                                 'sign-off in one portal. Get your submission-ready package faster.',
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
              'eyebrow': 'Builtly Arbeidsflyt',
              'title': "Fra <span class='accent'>rådata</span> til signerte leveranser.",
              'subtitle': 'Builtly er kundeportalen for teknisk rådgivning. Last opp prosjektdata, la plattformen validere, beregne og '
                          'utarbeide rapporten – før junior-QA og senior-signering gjør pakken klar til innsending.',
              'btn_setup': 'Åpne Project Setup',
              'btn_qa': 'Åpne QA & Sign-off',
              'proofs': ['Regelstyrt AI', 'Revisjonsspor', 'PDF + DOCX format', 'Digital signering', 'Strukturert QA-flyt'],
              'why_kicker': 'Hvorfor Builtly?',
              'stat1_v': '80-90%',
              'stat1_t': 'Tidsbesparelse',
              'stat1_d': 'på manuell rapportskriving og repetitivt arbeid',
              'stat2_v': 'Junior + Senior',
              'stat2_t': 'Kvalitetssikring',
              'stat2_d': 'teknisk kontroll og digital signering',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Komplette leveranser',
              'stat3_d': 'med vedlegg og full sporbarhet',
              'stat4_v': 'Full Sporbarhet',
              'stat4_t': 'Loggføring fra start til slutt',
              'stat4_d': 'Input, regel-sjekk og signaturer lagres',
              'sec_val_kicker': 'Kjerneprodukt',
              'sec_val_title': 'Portal først. Moduler under.',
              'sec_val_sub': 'Builtly er ikke en samling løsrevne verktøy. Det er én felles plattform for prosjektoppsett, '
                             'data-innsamling, AI-prosessering, QA og endelig leveranse.',
              'val_1_t': 'Kundeportal',
              'val_1_d': 'Opprett prosjekt, last opp data og generer dokumenter i én sømløs flyt.',
              'val_2_t': 'Regelstyrt AI',
              'val_2_d': 'AI-en opererer innenfor eksplisitte lovkrav, sjekklister og standardmaler.',
              'val_3_t': 'QA og signering',
              'val_3_d': 'Junior validerer struktur og logikk. Senior gir endelig teknisk godkjenning.',
              'val_4_t': 'Skalerbarhet',
              'val_4_d': 'Nye fagfelt plugges direkte inn i samme dokumentasjons- og rammeverk.',
              'sec_loop_kicker': 'Arbeidsflyt',
              'sec_loop_title': 'Slik fungerer Builtly',
              'sec_loop_sub': 'En deterministisk fire-stegs prosess som tar deg fra fragmentert prosjektdata til en ferdig teknisk pakke.',
              'loop_1_t': 'Input',
              'loop_1_d': 'Last opp PDF, IFC-modeller, Excel-filer og prosjektdata på ett sted.',
              'loop_2_t': 'Valider og analyser',
              'loop_2_d': 'Plattformen validerer data, sjekker regelverk, gjør beregninger og skriver utkastet.',
              'loop_3_t': 'QA og signering',
              'loop_3_d': 'Junior-sjekk, teknisk vurdering fra senior og digital signering – med versjonskontroll.',
              'loop_4_t': 'Output',
              'loop_4_d': 'Ferdig dokumentpakke i standardformater, klar for innsending eller utførelse.',
              'mod_sec_kicker': 'Moduler og veikart',
              'mod_sec_title': 'Spesialiserte agenter i én plattform',
              'mod_sec_sub': 'Hver modul har egen logikk og fagspesifikke regler, men deler samme portal og kvalitetskontroll.',
              'mod_sec1': 'Tilgjengelig nå (Klar for pilot)',
              'mod_sec2': 'Veikart og tidligfase',
              'mod_sec3': 'Bærekraft & Sikkerhet',
              'mod_sec3_sub': 'Integrerte tjenester for miljøoppfølging, sikkerhet og sertifisering, skreddersydd for å skape ansvarlige '
                              'og verdiskapende prosjekter.',
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
              'cta_title': 'Start med ett prosjekt. Last opp data.',
              'cta_desc': 'Builtly kombinerer selvbetjening for kunder, deterministiske sjekker, AI-utkast og formell signering i én '
                          'portal.',
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
                'eyebrow': 'Builtly Arbetsflöde',
                'title': "Från <span class='accent'>rådata</span> till signerade leveranser.",
                'subtitle': 'Builtly är kundportalen för teknisk rådgivning. Ladda upp projektdata, låt AI validera och utarbeta rapporten '
                            '– innan junior-QA och senior-signering gör det klart för inlämning.',
                'btn_setup': 'Starta i Project Setup',
                'btn_qa': 'Öppna QA & Sign-off',
                'proofs': ['Regelstyrd AI', 'Revisionsspår', 'PDF + DOCX', 'Digital Signering', 'Strukturerad QA'],
                'why_kicker': 'Varför Builtly?',
                'stat1_v': '80-90%',
                'stat1_t': 'Tidsbesparing',
                'stat1_d': 'Minskning av manuellt rapportarbete',
                'stat2_v': 'Junior + Senior',
                'stat2_t': 'Kvalitetssäkring',
                'stat2_d': 'Digital QA och signering av ansvarig',
                'stat3_v': 'PDF + DOCX',
                'stat3_t': 'Kompletta rapporter',
                'stat3_d': 'Med bilagor och spårbarhet',
                'stat4_v': 'Spårbarhet',
                'stat4_t': 'Dokumentation',
                'stat4_d': 'Versionshantering från input till PDF',
                'sec_val_kicker': 'Kärnprodukt',
                'sec_val_title': 'Portal först. Moduler under.',
                'sec_val_sub': 'Builtly är ett gemensamt system för projektuppsättning, AI-bearbetning och kvalitetssäkring.',
                'val_1_t': 'Kundportal',
                'val_1_d': 'Upprättande, input och dokumentgenerering i ett flöde.',
                'val_2_t': 'Regelstyrd AI',
                'val_2_d': 'AI arbetar inom strikta lagkrav och mallar.',
                'val_3_t': 'QA och Signering',
                'val_3_d': 'Junior validerer. Senior ger slutgiltigt godkännande.',
                'val_4_t': 'Skalbarhet',
                'val_4_d': 'Nya discipliner ansluts till samma ramverk.',
                'sec_loop_kicker': 'Arbetsflöde',
                'sec_loop_title': 'Så fungerar Builtly',
                'sec_loop_sub': 'En deterministisk fyra-stegs process.',
                'loop_1_t': 'Input',
                'loop_1_d': 'Ladda upp filer och data på ett ställe.',
                'loop_2_t': 'AI Analys',
                'loop_2_d': 'Plattformen kontrollerar regelverk och skriver utkast.',
                'loop_3_t': 'QA & Signering',
                'loop_3_d': 'Granskning och digital signering.',
                'loop_4_t': 'Output',
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
              'eyebrow': 'Builtly Workflow',
              'title': "Fra <span class='accent'>rådata</span> til underskrevne leverancer.",
              'subtitle': 'Builtly er kundeportalen for teknisk rådgivning. Upload projektdata, lad AI validere og udarbejde rapporten – '
                          'før junior-QA og senior-signering gør det klar.',
              'btn_setup': 'Start i Project Setup',
              'btn_qa': 'Åbn QA & Sign-off',
              'proofs': ['Regelstyret AI', 'Revisionsspor', 'PDF + DOCX', 'Digital Signatur', 'Struktureret QA'],
              'why_kicker': 'Hvorfor Builtly?',
              'stat1_v': '80-90%',
              'stat1_t': 'Tidsbesparelse',
              'stat1_d': 'Reduktion af manuelt rapportarbejde',
              'stat2_v': 'Junior + Senior',
              'stat2_t': 'Kvalitetssikring',
              'stat2_d': 'Digital QA og signering af fagansvarlig',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Komplette rapporter',
              'stat3_d': 'Med bilag og sporbarhed',
              'stat4_v': 'Sporbarhed',
              'stat4_t': 'Dokumentation',
              'stat4_d': 'Versionskontrol fra input til PDF',
              'sec_val_kicker': 'Kerneprodukt',
              'sec_val_title': 'Portal først. Moduler under.',
              'sec_val_sub': 'Builtly er et fælles system for projektoprettelse, AI-behandling og kvalitetssikring.',
              'val_1_t': 'Kundeportal',
              'val_1_d': 'Oprettelse, input og dokumentgenerering i ét flow.',
              'val_2_t': 'Regelstyret AI',
              'val_2_d': 'AI opererer inden for eksplicitte lovkrav og skabeloner.',
              'val_3_t': 'QA og Signering',
              'val_3_d': 'Junior validerer. Senior giver endelig godkendelse.',
              'val_4_t': 'Skalerbarhet',
              'val_4_d': 'Nye fagområder tilsluttes samme rammeværk.',
              'sec_loop_kicker': 'Arbejdsgang',
              'sec_loop_title': 'Sådan fungerer Builtly',
              'sec_loop_sub': 'En deterministisk fire-trins proces.',
              'loop_1_t': 'Input',
              'loop_1_d': 'Upload filer og data ét sted.',
              'loop_2_t': 'AI Analyse',
              'loop_2_d': 'Platformen tjekker bygningsreglement og skriver udkast.',
              'loop_3_t': 'QA & Signering',
              'loop_3_d': 'Gennemgang og digital signatur.',
              'loop_4_t': 'Output',
              'loop_4_d': 'Færdigt dokument til byggetilladelse.',
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
                     'eyebrow': 'Builtly Workflow',
                     'title': "From <span class='accent'>raw data</span> to signed deliverables.",
                     'subtitle': 'Builtly is the client portal for compliance-grade engineering delivery. Upload project inputs, let the '
                                 'platform validate, calculate, check rules, and draft the report before junior QA and senior sign-off '
                                 'turn it into a consistent, traceable, submission-ready package.',
                     'btn_setup': 'Open project setup',
                     'btn_qa': 'Open QA and sign-off',
                     'proofs': ['Rules-first', 'Audit trail', 'PDF + DOCX output', 'Digital sign-off', 'Structured QA workflow'],
                     'why_kicker': 'Why Builtly?',
                     'stat1_v': '80-90%',
                     'stat1_t': 'Reduction in manual drafting',
                     'stat1_d': 'and repetitive report production',
                     'stat2_v': 'Junior + Senior',
                     'stat2_t': 'Human-in-the-loop QA',
                     'stat2_d': 'technical control, and digital sign-off',
                     'stat3_v': 'PDF + DOCX',
                     'stat3_t': 'Complete report packages',
                     'stat3_d': 'with appendices and traceability',
                     'stat4_v': 'Full Traceability',
                     'stat4_t': 'End-to-end logging',
                     'stat4_d': 'Inputs, versions, compliance checks logged',
                     'sec_val_kicker': 'Core value proposition',
                     'sec_val_title': 'Portal first. Modules under.',
                     'sec_val_sub': 'Builtly is not a collection of disconnected tools. It is one secure portal for project setup, data '
                                    'intake, validation, AI processing, review, sign-off, and final delivery.',
                     'val_1_t': 'Client portal',
                     'val_1_d': 'Project creation, input uploads, missing-data follow-up, document generation, and audit trails in one '
                                'workflow.',
                     'val_2_t': 'Rules-first AI',
                     'val_2_d': 'AI operates inside explicit regulatory guardrails, checklists, and standard templates - not as free-form '
                                'guesswork.',
                     'val_3_t': 'QA and sign-off',
                     'val_3_d': 'Junior engineers validate plausibility and structure. Senior engineers provide final review and '
                                'certification.',
                     'val_4_t': 'Scalable delivery',
                     'val_4_d': 'Each new engineering discipline plugs into the same validation, documentation, and sign-off backbone.',
                     'sec_loop_kicker': 'Workflow',
                     'sec_loop_title': 'The Builtly Loop',
                     'sec_loop_sub': 'A deterministic four-step workflow that takes you from fragmented project data to a reviewable, '
                                     'compliant engineering package.',
                     'loop_1_t': 'Input',
                     'loop_1_d': 'Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place.',
                     'loop_2_t': 'Validate and analyze',
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
                     'cta_title': 'Start with one project. Upload raw data.',
                     'cta_desc': 'Builtly combines customer self-service, deterministic checks, AI-generated drafts, and professional '
                                 'sign-off in one portal. Get your submission-ready package faster.',
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
              'eyebrow': 'Builtly-työnkulku',
              'title': "Raakadatan <span class='accent'>muuntaminen</span> allekirjoitetuiksi toimituksiksi.",
              'subtitle': 'Builtly on teknisen suunnittelun asiakasportaali. Lataa projektin lähtötiedot, anna alustan validoida, laskea, '
                          'tarkistaa määräykset ja laatia raporttiluonnos ennen junior-QA:ta ja senior-hyväksyntää.',
              'btn_setup': 'Avaa projektin aloitus',
              'btn_qa': 'Avaa QA ja hyväksyntä',
              'proofs': ['Sääntöpohjainen AI', 'Audit trail', 'PDF + DOCX', 'Digitaalinen hyväksyntä', 'Jäsennelty QA'],
              'why_kicker': 'Miksi Builtly?',
              'stat1_v': '80-90%',
              'stat1_t': 'Ajansäästö',
              'stat1_d': 'manuaalisessa raportoinnissa ja toistuvassa työssä',
              'stat2_v': 'Junior + Senior',
              'stat2_t': 'Laadunvarmistus',
              'stat2_d': 'junior- ja senior-tarkastuksella sekä digitaalisella hyväksynnällä',
              'stat3_v': 'PDF + DOCX',
              'stat3_t': 'Valmiit raporttipaketit',
              'stat3_d': 'liitteineen ja jäljitettävyyksineen',
              'stat4_v': 'Täysi jäljitettävyys',
              'stat4_t': 'Lokitus alusta loppuun',
              'stat4_d': 'syötteet, versiot ja määräystarkistukset tallennetaan',
              'sec_val_kicker': 'Ydinratkaisu',
              'sec_val_title': 'Portaali ensin. Moduulit sen päälle.',
              'sec_val_sub': 'Builtly ei ole irrallisten työkalujen kokoelma. Se on yksi turvallinen portaali projektin aloitukseen, '
                             'tiedonkeruuseen, validointiin, AI-käsittelyyn, tarkastukseen, hyväksyntään ja lopulliseen toimitukseen.',
              'val_1_t': 'Asiakasportaali',
              'val_1_d': 'Projektin perustaminen, aineistojen lataus, puuttuvien tietojen seuranta, dokumenttien tuotanto ja audit trail '
                         'samassa työnkulussa.',
              'val_2_t': 'Sääntöpohjainen AI',
              'val_2_d': 'AI toimii selkeiden määräysten, tarkistuslistojen ja standardimallien sisällä - ei vapaana arvailuna.',
              'val_3_t': 'QA ja hyväksyntä',
              'val_3_d': 'Nuoremmat asiantuntijat tarkistavat rakenteen ja uskottavuuden. Seniorit tekevät lopullisen teknisen '
                         'tarkastuksen ja hyväksynnän.',
              'val_4_t': 'Skaalautuva toimitus',
              'val_4_d': 'Uudet suunnittelualat voidaan liittää samaan validointi-, dokumentointi- ja hyväksyntärunkoon.',
              'sec_loop_kicker': 'Työnkulku',
              'sec_loop_title': 'Näin Builtly toimii',
              'sec_loop_sub': 'Deterministinen nelivaiheinen prosessi, joka vie hajanaisesta projektidatasta tarkastettavaan ja määräysten '
                              'mukaiseen suunnittelupakettiin.',
              'loop_1_t': 'Syöte',
              'loop_1_d': 'Lataa PDF:t, IFC-mallit, Excel-tiedostot, piirustukset ja projektikohtaiset tiedot yhteen paikkaan.',
              'loop_2_t': 'Validoi ja analysoi',
              'loop_2_d': 'Alusta jäsentää tiedot, tarkistaa määräykset, tekee laskelmat ja laatii luonnoksen.',
              'loop_3_t': 'QA ja hyväksyntä',
              'loop_3_d': 'Junior-tarkastus, senior-arviointi ja digitaalinen hyväksyntä versiokontrollilla.',
              'loop_4_t': 'Tuloste',
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
                'eyebrow': 'Builtly Workflow',
                'title': "Von <span class='accent'>Rohdaten</span> zu freigegebenen Lieferpaketen.",
                'subtitle': 'Builtly ist das Kundenportal für regelkonforme Ingenieurleistungen. Projektunterlagen hochladen, validieren '
                            'lassen, Regeln prüfen, berechnen und Berichtsentwürfe erzeugen - bevor Junior-QA und Senior-Freigabe daraus '
                            'ein konsistentes, nachvollziehbares Paket machen.',
                'btn_setup': 'Projekt-Setup öffnen',
                'btn_qa': 'QA & Freigabe öffnen',
                'proofs': ['Regelbasierte KI', 'Audit-Trail', 'PDF + DOCX', 'Digitale Freigabe', 'Strukturierter QA-Flow'],
                'why_kicker': 'Warum Builtly?',
                'stat1_v': '80-90%',
                'stat1_t': 'Weniger manuelle Berichtsarbeit',
                'stat1_d': 'und repetitive Erstellung von Dokumenten',
                'stat2_v': 'Junior + Senior',
                'stat2_t': 'Human-in-the-loop QA',
                'stat2_d': 'technische Prüfung und digitale Freigabe',
                'stat3_v': 'PDF + DOCX',
                'stat3_t': 'Komplette Berichtspakete',
                'stat3_d': 'mit Anhängen und Nachvollziehbarkeit',
                'stat4_v': 'Volle Nachvollziehbarkeit',
                'stat4_t': 'End-to-end Logging',
                'stat4_d': 'Eingaben, Versionen und Regelprüfungen werden protokolliert',
                'sec_val_kicker': 'Kernangebot',
                'sec_val_title': 'Portal zuerst. Module darunter.',
                'sec_val_sub': 'Builtly ist keine lose Sammlung einzelner Werkzeuge. Es ist ein sicheres Portal für Projekt-Setup, '
                               'Datenerfassung, Validierung, KI-Verarbeitung, Review, Freigabe und finale Lieferung.',
                'val_1_t': 'Kundenportal',
                'val_1_d': 'Projektanlage, Uploads, Nachverfolgung fehlender Daten, Dokumentengenerierung und Audit-Trail in einem '
                           'Workflow.',
                'val_2_t': 'Regelbasierte KI',
                'val_2_d': 'Die KI arbeitet innerhalb klarer regulatorischer Leitplanken, Checklisten und Standardvorlagen - nicht als '
                           'freies Rätselraten.',
                'val_3_t': 'QA und Freigabe',
                'val_3_d': 'Junior-Ingenieure prüfen Struktur und Plausibilität. Senior-Ingenieure übernehmen die finale technische '
                           'Prüfung und Freigabe.',
                'val_4_t': 'Skalierbare Lieferung',
                'val_4_d': 'Neue Fachdisziplinen können an dasselbe Validierungs-, Dokumentations- und Freigabegerüst angebunden werden.',
                'sec_loop_kicker': 'Workflow',
                'sec_loop_title': 'So funktioniert Builtly',
                'sec_loop_sub': 'Ein deterministischer Vier-Schritte-Workflow von fragmentierten Projektdaten zu einem prüffähigen, '
                                'regelkonformen Ingenieurpaket.',
                'loop_1_t': 'Input',
                'loop_1_d': 'PDFs, IFC-Modelle, Excel-Dateien, Zeichnungen und projektspezifische Daten an einem Ort hochladen.',
                'loop_2_t': 'Validieren und analysieren',
                'loop_2_d': 'Die Plattform strukturiert die Daten, prüft Regeln, führt Berechnungen aus und erstellt den Entwurf.',
                'loop_3_t': 'QA und Freigabe',
                'loop_3_d': 'Junior-Review, Senior-Bewertung und digitale Freigabe mit Versionskontrolle.',
                'loop_4_t': 'Output',
                'loop_4_d': 'Finales Dokumentationspaket in Standardformaten, bereit für Genehmigung oder Ausführung.',
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


def get_text_bundle(lang_key: str) -> Dict:
    base = dict(TEXTS["🇬🇧 English (UK)"])
    base.update(TEXTS.get(lang_key, {}))
    return base


def get_locale_profile(lang_key: str) -> Dict:
    return LANGUAGE_PROFILES.get(lang_key, LANGUAGE_PROFILES["🇳🇴 Norsk"])


lang = get_text_bundle(st.session_state.app_lang)
locale_profile = get_locale_profile(st.session_state.app_lang)
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
    return page_route(page_key)


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


def sync_language_query_param(lang_key: str, keep_assistant: bool = False) -> None:
    params = get_query_params_dict()
    params["lang"] = language_slug(lang_key)
    if keep_assistant or st.session_state.get("assistant_dialog_open"):
        params["assistant"] = "open"
    else:
        params.pop("assistant", None)
    set_query_params_dict(params)


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
        justify-content: center;
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
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98));
        border: 1px solid var(--stroke) !important;
        border-radius: 22px !important;
        padding: 1.2rem 1.15rem 1.15rem 1.15rem !important;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
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
        background: rgba(255,255,255,0.03);
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

    .footer-block {
        text-align: center;
        margin-top: 2.5rem;
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
        .module-grid {
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
        .module-grid {
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

top_l, top_r = st.columns([5, 1])

with top_l:
    logo_uri = logo_data_uri()
    logo_html = f'<img src="{logo_uri}" class="brand-logo" alt="Builtly logo" />' if logo_uri else '<div class="brand-name">Builtly</div>'
    render_html(f'<div class="top-shell" style="margin-bottom: 0;"><div class="brand-left">{logo_html}</div></div>')

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

st.markdown("<div style='margin-bottom: 2rem;'></div>", unsafe_allow_html=True)

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
    module_card("geo", "🌍", "Phase 1 - Priority", "badge-priority", lang["m_geo_t"], lang["m_geo_d"], lang["m_geo_in"], lang["m_geo_out"], lang["m_geo_btn"]),
    module_card("akustikk", "🔊", "Phase 2", "badge-phase2", lang["m_aku_t"], lang["m_aku_d"], lang["m_aku_in"], lang["m_aku_out"], lang["m_aku_btn"]),
    module_card("brann", "🔥", "Phase 2", "badge-phase2", lang["m_brann_t"], lang["m_brann_d"], lang["m_brann_in"], lang["m_brann_out"], lang["m_brann_btn"]),
]

roadmap_cards = [
    module_card("mulighetsstudie", "📐", "Early phase", "badge-early", lang["m_ark_t"], lang["m_ark_d"], lang["m_ark_in"], lang["m_ark_out"], lang["m_ark_btn"]),
    module_card("konstruksjon", "🏢", "Roadmap", "badge-roadmap", lang["m_rib_t"], lang["m_rib_d"], lang["m_rib_in"], lang["m_rib_out"], lang["m_rib_btn"]),
    module_card("trafikk", "🚦", "Roadmap", "badge-roadmap", lang["m_tra_t"], lang["m_tra_d"], lang["m_tra_in"], lang["m_tra_out"], lang["m_tra_btn"]),
]

sustainability_cards = [
    module_card("sha", "🦺", "Compliance", "badge-priority", lang["m_sha_t"], lang["m_sha_d"], lang["m_sha_in"], lang["m_sha_out"], lang["m_sha_btn"]),
    module_card("breeam", "🌿", "Certification", "badge-phase2", lang["m_breeam_t"], lang["m_breeam_d"], lang["m_breeam_in"], lang["m_breeam_out"], lang["m_breeam_btn"]),
    module_card("mop", "♻️", "Environment", "badge-roadmap", lang["m_mop_t"], lang["m_mop_d"], lang["m_mop_in"], lang["m_mop_out"], lang["m_mop_btn"]),
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
        </div>
    </div>
    """
)

# -------------------------------------------------
# 13) FOOTER
# -------------------------------------------------
render_html(
    f"""
    <div class="footer-block">
        <div class="footer-copy">{lang['footer_copy']}</div>
        <div class="footer-meta">{lang['footer_meta']}</div>
    </div>
    """
)
