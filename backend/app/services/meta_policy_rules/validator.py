"""Motor de validación de textos publicitarios contra el catálogo de políticas de Meta Ads (Fase 6.7 / Roadmap 10)."""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RULES_FILE = Path(__file__).parent / "rules.json"


@dataclass
class PolicyViolation:
    rule_id: str
    category: str
    severity: str  # "BLOCKING" | "WARNING"
    title: str
    description: str
    remediation: str
    matched_text: str
    field_name: str


@dataclass
class PolicyValidationResult:
    is_compliant: bool
    blocking_violations: List[PolicyViolation] = field(default_factory=list)
    warnings: List[PolicyViolation] = field(default_factory=list)
    checked_rules_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_compliant": self.is_compliant,
            "has_blocking": len(self.blocking_violations) > 0,
            "blocking_violations": [
                {
                    "rule_id": v.rule_id,
                    "category": v.category,
                    "severity": v.severity,
                    "title": v.title,
                    "description": v.description,
                    "remediation": v.remediation,
                    "matched_text": v.matched_text,
                    "field": v.field_name,
                }
                for v in self.blocking_violations
            ],
            "warnings": [
                {
                    "rule_id": v.rule_id,
                    "category": v.category,
                    "severity": v.severity,
                    "title": v.title,
                    "description": v.description,
                    "remediation": v.remediation,
                    "matched_text": v.matched_text,
                    "field": v.field_name,
                }
                for v in self.warnings
            ],
            "checked_rules_count": self.checked_rules_count,
        }


class MetaPolicyValidator:
    """Validador estático y determinístico de políticas de anuncios de Meta."""

    def __init__(self, rules_path: Optional[Path] = None):
        self.rules_path = rules_path or RULES_FILE
        self._load_rules()

    def _load_rules(self):
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.version = data.get("version", "1.0")
                self.rules = data.get("rules", [])
        except Exception as e:
            logger.error(f"Error cargando reglas de políticas de Meta desde {self.rules_path}: {e}")
            self.version = "fallback"
            self.rules = []

    def validate(
        self,
        *,
        copy_text: Optional[str] = None,
        headline: Optional[str] = None,
        description: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> PolicyValidationResult:
        """Inspecciona todos los campos de texto del anuncio contra los patrones de políticas."""
        fields_to_check = {
            "copy_text": copy_text or "",
            "headline": headline or "",
            "description": description or "",
        }

        blocking: List[PolicyViolation] = []
        warnings: List[PolicyViolation] = []
        rules_checked = 0

        for rule in self.rules:
            rules_checked += 1
            rule_id = rule.get("id", "UNKNOWN")
            category = rule.get("category", "General")
            severity = rule.get("severity", "WARNING")
            title = rule.get("title", "")
            desc = rule.get("description", "")
            remediation = rule.get("remediation", "")
            patterns = rule.get("patterns", [])

            for field_name, field_value in fields_to_check.items():
                if not field_value:
                    continue

                for pattern in patterns:
                    match = re.search(pattern, field_value)
                    if match:
                        matched_snippet = match.group(0)
                        violation = PolicyViolation(
                            rule_id=rule_id,
                            category=category,
                            severity=severity,
                            title=title,
                            description=desc,
                            remediation=remediation,
                            matched_text=matched_snippet,
                            field_name=field_name,
                        )
                        if severity == "BLOCKING":
                            blocking.append(violation)
                        else:
                            warnings.append(violation)
                        # Avoid duplicate violation per rule per field
                        break

        is_compliant = len(blocking) == 0
        return PolicyValidationResult(
            is_compliant=is_compliant,
            blocking_violations=blocking,
            warnings=warnings,
            checked_rules_count=rules_checked,
        )


# Instancia singleton para importación directa
default_policy_validator = MetaPolicyValidator()
