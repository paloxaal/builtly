import streamlit as st
import google.generativeai as genai
from fpdf import FPDF
import markdown
import os
from datetime import datetime

# (Gjenbruker PDF-klasse og grunnfunksjoner fra Miljø, men med Arkitekt-prompt)
# Lagre denne som en kopi av Miljø-fila, men endre prompten og input-feltene:
# Endre st.title til "🏗️ Mulighetsstudie"
# Endre input til tomt_areal = st.number_input("Tomteareal m2")
# Endre prompt til "Du er Builtly AIs sjefsarkitekt. Beregn BRA/BYA og leilighetsmiks."