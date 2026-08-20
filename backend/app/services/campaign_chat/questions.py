"""Preguntas que el chat sabe responder sobre la cuenta del cliente (CH-6).

Alcance deliberadamente cerrado: SOLO datos de la marca (sus campañas, su
gasto, sus leads, su configuración de destino) y cómo funciona el producto.

Dos pasos para clasificar, en este orden:
  1. Regex sobre el texto sin tildes. Cubre la forma en que la gente pregunta
     esto el 90% de las veces y cuesta CERO tokens.
  2. Si ningún regex matchea, el motor le pide al LLM que elija una etiqueta
     del catálogo cerrado (`TOPIC_DESCRIPTIONS`). Rechazar directo era más
     barato pero descartaba preguntas que el sistema sí sabe responder — un
     typo no debería costar una respuesta que ya está en la base.
Fuera del catálogo, se rechaza.

Todas las respuestas se arman con cifras reales de `ReportBuilder` (vía
`report_cache.get_report`, el mismo dato que ve el dashboard y el reporte),
nunca con números redactados por un modelo — es la regla AI-1 aplicada
también acá.

La clasificación es por regex sobre el texto sin tildes y en minúsculas: son
preguntas básicas y repetitivas, no hace falta un modelo para entenderlas.
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.services.campaign_chat.period import DEFAULT_WINDOW_DAYS, Period, clamp_to_available
from app.services.campaign_chat.slots import strip_accents
from app.services.kpi_period import days_of_history, get_kpis_with_deltas
from app.services.report_cache import get_report

# Ventana por defecto cuando la pregunta no menciona ningún período. 7 días es
# lo que ya usan la lista de campañas y el dashboard — la respuesta del chat
# tiene que coincidir con lo que el usuario ve en pantalla, no contradecirlo.
# Cuando la pregunta sí menciona un período (`period.parse_period`), manda ese.
ANSWER_WINDOW_DAYS = DEFAULT_WINDOW_DAYS

# De dónde sale cada cifra, y por qué son DOS fuentes:
#
# Las cifras de titular (gasto, conversaciones, costo por conversación) salen
# de `get_kpis_with_deltas` — exactamente la misma función que alimenta las
# tarjetas del Resumen. Es deliberado: si el usuario pregunta "¿cuánto gasté?"
# mientras mira el dashboard, los dos números tienen que ser el mismo.
#
# `ReportBuilder` (vía `get_report`) usa una ventana un día más ancha para la
# misma etiqueta de "7 días": filtra `date >= hoy - 7` SIN límite superior, así
# que incluye el día de hoy (8 días de datos), mientras que `kpi_period` corta
# en `date < hoy`. Es una inconsistencia previa a este chat y de blast radius
# grande (Master Brief, PDF, motor de IA leen de ahí), así que no se toca acá:
# se usa el reporte solo para lo que no tiene equivalente en KPIs (ranking de
# campañas, alertas, recomendaciones, segmentación, CTR/alcance).
_NEEDS_KPIS = {"SPEND", "LEADS", "CPL", "SUMMARY", "BUDGET_ADVICE"}
_NEEDS_REPORT = {
    "BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "ALERTS",
    "RECOMMENDATIONS", "AUDIENCE", "CTR", "SUMMARY", "CPL", "BUDGET_ADVICE",
    "REACH", "SPEND_CAP", "SPEND", "SCHEDULE", "OBJECTIVE",
}

# Forma de pregunta: abre con "¿", cierra con "?", o arranca con una palabra
# interrogativa. Sirve para distinguir "¿cuánto gasté?" de "gasté mucho".
_QUESTION_SHAPE_RE = re.compile(
    r"^\s*¿|\?\s*$|^\s*(que|cual|cuales|cuanto|cuanta|cuantos|cuantas|como|cuando|donde|quien|"
    r"por que|porque|me pod(e|é)s decir|sab(e|é)s|dime|decime|explicame|explicame)\b",
    re.IGNORECASE,
)

# Temas soportados, en orden de prioridad (el primero que matchea gana). El
# orden importa: "costo por conversación" debe ganarle a "conversaciones".
_TOPIC_PATTERNS: List[tuple] = [
    ("HOW_IT_WORKS", re.compile(
        r"como funciona|que pod(e|é)s hacer|que haces|para que servis|para que sirve|"
        r"en que me ayudas|que sabes hacer|quien sos|que es esto", re.IGNORECASE)),
    ("WHY_PAUSED", re.compile(
        r"por que.{0,20}pausad|queda.{0,10}pausad|se activa sola|activar la campana|"
        r"quien la activa|se prende sola", re.IGNORECASE)),
    ("SCHEDULE", re.compile(
        r"cuando (fue|fueron) creada|fecha de (creacion|finalizacion|inicio)|"
        r"cuando (empieza|termina|vence)|hasta cuando|desde cuando|cuanto lleva corriendo",
        re.IGNORECASE)),
    ("OBJECTIVE", re.compile(r"\bobjetivo\b|tipos? de campanas?", re.IGNORECASE)),
    ("CPL", re.compile(
        r"\bcpl\b|costo por (lead|conversacion|mensaje)|cuanto (me )?(cuesta|sale) cada|"
        r"precio por lead|costo por contacto", re.IGNORECASE)),
    ("BEST_CAMPAIGN", re.compile(
        r"mejor campana|campana (ganadora|mas eficiente)|(cual|que).{0,20}rinde mejor|"
        r"(cual|que).{0,20}(funciona|va) mejor|mejor rendimiento|cual es la mejor", re.IGNORECASE)),
    ("WORST_CAMPAIGN", re.compile(
        r"peor campana|campana (fallida|mas cara)|(cual|que).{0,20}va mal|"
        r"(cual|que).{0,20}no funciona|peor rendimiento|cual es la peor|"
        r"(cual|que).{0,20}esta fallando", re.IGNORECASE)),
    ("BUDGET_ADVICE", re.compile(
        r"cuanto (deberia|tengo que|conviene|deberia de)? ?(invertir|gastar|poner|meter)|"
        r"presupuesto (recomendado|ideal|sugerido)|cuanto invierto|cuanto pongo", re.IGNORECASE)),
    ("SPEND_CAP", re.compile(
        r"tope de gasto|limite de gasto|limite de la cuenta|saldo restante|tope restante|"
        r"cuanto me queda|cuanto saldo", re.IGNORECASE)),
    ("SPEND", re.compile(
        r"cuanto (gaste|gastamos|llevo gastado|he gastado|invert)|\bgasto\b|\bgastos\b|"
        r"\binversion\b|\binvertid[oa]s?\b|total gastad[oa]s?|cuanto va|cuanto llevo", re.IGNORECASE)),
    ("REACH", re.compile(
        r"\balcance\b|personas alcanzadas|cuanta gente|a cuanta gente|a cuantas personas|"
        r"impresiones|frecuencia|a cuantos llegue|a cuanta gente le llegue", re.IGNORECASE)),
    ("LEADS", re.compile(
        r"\bleads?\b|conversacion|cuantos me escrib|cuantos mensajes|cuantos contactos|"
        r"\bclientes\b", re.IGNORECASE)),
    ("CTR", re.compile(r"\bctr\b|\bclics?\b|\bclicks?\b|tasa de clic", re.IGNORECASE)),
    ("ALERTS", re.compile(
        r"\balertas?\b|\bproblemas?\b|que esta mal|algo mal|hay algo que|"
        r"que anda mal|me preocupa", re.IGNORECASE)),
    ("RECOMMENDATIONS", re.compile(
        r"recomend|que (hago|deberia hacer)|conseja|consejo|sugerencia|que me convien|"
        r"que me sugeris", re.IGNORECASE)),
    ("AUDIENCE", re.compile(
        r"a quien le llega|\bpublico\b|audiencia|segmenta|que edad|rango de edad|"
        r"donde se muestra|placement|que dispositivo", re.IGNORECASE)),
    ("DESTINATION", re.compile(
        r"(que|cual) pagina|(que|cual) whatsapp|a donde llegan|numero de whatsapp|"
        r"donde me escriben|a que numero", re.IGNORECASE)),
    ("CAMPAIGNS_OVERVIEW", re.compile(
        r"cuantas campanas|que campanas|mis campanas|lista de campanas|campanas tengo|"
        # Singular incluido: "¿cuál es la campaña activa?" es la forma natural de
        # preguntarlo cuando hay una sola corriendo, y antes no matcheaba nada.
        r"campanas? (activas?|corriendo|prendidas?|encendidas?)|"
        r"(cual|que).{0,20}campana.{0,15}(activa|corriendo|prendida|encendida)",
        re.IGNORECASE)),
    ("SUMMARY", re.compile(
        r"como (me )?va|como voy|como van|\bresumen\b|como esta(n)? (mi|mis|la|las)|"
        r"que tal (va|van)|rendimiento general|como venimos", re.IGNORECASE)),
]

# Qué significa cada tema, en una línea. Es lo que se le pasa al LLM cuando los
# regex no reconocen la pregunta (ver `classify_question_with_llm` en el motor):
# el modelo NO responde ni calcula nada, solo elige una de estas etiquetas, y la
# respuesta la sigue armando el código con cifras reales (regla AI-1).
TOPIC_DESCRIPTIONS: Dict[str, str] = {
    "HOW_IT_WORKS": "qué es este asistente y qué puede hacer",
    "WHY_PAUSED": "por qué las campañas se crean pausadas y cómo activarlas",
    "SCHEDULE": "cuándo se crearon las campañas, cuándo empiezan o terminan, cuánto llevan corriendo",
    "OBJECTIVE": "qué objetivo o tipo tiene cada campaña",
    "CPL": "costo por lead / por conversación / por mensaje",
    "BEST_CAMPAIGN": "cuál campaña rinde mejor",
    "WORST_CAMPAIGN": "cuál campaña rinde peor o está fallando",
    "BUDGET_ADVICE": "cuánto conviene invertir",
    "SPEND_CAP": "tope o límite de gasto de la cuenta y saldo restante",
    "SPEND": "cuánto se gastó / la inversión hecha",
    "REACH": "alcance, personas alcanzadas, impresiones o frecuencia",
    "LEADS": "cuántos leads, conversaciones, mensajes o contactos llegaron",
    "CTR": "clics, CTR o tasa de clics",
    "ALERTS": "si hay algo mal, problemas o alertas en la cuenta",
    "RECOMMENDATIONS": "qué conviene hacer, consejos o sugerencias",
    "AUDIENCE": "a quién le llega el anuncio: público, edad, ubicación, dispositivo",
    "DESTINATION": "a qué página de Facebook y a qué número de WhatsApp van los mensajes",
    "CAMPAIGNS_OVERVIEW": "qué campañas tiene, cuántas hay, cuáles están activas",
    "SUMMARY": "cómo viene todo en general, un resumen del rendimiento",
}

SUPPORTED_TOPICS = frozenset(TOPIC_DESCRIPTIONS)


def looks_like_question(text: str) -> bool:
    return bool(_QUESTION_SHAPE_RE.search(strip_accents(text or "")))


def classify_question_topics(text: str, max_topics: int = 3) -> List[str]:
    """TODOS los temas cuyo regex matchea, en orden de prioridad de _TOPIC_PATTERNS.
    `classify_question` se conserva devolviendo el primero (compatibilidad).
    Tope de `max_topics` (por defecto 3) para evitar inflar el paquete de facts.
    """
    ascii_text = strip_accents(text or "").lower()
    matched: List[str] = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(ascii_text):
            matched.append(topic)
            if len(matched) >= max_topics:
                break
    return matched


def classify_question(text: str) -> Optional[str]:
    """Devuelve el tema soportado, o `None` si ningún regex lo reconoció.

    `None` no cierra la puerta: el motor le da una segunda oportunidad al LLM
    (`classify_question_with_llm`) antes de dar la pregunta por fuera de tema.
    Un typo —"¿cuál es la capana activa?"— no debería costar una respuesta que
    el sistema sí sabe dar.
    """
    topics = classify_question_topics(text, max_topics=1)
    return topics[0] if topics else None


# --- Respuestas estáticas (producto, no datos) ---

HOW_IT_WORKS_TEXT = (
    "Te ayudo a crear campañas de anuncios en Meta que llevan gente a tu WhatsApp.\n\n"
    "Vos me contás qué querés promocionar y me subís una foto. Yo saco el presupuesto, "
    "la duración y el texto del anuncio del historial real de tu cuenta, te lo muestro "
    "para que lo revises, y recién cuando lo aprobás la creo — siempre en PAUSA.\n\n"
    "También te puedo responder cómo van tus campañas: cuánto gastaste, cuántos leads "
    "trajiste, cuál rinde mejor y qué conviene hacer."
)

WHY_PAUSED_TEXT = (
    "Toda campaña que creo queda PAUSADA a propósito: así nada empieza a gastar sin que "
    "vos la revises primero.\n\n"
    "Para activarla entrás a Ads Manager y la prendés vos. Es la regla de oro del "
    "playbook — Meta puede bloquear una cuenta que crea y activa campañas de forma "
    "automática, y no vale la pena arriesgar tu cuenta por ahorrarte un clic."
)

OUT_OF_SCOPE_TEXT = (
    "Eso se me escapa — solo sé de tus campañas de Meta Ads.\n\n"
    "Puedo contarte cuánto gastaste, cuántos leads trajiste, cuál campaña rinde mejor, "
    "qué conviene hacer, o armarte una campaña nueva."
)

NO_DATA_TEXT = (
    "Todavía no tengo datos sincronizados de tu cuenta, así que no puedo darte cifras "
    "reales. Sincronizá desde el Resumen y volvé a preguntarme."
)


def _money(value: Optional[float]) -> str:
    return f"S/. {float(value or 0):.2f}"


def _pct(value: Optional[float]) -> str:
    """Un decimal, igual que las tarjetas del Resumen (`pct_change` ya redondea
    a 1) — con `.0f` el chat decía "-1%" donde el dashboard mostraba "↓1.2%"."""
    if value is None:
        return "—"
    signo = "+" if value > 0 else ""
    return f"{signo}{value:.1f}%"


def _campaigns_with_results(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in (report.get("campaigns") or []) if c.get("conversations", 0) > 0]


# --- Respuestas con datos reales ---

def _answer_spend(period: Period, kpis: Dict[str, Any], report: Optional[Dict[str, Any]]) -> str:
    actual = kpis.get("current") or {}
    delta = (kpis.get("deltas_pct") or {}).get("spend")
    linea = f"En {period.label} llevás {_money(actual.get('spend'))} de inversión."
    if delta is not None:
        linea += f" Eso es {_pct(delta)} respecto del período anterior."
    return linea


def _answer_leads(period: Period, kpis: Dict[str, Any], report: Optional[Dict[str, Any]]) -> str:
    actual = kpis.get("current") or {}
    conv = actual.get("conversations", 0)
    delta = (kpis.get("deltas_pct") or {}).get("conversations")
    linea = f"En {period.label} trajiste {conv} conversaciones por WhatsApp."
    if delta is not None:
        linea += f" ({_pct(delta)} vs. el período anterior.)"
    if conv:
        linea += f"\n\nTe salieron a {_money(actual.get('cost_per_conv'))} cada una en promedio."
    return linea


def _answer_cpl(period: Period, kpis: Dict[str, Any], report: Optional[Dict[str, Any]]) -> str:
    actual = kpis.get("current") or {}
    conv = actual.get("conversations", 0)
    if not conv:
        return (
            f"Todavía no hay conversaciones en {period.label}, así que no hay un costo por lead "
            "que calcular."
        )
    linea = (
        f"Tu costo por conversación promedio es {_money(actual.get('cost_per_conv'))} "
        f"en {period.label}."
    )
    objetivo = ((report or {}).get("kpis") or {}).get("target_cpl")
    if objetivo:
        linea += f"\n\nTu objetivo de referencia es {_money(objetivo)}."
    return linea


def _answer_summary(period: Period, kpis: Dict[str, Any], report: Optional[Dict[str, Any]]) -> str:
    actual = kpis.get("current") or {}
    conv = actual.get("conversations", 0)
    partes = [
        f"En {period.label}: {_money(actual.get('spend'))} invertidos, "
        f"{conv} conversaciones"
        + (f" a {_money(actual.get('cost_per_conv'))} cada una." if conv else ".")
    ]
    findings = (report or {}).get("findings") or []
    if findings:
        partes.append("\n" + "\n".join(f"• {f}" for f in findings[:3]))
    return "\n".join(partes)


def _answer_budget_advice(period: Period, kpis: Dict[str, Any], report: Optional[Dict[str, Any]]) -> str:
    actual = kpis.get("current") or {}
    if not actual.get("conversations"):
        return (
            "Todavía no tengo un costo por conversación real de tu cuenta con qué calcularlo. "
            "Cuando armemos la campaña te propongo un presupuesto y te explico de dónde sale."
        )
    costo = ((report or {}).get("kpis") or {}).get("best_cost_per_conv") or actual["cost_per_conv"]
    return (
        f"Tu mejor costo por conversación es {_money(costo)}. Para apuntar a unas 20 "
        f"conversaciones, un presupuesto de arranque razonable es {_money(costo * 20)}.\n\n"
        "Igual, cuando armemos la campaña te propongo el número exacto según el historial."
    )


def _answer_best_campaign(period: Period, report: Dict[str, Any]) -> str:
    winners = _campaigns_with_results(report)
    if not winners:
        return f"Ninguna campaña trajo conversaciones en {period.label} todavía."
    best = winners[0]
    return (
        f"La que mejor te está rindiendo es **{best['name']}**: "
        f"{best['conversations']} conversaciones a {_money(best['cost_per_conv'])} cada una, "
        f"con {_money(best['spend'])} invertidos."
    )


def _answer_worst_campaign(period: Period, report: Dict[str, Any]) -> str:
    campaigns = report.get("campaigns") or []
    if not campaigns:
        return NO_DATA_TEXT
    sin_resultados = [c for c in campaigns if c.get("conversations", 0) == 0 and c.get("spend", 0) > 0]
    if sin_resultados:
        peor = max(sin_resultados, key=lambda c: c.get("spend", 0))
        return (
            f"La más floja es **{peor['name']}**: lleva {_money(peor['spend'])} invertidos y "
            "todavía no trajo ninguna conversación."
        )
    winners = _campaigns_with_results(report)
    if not winners:
        return NO_DATA_TEXT
    peor = winners[-1]
    return (
        f"La más cara es **{peor['name']}**: {peor['conversations']} conversaciones a "
        f"{_money(peor['cost_per_conv'])} cada una."
    )


def _answer_ctr(period: Period, report: Dict[str, Any]) -> str:
    kpis = report.get("kpis") or {}
    return (
        f"Tu CTR promedio es {kpis.get('avg_ctr', 0):.2f}% en {period.label}, "
        f"sobre un alcance de {kpis.get('total_reach', 0):,} personas.".replace(",", ".")
    )


def _answer_campaigns_overview(period: Period, report: Dict[str, Any]) -> str:
    campaigns = report.get("campaigns") or []
    if not campaigns:
        return NO_DATA_TEXT
    activas = [c for c in campaigns if c.get("status") == "ACTIVE"]
    lineas = [
        f"Tenés {len(campaigns)} campañas con datos en {period.label} "
        f"({len(activas)} activas en Meta):"
    ]
    for c in campaigns[:5]:
        detalle = (
            f"{c['conversations']} conv. a {_money(c['cost_per_conv'])}"
            if c.get("conversations") else f"{_money(c.get('spend', 0))} sin conversaciones"
        )
        lineas.append(f"• {c['name']} — {detalle}")
    if len(campaigns) > 5:
        lineas.append(f"…y {len(campaigns) - 5} más.")
    return "\n".join(lineas)


_OBJECTIVE_LABELS: Dict[str, str] = {
    "OUTCOME_ENGAGEMENT": "Mensajes / interacción",
    "OUTCOME_LEADS": "Generación de leads",
    "OUTCOME_SALES": "Ventas",
    "OUTCOME_TRAFFIC": "Tráfico",
    "OUTCOME_AWARENESS": "Reconocimiento de marca",
    "OUTCOME_APP_PROMOTION": "Promoción de app",
    "MESSAGES": "Mensajes / interacción",
    "LEAD_GENERATION": "Generación de leads",
}


def _objective_label(objective: Optional[str]) -> str:
    if not objective:
        return "sin objetivo registrado"
    return _OBJECTIVE_LABELS.get(objective, objective)


def _answer_schedule(period: Period, report: Dict[str, Any]) -> str:
    campaigns = report.get("campaigns") or []
    if not campaigns:
        return NO_DATA_TEXT
    con_fechas = [c for c in campaigns if c.get("created_time") or c.get("start_time") or c.get("stop_time")]
    if not con_fechas:
        return (
            "Todavía no tengo las fechas de creación ni de finalización de tus campañas "
            "sincronizadas desde Meta. Sí puedo decirte cuáles están activas y cuánto gastaron."
        )
    hoy = date.today()
    lineas = ["Fechas de tus campañas:"]
    for c in campaigns[:8]:
        creada = (c.get("created_time") or c.get("start_time") or "")[:10]
        creada_txt = f"creada el {creada}" if creada else "sin fecha de creación registrada"
        stop = (c.get("stop_time") or "")[:10]
        if stop:
            fin_txt = f"finalizó el {stop}"
        elif c.get("status") == "ACTIVE" and creada:
            dias = (hoy - date.fromisoformat(creada)).days
            fin_txt = f"sigue activa ({dias} días corriendo)"
        else:
            fin_txt = "sin fecha de finalización registrada"
        lineas.append(f"• {c['name']}: {creada_txt}, {fin_txt}.")
    return "\n".join(lineas)


def _answer_objective(period: Period, report: Dict[str, Any]) -> str:
    campaigns = report.get("campaigns") or []
    if not campaigns:
        return NO_DATA_TEXT
    con_objetivo = [c for c in campaigns if c.get("objective")]
    if not con_objetivo:
        return (
            "Todavía no tengo el objetivo de tus campañas sincronizado desde Meta. "
            "Sí puedo decirte cuáles están activas y cuánto gastaron."
        )
    lineas = [f"Objetivos de tus campañas ({len(campaigns)} en total):"]
    for c in campaigns[:8]:
        lineas.append(f"• {c['name']}: {_objective_label(c.get('objective'))} ({c.get('status')}).")
    return "\n".join(lineas)


def _answer_alerts(period: Period, report: Dict[str, Any]) -> str:
    alerts = report.get("alerts") or {}
    red = alerts.get("red") or []
    if not red:
        return "No veo nada preocupante en tus campañas ahora mismo. 👍"
    lineas = ["Esto es lo que conviene mirar:"]
    lineas += [f"• {a['name']}: {a['reason']}" for a in red[:5]]
    return "\n".join(lineas)


def _answer_recommendations(period: Period, report: Dict[str, Any]) -> str:
    recs = report.get("recommendations") or []
    if not recs:
        return (
            "Todavía no tengo suficientes datos para recomendarte algo con fundamento. "
            "Cuando tus campañas junten unos días de actividad te lo digo."
        )
    return "\n".join(["Con lo que veo en tu cuenta:"] + [f"• {r}" for r in recs[:4]])


def _answer_audience(period: Period, report: Dict[str, Any]) -> str:
    seg = report.get("segmentation") or {}
    partes = []
    for clave, etiqueta in (("age", "Edad"), ("device", "Dispositivo"), ("region", "Zona")):
        entradas = {k: v for k, v in (seg.get(clave) or {}).items() if v.get("conversions", 0) > 0}
        if not entradas:
            continue
        mejor = min(entradas.items(), key=lambda kv: kv[1]["spend"] / kv[1]["conversions"])
        costo = mejor[1]["spend"] / mejor[1]["conversions"]
        partes.append(f"• {etiqueta}: {mejor[0]} es lo que mejor te rinde ({_money(costo)} por conversación)")
    if not partes:
        return (
            "Todavía no tengo suficientes conversaciones como para decirte a qué público te "
            "rinde mejor. Con unos días más de datos te lo puedo responder."
        )
    return "\n".join(["Según tus propios resultados:"] + partes)


async def _answer_destination(db: AsyncSession, organization_id: str, slots: Dict[str, Any]) -> str:
    page_id = slots.get("page_id")
    whatsapp = slots.get("whatsapp_number")
    if not page_id:
        return (
            "Todavía no tenés una página de Facebook con WhatsApp conectada. "
            "Configurala en Configuración → Páginas y WhatsApp."
        )
    page = (await db.execute(select(MetaPage).where(
        MetaPage.organization_id == organization_id,
        MetaPage.external_page_id == page_id,
    ))).scalar_one_or_none()
    nombre = page.name if page else page_id
    return (
        f"Los anuncios salen desde la página **{nombre}** y la gente te escribe al "
        f"WhatsApp **{whatsapp or 'sin número configurado'}**."
    )


def _answer_reach(period: Period, report: Dict[str, Any]) -> str:
    kpis = (report or {}).get("kpis") or {}
    reach = kpis.get("total_reach", 0)
    impr = kpis.get("total_impressions") or sum(c.get("impressions", 0) for c in ((report or {}).get("campaigns") or []))
    freq = (impr / reach) if reach else 0
    return f"En {period.label} alcanzaste a {reach:,} personas con {impr:,} impresiones (frecuencia promedio de {freq:.2f}).".replace(",", ".")


def _answer_spend_cap(period: Period, report: Dict[str, Any]) -> str:
    acc = (report or {}).get("account") or {}
    cap = acc.get("spend_cap", 0)
    spent = acc.get("amount_spent", 0)
    remaining = max(0, cap - spent) if cap else 0
    if cap:
        return f"Tu tope de gasto en la cuenta es {_money(cap)} y te quedan {_money(remaining)} disponibles."
    return "No tenés un tope de gasto configurado en Meta Ads."


# Respuestas que combinan KPIs (fuente del dashboard) con el reporte.
_KPI_ANSWERS = {
    "SPEND": _answer_spend,
    "LEADS": _answer_leads,
    "CPL": _answer_cpl,
    "SUMMARY": _answer_summary,
    "BUDGET_ADVICE": _answer_budget_advice,
}

# Respuestas que solo existen en el reporte (no hay KPI equivalente).
_REPORT_ANSWERS = {
    "BEST_CAMPAIGN": _answer_best_campaign,
    "WORST_CAMPAIGN": _answer_worst_campaign,
    "CTR": _answer_ctr,
    "REACH": _answer_reach,
    "SPEND_CAP": _answer_spend_cap,
    "CAMPAIGNS_OVERVIEW": _answer_campaigns_overview,
    "ALERTS": _answer_alerts,
    "RECOMMENDATIONS": _answer_recommendations,
    "AUDIENCE": _answer_audience,
    "SCHEDULE": _answer_schedule,
    "OBJECTIVE": _answer_objective,
}


# Qué se le pasa al LLM como datos crudos cuando redacta la respuesta. Es un
# recorte del reporte: lo que los builders ya miraban, nada nuevo.
_FACT_KEYS_FROM_REPORT = ("kpis", "campaigns", "alerts", "recommendations", "segmentation", "account")


def _build_facts(period: Period, kpis: Optional[Dict[str, Any]], report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Las cifras reales que respaldan la respuesta.

    Sirve para dos cosas: dárselas al LLM para que redacte, y ser la lista
    blanca contra la que se valida lo que escribió (`numbers_are_grounded`).
    Todo número que no esté acá no puede aparecer en la respuesta.
    """
    facts: Dict[str, Any] = {"ventana": period.label, "dias_analizados": period.days}
    if kpis:
        facts["kpis_del_periodo"] = kpis
    if report:
        facts.update({k: report[k] for k in _FACT_KEYS_FROM_REPORT if report.get(k)})
    return facts


async def answer_question(
    db: AsyncSession,
    organization_id: str,
    ad_account: AdAccount,
    topic: str,
    slots: Dict[str, Any],
    period: Optional[Period] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Responde un tema ya clasificado.

    Devuelve `(texto, facts)`. El texto es la respuesta armada por el código
    con cifras reales — siempre válida por sí sola. `facts` son esas mismas
    cifras en crudo: si hay LLM disponible, el motor le pide que redacte a
    partir de ellas y valida que no haya inventado ningún número. Cuando es
    `None` (textos de producto como "cómo funciona" o la página de destino) no
    hay nada que analizar y el texto se usa tal cual.

    La ventana se recorta a lo que realmente hay sincronizado: preguntar por 90
    días con 10 días de datos responde sobre 10 y lo aclara.
    """
    if topic == "HOW_IT_WORKS":
        return HOW_IT_WORKS_TEXT, None
    if topic == "WHY_PAUSED":
        return WHY_PAUSED_TEXT, None
    if topic == "DESTINATION":
        return await _answer_destination(db, organization_id, slots), None

    if topic not in _KPI_ANSWERS and topic not in _REPORT_ANSWERS:
        return OUT_OF_SCOPE_TEXT, None

    periodo = clamp_to_available(
        period or Period(ANSWER_WINDOW_DAYS, f"los últimos {ANSWER_WINDOW_DAYS} días"),
        await days_of_history(db, ad_account.id),
    )

    report = None
    if topic in _NEEDS_REPORT:
        report = await get_report(db, ad_account, window_days=periodo.days)

    if topic in _KPI_ANSWERS:
        kpis = await get_kpis_with_deltas(db, ad_account.id, window_days=periodo.days)
        actual = kpis.get("current") or {}
        if not actual.get("spend") and not actual.get("conversations"):
            return NO_DATA_TEXT, None
        return _KPI_ANSWERS[topic](periodo, kpis, report), _build_facts(periodo, kpis, report)

    if not report or not (report.get("campaigns") or report.get("kpis")):
        return NO_DATA_TEXT, None
    return _REPORT_ANSWERS[topic](periodo, report), _build_facts(periodo, None, report)
