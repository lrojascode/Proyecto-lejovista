"""Catálogo y validador preventivo de políticas de Meta Ads (Fase 6.7 / Roadmap 10)."""
from .validator import MetaPolicyValidator, PolicyValidationResult, PolicyViolation

__all__ = ["MetaPolicyValidator", "PolicyValidationResult", "PolicyViolation"]
