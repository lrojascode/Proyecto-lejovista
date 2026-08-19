"""Resolvedor de bloques de datos para el chat de IA (Fase 5.2).

Sustituye el switch rígido de 15 ramas por un catálogo de bloques estructurados
(kpis, ranking, breakdowns, timeseries, alerts, recommendations, account, destination, creative, product).

Pre-computa cálculos analíticos deterministas en Python (promedios diarios, proyecciones, brechas,
participación de gasto) para que el modelo no tenga que calcular nada alucinado.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.meta_page import MetaPage
from app.services.campaign_chat.grounding import GroundingSet, MAX_GROUNDING_VALUES
from app.services.campaign_chat.period import DEFAULT_WINDOW_DAYS, Period, clamp_to_available
from app.services.campaign_chat import questions
from app.services.kpi_period import days_of_history, get_kpis_with_deltas
from app.services.report_cache import get_report

logger = logging.getLogger("lejovista.facts")

RANKING_TOP_K = 8
BREAKDOWN_TOP_K = 3

BLOCK_PRIORITY: List[str] = [
    "kpis",
    "reach",
    "ranking",
    "account",
    "alerts",
    "recommendations",
    "breakdowns",
    "destination",
]

BLOCK_DESCRIPTIONS: Dict[str, str] = {
    "kpis": "métricas clave: inversión, conversaciones, costo por lead y variaciones",
    "reach": "alcance de personas, impresiones, frecuencia y CPM",
    "ranking": "ranking de campañas ordenadas por rendimiento",
    "account": "datos de la cuenta publicitaria, estado, tope de gasto y saldo restante",
    "alerts": "alertas y desvíos detectados en la cuenta",
    "recommendations": "sugerencias y recomendaciones optimizadas",
    "breakdowns": "desglose por ubicación/placement y datos demográficos",
    "destination": "página de Facebook y número de WhatsApp de destino",
}

def _safe_add_block(facts: Dict[str, Any], block_dict: Dict[str, Any], block_name: str) -> bool:
    """Verifica si anadir block_dict mantiene el total dentro de MAX_GROUNDING_VALUES.
    Si entra, lo anade a facts y devuelve True; si no, lo omite y devuelve False (H7).
    """
    candidate = {**facts, **block_dict}
    if GroundingSet.from_data(candidate).size() <= MAX_GROUNDING_VALUES:
        facts.update(block_dict)
        return True
    logger.warning(
        "Bloque '%s' omitido para no superar MAX_GROUNDING_VALUES (%d)",
        block_name, MAX_GROUNDING_VALUES
    )
    return False

PLAN_BY_TOPIC: Dict[str, List[str]] = {
    "SPEND": ["kpis"],
    "SPEND_CAP": ["account"],
    "REACH": ["reach"],
    "LEADS": ["kpis", "ranking"],
    "CPL": ["kpis", "ranking"],
    "CTR": ["reach", "ranking"],
    "BEST_CAMPAIGN": ["ranking", "kpis"],
    "WORST_CAMPAIGN": ["ranking", "kpis"],
    "CAMPAIGNS_OVERVIEW": ["ranking"],
    "ALERTS": ["alerts", "kpis"],
    "RECOMMENDATIONS": ["recommendations", "kpis"],
    "AUDIENCE": ["breakdowns", "ranking"],
    "DESTINATION": ["destination"],
    "HOW_IT_WORKS": ["destination"],
    "WHY_PAUSED": [],
    "BUDGET_ADVICE": ["kpis", "account", "recommendations"],
    "SUMMARY": ["kpis", "reach", "ranking", "alerts", "recommendations"],
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

    # 3. Traer datos de reporte (cacheado) y KPIs si son necesarios
    report = None
    if any(b in ("ranking", "breakdowns", "alerts", "recommendations", "account", "creative", "reach") for b in blocks) or topic in (
        "BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "ALERTS", "RECOMMENDATIONS", "AUDIENCE", "CTR", "SUMMARY", "CPL", "BUDGET_ADVICE", "REACH", "SPEND_CAP"
    ):
        report = await get_report(db, ad_account, effective_days)

    # Ordenar los bloques a procesar según BLOCK_PRIORITY (H7)
    priority_map = {b: i for i, b in enumerate(BLOCK_PRIORITY)}
    active_blocks = set(blocks)
    if topic in ("SPEND", "LEADS", "CPL", "SUMMARY", "BUDGET_ADVICE", "BEST_CAMPAIGN", "WORST_CAMPAIGN"):
        active_blocks.add("kpis")
    if topic in ("REACH", "CTR", "SUMMARY"):
        active_blocks.add("reach")
    if topic in ("SPEND_CAP", "BUDGET_ADVICE"):
        active_blocks.add("account")
    if topic in ("BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "LEADS", "CPL", "CTR", "SUMMARY"):
        active_blocks.add("ranking")
    if topic in ("AUDIENCE", "SUMMARY"):
        active_blocks.add("breakdowns")
    if topic in ("ALERTS", "SUMMARY"):
        active_blocks.add("alerts")
    if topic in ("RECOMMENDATIONS", "SUMMARY", "BUDGET_ADVICE"):
        active_blocks.add("recommendations")
    if topic in ("DESTINATION", "HOW_IT_WORKS"):
        active_blocks.add("destination")

    ordered_blocks = sorted(active_blocks, key=lambda b: priority_map.get(b, 999))

    kpis_data = None
    for b in ordered_blocks:
        if b == "kpis":
            if "kpis_del_periodo" not in facts:
                kpis_data = await get_kpis_with_deltas(db, ad_account.id, effective_days)
                curr = kpis_data.get("current", {})
                deltas = kpis_data.get("deltas_pct", {})
                spend = round(curr.get("spend", 0.0), 2)
                convs = int(curr.get("conversations", 0))
                cpl = round(curr.get("cost_per_conv", 0.0), 2)
                daily_avg_spend = round(spend / max(effective_days, 1), 2)
                projection_30d_spend = round(daily_avg_spend * 30, 2)
                
                kpi_dict = {
                    "kpis_del_periodo": {
                        "spend": spend,
                        "conversations": convs,
                        "cost_per_conv": cpl,
                        "gasto_promedio_diario": daily_avg_spend,
                        "proyeccion_mensual_gasto": projection_30d_spend,
                        "delta_spend_pct": deltas.get("spend", 0.0),
                        "delta_conversations_pct": deltas.get("conversations", 0.0),
                        "delta_cpl_pct": deltas.get("cost_per_conv", 0.0),
                    }
                }
                _safe_add_block(facts, kpi_dict, "kpis")

        elif b == "reach" and report:
            if "alcance" not in facts:
                rep_kpis = report.get("kpis", {})
                camps = report.get("campaigns", [])
                reach_val = int(rep_kpis.get("total_reach", 0))
                impressions_val = int(rep_kpis.get("total_impressions") or sum(c.get("impressions", 0) for c in camps))
                spend_val = float(rep_kpis.get("total_spend", 0.0))
                avg_ctr = float(rep_kpis.get("avg_ctr", 0.0))
                freq = round(impressions_val / max(reach_val, 1), 2) if reach_val > 0 else 0.0
                cpm = round((spend_val / max(impressions_val, 1)) * 1000, 2) if impressions_val > 0 else 0.0

                reach_dict = {
                    "alcance": {
                        "total_personas": reach_val,
                        "impresiones": impressions_val,
                        "frecuencia": freq,
                        "cpm": cpm,
                        "ctr_promedio_pct": avg_ctr,
                    }
                }
                _safe_add_block(facts, reach_dict, "reach")

        elif b == "account":
            if "account" not in facts:
                cap = float(ad_account.spend_cap or 0.0)
                spent = float(ad_account.amount_spent or 0.0)
                remaining = max(0.0, cap - spent) if cap > 0 else 0.0
                days_to_cap = None
                if cap > 0 and kpis_data:
                    daily = facts.get("kpis_del_periodo", {}).get("gasto_promedio_diario", 0.0)
                    if daily > 0:
                        days_to_cap = round(remaining / daily, 1)

                acc_dict = {
                    "account": {
                        "name": ad_account.name,
                        "status": ad_account.status,
                        "spend_cap": cap,
                        "amount_spent": spent,
                        "tope_restante": round(remaining, 2),
                        "dias_para_agotar_tope": days_to_cap,
                    }
                }
                _safe_add_block(facts, acc_dict, "account")

        elif b == "ranking" and report:
            if "campaigns" not in facts:
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
                rank_dict = {
                    "campaigns": top_camps,
                    "total_campanas_activas": sum(1 for c in camps if c.get("status") == "ACTIVE"),
                    "total_campanas": len(camps),
                }
                _safe_add_block(facts, rank_dict, "ranking")

        elif b == "breakdowns" and report:
            if "placements" not in facts:
                bk_dict = {
                    "placements": report.get("placements_summary", {}),
                    "demographics": report.get("demographics_summary", {}),
                    "extra_breakdowns": report.get("extra_breakdowns", {}),
                }
                _safe_add_block(facts, bk_dict, "breakdowns")

        elif b == "alerts" and report:
            if "alertas_activas" not in facts:
                alt_dict = {
                    "alertas_activas": [
                        {"level": a.get("level"), "category": a.get("category"), "title": a.get("title")}
                        for a in report.get("insights", [])[:5]
                    ]
                }
                _safe_add_block(facts, alt_dict, "alerts")

        elif b == "recommendations" and report:
            if "recomendaciones" not in facts:
                rec_dict = {"recomendaciones": report.get("recommendations", [])[:5]}
                _safe_add_block(facts, rec_dict, "recommendations")

        elif b == "destination":
            if "destino" not in facts:
                page_info = None
                if db is not None:
                    pages = (await db.execute(select(MetaPage).where(MetaPage.organization_id == organization_id))).scalars().all()
                    if pages:
                        p = pages[0]
                        page_info = {"name": p.name, "whatsapp_number": p.whatsapp_number, "id": p.external_page_id}
                dest_dict = {"destino": page_info}
                _safe_add_block(facts, dest_dict, "destination")

    # 4. Narración Canónica determinista
    canonical_text = narrate_facts(facts, topic, ad_account)
    return facts, canonical_text


def narrate_facts(facts: Dict[str, Any], topic: str, ad_account: AdAccount) -> str:
    """Genera texto canónico determinista según el tema y los facts pre-computados."""
    ventana = facts.get("ventana", "el período analizado")
    kpis = facts.get("kpis_del_periodo", {})
    acc = facts.get("account", {})

    if topic == "HOW_IT_WORKS":
        return questions.HOW_IT_WORKS_TEXT

    if topic == "WHY_PAUSED":
        return questions.WHY_PAUSED_TEXT

    if topic == "SPEND_CAP":
        if acc.get("spend_cap", 0) > 0:
            return f"Tu tope de gasto en la cuenta es S/. {acc['spend_cap']:.2f} y te quedan S/. {acc['tope_restante']:.2f} disponibles."
        return f"Tu cuenta {ad_account.name} no tiene un tope de gasto (spend cap) configurado en Meta Ads."

    if topic == "SPEND":
        spend = kpis.get("spend", 0.0)
        convs = kpis.get("conversations", 0)
        if not spend and not convs:
            return questions.NO_DATA_TEXT
        delta = kpis.get("delta_spend_pct")
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
        text = f"En {ventana} llevás S/. {spend:.2f} de inversión{delta_str}."
        if "account" in facts and acc.get("spend_cap", 0) > 0:
            text += f" Tu tope de gasto en la cuenta es S/. {acc['spend_cap']:.2f} y te quedan S/. {acc['tope_restante']:.2f}."
        return text

    if topic == "REACH":
        alc = facts.get("alcance", {})
        personas = alc.get("total_personas", 0)
        impresiones = alc.get("impresiones", 0)
        if not personas and not impresiones:
            return questions.NO_DATA_TEXT
        freq = alc.get("frecuencia", 0.0)
        return f"En {ventana} alcanzaste a {personas:,} personas con {impresiones:,} impresiones (frecuencia promedio de {freq:.2f}).".replace(",", ".")

    if topic == "CTR":
        alc = facts.get("alcance", {})
        personas = alc.get("total_personas", 0)
        impresiones = alc.get("impresiones", 0)
        if not personas and not impresiones:
            return questions.NO_DATA_TEXT
        ctr = alc.get("ctr_promedio_pct", 0.0)
        return f"Tu CTR promedio es {ctr:.2f}% en {ventana}, sobre un alcance de {personas:,} personas.".replace(",", ".")

    if topic == "LEADS":
        convs = kpis.get("conversations", 0)
        spend = kpis.get("spend", 0.0)
        if not spend and not convs:
            return questions.NO_DATA_TEXT
        delta = kpis.get("delta_conversations_pct")
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
        camps = facts.get("campaigns", [])
        top_str = f" La que más generó fue {camps[0]['name']} con {camps[0]['conversations']} contactos." if camps else ""
        return f"En {ventana} lograste {convs} conversaciones iniciadas{delta_str}.{top_str}"

    if topic == "CPL":
        convs = kpis.get("conversations", 0)
        if not convs:
            return f"Todavía no hay conversaciones en {ventana}, así que no hay un costo por lead que calcular."
        cpl = kpis.get("cost_per_conv", 0.0)
        delta = kpis.get("delta_cpl_pct")
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
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
