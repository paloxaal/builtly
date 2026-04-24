"""
Kjør dette scriptet for å verifisere at uke 2+3-koden er riktig deployet.

Bruk:
    cd /path/to/builtly && python VERIFY_DEPLOY.py

Dette vil:
  1. Sjekke at builtly/typologi_primitiver.py finnes
  2. Sjekke at builtly/geometry.py har _place_typologi_v2
  3. Kalle plan_karre_for_field direkte på et testfelt
  4. Printe om det ga 50×28m eller noe annet

Hvis output viser "KARRE 50×28m" → deploy er riktig og karré skal fungere
Hvis output viser feil eller gamle dimensjoner → geometry.py eller
typologi_primitiver.py er ikke oppdatert på serveren.
"""
from __future__ import annotations

import sys
import os


def main():
    print("=" * 60)
    print("BUILTLY UKE 2+3 DEPLOY-VERIFIKASJON")
    print("=" * 60)

    # 1. typologi_primitiver finnes?
    try:
        from builtly import typologi_primitiver
        print("✓ builtly.typologi_primitiver importert OK")
    except ImportError as exc:
        print(f"✗ FEIL: typologi_primitiver ikke funnet: {exc}")
        print("  → typologi_primitiver.py er IKKE deployet.")
        print("  → Kopier /path/to/uke1_2_3_deploy/builtly/typologi_primitiver.py")
        print("    inn i din builtly/-mappe.")
        sys.exit(1)

    # 2. geometry.py har _place_typologi_v2?
    try:
        from builtly import geometry
        if not hasattr(geometry, "_place_typologi_v2"):
            print("✗ FEIL: geometry._place_typologi_v2 finnes ikke")
            print("  → geometry.py er IKKE oppdatert.")
            print("  → Kopier /path/to/uke1_2_3_deploy/builtly/geometry.py")
            print("    inn i din builtly/-mappe.")
            sys.exit(1)
        print("✓ geometry._place_typologi_v2 funnet")
    except ImportError as exc:
        print(f"✗ FEIL: builtly.geometry kan ikke importeres: {exc}")
        sys.exit(1)

    # 3. Test plan_karre_for_field på et realistisk felt
    from shapely.geometry import box
    from builtly.typologi_primitiver import plan_karre_for_field, MasterplanProfile

    print()
    print("Tester plan_karre_for_field på 85×80m felt (6 800 m²)...")
    core = box(0, 0, 85, 80)
    plan = plan_karre_for_field(
        core,
        target_bra_m2=6200.0,
        target_building_count=1,
        floors_min=4,
        floors_max=6,
        profile=MasterplanProfile.FORSTAD,
    )

    if not plan.bygninger:
        print(f"✗ FEIL: plan gav 0 bygninger")
        sys.exit(1)

    b = plan.bygninger[0]
    print(f"  Bygg ID: {b.bygg_id}")
    print(f"  Dimensjoner: {b.length_m:.0f} × {b.depth_m:.0f} m")
    print(f"  Etasjer: {b.floors}")
    print(f"  Fotavtrykk: {b.footprint_m2:.0f} m²")
    print(f"  BRA: {b.bra_m2:.0f} m²")

    if abs(b.length_m - 50.0) < 2.0 and abs(b.depth_m - 28.0) < 2.0:
        print()
        print("✓ SUKSESS: Karré er 50×28m (Pål's arkitekt-spec)")
        print()
        print("Deploy er korrekt. Hvis du likevel ser smale L-former i")
        print("3D-terrengscenen, kan det være:")
        print("  - Streamlit-cache må tømmes (Cmd+Shift+R)")
        print("  - Python-prosessen må restartes for å laste nye moduler")
    else:
        print(f"✗ FEIL: Forventet 50×28m, fikk {b.length_m:.0f}×{b.depth_m:.0f}m")
        print("  → typologi_primitiver.py er en gammel versjon.")
        sys.exit(1)


if __name__ == "__main__":
    main()
