def clean_pdf_text(text):
    if not text:
        return ""
    # Ordbok over tegn som Helvetica hater -> og hva de skal erstattes med
    rep = {
        "–": "-",  # Lang tankestrek til vanlig bindestrek
        "—": "-",  # Em-dash til vanlig bindestrek
        "“": "\"", # Smarte anførselstegn
        "”": "\"", 
        "‘": "'",  # Smarte apostrofer
        "’": "'",
        "…": "...", # Ellipse-tegn
        "•": "*",   # Punkttegn
        "Ø": "O",   # Valgfritt: Hvis den fortsatt klager på særnorske tegn
        "æ": "ae", 
        "ø": "o", 
        "å": "a"
    }
    for old, new in rep.items():
        text = text.replace(old, new)
    
    # Denne linjen tvinger teksten til å bruke tegnsettet Latin-1 (som Helvetica støtter)
    # og fjerner alt annet som lager krøll.
    return text.encode('latin-1', 'replace').decode('latin-1')
