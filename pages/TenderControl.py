from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from builtly_module_kit import (
    configure_page,
    dataframe_download,
    json_download,
    render_metric_cards,
    render_panel,
    render_project_snapshot,
    render_section,
    render_hero,
    sample_revision_trace,
)

project = configure_page("Builtly | Anbudskontroll", "📑")

render_hero(
    eyebrow="Tender Control",
    title="Anbudskontroll som finner hull før markedet gjør det.",
    subtitle=(
        "Sammenstill konkurransegrunnlag, beskrivelser, tegninger, IFC/PDF og tilbudsdokumenter i én arbeidsflate. "
        "Modulen lager avviksmatrise, mangelliste, uklarhetslogg, scopesammenstilling og forslag til spørsmål før innlevering."
    ),
    pills=[
        "Entreprenør", "Rådgiver", "Utbygger", "Audit trail", "RFI-generator"
    ],
    badge="Horizontal engine",
)

uploaded_docs = st.session_state.get("tender_uploaded_docs", [])

with st.container():
    left, right = st.columns([1.25, 0.75], gap="large")
    with left:
        render_section(
            "Inntak og kontrollparametere",
            "Definer hva som inngår i tilbudspakken og hvilke kontroller Builtly skal prioritere i første analyse.",
            "Tender intake",
        )
        with st.form("tender_control_form"):
            c1, c2 = st.columns(2)
            with c1:
                procurement_mode = st.selectbox(
                    "Anskaffelsesform",
                    ["Totalentreprise", "Utførelsesentreprise", "Samspillsentreprise", "Design & Build"],
                    index=0,
                )
                discipline_focus = st.multiselect(
                    "Fag/scope som skal kvalitetssikres først",
                    [
                        "ARK", "RIB", "RIV", "RIE", "Brann", "Akustikk", "Geo", "Trafikk", "SHA", "MOP", "BREEAM"
                    ],
                    default=["ARK", "RIB", "Geo", "Brann"],
                )
                submission_deadline = st.date_input("Tilbudsfrist")
                include_bid_documents = st.toggle("Tilbudsdokumenter er lastet opp", value=True)
            with c2:
                packages = st.multiselect(
                    "Pakker / delentrepriser",
                    [
                        "Grunnarbeid", "Betong", "Stål", "Fasade", "Tømrer", "Tak", "VVS", "Elektro", "Utomhus"
                    ],
                    default=["Grunnarbeid", "Betong", "Fasade", "VVS", "Elektro"],
                )
                qa_level = st.select_slider(
                    "Kontrolldybde",
                    options=["Lett", "Standard", "Dyp", "Pre-bid review"],
                    value="Dyp",
                )
                required_outputs = st.multiselect(
                    "Ønskede leveranser",
                    [
                        "Avviksmatrise",
                        "Mangelliste",
                        "Uklarhetslogg",
                        "Scopesammenstilling",
                        "Forslag til spørsmål/RFI",
                        "Submission readiness",
                    ],
                    default=[
                        "Avviksmatrise",
                        "Mangelliste",
                        "Uklarhetslogg",
                        "Scopesammenstilling",
                        "Forslag til spørsmål/RFI",
                    ],
                )
                bid_value_mnok = st.number_input("Estimert tilbudsverdi (MNOK)", min_value=1.0, value=120.0, step=1.0)

            files = st.file_uploader(
                "Last opp konkurransegrunnlag, tegninger, IFC/PDF og tilbudsdokumenter",
                type=["pdf", "ifc", "xlsx", "xls", "docx", "csv", "zip"],
                accept_multiple_files=True,
                key="tender_files",
            )
            notes = st.text_area(
                "Prosjektspesifikke forhold som bør vektes høyt",
                value="Særskilt fokus på grensesnitt mellom grunnarbeid, betong og fasade. Kontroller at rigg/logistikk, SHA og ytre miljø er konsistente i alle dokumenter.",
                height=110,
            )
            submitted = st.form_submit_button("Kjør anbudskontroll")

        if submitted:
            st.session_state.tender_uploaded_docs = [f.name for f in files] if files else []
            uploaded_docs = st.session_state.tender_uploaded_docs

        docs_count = max(len(uploaded_docs), 6 if include_bid_documents else 4)
        package_count = len(packages)
        scope_gaps = max(1, 8 - package_count)
        ambiguity_count = max(2, len(discipline_focus) + (2 if qa_level in {"Dyp", "Pre-bid review"} else 0))
        revision_conflicts = 2 if include_bid_documents else 1
        readiness = max(42, 92 - scope_gaps * 7 - revision_conflicts * 5)

        render_metric_cards(
            [
                {"label": "Indekserte dokumenter", "value": f"{docs_count}", "desc": "Konkurransegrunnlag, tegninger og bid package i én kontrollsløyfe."},
                {"label": "Scope-hull", "value": f"{scope_gaps}", "desc": "Pakker eller grensesnitt der grunnlag eller ansvar må avklares."},
                {"label": "Uklarheter / RFI", "value": f"{ambiguity_count}", "desc": "Punkter der teksten bør presiseres før innlevering."},
                {"label": "Submission readiness", "value": f"{readiness}%", "desc": "Foreløpig modenhet basert på kompletthet, avvik og dokumentkonsistens."},
            ]
        )

        tabs = st.tabs([
            "Avviksmatrise",
            "Mangelliste",
            "Uklarheter & RFI",
            "Scope / pakker",
            "Revisjonslogg",
        ])

        deviations = pd.DataFrame(
            [
                {
                    "Fag": "ARK / Geo",
                    "Tema": "Adkomst og rigg",
                    "Alvorlighet": "Høy",
                    "Kilde": "Beskrivelse kap. B2 vs situasjonsplan",
                    "Forslag": "Avklar snuplass, riggområde og fremdriftstakt i samme dokumentpakke.",
                },
                {
                    "Fag": "Betong / Fasade",
                    "Tema": "Toleranser og grensesnitt",
                    "Alvorlighet": "Middels",
                    "Kilde": "Fasadetegning vs RIB-notat",
                    "Forslag": "Legg inn eksplisitt grensesnittmatrise med ansvar og kontrollpunkter.",
                },
                {
                    "Fag": "SHA / MOP",
                    "Tema": "Masselogistikk og ytre miljø",
                    "Alvorlighet": "Høy",
                    "Kilde": "SHA-planutkast / miljøkrav",
                    "Forslag": "Sikre at massedisponering, støy og støv tiltak er samordnet mot faseplan.",
                },
                {
                    "Fag": "VVS / Elektro",
                    "Tema": "Underentreprisegrenser",
                    "Alvorlighet": "Lav",
                    "Kilde": "Tilbudsbok",
                    "Forslag": "Spesifiser grensesnitt for montasje, testing og idriftsettelse.",
                },
            ]
        )
        missing = pd.DataFrame(
            [
                {"Kategori": "Dokument", "Manglende element": "Samlet tegningsliste med revisjonsstatus", "Konsekvens": "Feil dokumentgrunnlag kan prises", "Anbefaling": "Generer konsolidert dokumentindeks"},
                {"Kategori": "Ansvar", "Manglende element": "Tydelig ansvar for midlertidig rigg/faseomlegging", "Konsekvens": "Uklar pris og risikoallokering", "Anbefaling": "Legg inn ansvarsmatrise i tilbudsgrunnlaget"},
                {"Kategori": "Scope", "Manglende element": "Beskrivelse av utomhusleveranser i grensesnitt mot VA", "Konsekvens": "Fare for dobbeltprising eller hull", "Anbefaling": "Klargjør scope før utsendelse"},
            ]
        )
        rfis = pd.DataFrame(
            [
                {"Prioritet": "1", "Spørsmål": "Hvilke dokumenter er gjeldende ved motstrid mellom fasadeplan og snitt?", "Eier": "Tilbudsleder", "Status": "Klar til utsendelse"},
                {"Prioritet": "2", "Spørsmål": "Er SHA-kravene priset som egne rigg- og driftsposter eller inngår de i delentreprisene?", "Eier": "HMS/tilbud", "Status": "Må verifiseres"},
                {"Prioritet": "3", "Spørsmål": "Skal massedisponering prises som del av grunnarbeid eller miljøleveranse?", "Eier": "Geo / kalkyle", "Status": "Må verifiseres"},
            ]
        )
        scope = pd.DataFrame(
            [
                {"Pakke": pkg, "Status": "Dekket" if i < max(1, len(packages) - 1) else "Krevende grensesnitt", "Dokumentgrunnlag": "OK" if i % 2 == 0 else "Mangler presisering", "Kommentar": "Kontroller mengder og delingslinjer"}
                for i, pkg in enumerate(packages or ["Grunnarbeid", "Betong", "Fasade"])
            ]
        )

        with tabs[0]:
            st.dataframe(deviations, use_container_width=True, hide_index=True)
            dataframe_download(deviations, "Last ned avviksmatrise (.csv)", "tender_avviksmatrise.csv")
        with tabs[1]:
            st.dataframe(missing, use_container_width=True, hide_index=True)
            dataframe_download(missing, "Last ned mangelliste (.csv)", "tender_mangelliste.csv")
        with tabs[2]:
            st.dataframe(rfis, use_container_width=True, hide_index=True)
            dataframe_download(rfis, "Last ned RFI-utkast (.csv)", "tender_rfi.csv")
        with tabs[3]:
            st.dataframe(scope, use_container_width=True, hide_index=True)
            dataframe_download(scope, "Last ned scope-sammenstilling (.csv)", "tender_scope.csv")
        with tabs[4]:
            st.dataframe(sample_revision_trace(), use_container_width=True, hide_index=True)

        json_download(
            {
                "module": "Tender Control",
                "procurement_mode": procurement_mode,
                "qa_level": qa_level,
                "deadline": str(submission_deadline),
                "uploaded_docs": uploaded_docs,
                "discipline_focus": discipline_focus,
                "packages": packages,
                "readiness": readiness,
                "notes": notes,
            },
            "Eksporter kontrollsammendrag (.json)",
            "tender_control_summary.json",
        )

    with right:
        render_section(
            "Operativt bilde",
            "Denne modulen er designet som en horisontal motor som både entreprenører, rådgivere og utbyggere kan bruke uavhengig av land og lokal ansvarsrett.",
            "Builtly edge",
        )
        render_project_snapshot(project)
        render_panel(
            "Hva modulen effektiviserer",
            "I stedet for å bygge et nytt konsulentlag i hvert marked, gjør Builtly prosessen rundt anbud mer standardisert, sporbar og raskere.",
            [
                "Sammenstilling av konkurransegrunnlag, tegninger og tilbudsdokumenter",
                "Automatisk avviksmatrise og uklarhetslogg før innlevering",
                "Konsistent scope-/pakkeoversikt mellom fag og entrepriser",
                "Manuell overstyring, revisjonslogg og eksport til kjente formater",
            ],
            tone="blue",
            badge="Workflow infrastructure",
        )
        render_panel(
            "Neste anbefalte steg",
            "Dette er det jeg ville gjort før modulen brukes i live pilot med betalende kunde.",
            [
                "Koble dokumentparser mot konkurransegrunnlag og dokumentindeks",
                "Legg på dokument-sammenligning mellom revisjoner og tilbudsbok",
                "Bygg scoremodell for submission readiness og risikoallokering",
                "La brukere markere avvik som godkjent, ignorert eller sendt som RFI",
            ],
            tone="gold",
            badge="Pilot backlog",
        )
        st.metric("Anbefalt prismodell", "Abonnement + per prosjekt", "God for entreprenør, rådgiver og developer")
        st.metric("Estimert tidskutt", "30–60%", "I sammenstilling, kontroll og spørsmålspakker")

render_section(
    "Hvorfor denne modulen bør bygges først",
    "Anbudskontroll er prosessnær, repeterbar og mindre avhengig av lokal ansvarsrett enn rene fagrapporter. Derfor er den godt egnet som internasjonal abonnementsmodul.",
    "Go-to-market",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "Primære kundetyper",
        "Først utbyggere og entreprenører som kjøper fart, risikoreduksjon og bedre fremdrift. Deretter rådgivere som vil komprimere QA og dokumentkontroll.",
        [
            "Entreprenør: submission readiness, dokumentkonsistens, RFI-generator",
            "Utbygger: tryggere scope før utsendelse og færre hull i konkurransegrunnlaget",
            "Rådgiver: raskere kvalitetssikring uten å miste kontroll eller ansvar",
        ],
        tone="green",
        badge="ICP",
    )
with c2:
    render_panel(
        "MVP-leveranser",
        "Det første produktet bør være lite nok til å selges raskt, men sterkt nok til å gi målbar effekt.",
        [
            "Dokumentindeks og revisjonskontroll",
            "Avviksmatrise og mangelliste",
            "Uklarhetslogg / RFI-utkast",
            "Scope-sammenstilling mellom pakker og fag",
        ],
        tone="blue",
        badge="MVP",
    )
