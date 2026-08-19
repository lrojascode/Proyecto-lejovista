"""Resolvedor de bloques de datos para el chat de IA (Fase 5.2).

Sustituye el switch rígido de 15 ramas por un catálogo de bloques estructurados
(kpis, ranking, breakdowns, timeseries, alerts, recommendations, account, destination, creative, product).

Pre-computa cálculos analíticos deterministas en Python (promedios diarios, proyecciones, brechas,
participación de gasto) para que el modelo no tenga que calcular nada alucinado.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.meta_page import MetaPage
from app.services.campaign_chat.period import DEFAULT_WINDOW_DAYS, Period, clamp_to_available
from app.services.kpi_period import days_of_history, get_kpis_with_deltas
from app.services.report_cache import get_report

RANKING_TOP_K = 8
BREAKDOWN_TOP_K = 3

PLAN_BY_TOPIC: Dict[str, List[str]] = {
    "SPEND": ["kpis", "account"],
    "LEADS": ["kpis", "ranking"],
    "CPL": ["kpis", "ranking"],
    "CTR": ["kpis", "ranking"],
    "BEST_CAMPAIGN": ["ranking", "kpis"],
    "WORST_CAMPAIGN": ["ranking", "kpis"],
    "CAMPAIGNS_OVERVIEW": ["ranking", "account"],
    "ALERTS": ["alerts", "kpis"],
    "RECOMMENDATIONS": ["recommendations", "kpis"],
    "AUDIENCE": ["breakdowns", "ranking"],
    "DESTINATION": ["destination"],
    "HOW_IT_WORKS": ["destination"],
    "WHY_PAUSED": [],
    "BUDGET_ADVICE": ["kpis", "account", "recommendations"],
    "SUMMARY": ["kpis", "ranking", "alerts", "recommendations"],
}


async def resolve_facts(
    db: AsyncSession,
    organization_id: str,
    ad_account: AdAccount,
    topic_or_blocks: Any,
    slots: Optional[Dict[str, Any]] = None,
    window_days: Optional[int] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Construye el paquete de facts combinando los bloques solicitados y genera
    la narración canónica determinista.
    """
    slots = slots or {}
    
    # 1. Determinar lista de bloques y tema
    if isinstance(topic_or_blocks, str):
        topic = topic_or_blocks
        blocks = PLAN_BY_TOPIC.get(topic, ["kpis"])
    elif isinstance(topic_or_blocks, (list, set, tuple)):
        blocks = list(topic_or_blocks)
        topic = "CUSTOM_PLAN"
    else:
        topic = getattr(topic_or_blocks, "topic", "SUMMARY")
        blocks = getattr(topic_or_blocks, "blocks", ["kpis", "ranking"])

    # 2. Ventana de tiempo
    if window_days is None:
        window_days = slots.get("window_days") or DEFAULT_WINDOW_DAYS
    
    available_days = await days_of_history(db, ad_account.id)
    raw_period = Period(window_days, f"los últimos {window_days} días")
    clamped_period = clamp_to_available(raw_period, available_days)
    effective_days = clamped_period.days
    window_label = clamped_period.label

    facts: Dict[str, Any] = {
        "ventana": window_label,
        "dias_analizados": effective_days,
        "topic": topic,
    }

    # 3. Traer datos de reporte (cacheado) y KPIs
    report = None
    if any(b in ("ranking", "breakdowns", "alerts", "recommendations", "account", "creative") for b in blocks) or topic in (
        "BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "ALERTS", "RECOMMENDATIONS", "AUDIENCE", "CTR", "SUMMARY", "CPL", "BUDGET_ADVICE", "SPEND"
    ):
        report = await get_report(db, ad_account, effective_days)

    # Bloque: KPIs
    kpis_data = None
    if "kpis" in blocks or any(topic in ("SPEND", "LEADS", "CPL", "SUMMARY", "BUDGET_ADVICE", "CTR", "BEST_CAMPAIGN", "WORST_CAMPAIGN") for _ in [1]):
        kpis_data = await get_kpis_with_deltas(db, ad_account.id, effective_days)
        curr = kpis_data.get("current", {})
        prev = kpis_data.get("previous", {})
        deltas = kpis_data.get("deltas_pct", {})
        
        spend = round(curr.get("spend", 0.0), 2)
        convs = int(curr.get("conversations", 0))
        cpl = round(curr.get("cost_per_conv", 0.0), 2)
        
        daily_avg_spend = round(spend / max(effective_days, 1), 2)
        projection_30d_spend = round(daily_avg_spend * 30, 2)
        
        facts["kpis_del_periodo"] = {
            "spend": spend,
            "conversations": convs,
            "cost_per_conv": cpl,
            "gasto_promedio_diario": daily_avg_spend,
            "proyeccion_mensual_gasto": projection_30d_spend,
            "delta_spend_pct": deltas.get("spend", 0.0),
            "delta_conversations_pct": deltas.get("conversations", 0.0),
            "delta_cpl_pct": deltas.get("cost_per_conv", 0.0),
        }

    # Bloque: Account
    if "account" in blocks or topic in ("SPEND", "BUDGET_ADVICE", "CAMPAIGNS_OVERVIEW"):
        cap = float(ad_account.spend_cap or 0.0)
        spent = float(ad_account.amount_spent or 0.0)
        remaining = max(0.0, cap - spent) if cap > 0 else 0.0
        
        days_to_cap = None
        if cap > 0 and kpis_data:
            daily = facts.get("kpis_del_periodo", {}).get("gasto_promedio_diario", 0.0)
            if daily > 0:
                days_to_cap = round(remaining / daily, 1)

        facts["account"] = {
            "name": ad_account.name,
            "status": ad_account.status,
            "spend_cap": cap,
            "amount_spent": spent,
            "tope_restante": round(remaining, 2),
            "dias_para_agotar_tope": days_to_cap,
        }

    # Bloque: Ranking
    if ("ranking" in blocks or topic in ("BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "LEADS", "CPL", "CTR", "SUMMARY")) and report:
        camps = report.get("campaigns", [])
        top_camps = []
        for c in camps[:RANKING_TOP_K]:
            top_camps.append({
                "name": c.get("name"),
                "status": c.get("status"),
                "spend": round(float(c.get("spend", 0.0)), 2),
                "conversations": int(c.get("conversations", 0)),
                "cost_per_conv": round(float(c.get("cpl", 0.0)), 2),
                "ctr": round(float(c.get("ctr", 0.0)), 2),
            })
        facts["campaigns"] = top_camps
        facts["total_campanas_activas"] = sum(1 for c in camps if c.get("status") == "ACTIVE")
        facts["total_campanas"] = len(camps)

    # Bloque: Breakdowns
    if ("breakdowns" in blocks or topic in ("AUDIENCE", "SUMMARY")) and report:
        facts["placements"] = report.get("placements_summary", {})
        facts["demographics"] = report.get("demographics_summary", {})
        facts["extra_breakdowns"] = report.get("extra_breakdowns", {})

    # Bloque: Alerts
    if ("alerts" in blocks or topic in ("ALERTS", "SUMMARY")) and report:
        facts["alertas_activas"] = [
            {"level": a.get("level"), "category": a.get("category"), "title": a.get("title")}
            for a in report.get("insights", [])[:5]
        ]

    # Bloque: Recommendations
    if ("recommendations" in blocks or topic in ("RECOMMENDATIONS", "SUMMARY", "BUDGET_ADVICE")) and report:
        facts["recomendaciones"] = report.get("recommendations", [])[:5]

    # Bloque: Destination
    if "destination" in blocks or topic in ("DESTINATION", "HOW_IT_WORKS"):
        page_info = None
        if db is not None:
            pages = (await db.execute(select(MetaPage).where(MetaPage.organization_id == organization_id))).scalars().all()
            if pages:
                p = pages[0]
                page_info = {"name": p.name, "whatsapp_number": p.whatsapp_number, "id": p.external_page_id}
        facts["destino"] = page_info

    # 4. Narración Canónica determinista
    canonical_text = narrate_facts(facts, topic, ad_account)
    return facts, canonical_text


def narrate_facts(facts: Dict[str, Any], topic: str, ad_account: AdAccount) -> str:
    """Genera texto canónico determinista según el tema y los facts pre-computados."""
    ventana = facts.get("ventana", "el período analizado")
    kpis = facts.get("kpis_del_periodo", {})
    acc = facts.get("account", {})

    if topic == "HOW_IT_WORKS":
        dest = facts.get("destino") or {}
        p_name = dest.get("name") or "tu página de Facebook"
        wa = dest.get("whatsapp_number")
        wa_text = f" y WhatsApp {wa}" if wa else ""
        return (
            f"Soy tu asistente de Meta Ads para {ad_account.name}. Te ayudo a monitorear "
            f"métricas en tiempo real, detectar desvíos y crear campañas listas para publicar en {p_name}{wa_text}."
        )

    if topic == "WHY_PAUSED":
        return (
            "Todas las campañas se crean en estado PAUSED por seguridad financiera, para que puedas revisar "
            "los creativos, segmentación y presupuesto en Meta Ads Manager antes de encenderlas."
        )

    if topic == "SPEND":
        spend = kpis.get("spend", 0.0)
        delta = kpis.get("delta_spend_pct", 0.0)
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta != 0 else ""
        text = f"En {ventana} llevás S/. {spend:.2f} de inversión{delta_str}."
        if acc.get("spend_cap", 0) > 0:
            text += f" Tu tope de gasto en la cuenta es S/. {acc['spend_cap']:.2f} y te quedan S/. {acc['tope_restante']:.2f}."
        return text

    if topic == "LEADS":
        convs = kpis.get("conversations", 0)
        delta = kpis.get("delta_conversations_pct", 0.0)
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta != 0 else ""
        camps = facts.get("campaigns", [])
        top_str = f" La que más generó fue {camps[0]['name']} con {camps[0]['conversations']} contactos." if camps else ""
        return f"En {ventana} lograste {convs} conversaciones iniciadas{delta_str}.{top_str}"

    if topic == "CPL":
        cpl = kpis.get("cost_per_conv", 0.0)
        delta = kpis.get("delta_cpl_pct", 0.0)
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta != 0 else ""
        camps = facts.get("campaigns", [])
        best_cpl = camps[0] if camps else None
        best_str = f" La más eficiente es {best_cpl['name']} con S/. {best_cpl['cost_per_conv']:.2f} por lead." if best_cpl else ""
        return f"Tu costo por conversación promedio en {ventana} es de S/. {cpl:.2f}{delta_str}.{best_str}"

    if topic == "BEST_CAMPAIGN":
        camps = facts.get("campaigns", [])
        if not camps:
            return f"No hay campañas activas con métricas registradas en {ventana}."
        best = camps[0]
        return f"Tu mejor campaña en {ventana} es {best['name']} con {best['conversations']} conversaciones a un costo de S/. {best['cost_per_conv']:.2f} por contacto."

    if topic == "WORST_CAMPAIGN":
        camps = facts.get("campaigns", [])
        if not camps:
            return f"No hay campañas registradas en {ventana}."
        worst = camps[-1]
        return f"La campaña con menor rendimiento en {ventana} es {worst['name']} con un costo por contacto de S/. {worst['cost_per_conv']:.2f} ({worst['conversations']} conversaciones)."

    if topic == "CAMPAIGNS_OVERVIEW":
        total_act = facts.get("total_campanas_activas", 0)
        total = facts.get("total_campanas", 0)
        camps = facts.get("campaigns", [])
        names = ", ".join(c["name"] for c in camps[:3])
        return f"Tenés {total_act} campañas activas (de {total} totales en {ventana}): {names}."

    if topic == "DESTINATION":
        dest = facts.get("destino")
        if not dest or not dest.get("name"):
            return "No tenés una página de Facebook ni WhatsApp configurados como destino todavía."
        wa = dest.get("whatsapp_number")
        wa_text = f" y los mensajes van al WhatsApp {wa}" if wa else " (sin número de WhatsApp asignado)"
        return f"Las campañas se dirigen a la página {dest['name']}{wa_text}."

    # Default / SUMMARY
    spend = kpis.get("spend", 0.0)
    convs = kpis.get("conversations", 0)
    cpl = kpis.get("cost_per_conv", 0.0)
    return f"Resumen de {ventana}: S/. {spend:.2f} invertidos, {convs} conversaciones y un costo promedio de S/. {cpl:.2f} por lead."
