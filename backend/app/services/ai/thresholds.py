"""Umbrales del motor de IA, centralizados y configurables.

Hasta ahora estaban hardcodeados y atados a soles/Perú dentro de rules.py y
statistics.py. Este módulo los reúne en un solo lugar y expone un punto de
extensión (`get_thresholds_for_org`) para, más adelante, leer overrides por
organización o vertical desde la base de datos.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AIThresholds:
    # --- Capa 1: reglas ---
    cpl_critical_multiplier: float = 2.0        # CPL > N x objetivo => crítico
    frequency_saturation: float = 3.0           # frecuencia media > N => saturación
    ctr_min_pct: float = 0.5                     # CTR mínimo aceptable (%)
    min_impressions_for_ctr: int = 200          # impresiones mínimas para evaluar CTR
    min_impressions_for_zero_conv: int = 500    # impresiones mínimas para alertar 0 conv
    target_cpl_floor: float = 1.50              # piso del CPL objetivo dinámico
    target_cpl_ceiling: float = 10.00           # techo del CPL objetivo dinámico
    target_cpl_fallback: float = 3.50           # CPL objetivo si no hay histórico
    placement_min_spend: float = 15.0           # gasto mínimo para evaluar un placement
    instagram_cpl_ceiling: float = 5.0          # CPL de Instagram considerado ineficiente
    reels_cpl_advantage: float = 0.7            # Reels es oportunidad si CPL < feed * N

    # --- Capa 2: estadística ---
    min_history_days: int = 7                   # días mínimos para análisis estadístico
    trend_window_days: int = 3                  # ventana reciente
    base_window_days: int = 14                  # ventana base de comparación
    cpl_trend_pct: float = 30.0                 # variación de CPL (%) que dispara tendencia
    zscore_anomaly: float = 2.0                 # Z-score que marca anomalía
    min_std_dev: float = 0.1                    # desviación mínima para evitar ruido
    min_points_for_zscore: int = 5              # puntos mínimos para calcular Z-score


DEFAULT_THRESHOLDS = AIThresholds()


def get_thresholds_for_org(organization: Optional[object] = None) -> AIThresholds:
    """Devuelve los umbrales aplicables a una organización.

    Punto de extensión: hoy retorna los defaults; en el futuro puede leer
    overrides por organización/vertical (p. ej. una tabla TenantRules).
    """
    return DEFAULT_THRESHOLDS
