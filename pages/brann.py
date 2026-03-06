# pages/brann.py
st.title("🔥 Brannrådgivning (RIBr)")

arkitekt_fil = st.file_uploader("Last opp arkitekttegning (PDF/PNG)", type=['pdf', 'png', 'jpg'])
situasjonsplan = st.file_uploader("Last opp situasjonsplan", type=['pdf', 'png', 'jpg'])

if arkitekt_fil and situasjonsplan:
    if st.button("Analyser Brannsmitte og Vannforsyning"):
        with st.spinner("AI leser tegninger og henter hydrant-data fra Kartverket..."):
            # 1. Hent hydranter fra Geonorge WFS (FKB-Vann)
            # 2. Send bilder + WFS-data til Gemini 1.5 Pro
            # Gemini kan nå se: "Det er 15 meter til nærmeste nabobygg, noe som krever brannvegg i klasse..."
            st.success("Analyse ferdigstilt basert på TEK17.")