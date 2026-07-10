"""Recordatorios curados de políticas de anuncios de Meta, por rubro de negocio.

Contenido fijo y curado a mano — NO generado por IA. Las políticas de Meta son
un tema de cumplimiento real (una campaña mal etiquetada puede terminar en
desaprobación o suspensión de cuenta) y este motor ya tiene la regla explícita
de nunca inventar/asumir hechos (ver AI-1 en CampaignSuggester); usar un LLM
para resumir o citar políticas reales es más riesgoso que para sugerencias de
campaña, así que aquí se opta por contenido estático mantenido por el equipo.

Estos textos son recordatorios en lenguaje propio, no citas literales de Meta.
El texto autoritativo vive en https://www.facebook.com/policies/ads/ — el
dashboard siempre debe enlazar ahí, nunca presentar esto como la política
oficial completa.
"""
from typing import Any, Dict, List, TypedDict


class PolicyItem(TypedDict):
    title: str
    description: str


INDUSTRIES: List[Dict[str, str]] = [
    {"value": "telecom_cable", "label": "Telecom / Internet / Cable"},
    {"value": "salud_belleza", "label": "Salud y belleza"},
    {"value": "alimentos", "label": "Alimentos y restaurantes"},
    {"value": "inmobiliaria", "label": "Inmobiliaria"},
    {"value": "finanzas", "label": "Finanzas y préstamos"},
    {"value": "educacion", "label": "Educación"},
    {"value": "moda_retail", "label": "Moda y retail"},
    {"value": "automotriz", "label": "Automotriz"},
    {"value": "tecnologia", "label": "Tecnología / software"},
    {"value": "servicios_profesionales", "label": "Servicios profesionales"},
    {"value": "otros", "label": "Otros"},
]

GENERIC_POLICIES: List[PolicyItem] = [
    {
        "title": "Afirmaciones engañosas o exageradas",
        "description": (
            "Evita promesas de resultados garantizados, precios \"desde\" sin condiciones claras, "
            "o comparaciones no verificables. Meta rechaza anuncios con afirmaciones que no se "
            "puedan sustentar."
        ),
    },
    {
        "title": "Inferencia de atributos personales",
        "description": (
            "El copy o la imagen no deben implicar que conoces datos sensibles del usuario "
            "(salud, orientación, situación financiera, religión) a partir de haberlo segmentado."
        ),
    },
    {
        "title": "Texto excesivo en la imagen",
        "description": (
            "Las imágenes con mucho texto superpuesto reducen el alcance. Usa el texto del "
            "anuncio (headline/primary text) para el mensaje principal, no la imagen."
        ),
    },
    {
        "title": "\"Antes y después\" y contenido sensacionalista",
        "description": (
            "Imágenes de transformación corporal, choques emocionales o contenido alarmista "
            "suelen ser rechazados o limitados, incluso fuera del rubro salud/belleza."
        ),
    },
    {
        "title": "Destino del anuncio coherente con el mensaje",
        "description": (
            "El WhatsApp/página de destino debe ofrecer lo que el anuncio promete. Desajustes "
            "entre el copy y lo que el usuario encuentra al hacer clic son motivo de rechazo."
        ),
    },
]

INDUSTRY_POLICIES: Dict[str, List[PolicyItem]] = {
    "salud_belleza": [
        {
            "title": "Reclamos de salud y resultados garantizados",
            "description": (
                "No prometas curas, resultados garantizados ni uses testimonios médicos sin "
                "respaldo. Meta es especialmente estricto con suplementos, tratamientos estéticos "
                "y pérdida de peso."
            ),
        },
        {
            "title": "Imágenes de cuerpos y \"antes/después\"",
            "description": (
                "Evita primeros planos de partes del cuerpo enfocados en inseguridades (peso, "
                "piel, arrugas) y comparaciones de transformación física."
            ),
        },
    ],
    "finanzas": [
        {
            "title": "Requisitos reforzados para productos financieros",
            "description": (
                "Préstamos, tarjetas de crédito y productos de inversión están en la categoría de "
                "\"servicios financieros especiales\" — Meta puede pedir certificación adicional del "
                "anunciante antes de aprobar la campaña."
            ),
        },
        {
            "title": "Tasas, plazos y condiciones deben ser explícitos",
            "description": (
                "No anuncies \"préstamo garantizado\" o tasas sin condiciones. El costo real del "
                "crédito debe quedar claro o accesible con un clic."
            ),
        },
    ],
    "inmobiliaria": [
        {
            "title": "Riesgo de discriminación en segmentación",
            "description": (
                "Vivienda, empleo y crédito son categorías especiales en Meta: no se debe segmentar "
                "ni excluir por edad, género, código postal o intereses que actúen como proxy de "
                "raza/origen. Usa Advantage+ Audience en vez de segmentación manual estrecha."
            ),
        },
    ],
    "educacion": [
        {
            "title": "Anuncios dirigidos a menores de edad",
            "description": (
                "Si el servicio educativo es para niños/adolescentes, revisa las reglas de "
                "publicidad dirigida a menores (sin recolección de datos innecesaria, sin presión "
                "de compra directa al menor)."
            ),
        },
    ],
    "automotriz": [
        {
            "title": "Categoría de crédito/financiamiento vehicular",
            "description": (
                "Si el anuncio incluye financiamiento (no solo el vehículo), aplican las mismas "
                "reglas reforzadas que servicios financieros — evita prometer aprobación garantizada."
            ),
        },
    ],
    "alimentos": [
        {
            "title": "Etiquetado de marca y calidad de imagen",
            "description": (
                "No hay restricciones especiales de contenido, pero Meta penaliza fotos de baja "
                "calidad o que parezcan clickbait (ej. \"no lo vas a creer\") con menor entrega."
            ),
        },
    ],
    "tecnologia": [
        {
            "title": "Software y datos personales",
            "description": (
                "Si el producto recolecta datos del usuario (apps, CRMs, IA), el anuncio no puede "
                "prometer capacidades de vigilancia/espionaje ni ocultar que hay recolección de datos."
            ),
        },
    ],
    "moda_retail": [
        {
            "title": "Productos falsificados o de marca ajena",
            "description": (
                "No uses logos de marcas de terceros que no representas ni vendas réplicas — es de "
                "las causas más comunes de rechazo/suspensión en retail."
            ),
        },
    ],
    "servicios_profesionales": [
        {
            "title": "Resultados legales o profesionales garantizados",
            "description": (
                "Servicios legales, contables o de consultoría no deben prometer resultados "
                "específicos (\"ganamos tu caso\", \"aprobación segura\") sin matizar que dependen "
                "del caso."
            ),
        },
    ],
    "telecom_cable": [
        {
            "title": "Velocidad y cobertura anunciada",
            "description": (
                "Evita anunciar velocidades \"hasta X Mbps\" como si fueran garantizadas, o "
                "cobertura en zonas donde el servicio aún no está disponible."
            ),
        },
    ],
}


def get_policies_for_industry(industry: str | None) -> List[PolicyItem]:
    """Genéricas + específicas del rubro (vacío si no hay rubro o no hay match)."""
    specific = INDUSTRY_POLICIES.get(industry or "", [])
    return GENERIC_POLICIES + specific
