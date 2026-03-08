import os
import base64
from pathlib import Path
from typing import Optional

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
# 2) SESSION STATE (Hjernen)
# -------------------------------------------------
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)", "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500, "tomteareal": 0,
        "last_sync": "Ikke synket enda"
    }

# SETTER NORSK SOM STANDARD SPRÅK NÅ
if "app_lang" not in st.session_state:
    st.session_state.app_lang = "🇳🇴 Norsk"


# -------------------------------------------------
# 3) OVERSETTELSESMOTOR (i18n)
# -------------------------------------------------
TEXTS = {
    "🇬🇧 English": {
        "rule_set": "UK (Building Regs)",
        "eyebrow": "The Builtly Loop",
        "title": "From <span class='accent'>raw data</span> to signed deliverables.",
        "subtitle": "Builtly is the customer portal for compliance-grade engineering delivery. Upload project inputs, let the platform validate, calculate, check rules, and draft the report - before junior QA and senior sign-off turn it into a consistent, traceable, submission-ready package.",
        "btn_setup": "Open project setup",
        "btn_qa": "Open QA and sign-off",
        "proofs": ["Rules-first", "Audit trail", "PDF + DOCX output", "Digital sign-off", "Structured QA workflow"],
        
        "why_kicker": "Why Builtly?",
        "stat1_v": "80-90%", "stat1_t": "Reduction in manual drafting", "stat1_d": "and repetitive report production",
        "stat2_v": "Junior + Senior", "stat2_t": "Human-in-the-loop QA", "stat2_d": "technical control, and digital sign-off",
        "stat3_v": "PDF + DOCX", "stat3_t": "Complete report packages", "stat3_d": "with appendices and traceability",
        "stat4_v": "Full Traceability", "stat4_t": "End-to-end logging", "stat4_d": "Inputs, versions, compliance checks logged",

        "sec_val_kicker": "Core value proposition", "sec_val_title": "Portal first. Modules under.", "sec_val_sub": "Builtly is not a collection of disconnected tools. It is one secure portal for project setup, data ingestion, validation, AI processing, review, sign-off, and final delivery.",
        "val_1_t": "Client portal", "val_1_d": "Project creation, input uploads, missing-data follow-up, document generation, and audit trails in one workflow.",
        "val_2_t": "Rules-first AI", "val_2_d": "AI operates inside explicit regulatory guardrails, checklists, and standard templates - not as free-form guesswork.",
        "val_3_t": "QA and sign-off", "val_3_d": "Junior engineers validate plausibility and structure. Senior engineers provide final review and certification.",
        "val_4_t": "Scalable delivery", "val_4_d": "Each new engineering discipline plugs into the same validation, documentation, and sign-off backbone.",

        "sec_loop_kicker": "Workflow", "sec_loop_title": "The Builtly Loop", "sec_loop_sub": "A deterministic four-step workflow that takes you from fragmented project data to a reviewable, compliant engineering package.",
        "loop_1_t": "Input", "loop_1_d": "Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place.",
        "loop_2_t": "Validate and analyze", "loop_2_d": "The platform parses, validates, applies local rule checks, performs calculations, and drafts the deliverable.",
        "loop_3_t": "QA and sign-off", "loop_3_d": "Junior review, senior technical assessment, and digital sign-off - with version control throughout.",
        "loop_4_t": "Output", "loop_4_d": "Finalized documentation package in standard formats, ready for municipal submission or execution.",

        "mod_sec_kicker": "Modules and roadmap", "mod_sec_title": "Specialized agents in one platform", "mod_sec_sub": "Each module has dedicated ingestion logic, discipline-specific rules, and output templates while sharing the same portal, validation, QA, and sign-off backbone.",
        "mod_sec1": "Available now and pilot-ready", "mod_sec2": "Roadmap and early-phase tools",
        "mod_sec3": "Sustainability & Compliance", "mod_sec3_sub": "Integrated services for environmental follow-up, safety, and certification, tailored to create responsible and value-driven developments.",

        "m_geo_t": "GEO / ENV - Ground Conditions", "m_geo_d": "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans.", "m_geo_in": "XLSX / CSV / PDF + plans", "m_geo_out": "Environmental action plan, logs", "m_geo_btn": "Open Geo & Env",
        "m_aku_t": "ACOUSTICS - Noise & Sound", "m_aku_d": "Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.", "m_aku_in": "Noise map + floor plan", "m_aku_out": "Acoustics report, facade evaluation", "m_aku_btn": "Open Acoustics",
        "m_brann_t": "FIRE - Safety Strategy", "m_brann_d": "Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and fire strategy.", "m_brann_in": "Architectural drawings + class", "m_brann_out": "Fire strategy concept, deviations", "m_brann_btn": "Open Fire Strategy",
        
        "m_ark_t": "ARK - Feasibility Study", "m_ark_d": "Site screening, volume analysis, and early-phase decision support before full engineering design.", "m_ark_in": "Site data, zoning plans", "m_ark_out": "Feasibility report, utilization metrics", "m_ark_btn": "Open Feasibility",
        "m_rib_t": "STRUC - Structural Concept", "m_rib_d": "Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.", "m_rib_in": "Models, load parameters", "m_rib_out": "Concept memo, grid layouts", "m_rib_btn": "Open Structural",
        "m_tra_t": "TRAFFIC - Mobility", "m_tra_d": "Traffic generation, parking requirements, access logic, and soft-mobility planning for early project phases.", "m_tra_in": "Site plans, local norms", "m_tra_out": "Traffic memo, mobility plan", "m_tra_btn": "Open Traffic & Mobility",
        
        "m_sha_t": "SHA - Safety & Health Plan", "m_sha_d": "Safety, health, and working environment. Generates routines for site logistics and high-risk operations.", "m_sha_in": "Project data + Risk factors", "m_sha_out": "Complete SHA plan", "m_sha_btn": "Open SHA Module",
        "m_breeam_t": "BREEAM Assistant", "m_breeam_d": "Early-phase assessment of BREEAM potential, credit requirements, and material strategies.", "m_breeam_in": "Building data + Ambitions", "m_breeam_out": "BREEAM Pre-assessment", "m_breeam_btn": "Open BREEAM Assistant",
        "m_mop_t": "MOP - Environment Plan", "m_mop_d": "Environmental follow-up plan. Assesses waste management, reuse, emissions, and nature preservation.", "m_mop_in": "Project data + Eco goals", "m_mop_out": "MOP Document", "m_mop_btn": "Open MOP Module",
        
        "btn_dev": "In development",

        "cta_title": "Start with one project. Upload raw data.",
        "cta_desc": "Builtly combines customer self-service, deterministic checks, AI-generated drafts, and professional sign-off in one portal. Get your submission-ready package faster.",
        "cta_btn1": "Start in project setup", "cta_btn2": "Go to review queue",

        "footer_copy": "AI-assisted engineering. Human-verified. Compliance-grade.",
        "footer_meta": "© 2026 Builtly Engineering AS. All rights reserved."
    },
    "🇳🇴 Norsk": {
        "rule_set": "Norge (TEK17 / Kartverket)",
        "eyebrow": "Builtly Arbeidsflyt",
        "title": "Fra <span class='accent'>rådata</span> til signerte leveranser.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Last opp prosjektdata, la plattformen validere, beregne og utarbeide rapporten – før junior-QA og senior-signering gjør pakken klar til innsending.",
        "btn_setup": "Åpne Project Setup",
        "btn_qa": "Åpne QA & Sign-off",
        "proofs": ["Regelstyrt AI", "Revisjonsspor", "PDF + DOCX format", "Digital signering", "Strukturert QA-flyt"],
        
        "why_kicker": "Hvorfor Builtly?",
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparelse", "stat1_d": "på manuell rapportskriving og repetitivt arbeid",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssikring", "stat2_d": "teknisk kontroll og digital signering",
        "stat3_v": "PDF + DOCX", "stat3_t": "Komplette leveranser", "stat3_d": "med vedlegg og full sporbarhet",
        "stat4_v": "Full Sporbarhet", "stat4_t": "Loggføring fra start til slutt", "stat4_d": "Input, regel-sjekk og signaturer lagres",

        "sec_val_kicker": "Kjerneprodukt", "sec_val_title": "Portal først. Moduler under.", "sec_val_sub": "Builtly er ikke en samling løsrevne verktøy. Det er én felles plattform for prosjektoppsett, data-innsamling, AI-prosessering, QA og endelig leveranse.",
        "val_1_t": "Kundeportal", "val_1_d": "Opprett prosjekt, last opp data og generer dokumenter i én sømløs flyt.",
        "val_2_t": "Regelstyrt AI", "val_2_d": "AI-en opererer innenfor eksplisitte lovkrav, sjekklister og standardmaler.",
        "val_3_t": "QA og signering", "val_3_d": "Junior validerer struktur og logikk. Senior gir endelig teknisk godkjenning.",
        "val_4_t": "Skalerbarhet", "val_4_d": "Nye fagfelt plugges direkte inn i samme dokumentasjons- og rammeverk.",

        "sec_loop_kicker": "Arbeidsflyt", "sec_loop_title": "Slik fungerer Builtly", "sec_loop_sub": "En deterministisk fire-stegs prosess som tar deg fra fragmentert prosjektdata til en ferdig teknisk pakke.",
        "loop_1_t": "Input", "loop_1_d": "Last opp PDF, IFC-modeller, Excel-filer og prosjektdata på ett sted.",
        "loop_2_t": "Valider og analyser", "loop_2_d": "Plattformen validerer data, sjekker regelverk, gjør beregninger og skriver utkastet.",
        "loop_3_t": "QA og signering", "loop_3_d": "Junior-sjekk, teknisk vurdering fra senior og digital signering – med versjonskontroll.",
        "loop_4_t": "Output", "loop_4_d": "Ferdig dokumentpakke i standardformater, klar for innsending eller utførelse.",

        "mod_sec_kicker": "Moduler og veikart", "mod_sec_title": "Spesialiserte agenter i én plattform", "mod_sec_sub": "Hver modul har egen logikk og fagspesifikke regler, men deler samme portal og kvalitetskontroll.",
        "mod_sec1": "Tilgjengelig nå (Klar for pilot)", "mod_sec2": "Veikart og tidligfase",
        "mod_sec3": "Bærekraft & Sikkerhet", "mod_sec3_sub": "Integrerte tjenester for miljøoppfølging, sikkerhet og sertifisering, skreddersydd for å skape ansvarlige og verdiskapende prosjekter.",

        "m_geo_t": "GEO / MILJØ - Grunnforhold", "m_geo_d": "Analyserer lab-filer og graveceller. Klassifiserer masser og utarbeider tiltaksplaner.", "m_geo_in": "XLSX / CSV / PDF + Kart", "m_geo_out": "Tiltaksplan, logg", "m_geo_btn": "Åpne Geo & Miljø",
        "m_aku_t": "AKUSTIKK - Støy & Lyd", "m_aku_d": "Leser støykart og plantegninger. Genererer krav til fasade, vinduer og skjerming.", "m_aku_in": "Støykart + Plan", "m_aku_out": "Akustikkrapport", "m_aku_btn": "Åpne Akustikk",
        "m_brann_t": "BRANN - Sikkerhetskonsept", "m_brann_d": "Vurderer arkitektur mot forskrifter. Definerer rømning og brannceller.", "m_brann_in": "Tegninger + Klasse", "m_brann_out": "Brannkonsept (RIBr)", "m_brann_btn": "Åpne Brannkonsept",
        
        "m_ark_t": "ARK - Mulighetsstudie", "m_ark_d": "Tomteanalyse, volumvurdering og beslutningsgrunnlag for tidligfase.", "m_ark_in": "Regulering + Tomt", "m_ark_out": "Mulighetsstudie", "m_ark_btn": "Åpne Feasibility",
        "m_rib_t": "RIB - Konstruksjon", "m_rib_d": "Konseptuelle struktursjekker, spennvidder og integrasjon med klimagass.", "m_rib_in": "Modeller, Laster", "m_rib_out": "Konseptnotat RIB", "m_rib_btn": "Åpne Konstruksjon",
        "m_tra_t": "TRAFIKK - Mobilitet", "m_tra_d": "Trafikkgenerering, parkering, adkomstlogikk og myke trafikanter for tidligfase.", "m_tra_in": "Situasjonsplan", "m_tra_out": "Trafikknotat", "m_tra_btn": "Åpne Trafikk & Mobilitet",
        
        "m_sha_t": "SHA-Plan (Sikkerhet)", "m_sha_d": "Sikkerhet, helse og arbeidsmiljø. Genererer rutiner for rigg, logistikk og risikofylte operasjoner.", "m_sha_in": "Prosjektdata + Risiko", "m_sha_out": "Komplett SHA-plan", "m_sha_btn": "Åpne SHA",
        "m_breeam_t": "BREEAM Assistent", "m_breeam_d": "Tidligfase vurdering av BREEAM-NOR potensial, poengkrav og materialstrategi.", "m_breeam_in": "Byggdata + Ambisjon", "m_breeam_out": "BREEAM Pre-assessment", "m_breeam_btn": "Åpne BREEAM",
        "m_mop_t": "MOP (Miljøoppfølging)", "m_mop_d": "Miljøoppfølgingsplan for byggeplass. Vurderer avfall, ombruk, utslipp og natur.", "m_mop_in": "Prosjektdata + Miljømål", "m_mop_out": "MOP Dokument", "m_mop_btn": "Åpne MOP",
        
        "btn_dev": "Under utvikling",

        "cta_title": "Start med ett prosjekt. Last opp data.",
        "cta_desc": "Builtly kombinerer selvbetjening for kunder, deterministiske sjekker, AI-utkast og formell signering i én portal.",
        "cta_btn1": "Start i Project Setup", "cta_btn2": "Gå til kontroll-kø",

        "footer_copy": "AI-assisted engineering. Human-verified. Compliance-grade.",
        "footer_meta": "© 2026 Builtly Engineering AS. All rights reserved."
    },
    "🇸🇪 Svensk": {
        "rule_set": "Sverige (BBR)",
        "eyebrow": "Builtly Arbetsflöde",
        "title": "Från <span class='accent'>rådata</span> till signerade leveranser.",
        "subtitle": "Builtly är kundportalen för teknisk rådgivning. Ladda upp projektdata, låt AI validera och utarbeta rapporten – innan junior-QA och senior-signering gör det klart för inlämning.",
        "btn_setup": "Starta i Project Setup",
        "btn_qa": "Öppna QA & Sign-off",
        "proofs": ["Regelstyrd AI", "Revisionsspår", "PDF + DOCX", "Digital Signering", "Strukturerad QA"],
        
        "why_kicker": "Varför Builtly?",
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparing", "stat1_d": "Minskning av manuellt rapportarbete",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssäkring", "stat2_d": "Digital QA och signering av ansvarig",
        "stat3_v": "PDF + DOCX", "stat3_t": "Kompletta rapporter", "stat3_d": "Med bilagor och spårbarhet",
        "stat4_v": "Spårbarhet", "stat4_t": "Dokumentation", "stat4_d": "Versionshantering från input till PDF",

        "sec_val_kicker": "Kärnprodukt", "sec_val_title": "Portal först. Moduler under.", "sec_val_sub": "Builtly är ett gemensamt system för projektuppsättning, AI-bearbetning och kvalitetssäkring.",
        "val_1_t": "Kundportal", "val_1_d": "Upprättande, input och dokumentgenerering i ett flöde.",
        "val_2_t": "Regelstyrd AI", "val_2_d": "AI arbetar inom strikta lagkrav och mallar.",
        "val_3_t": "QA och Signering", "val_3_d": "Junior validerer. Senior ger slutgiltigt godkännande.",
        "val_4_t": "Skalbarhet", "val_4_d": "Nya discipliner ansluts till samma ramverk.",

        "sec_loop_kicker": "Arbetsflöde", "sec_loop_title": "Så fungerar Builtly", "sec_loop_sub": "En deterministisk fyra-stegs process.",
        "loop_1_t": "Input", "loop_1_d": "Ladda upp filer och data på ett ställe.",
        "loop_2_t": "AI Analys", "loop_2_d": "Plattformen kontrollerar regelverk och skriver utkast.",
        "loop_3_t": "QA & Signering", "loop_3_d": "Granskning och digital signering.",
        "loop_4_t": "Output", "loop_4_d": "Färdigt dokument för bygglov.",

        "mod_sec_kicker": "Moduler", "mod_sec_title": "Specialiserade agenter", "mod_sec_sub": "Varje modul delar samma portal och kvalitetskontroll.",
        "mod_sec1": "Tillgängligt nu", "mod_sec2": "Roadmap och tidiga skeden",
        "mod_sec3": "Hållbarhet & Säkerhet", "mod_sec3_sub": "Integrerade tjänster för miljöuppföljning, säkerhet och certifiering, anpassade för att skapa ansvarsfulla projekt.",

        "m_geo_t": "GEO / MILJÖ", "m_geo_d": "Analyserar labbfiler. Klassificerar massor och åtgärdsplaner.", "m_geo_in": "XLSX / CSV + Karta", "m_geo_out": "Åtgärdsplan", "m_geo_btn": "Öppna Geo",
        "m_aku_t": "AKUSTIK", "m_aku_d": "Läser bullerkartor och planritningar. Genererar fasadkrav.", "m_aku_in": "Bullerkarta + Plan", "m_aku_out": "Akustikrapport", "m_aku_btn": "Öppna Akustik",
        "m_brann_t": "BRAND - Koncept", "m_brann_d": "Utvärderar arkitektur mot BBR. Definierar brandceller.", "m_brann_in": "Ritningar + Klass", "m_brann_out": "Brandkoncept", "m_brann_btn": "Öppna Brand",
        
        "m_ark_t": "ARK - Förstudie", "m_ark_d": "Tomtanalys och volymbedömning för tidiga skeden.", "m_ark_in": "Detaljplan + Tomt", "m_ark_out": "Förstudie", "m_ark_btn": "Öppna ARK",
        "m_rib_t": "Konstruktion", "m_rib_d": "Konceptuella strukturkontroller och byggfysik.", "m_rib_in": "Sektion + Laster", "m_rib_out": "Koncept-PM", "m_rib_btn": "Öppna Konstruktion",
        "m_tra_t": "TRAFIK", "m_tra_d": "Trafikalstring, parkering och logistik.", "m_tra_in": "Situationsplan", "m_tra_out": "Trafik-PM", "m_tra_btn": "Öppna Trafik",
        
        "m_sha_t": "SHA-Plan (Säkerhet)", "m_sha_d": "Säkerhet, hälsa och arbetsmiljö. Genererar rutiner för byggarbetsplatsen.", "m_sha_in": "Projektdata + Risker", "m_sha_out": "Komplett SHA-plan", "m_sha_btn": "Öppna SHA",
        "m_breeam_t": "BREEAM Assistent", "m_breeam_d": "Tidig bedömning av BREEAM-krav och materialstrategi.", "m_breeam_in": "Byggdata + Ambition", "m_breeam_out": "BREEAM Pre-assessment", "m_breeam_btn": "Öppna BREEAM",
        "m_mop_t": "MOP (Miljöplan)", "m_mop_d": "Miljöuppföljningsplan för avfall, återbruk och utsläpp.", "m_mop_in": "Projektdata + Miljömål", "m_mop_out": "MOP Dokument", "m_mop_btn": "Öppna MOP",

        "btn_dev": "Under utveckling",

        "cta_title": "Starta ett projekt. Ladda upp data.",
        "cta_desc": "Builtly kombinerar insamling, AI och professionell signering i en portal.",
        "cta_btn1": "Starta i Project Setup", "cta_btn2": "Gå till QA-kö",

        "footer_copy": "AI-assisted engineering. Human-verified. Compliance-grade.",
        "footer_meta": "© 2026 Builtly Engineering AS. Alla rättigheter förbehållna."
    },
    "🇩🇰 Dansk": {
        "rule_set": "Danmark (BR18)",
        "eyebrow": "Builtly Workflow",
        "title": "Fra <span class='accent'>rådata</span> til underskrevne leverancer.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Upload projektdata, lad AI validere og udarbejde rapporten – før junior-QA og senior-signering gør det klar.",
        "btn_setup": "Start i Project Setup",
        "btn_qa": "Åbn QA & Sign-off",
        "proofs": ["Regelstyret AI", "Revisionsspor", "PDF + DOCX", "Digital Signatur", "Struktureret QA"],
        
        "why_kicker": "Hvorfor Builtly?",
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparelse", "stat1_d": "Reduktion af manuelt rapportarbejde",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssikring", "stat2_d": "Digital QA og signering af fagansvarlig",
        "stat3_v": "PDF + DOCX", "stat3_t": "Komplette rapporter", "stat3_d": "Med bilag og sporbarhed",
        "stat4_v": "Sporbarhed", "stat4_t": "Dokumentation", "stat4_d": "Versionskontrol fra input til PDF",

        "sec_val_kicker": "Kerneprodukt", "sec_val_title": "Portal først. Moduler under.", "sec_val_sub": "Builtly er et fælles system for projektoprettelse, AI-behandling og kvalitetssikring.",
        "val_1_t": "Kundeportal", "val_1_d": "Oprettelse, input og dokumentgenerering i ét flow.",
        "val_2_t": "Regelstyret AI", "val_2_d": "AI opererer inden for eksplicitte lovkrav og skabeloner.",
        "val_3_t": "QA og Signering", "val_3_d": "Junior validerer. Senior giver endelig godkendelse.",
        "val_4_t": "Skalerbarhet", "val_4_d": "Nye fagområder tilsluttes samme rammeværk.",

        "sec_loop_kicker": "Arbejdsgang", "sec_loop_title": "Sådan fungerer Builtly", "sec_loop_sub": "En deterministisk fire-trins proces.",
        "loop_1_t": "Input", "loop_1_d": "Upload filer og data ét sted.",
        "loop_2_t": "AI Analyse", "loop_2_d": "Platformen tjekker bygningsreglement og skriver udkast.",
        "loop_3_t": "QA & Signering", "loop_3_d": "Gennemgang og digital signatur.",
        "loop_4_t": "Output", "loop_4_d": "Færdigt dokument til byggetilladelse.",

        "mod_sec_kicker": "Moduler", "mod_sec_title": "Specialiserede agenter", "mod_sec_sub": "Hvert modul deler samme portal og kvalitetskontrol.",
        "mod_sec1": "Tilgængelig nu", "mod_sec2": "Roadmap",
        "mod_sec3": "Bæredygtighed & Sikkerhed", "mod_sec3_sub": "Integrerede tjenester til miljøopfølgning, sikkerhed og certificering, skræddersyet til ansvarlige projekter.",

        "m_geo_t": "GEO / MILJØ", "m_geo_d": "Analyserer lab-filer og udarbejder miljøhandlingsplaner.", "m_geo_in": "XLSX / CSV + Kort", "m_geo_out": "Handlingsplan", "m_geo_btn": "Åbn Geo",
        "m_aku_t": "AKUSTIK", "m_aku_d": "Læser støjkort. Genererer krav til facade.", "m_aku_in": "Støjkort + Plan", "m_aku_out": "Akustikrapport", "m_aku_btn": "Åbn Akustik",
        "m_brann_t": "BRAND", "m_brann_d": "Vurderer arkitektur mod BR18. Definerer brandceller.", "m_brann_in": "Tegninger + Klasse", "m_brann_out": "Brandstrategi", "m_brann_btn": "Åbn Brand",
        
        "m_ark_t": "ARK - Studie", "m_ark_d": "Grundanlyse og volumen for tidlige faser.", "m_ark_in": "Lokalplan + Grund", "m_ark_out": "Mulighedsstudie", "m_ark_btn": "Åbn ARK",
        "m_rib_t": "Konstruktion", "m_rib_d": "Konceptuelle strukturtjek og bygningsfysik.", "m_rib_in": "Snit + Laster", "m_rib_out": "Konceptnotat", "m_rib_btn": "Åbn Konstruktion",
        "m_tra_t": "TRAFIK", "m_tra_d": "Trafikgenerering og parkering.", "m_tra_in": "Situationsplan", "m_tra_out": "Trafiknotat", "m_tra_btn": "Åbn Trafik",
        
        "m_sha_t": "SHA-Plan (Sikkerhed)", "m_sha_d": "Sikkerhed, sundhed og arbejdsmiljø. Genererer rutiner for byggepladsen.", "m_sha_in": "Projektdata + Risici", "m_sha_out": "Komplet SHA-plan", "m_sha_btn": "Åbn SHA",
        "m_breeam_t": "BREEAM Assistent", "m_breeam_d": "Tidlig vurdering af BREEAM potentiale og materialestrategi.", "m_breeam_in": "Byggedata + Ambition", "m_breeam_out": "BREEAM Pre-assessment", "m_breeam_btn": "Åbn BREEAM",
        "m_mop_t": "MOP (Miljøplan)", "m_mop_d": "Miljøopfølgningsplan for affald, genbrug og udledning.", "m_mop_in": "Projektdata + Miljømål", "m_mop_out": "MOP Dokument", "m_mop_btn": "Åbn MOP",

        "btn_dev": "Under udvikling",

        "cta_title": "Start et projekt. Upload data.",
        "cta_desc": "Builtly kombinerer dataindsamling, AI og faglig signering i én portal.",
        "cta_btn1": "Start i Project Setup", "cta_btn2": "Gå til QA",

        "footer_copy": "AI-assisted engineering. Human-verified. Compliance-grade.",
        "footer_meta": "© 2026 Builtly Engineering AS. Alle rettigheder forbeholdes."
    }
}

# Hent tekster for valgt språk
lang = TEXTS.get(st.session_state.app_lang, TEXTS["🇳🇴 Norsk"])

# -------------------------------------------------
# 4) PAGE MAP & SMART SØKER
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
# 5) HELPERS & ANTI-BUG RENDERER
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

def top_link(page_key: str, label: str, kind: str = "ghost") -> str:
    href = href_or_none(page_key)
    if href:
        return f'<a href="{href}" target="_self" class="top-link {kind}">{label}</a>'
    return f'<span class="top-link {kind} disabled">{label}</span>'

def hero_action(page_key: str, label: str, kind: str = "primary") -> str:
    href = href_or_none(page_key)
    if href:
        return f'<a href="{href}" target="_self" class="hero-action {kind}">{label}</a>'
    return f'<span class="hero-action {kind} disabled">{label}</span>'

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

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
                <strong>Input:</strong> {input_text}<br/>
                <strong>Output:</strong> {output_text}
            </div>
            <div class="module-cta-wrap">
                {action_html}
            </div>
        </div>
    """

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

# -------------------------------------------------
# 6) CSS (Oppdatert Footer og Horisontal CTA)
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
        padding-bottom: 2rem !important; /* Redusert padding nederst på siden */
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

    [data-testid="stSelectbox"] { margin-bottom: 0 !important; width: 150px; float: right; }
    [data-testid="stSelectbox"] label { display: none !important; }
    [data-testid="stSelectbox"] > div > div {
        background-color: rgba(255,255,255,0.05) !important; color: #f5f7fb !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important;
        min-height: 42px !important; padding-left: 10px !important; cursor: pointer;
    }
    [data-testid="stSelectbox"] > div > div:hover { border-color: var(--accent) !important; background-color: rgba(255,255,255,0.08) !important; }

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
        margin-bottom: 1.25rem;
        height: 560px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    .hero-panel {
        background: rgba(20, 35, 50, 0.4);
        border: 1px solid var(--stroke);
        border-radius: var(--radius-xl);
        padding: 2.5rem;
        height: 560px;
        display: flex;
        flex-direction: column;
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

    .panel-title {
        font-size: 0.86rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
        margin-bottom: 0.85rem;
    }

    .mini-stat {
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--stroke);
        border-radius: 16px;
        padding: 1.1rem 1.2rem;
        margin-bottom: 0.8rem;
        flex: 1;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    .mini-stat:last-child {
        margin-bottom: 0;
    }

    .mini-stat-value {
        font-size: 1.35rem;
        font-weight: 750;
        color: var(--text);
        line-height: 1.1;
    }

    .mini-stat-label {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
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

    /* CSS FIKS FOR PERFEKT SYMMETRI */
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

    /* HORISONTAL CTA BOKS (LØSER DET STORE TOMROMMET) */
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

    /* STRAM OG ELEGANT FOOTER */
    .footer-block {
        text-align: center;
        margin-top: 2.5rem; /* Redusert kraftig */
        padding-top: 1.5rem; /* Redusert */
        padding-bottom: 1rem;
        border-top: 1px solid rgba(120,145,170,0.15);
    }

    .footer-name {
        font-size: 1.02rem;
        color: var(--text);
        margin-bottom: 0.35rem;
        font-weight: 650;
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
        .trust-grid, .loop-grid, .module-grid {
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

    @media (max-width: 760px) {
        .trust-grid, .loop-grid, .module-grid {
            grid-template-columns: 1fr;
        }
        .hero, .hero-panel {
            height: auto;
            min-height: auto;
        }
        .hero-title {
            max-width: none;
        }
        .brand-logo {
            height: 60px;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 7) TOP BAR (Kun Logo og Språk)
# -------------------------------------------------
top_l, top_r = st.columns([5, 1])

with top_l:
    logo_uri = logo_data_uri()
    logo_html = f'<img src="{logo_uri}" class="brand-logo" alt="Builtly logo" />' if logo_uri else '<div class="brand-name">Builtly</div>'
    render_html(f'<div class="top-shell" style="margin-bottom: 0;"><div class="brand-left">{logo_html}</div></div>')

with top_r:
    st.markdown("<div style='margin-top: 1.25rem;'></div>", unsafe_allow_html=True)
    valgt_språk = st.selectbox(
        "Språk", 
        list(TEXTS.keys()), 
        index=list(TEXTS.keys()).index(st.session_state.app_lang)
    )
    if valgt_språk != st.session_state.app_lang:
        st.session_state.app_lang = valgt_språk
        st.session_state.project_data["land"] = TEXTS[valgt_språk]["rule_set"]
        st.rerun()

st.markdown("<div style='margin-bottom: 2rem;'></div>", unsafe_allow_html=True)

# -------------------------------------------------
# 8) HERO (50/50 Symmetri)
# -------------------------------------------------
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
                {"".join([f'<div class="proof-chip">{p}</div>' for p in lang['proofs']])}
            </div>
        </div>
        """
    )

with right:
    render_html(
        f"""
        <div class="hero-panel">
            <div class="panel-title">{lang['why_kicker']}</div>
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
            <div class="mini-stat" style="margin-bottom:0;">
                <div class="mini-stat-value">{lang['stat4_v']}</div>
                <div class="mini-stat-label"><b>{lang['stat4_t']}</b><br>{lang['stat4_d']}</div>
            </div>
        </div>
        """
    )

# -------------------------------------------------
# 9) KJERNEPRODUKT & ARBEIDSFLYT
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
# 10) MODULER
# -------------------------------------------------
available_cards = [
    module_card("geo", "🌍", "Phase 1 - Priority", "badge-priority", lang["m_geo_t"], lang["m_geo_d"], lang["m_geo_in"], lang["m_geo_out"], lang["m_geo_btn"]),
    module_card("akustikk", "🔊", "Phase 2", "badge-phase2", lang["m_aku_t"], lang["m_aku_d"], lang["m_aku_in"], lang["m_aku_out"], lang["m_aku_btn"]),
    module_card("brann", "🔥", "Phase 2", "badge-phase2", lang["m_brann_t"], lang["m_brann_d"], lang["m_brann_in"], lang["m_brann_out"], lang["m_brann_btn"])
]

roadmap_cards = [
    module_card("mulighetsstudie", "📐", "Early phase", "badge-early", lang["m_ark_t"], lang["m_ark_d"], lang["m_ark_in"], lang["m_ark_out"], lang["m_ark_btn"]),
    module_card("konstruksjon", "🏢", "Roadmap", "badge-roadmap", lang["m_rib_t"], lang["m_rib_d"], lang["m_rib_in"], lang["m_rib_out"], lang["m_rib_btn"]),
    module_card("trafikk", "🚦", "Roadmap", "badge-roadmap", lang["m_tra_t"], lang["m_tra_d"], lang["m_tra_in"], lang["m_tra_out"], lang["m_tra_btn"])
]

sustainability_cards = [
    module_card("sha", "🦺", "Compliance", "badge-priority", lang["m_sha_t"], lang["m_sha_d"], lang["m_sha_in"], lang["m_sha_out"], lang["m_sha_btn"]),
    module_card("breeam", "🌿", "Certification", "badge-phase2", lang["m_breeam_t"], lang["m_breeam_d"], lang["m_breeam_in"], lang["m_breeam_out"], lang["m_breeam_btn"]),
    module_card("mop", "♻️", "Environment", "badge-roadmap", lang["m_mop_t"], lang["m_mop_d"], lang["m_mop_in"], lang["m_mop_out"], lang["m_mop_btn"])
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
# 11) CTA BAND (NÅ HORISONTAL PÅ DESKTOP!)
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
# 12) FOOTER (STRAM OG TETT)
# -------------------------------------------------
render_html(
    f"""
    <div class="footer-block">
        <div class="footer-copy">{lang['footer_copy']}</div>
        <div class="footer-meta">{lang['footer_meta']}</div>
    </div>
    """
)
