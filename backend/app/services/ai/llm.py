import logging
import json
import datetime
from typing import Dict, Any, Optional
import httpx
from app.config import settings
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.token_tracking import UsageContext, record_token_usage
from app.services.ai.thresholds import classify_campaign, compute_target_cpl

logger = logging.getLogger("lejovista.llm")

# Versión del prompt del Master Brief. Se persiste junto a la narrativa para
# poder trazar qué versión generó cada informe (AI-4).
# v2 (C-3): contexto enriquecido (target_cpl, comparativa 7d vs 7d previos,
# top/bottom campañas, pacing) + contrato de salida fijo en 5 secciones, en
# vez del prompt libre de v1 que solo mandaba un resumen plano de 7 días.
PROMPT_VERSION = "master-brief-v2"

# Versión del prompt de copy de campañas (Fase 3B del plan de creación de
# campañas). Se persiste en el CampaignDraft para trazar qué prompt generó
# cada copy (AI-4), igual que PROMPT_VERSION para el Master Brief.
# v2 (3.4, anexo 6.2): 2 variantes de headline/primary_text (el usuario elige
# en la UI), rubro de la organización inyectado, límites de caracteres de
# Meta instruidos y validados, response_format json_object cuando el
# proveedor lo soporta.
CAMPAIGN_COPY_PROMPT_VERSION = "campaign-copy-v2"

# Claves obligatorias del JSON de copy que debe devolver el LLM. headline y
# primary_text son ARRAYS de 2 variantes (v2); description/welcome_message
# siguen siendo un único string.
_REQUIRED_COPY_KEYS = {"headline", "primary_text", "description", "welcome_message"}
_VARIANT_KEYS = {"headline", "primary_text"}
_COPY_VARIANTS = 2

# Límites de caracteres de Meta para el Ad Creative (Graph API: adcreatives /
# object_story_spec.link_data). El copy_text libre (primary_text) no tiene
# límite estricto de Meta, pero headline y description sí.
META_HEADLINE_MAX_CHARS = 40
META_DESCRIPTION_MAX_CHARS = 30

# Modelos válidos por proveedor, para que el selector de Preferencias solo
# muestre los que realmente puede usar el proveedor activo (ver
# GET /organizations/ai-models). El proveedor activo depende de qué API key
# esté configurada (OPENAI_API_KEY > GEMINI_API_KEY > OPENROUTER_API_KEY),
# no de lo que elija cada organización — antes se podía guardar un modelo de
# un proveedor distinto al que realmente atendía las llamadas.
MODELS_BY_PROVIDER = {
    "openai": [
        {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini (rápido y económico)"},
        {"value": "gpt-5.4", "label": "GPT-5.4 (estándar)"},
        {"value": "gpt-5.4-nano", "label": "GPT-5.4 Nano (el más rápido)"},
    ],
    "gemini_direct": [
        {"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash (estándar)"},
        {"value": "gemini-2.5-pro", "label": "Gemini 2.5 Pro (avanzado)"},
        {"value": "gemini-3.5-flash", "label": "Gemini 3.5 Flash (más rápido y avanzado)"},
    ],
    "openrouter": [
        {"value": "google/gemini-3.5-flash", "label": "Gemini 3.5 Flash (más rápido y avanzado)"},
        {"value": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash (estándar)"},
        {"value": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro (avanzado)"},
        {"value": "anthropic/claude-3-5-sonnet", "label": "Claude 3.5 Sonnet (premium)"},
    ],
}


def _filter_insufficient_history(insights_summary: str) -> str:
    """C-3: el aviso informativo de "histórico insuficiente" (Capa 2) es ruido
    para el diagnóstico del LLM, no una alerta accionable — se filtra antes
    de mandarlo al prompt."""
    lines = [l for l in insights_summary.split("\n") if "Histórico insuficiente" not in l]
    text = "\n".join(l for l in lines if l.strip())
    return text or "Sin alertas relevantes en esta ventana."


def _build_master_brief_prompt(
    org: Organization,
    ad_account: AdAccount,
    insights_summary: str,
    raw_data_summary: Dict[str, Any],
    brief_context: Optional[Dict[str, Any]],
) -> str:
    """master-brief-v2 (C-3): contexto enriquecido (target_cpl, comparativa 7d
    vs 7d previos, top/bottom campañas, pacing) + contrato de salida fijo en
    5 secciones. Sin `brief_context` (compatibilidad hacia atrás), cae al
    resumen plano de v1."""
    if not brief_context:
        return f"""
        Eres un experto en Meta Ads y analista de crecimiento (Growth Analyst) para la marca LejoVista.
        Analiza los siguientes datos y alertas de campañas del cliente '{org.name}' (cuenta: '{ad_account.name}'):

        Alertas y Hallazgos Previos (Capas 1 y 2):
        {insights_summary}

        Resumen de Métricas de los Últimos 7 Días (ÚNICA fuente de verdad):
        {json.dumps(raw_data_summary, indent=2)}

        Responde con este contrato de salida (Markdown):
        ## Resumen / ## Ganadoras / ## En alerta / ## Fallidas / ## Prioridades de la semana
        Usa un tono profesional, directo y orientado a la acción (en español).
        """

    return f"""
Eres un experto en Meta Ads y analista de crecimiento (Growth Analyst) para la marca LejoVista.
Cliente: '{org.name}' — cuenta: '{ad_account.name}'. Moneda: {brief_context.get("currency", "PEN")}, país: {brief_context.get("country", "Perú")}.

CPL objetivo de esta cuenta (target_cpl, calibrado con su propio histórico real — clasifica SOLO contra este número, no uses umbrales propios): {brief_context.get("target_cpl")}
Métrica primaria: conversaciones (mensajes de WhatsApp iniciados; leads si la cuenta aún no tiene mensajería activa).

KPIs de los últimos 7 días: {json.dumps(brief_context.get("kpis_7d", {}), indent=2)}
KPIs de los 7 días previos (para comparar tendencia): {json.dumps(brief_context.get("kpis_prev_7d", {}), indent=2)}
Variación % vs el periodo previo: {json.dumps(brief_context.get("deltas_pct", {}), indent=2)}

Pacing (presupuesto diario total configurado vs gasto real diario promedio): {json.dumps(brief_context.get("pacing", {}), indent=2)}

Top 3 campañas por menor costo/conversación: {json.dumps(brief_context.get("top3", []), indent=2)}
Bottom 3 campañas por mayor costo/conversación (o sin conversiones): {json.dumps(brief_context.get("bottom3", []), indent=2)}

Breakdown agregado de la cuenta en los últimos 7 días (placement/edad/dispositivo/región):
{json.dumps(brief_context.get("segmentation", {}), indent=2)}

Alertas y hallazgos previos, ya calculados de forma determinista (Capas 1 y 2):
{insights_summary}

Responde EXACTAMENTE con este contrato de salida en Markdown, sin texto antes ni después:
## Resumen
(máximo 80 palabras: pacing, tendencia general vs el periodo previo, 1-2 cifras clave)
## Ganadoras
(campañas con costo/conversación por debajo del target_cpl — qué las hace funcionar)
## En alerta
(campañas cerca o por encima del target_cpl que aún no ameritan pausar)
## Fallidas
(campañas sin conversaciones o muy por encima del target_cpl — recomendación concreta)
## Prioridades de la semana
(exactamente 3 bullets accionables, ordenados por impacto)
"""


class OpenRouterClient:
    def __init__(self, api_key: Optional[str] = None):
        if settings.OPENAI_API_KEY:
            self.api_key = settings.OPENAI_API_KEY
            self.base_url = "https://api.openai.com/v1/chat/completions"
            self.is_gemini_direct = False
            self.provider = "openai"
        elif settings.GEMINI_API_KEY:
            self.api_key = settings.GEMINI_API_KEY
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            self.is_gemini_direct = True
            self.provider = "gemini_direct"
        else:
            self.api_key = api_key or settings.OPENROUTER_API_KEY
            self.base_url = "https://openrouter.ai/api/v1/chat/completions"
            self.is_gemini_direct = False
            self.provider = "openrouter"

    async def analyze_campaigns(
        self,
        org: Organization,
        ad_account: AdAccount,
        insights_summary: str,
        raw_data_summary: Dict[str, Any],
        usage_ctx: Optional[UsageContext] = None,
        brief_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, bool]:
        """
        Envía los insights crudos de Capa 1 y 2 al LLM y recibe un análisis ejecutivo narrativo.
        Si MOCK_SERVICES está activo o no hay API key, utiliza el motor de simulación local.

        Devuelve (narrativa, used_fallback). `used_fallback=True` significa que la
        narrativa NO la generó el LLM sino la plantilla local (C-4): el caller debe
        persistirlo para que la UI no presente un template como "Procesado por IA".

        `brief_context` (C-3, master-brief-v2): target_cpl/moneda/país, KPIs 7d
        vs 7d previos, top/bottom campañas y pacing — armado por AIEngine desde
        ReportBuilder (no recalculado aquí). Opcional por compatibilidad hacia
        atrás; sin él, cae al contrato v1 (solo `raw_data_summary`).
        """
        if settings.MOCK_SERVICES or not self.api_key:
            return self._generate_mock_narrative(org, ad_account, raw_data_summary, brief_context), True

        # ---- LLAMADA REAL A OPENROUTER ----
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": f"http://{settings.BASE_DOMAIN}",
            "X-Title": "LejoVista Platform",
            "Content-Type": "application/json"
        }

        insights_summary = _filter_insufficient_history(insights_summary)
        prompt = _build_master_brief_prompt(org, ad_account, insights_summary, raw_data_summary, brief_context)

        # Control de alucinación numérica: el modelo NO debe inventar cifras.
        system_prompt = (
            "Eres un asistente experto de IA que genera informes ejecutivos sobre publicidad "
            "digital en Meta Ads. REGLA INQUEBRANTABLE: usa EXCLUSIVAMENTE las cifras presentes "
            "en los datos provistos por el usuario. No inventes, redondees ni estimes números "
            "(CPL, gasto, leads, CTR) que no aparezcan literalmente en esos datos. Si un dato no "
            "está, dilo explícitamente ('dato no disponible') en lugar de suponerlo. Este informe "
            "decide gasto publicitario real. Clasifica las campañas EXCLUSIVAMENTE contra el "
            "target_cpl provisto en los datos — no uses umbrales propios."
        )

        model = org.ai_model or settings.DEFAULT_AI_MODEL
        if self.is_gemini_direct and model.startswith("google/"):
            model = model.replace("google/", "")

        payload = {
            "model": model,
            "temperature": 0.2,  # menor creatividad => menos invención de cifras
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(self.base_url, json=payload, headers=headers)
                if response.status_code == 200:
                    result = response.json()
                    await record_token_usage(usage_ctx, self.provider, model, result.get("usage"))
                    return result["choices"][0]["message"]["content"], False
                else:
                    logger.error(f"OpenRouter API returned error {response.status_code}: {response.text}")
                    return self._generate_mock_narrative(org, ad_account, raw_data_summary, brief_context), True
        except Exception as e:
            logger.exception("Error calling OpenRouter API. Falling back to mock narrative.")
            return self._generate_mock_narrative(org, ad_account, raw_data_summary, brief_context), True

    async def suggest_campaign_copy(
        self,
        org: Organization,
        ad_account: AdAccount,
        product_name: str,
        winning_context: Optional[Dict[str, Any]] = None,
        winning_ad_copy: Optional[Dict[str, Any]] = None,
        usage_ctx: Optional[UsageContext] = None,
    ) -> Dict[str, Any]:
        """
        Fase 3B del plan de creación de campañas: genera el copy de una campaña
        nueva siguiendo la estructura ganadora del playbook (§6: DOLOR →
        PROMESA → BENEFICIOS → URGENCIA → CTA). El LLM SOLO redacta texto —
        las cifras (presupuesto, placements, edad) las decide
        `CampaignSuggester` (Fase 3A) de forma determinista, nunca el LLM.

        v2 (3.4): `headline` y `primary_text` vienen como 2 variantes (arrays)
        para que el usuario elija en la UI; `description` y `welcome_message`
        siguen siendo un único string. Ambos textos respetan los límites de
        caracteres de Meta (`META_HEADLINE_MAX_CHARS`/`META_DESCRIPTION_MAX_CHARS`).

        `winning_ad_copy` (título/cuerpo/CTA reales del anuncio ganador,
        capturados por el sync — antes solo se guardaba la imagen) es
        inspiración más fuerte que `winning_context`: el LLM puede reutilizar
        frases o ganchos que ya demostraron funcionar, no solo el tono.
        """
        if settings.MOCK_SERVICES or not self.api_key:
            return self._generate_mock_copy(product_name, winning_context)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": f"http://{settings.BASE_DOMAIN}",
            "X-Title": "LejoVista Platform",
            "Content-Type": "application/json",
        }

        winning_summary = ""
        if winning_context:
            winning_summary = (
                f"Contexto de la campaña más eficiente de esta cuenta (úsalo solo para inspirar "
                f"el tono, NUNCA menciones estas cifras en el copy): '{winning_context.get('name')}' — "
                f"S/.{winning_context.get('cost_per_conv', 0):.2f} por conversación, "
                f"CTR {winning_context.get('ctr', 0):.2f}%."
            )

        winning_copy_block = ""
        if winning_ad_copy and winning_ad_copy.get("body"):
            winning_copy_block = (
                "\nCopy REAL del anuncio más eficiente de esta cuenta (puedes reutilizar frases, "
                "ganchos o la estructura si encajan con el producto nuevo — es texto que ya "
                "demostró funcionar, no una cifra):\n"
                f"Título: {winning_ad_copy.get('title') or '(sin título)'}\n"
                f"Cuerpo: {winning_ad_copy.get('body')}\n"
            )

        system_prompt = (
            "Eres un copywriter experto en anuncios de Meta Ads (Facebook/Instagram) orientados a "
            "WhatsApp, para el mercado peruano. Escribe SIEMPRE en español, con emojis y saltos de "
            "línea naturales, tono directo y cercano.\n\n"
            "Estructura OBLIGATORIA del primary_text: DOLOR → PROMESA → BENEFICIOS (✅) → URGENCIA → "
            "CTA (📲).\n\n"
            f"VARIANTES: genera exactamente {_COPY_VARIANTS} variantes de headline y de primary_text "
            "(distinto ángulo o gancho cada una, mismo producto), para que el usuario elija.\n\n"
            "LÍMITES DE META (obligatorios, Meta rechaza el anuncio si se exceden): "
            f"cada headline ≤ {META_HEADLINE_MAX_CHARS} caracteres; description ≤ {META_DESCRIPTION_MAX_CHARS} "
            "caracteres. primary_text y welcome_message no tienen límite estricto.\n\n"
            "REGLA INQUEBRANTABLE: no inventes ni menciones cifras de rendimiento (CPL, CTR, "
            "conversiones, costos) en el copy — esas nunca forman parte del texto del anuncio.\n\n"
            "REGLA DE POLÍTICAS DE META: evita por completo lenguaje de medicamentos o farmacéutico "
            "('fórmula magistral', 'medicamento', 'dosis', 'tratamiento', 'receta médica'); si el "
            "producto pertenece a ese rubro, enfócalo como cuidado personal o cosmético, o describe "
            "el producto físico en vez del fármaco.\n\n"
            "Responde ÚNICAMENTE con un JSON válido, sin texto adicional ni markdown, con "
            'exactamente estas claves: {"headline": ["variante 1", "variante 2"], '
            '"primary_text": ["variante 1", "variante 2"], "description": "...", "welcome_message": "..."}.'
        )

        industry_line = f"Rubro del negocio: {org.industry}.\n" if org.industry else ""
        user_prompt = (
            f"Producto o servicio: {product_name}\n"
            f"{industry_line}"
            f"{winning_summary}\n"
            f"{winning_copy_block}\n"
            "Genera el copy para una campaña nueva de mensajes de WhatsApp de esta marca."
        )

        model = org.ai_model or settings.DEFAULT_AI_MODEL
        if self.is_gemini_direct and model.startswith("google/"):
            model = model.replace("google/", "")

        payload = {
            "model": model,
            "temperature": 0.7,  # más creatividad que el brief (aquí sí redacta, no reporta cifras)
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # 3.4(d): la mayoría de proveedores OpenAI-compatibles (openai,
            # openrouter, Gemini vía su endpoint OpenAI-compat) soportan JSON
            # forzado — reduce la tasa de fallback a la plantilla local por
            # respuestas mal formadas. El parser por regex sigue como red de
            # seguridad si el proveedor lo ignora.
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(self.base_url, json=payload, headers=headers)
                if response.status_code == 200:
                    result = response.json()
                    await record_token_usage(usage_ctx, self.provider, model, result.get("usage"))
                    content = result["choices"][0]["message"]["content"]
                    parsed = self._parse_copy_json(content)
                    if parsed:
                        return parsed
                    logger.error(
                        "Copy del LLM no es un JSON válido, usando plantilla local. Contenido: %s",
                        content[:300],
                    )
                else:
                    logger.error(
                        f"OpenRouter API (copy de campaña) devolvió error {response.status_code}: {response.text}"
                    )
        except Exception:
            logger.exception("Error llamando a la API del LLM para copy de campaña. Usando plantilla local.")

        return self._generate_mock_copy(product_name, winning_context)

    @staticmethod
    def _parse_copy_json(content: str) -> Optional[Dict[str, Any]]:
        """Extrae el JSON del copy de la respuesta del LLM. Tolera que el modelo
        envuelva el JSON en texto o ```json ... ``` pese a la instrucción de no
        hacerlo. Devuelve None si no se puede parsear, faltan claves, las
        variantes de headline/primary_text no son exactamente 2, o algún
        headline/description excede el límite de caracteres de Meta."""
        import re
        match = re.search(r"\{.*\}", content.strip(), re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict) or not _REQUIRED_COPY_KEYS.issubset(data.keys()):
            return None

        result: Dict[str, Any] = {}
        for key in _VARIANT_KEYS:
            variants = data[key]
            if not isinstance(variants, list) or len(variants) != _COPY_VARIANTS:
                return None
            variants = [str(v) for v in variants]
            if key == "headline" and any(len(v) > META_HEADLINE_MAX_CHARS for v in variants):
                return None
            result[key] = variants

        description = str(data["description"])
        if len(description) > META_DESCRIPTION_MAX_CHARS:
            return None
        result["description"] = description
        result["welcome_message"] = str(data["welcome_message"])
        return result

    def _generate_mock_copy(
        self, product_name: str, winning_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Plantilla local determinista (sin LLM) — nunca rompe la creación de
        campañas si el LLM falla, no hay API key configurada, o MOCK_SERVICES
        está activo. Sigue la misma estructura DOLOR→PROMESA→BENEFICIOS→URGENCIA→CTA
        del playbook (§6), sin inventar cifras de rendimiento. Respeta los
        mismos límites de Meta que se le exigen al LLM (headline/description)."""
        headline_a = f"{product_name} 🔥"[:META_HEADLINE_MAX_CHARS]
        headline_b = f"Cotiza {product_name} ya"[:META_HEADLINE_MAX_CHARS]
        return {
            "headline": [headline_a, headline_b],
            "primary_text": [
                (
                    f"¿Buscas {product_name.lower()} de confianza? 😩\n\n"
                    f"Con nosotros lo tienes fácil: calidad garantizada y atención inmediata. ✅\n"
                    f"✅ Entrega rápida\n✅ Atención personalizada\n✅ Precios justos\n\n"
                    f"🔥 Cupos limitados esta semana.\n"
                    f"📲 Escríbenos ahora por WhatsApp y cotiza sin compromiso."
                ),
                (
                    f"¿Cansado de no encontrar {product_name.lower()} de calidad? 😩\n\n"
                    f"Te lo ponemos fácil: atención rápida y precios justos. ✅\n"
                    f"✅ Stock disponible\n✅ Asesoría personalizada\n✅ Entrega puntual\n\n"
                    f"🔥 Promoción válida esta semana.\n"
                    f"📲 Escríbenos por WhatsApp y cotiza gratis."
                ),
            ],
            "description": f"{product_name} · Cotiza por WhatsApp"[:META_DESCRIPTION_MAX_CHARS],
            "welcome_message": f"¡Hola! 👋 Gracias por escribir. Cuéntanos qué necesitas de {product_name} y te ayudamos al toque.",
        }

    def _generate_mock_narrative(
        self,
        org: Organization,
        ad_account: AdAccount,
        raw_data_summary: Dict[str, Any],
        brief_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generador local de diagnósticos narrativos para pruebas sin LLM.

        IMPORTANTE (AI-1): todas las cifras que aparecen en el texto se derivan de
        `raw_data_summary`/`brief_context` (los datos reales de la cuenta). No se
        inventa ningún número, porque este informe puede orientar decisiones de
        gasto real.

        C-2/C-3: usa el mismo `classify_campaign` (relativo al target_cpl
        dinámico) que /reporte, y el mismo contrato de salida de 5 secciones
        que el prompt real — para que la plantilla local no sea, en la
        práctica, un informe distinto al que produciría el LLM.
        """
        total_spend = sum(c.get("spend", 0.0) for c in raw_data_summary.values())
        total_conv = sum(
            (c.get("conversations") or c.get("conversions", 0)) for c in raw_data_summary.values()
        )
        target_cpl = (brief_context or {}).get("target_cpl") or compute_target_cpl(total_spend, total_conv)

        ganadoras, alertas, fallidas = [], [], []
        for c_data in raw_data_summary.values():
            spend = c_data.get("spend", 0.0)
            conv = c_data.get("conversations") or c_data.get("conversions", 0)
            cpl = spend / conv if conv > 0 else spend
            ctr = c_data.get("ctr", 0.0)
            entry = {
                "name": c_data.get("name", "(sin nombre)"),
                "spend": spend, "conv": conv, "cpl": cpl, "ctr": ctr,
            }
            label = classify_campaign(cpl, conv, target_cpl)
            if label in ("GANADORA", "BUENA"):
                ganadoras.append(entry)
            elif label == "REGULAR":
                alertas.append(entry)
            else:  # CARA, PAUSAR
                fallidas.append(entry)

        avg_cpl = (total_spend / total_conv) if total_conv > 0 else total_spend

        def _fmt(items) -> str:
            if not items:
                return "_Ninguna campaña en esta categoría._"
            return "\n".join(
                f"* **{e['name']}** — Inversión S/.{e['spend']:.2f}, {e['conv']} conversaciones, "
                f"CPL S/.{e['cpl']:.2f}, CTR {e['ctr']:.2f}%."
                for e in items
            )

        pacing_note = ""
        pacing = (brief_context or {}).get("pacing") or {}
        if pacing:
            pacing_note = (
                f" Presupuesto diario configurado S/.{pacing.get('daily_budget_total', 0):.2f} vs "
                f"gasto real diario promedio S/.{pacing.get('daily_spend_avg', 0):.2f}."
            )

        delta_note = ""
        deltas = (brief_context or {}).get("deltas_pct") or {}
        conv_delta = deltas.get("conversations")
        if conv_delta is not None:
            sign = "+" if conv_delta >= 0 else ""
            delta_note = f" Conversaciones {sign}{conv_delta:.0f}% vs los 7 días previos."

        priorities = []
        if fallidas:
            worst = max(fallidas, key=lambda e: e["cpl"])
            priorities.append(
                f"Pausar o rediseñar **{worst['name']}** (CPL S/.{worst['cpl']:.2f} vs objetivo S/.{target_cpl:.2f})."
            )
        if alertas:
            priorities.append(f"Revisar creativo/segmentación de **{alertas[0]['name']}** antes de que empeore.")
        if ganadoras:
            best = min(ganadoras, key=lambda e: e["cpl"])
            priorities.append(f"Escalar presupuesto de **{best['name']}** de forma controlada, manteniendo el CPL bajo.")
        while len(priorities) < 3:
            priorities.append("Sin más prioridades detectadas en esta ventana — mantener el monitoreo habitual.")

        priorities_md = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(priorities[:3]))

        return f"""## Resumen
Cuenta **{ad_account.name}** ({org.name}). Inversión últimos 7 días: S/.{total_spend:.2f}, {total_conv} conversaciones, CPL promedio S/.{avg_cpl:.2f} (objetivo: S/.{target_cpl:.2f}).{pacing_note}{delta_note}

## Ganadoras
{_fmt(ganadoras)}

## En alerta
{_fmt(alertas)}

## Fallidas
{_fmt(fallidas)}

## Prioridades de la semana
{priorities_md}"""
