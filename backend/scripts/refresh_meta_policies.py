#!/usr/bin/env python3
"""Script CLI para validar y refrescar el catálogo de políticas de Meta Ads (Fase 6.7 / Roadmap 10)."""
import json
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.services.meta_policy_rules.validator import MetaPolicyValidator, RULES_FILE


def main():
    print(f"=== Inspeccionando catálogo de políticas de Meta: {RULES_FILE} ===")
    if not RULES_FILE.exists():
        print(f"ERROR: Archivo {RULES_FILE} no encontrado.")
        sys.exit(1)

    with open(RULES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    version = data.get("version", "N/A")
    rules = data.get("rules", [])
    print(f"Versión de políticas: {version}")
    print(f"Total de reglas cargadas: {len(rules)}")

    validator = MetaPolicyValidator()

    # Casos de prueba conocidos
    test_cases = [
        ("¡Inscríbete hoy y aprende inglés!", True, "Copy seguro"),
        ("Pierde 10kg en 3 días con este método milagroso", False, "Salud engañosa"),
        ("Oportunidad de empleo solo para mujeres solteras", False, "Discriminación de empleo/género"),
        ("¿Estás endeudado y no sabes qué hacer?", False, "Atributos personales"),
        ("Gana dinero sin trabajar desde tu casa", False, "Finanzas engañosas"),
    ]

    print("\n--- Ejecutando casos de prueba contra el catálogo ---")
    all_passed = True
    for text, expected_compliant, label in test_cases:
        res = validator.validate(copy_text=text)
        is_ok = (res.is_compliant == expected_compliant)
        status = "PASSED" if is_ok else "FAILED"
        if not is_ok:
            all_passed = False
        print(f"[{status}] {label}: '{text[:40]}...' -> Compliant={res.is_compliant} (Esperado={expected_compliant})")
        if res.blocking_violations:
            for v in res.blocking_violations:
                print(f"       -> Bloqueado por: {v.rule_id} ({v.title}) | Coincidencia: '{v.matched_text}'")

    if all_passed:
        print("\n✅ Todas las validaciones de políticas funcionaron correctamente.")
        sys.exit(0)
    else:
        print("\n❌ Hubo discrepancias en la validación de políticas.")
        sys.exit(1)


if __name__ == "__main__":
    main()
