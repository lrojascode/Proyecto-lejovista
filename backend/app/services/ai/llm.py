import logging
import json
import datetime
from typing import Dict, Any, Optional
import httpx
from app.config import settings
from app.models.organization import Organization
from app.models.ad_account import AdAccount

logger = logging.getLogger("lejovista.llm")

# Versión del prompt del Master Brief. Se persiste junto a la narrativa para
# poder trazar qué versión generó cada informe (AI-4).
PROMPT_VERSION = "master-brief-v1"

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
        raw_data_summary: Dict[str, Any]
    ) -> str:
        """
        Envía los insights crudos de Capa 1 y 2 al LLM y recibe un análisis ejecutivo narrativo.
        Si MOCK_SERVICES está activo o no hay API key, utiliza el motor de simulación local.
        """
        if settings.MOCK_SERVICES or not self.api_key:
            return self._generate_mock_narrative(org, ad_account, raw_data_summary)

        # ---- LLAMADA REAL A OPENROUTER ----
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": f"http://{settings.BASE_DOMAIN}", 
            "X-Title": "LejoVista Platform",
            "Content-Type": "application/json"
        }
        
        prompt = f"""
        Eres un experto en Meta Ads y analista de crecimiento (Growth Analyst) para la marca LejoVista.
        Analiza los siguientes datos y alertas de campañas del cliente '{org.name}' (cuenta: '{ad_account.name}'):

        Alertas y Hallazgos Previos (Capas 1 y 2):
        {insights_summary}

        Resumen de Métricas de los Últimos 7 Días (ÚNICA fuente de verdad):
        {json.dumps(raw_data_summary, indent=2)}

        Escribe un diagnóstico narrativo profesional de las campañas activas. Clasifícalas en:
        - ✅ GANADORAS (buen CPL, conversiones estables)
        - ⚠️ ALERTAS (frecuencia alta, CTR bajo, o CPL subiendo)
        - ❌ FALLIDAS (CPL > 2x objetivo, 0 conversiones, o anomalía crítica)

        Da recomendaciones específicas sobre presupuesto, targeting y creativos para cada una.
        Usa un tono profesional, directo y orientado a la acción (en español).
        """

        # Control de alucinación numérica: el modelo NO debe inventar cifras.
        system_prompt = (
            "Eres un asistente experto de IA que genera informes ejecutivos sobre publicidad "
            "digital en Meta Ads. REGLA INQUEBRANTABLE: usa EXCLUSIVAMENTE las cifras presentes "
            "en los datos provistos por el usuario. No inventes, redondees ni estimes números "
            "(CPL, gasto, leads, CTR) que no aparezcan literalmente en esos datos. Si un dato no "
            "está, dilo explícitamente ('dato no disponible') en lugar de suponerlo. Este informe "
            "decide gasto publicitario real."
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
                    return result["choices"][0]["message"]["content"]
                else:
                    logger.error(f"OpenRouter API returned error {response.status_code}: {response.text}")
                    return self._generate_mock_narrative(org, ad_account, raw_data_summary)
        except Exception as e:
            logger.exception("Error calling OpenRouter API. Falling back to mock narrative.")
            return self._generate_mock_narrative(org, ad_account, raw_data_summary)

    async def generate_html_brief(
        self, 
        org: Organization, 
        ad_account: AdAccount,
        narrative_analysis: str, 
        raw_data_summary: Dict[str, Any]
    ) -> str:
        """
        Genera el HTML final del Master Brief usando estilos premium (Glassmorphism, Modo Oscuro).
        """
        # Formatear la tabla de campañas en base a los datos
        campaigns_html = ""
        for c_id, c_data in raw_data_summary.items():
            cpl = c_data["spend"] / c_data["conversions"] if c_data["conversions"] > 0 else c_data["spend"]
            ctr = c_data["ctr"]
            
            # Etiqueta de estado visual
            if cpl < 2.0:
                badge = '<span class="badge badge-success">✅ GANADORA</span>'
            elif cpl > 7.0 or c_data["conversions"] == 0:
                badge = '<span class="badge badge-danger">❌ FALLIDA</span>'
            else:
                badge = '<span class="badge badge-warning">⚠️ ALERTA</span>'

            campaigns_html += f"""
            <tr>
                <td><strong>{c_data['name']}</strong></td>
                <td>S/.{c_data['spend']:.2f}</td>
                <td>{c_data['conversions']}</td>
                <td class="font-bold">S/.{cpl:.2f}</td>
                <td>{ctr:.2f}%</td>
                <td>{badge}</td>
            </tr>
            """

        # Formatear narrativa reemplazando saltos de línea con HTML
        formatted_narrative = narrative_analysis.replace("\n", "<br/>")

        # HTML completo con diseño premium y moderno
        html_template = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Master Brief — LejoVista ({org.name})</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0d0e12;
            --card-bg: rgba(20, 22, 31, 0.65);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --accent-primary: #8b5cf6;
            --accent-glow: rgba(139, 92, 246, 0.15);
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            line-height: 1.6;
            padding: 2.5rem 1.5rem;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(139, 92, 246, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(59, 130, 246, 0.08) 0%, transparent 40%);
            background-attachment: fixed;
        }}

        .container {{
            max-width: 1100px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 3rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }}

        .logo-area h1 {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 2.2rem;
            background: linear-gradient(135deg, #a78bfa 0%, #60a5fa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .logo-area p {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            margin-top: 0.2rem;
        }}

        .meta-info {{
            text-align: right;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}

        .grid {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 2rem;
            margin-bottom: 3rem;
        }}

        @media (max-width: 900px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
        }}

        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 18px;
            padding: 2rem;
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            margin-bottom: 2rem;
        }}

        .card-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.4rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            color: #ffffff;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 0.75rem;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
            font-size: 0.95rem;
        }}

        th, td {{
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        .font-bold {{
            font-weight: 700;
        }}

        .badge {{
            display: inline-block;
            padding: 0.35rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.03em;
        }}

        .badge-success {{
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .badge-warning {{
            background: rgba(245, 158, 11, 0.15);
            color: var(--warning);
            border: 1px solid rgba(245, 158, 11, 0.3);
        }}

        .badge-danger {{
            background: rgba(239, 68, 68, 0.15);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}

        .narrative-content {{
            font-size: 1rem;
            color: var(--text-primary);
            line-height: 1.7;
        }}

        .narrative-content p, .narrative-content br {{
            margin-bottom: 0.75rem;
        }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}

        .kpi-card {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.25rem;
            text-align: center;
        }}

        .kpi-val {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: #ffffff;
            margin-top: 0.25rem;
        }}

        .kpi-lbl {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-area">
                <h1>LejoVista</h1>
                <p>AI Master Brief de Análisis de Campañas</p>
            </div>
            <div class="meta-info">
                <p><strong>Organización:</strong> {org.name}</p>
                <p><strong>Cuenta:</strong> {ad_account.name} ({ad_account.external_account_id})</p>
                <p><strong>Generado el:</strong> {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
            </div>
        </header>

        <div class="card">
            <h2 class="card-title">📊 Resumen General (Últimos 7 Días)</h2>
            <div class="kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-lbl">Presupuesto Diario Total</div>
                    <div class="kpi-val">S/.{sum(c["budget"] for c in raw_data_summary.values()):.2f}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-lbl">Inversión Total</div>
                    <div class="kpi-val">S/.{sum(c["spend"] for c in raw_data_summary.values()):.2f}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-lbl">Leads Totales</div>
                    <div class="kpi-val">{sum(c["conversions"] for c in raw_data_summary.values())}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-lbl">CPL Promedio</div>
                    <div class="kpi-val">S/.{(sum(c["spend"] for c in raw_data_summary.values()) / max(1, sum(c["conversions"] for c in raw_data_summary.values()))):.2f}</div>
                </div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2 class="card-title">🧠 Diagnóstico Narrativo de la IA</h2>
                <div class="narrative-content">
                    {formatted_narrative}
                </div>
            </div>

            <div>
                <div class="card">
                    <h2 class="card-title">🎯 Desempeño de Campañas</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Campaña</th>
                                <th>Spend</th>
                                <th>Leads</th>
                                <th>CPL</th>
                                <th>CTR</th>
                                <th>Clasificación</th>
                            </tr>
                        </thead>
                        <tbody>
                            {campaigns_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
        return html_template

    def _generate_mock_narrative(self, org: Organization, ad_account: AdAccount, raw_data_summary: Dict[str, Any]) -> str:
        """
        Generador local de diagnósticos narrativos para pruebas sin LLM.

        IMPORTANTE (AI-1): todas las cifras que aparecen en el texto se derivan de
        `raw_data_summary` (los datos reales de la cuenta). No se inventa ningún
        número, porque este informe puede orientar decisiones de gasto real.
        """
        ganadoras, alertas, fallidas = [], [], []

        for c_data in raw_data_summary.values():
            spend = c_data.get("spend", 0.0)
            conv = c_data.get("conversions", 0)
            cpl = spend / conv if conv > 0 else spend
            ctr = c_data.get("ctr", 0.0)
            entry = {
                "name": c_data.get("name", "(sin nombre)"),
                "spend": spend, "conv": conv, "cpl": cpl, "ctr": ctr,
            }
            if conv == 0 or cpl > 7.0:
                fallidas.append(entry)
            elif cpl < 2.0:
                ganadoras.append(entry)
            else:
                alertas.append(entry)

        total_spend = sum(c.get("spend", 0.0) for c in raw_data_summary.values())
        total_conv = sum(c.get("conversions", 0) for c in raw_data_summary.values())
        avg_cpl = (total_spend / total_conv) if total_conv > 0 else total_spend

        def _fmt(items) -> str:
            if not items:
                return "  _Ninguna campaña en esta categoría._"
            return "\n".join(
                f"* **{e['name']}** — Inversión S/.{e['spend']:.2f}, {e['conv']} leads, "
                f"CPL S/.{e['cpl']:.2f}, CTR {e['ctr']:.2f}%."
                for e in items
            )

        narrative = f"""### 📋 Análisis de Desempeño para {org.name}

Auditoría de los últimos 7 días en la cuenta **{ad_account.name}**. Inversión total: **S/.{total_spend:.2f}**, leads totales: **{total_conv}**, CPL promedio: **S/.{avg_cpl:.2f}**.

---

### 🟢 Campañas GANADORAS (CPL < S/.2.00)
{_fmt(ganadoras)}
{"**Recomendación:** escalar presupuesto de forma controlada manteniendo el CPL bajo." if ganadoras else ""}

### 🟡 Campañas en ALERTA (CPL entre S/.2.00 y S/.7.00)
{_fmt(alertas)}
{"**Recomendación:** revisar frecuencia/creativo y vigilar la tendencia del CPL." if alertas else ""}

### 🔴 Campañas FALLIDAS (0 leads o CPL > S/.7.00)
{_fmt(fallidas)}
{"**Recomendación:** pausar y rediseñar copy/segmentación antes de seguir invirtiendo." if fallidas else ""}

---

### 🛡 Nota
Todas las acciones sugeridas quedan pendientes de aprobación en tu panel antes de aplicarse en Meta. Las cifras de este informe provienen directamente de los datos sincronizados de la cuenta."""
        return narrative
