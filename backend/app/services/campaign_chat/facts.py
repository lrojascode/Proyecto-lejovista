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
from app.services.campaign_chat import formatting
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
    "estructura",
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
    "estructura": "objetivo, estado y fechas (creación, inicio, fin) de cada campaña",
    "account": "datos de la cuenta publicitaria, estado, tope de gasto y saldo restante",
    "alerts": "alertas y desvíos detectados en la cuenta",
    "recommendations": "sugerencias y recomendaciones optimizadas",
    "breakdowns": "desglose por ubicación/placement y datos demográficos",
    "destination": "página de Facebook y número de WhatsApp de destino",
}

# Definidos una sola vez en `formatting`: tener dos copias de esto fue
# exactamente lo que produjo que el mismo monto saliera con dos formatos.
_money = formatting.money
_fmt_int = formatting.fmt_int


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
    "SCHEDULE": ["estructura"],
    "OBJECTIVE": ["estructura"],
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
        blocks = list(PLAN_BY_TOPIC.get(topic, ["kpis"]))
        topics_to_check = [topic]
    elif isinstance(topic_or_blocks, (list, set, tuple)):
        raw_items = list(topic_or_blocks)
        blocks = []
        topics = []
        for item in raw_items:
            if item in PLAN_BY_TOPIC:
                topics.append(item)
                for b in PLAN_BY_TOPIC[item]:
                    if b not in blocks:
                        blocks.append(b)
            else:
                if item not in blocks:
                    blocks.append(item)
        if len(topics) == 1:
            topic = topics[0]
            topics_to_check = [topic]
        elif topics:
            topic = topics
            topics_to_check = list(topics)
        else:
            topic = "CUSTOM_PLAN"
            topics_to_check = []
    else:
        topic_attr = getattr(topic_or_blocks, "topic", None)
        topic = topic_attr if topic_attr is not None else "CUSTOM_PLAN"
        blocks = list(getattr(topic_or_blocks, "blocks", None) or ["kpis", "ranking"])
        if isinstance(topic, str) and topic != "CUSTOM_PLAN":
            topics_to_check = [topic]
        elif isinstance(topic, (list, tuple, set)):
            topics_to_check = list(topic)
        else:
            topics_to_check = []

    # 2. Ventana de tiempo
    if window_days is None:
        window_days = slots.get("window_days") or DEFAULT_WINDOW_DAYS
    
    available_days = await days_of_history(db, ad_account.id) if db is not None else window_days
    raw_period = Period(window_days, f"los últimos {window_days} días")
    clamped_period = clamp_to_available(raw_period, available_days)
    effective_days = clamped_period.days
    window_label = clamped_period.label

    facts: Dict[str, Any] = {
        "ventana": window_label,
        "dias_analizados": effective_days,
        "topic": topic,
    }
    if isinstance(topic, list):
        facts["topics"] = topic

    # 3. Traer datos de reporte (cacheado) y KPIs si son necesarios
    # Ordenar los bloques a procesar según BLOCK_PRIORITY (H7)
    priority_map = {b: i for i, b in enumerate(BLOCK_PRIORITY)}
    active_blocks = set(blocks)
    for t in topics_to_check:
        if t in ("SPEND", "LEADS", "CPL", "SUMMARY", "BUDGET_ADVICE", "BEST_CAMPAIGN", "WORST_CAMPAIGN"):
            active_blocks.add("kpis")
        if t in ("REACH", "CTR", "SUMMARY"):
            active_blocks.add("reach")
        if t in ("SPEND_CAP", "BUDGET_ADVICE"):
            active_blocks.add("account")
        if t in ("BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "LEADS", "CPL", "CTR", "SUMMARY"):
            active_blocks.add("ranking")
        if t in ("AUDIENCE", "SUMMARY"):
            active_blocks.add("breakdowns")
        if t in ("SCHEDULE", "OBJECTIVE"):
            active_blocks.add("estructura")
        if t in ("ALERTS", "SUMMARY"):
            active_blocks.add("alerts")
        if t in ("RECOMMENDATIONS", "SUMMARY", "BUDGET_ADVICE"):
            active_blocks.add("recommendations")
        if t in ("DESTINATION", "HOW_IT_WORKS"):
            active_blocks.add("destination")

    report = None
    if any(b in ("ranking", "breakdowns", "alerts", "recommendations", "account", "creative", "reach") for b in active_blocks) or any(
        t in ("BEST_CAMPAIGN", "WORST_CAMPAIGN", "CAMPAIGNS_OVERVIEW", "ALERTS", "RECOMMENDATIONS", "AUDIENCE", "CTR", "SUMMARY", "CPL", "BUDGET_ADVICE", "REACH", "SPEND_CAP")
        for t in topics_to_check
    ):
        report = await get_report(db, ad_account, effective_days)

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
                all_camps = report.get("campaigns", [])
                camps_with_conv = [c for c in all_camps if int(c.get("conversations", 0)) > 0]
                top_camps = []
                for c in camps_with_conv[:RANKING_TOP_K]:
                    top_camps.append({
                        "name": c.get("name"),
                        "status": c.get("status"),
                        "spend": round(float(c.get("spend", 0.0)), 2),
                        "conversations": int(c.get("conversations", 0)),
                        "cost_per_conv": round(float(c.get("cost_per_conv", 0.0)), 2),
                        "ctr": round(float(c.get("ctr", 0.0)), 2),
                    })
                rank_dict = {
                    "campaigns": top_camps,
                    "total_campanas_activas": sum(1 for c in all_camps if c.get("status") == "ACTIVE"),
                    "total_campanas": len(all_camps),
                }
                _safe_add_block(facts, rank_dict, "ranking")

        elif b == "estructura":
            if "campanas_estructura" not in facts and db is not None:
                latest_dates = (
                    select(
                        CampaignSnapshot.campaign_id,
                        func.max(CampaignSnapshot.date).label("max_date"),
                    )
                    .where(CampaignSnapshot.ad_account_id == ad_account.id)
                    .group_by(CampaignSnapshot.campaign_id)
                    .subquery()
                )
                q = select(CampaignSnapshot).join(
                    latest_dates,
                    (CampaignSnapshot.campaign_id == latest_dates.c.campaign_id)
                    & (CampaignSnapshot.date == latest_dates.c.max_date),
                ).where(CampaignSnapshot.ad_account_id == ad_account.id)
                rows = (await db.execute(q)).scalars().all()
                today_d = date.today()
                estructura = []
                for r in rows:
                    created = r.created_time.date() if r.created_time else None
                    start = r.start_time.date() if r.start_time else None
                    stop = r.stop_time.date() if r.stop_time else None
                    ref_start = start or created
                    # "Días corriendo" precalculado en Python: el modelo cita, no calcula (AI-1).
                    if ref_start and stop:
                        dias_corriendo = (stop - ref_start).days
                    elif ref_start and r.status == "ACTIVE":
                        dias_corriendo = (today_d - ref_start).days
                    else:
                        dias_corriendo = None
                    estructura.append({
                        "name": r.campaign_name,
                        "status": r.status,
                        "objective": r.objective,
                        "created_time": created.isoformat() if created else None,
                        "start_time": start.isoformat() if start else None,
                        "stop_time": stop.isoformat() if stop else None,
                        "dias_corriendo": dias_corriendo,
                    })
                estructura.sort(key=lambda c: c["name"] or "")
                est_dict = {"campanas_estructura": estructura}
                _safe_add_block(facts, est_dict, "estructura")

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


def _no_estructura_text(facts: Dict[str, Any], reason: str) -> str:
    """"No sé" honesto para el bloque `estructura`: dice qué falta y ofrece lo
    que sí hay (cuántas campañas y cuántas activas), sin tocar `account` — así
    nunca puede colarse el tope de gasto en una respuesta que no lo pidió."""
    campanas = facts.get("campanas_estructura", [])
    if reason == "fechas":
        detalle = "las fechas de creación y finalización de tus campañas todavía no se sincronizan desde Meta"
    elif reason == "objetivo":
        detalle = "el objetivo de tus campañas todavía no se sincroniza desde Meta"
    else:
        detalle = "todavía no tengo datos estructurales de tus campañas sincronizados desde Meta"
    if campanas:
        activas = sum(1 for c in campanas if c.get("status") == "ACTIVE")
        extra = f" Sí puedo decirte que tenés {len(campanas)} campañas ({activas} activas)."
    else:
        extra = " Sincronizá desde el Resumen y volvé a preguntarme."
    return f"Por ahora {detalle}.{extra}"


def narrate_facts(facts: Dict[str, Any], topic: Any, ad_account: AdAccount) -> str:
    """Genera texto canónico determinista según el tema y los facts pre-computados."""
    ventana = facts.get("ventana", "el período analizado")
    kpis = facts.get("kpis_del_periodo", {})
    acc = facts.get("account", {})
    alc = facts.get("alcance", {})
    camps = facts.get("campaigns", [])
    estructura = facts.get("campanas_estructura", [])

    if topic == "HOW_IT_WORKS":
        return questions.HOW_IT_WORKS_TEXT

    if topic == "WHY_PAUSED":
        return questions.WHY_PAUSED_TEXT

    if topic == "DESTINATION":
        dest = facts.get("destino")
        if not dest or not dest.get("name"):
            return "No tenés una página de Facebook ni WhatsApp configurados como destino todavía."
        wa = dest.get("whatsapp_number")
        wa_text = f" y los mensajes van al WhatsApp {wa}" if wa else " (sin número de WhatsApp asignado)"
        return f"Las campañas se dirigen a la página {dest['name']}{wa_text}."

    is_multi_topic = isinstance(topic, (list, tuple, set)) and len(topic) > 1

    if not is_multi_topic:
        single_topic = topic[0] if isinstance(topic, (list, tuple)) and len(topic) == 1 else topic

        if single_topic == "SPEND_CAP":
            if acc.get("spend_cap", 0) > 0:
                return f"Tu tope de gasto en la cuenta es {_money(acc['spend_cap'])} y te quedan {_money(acc['tope_restante'])} disponibles."
            return f"Tu cuenta {ad_account.name} no tiene un tope de gasto (spend cap) configurado en Meta Ads."

        if single_topic == "SPEND":
            spend = kpis.get("spend", 0.0)
            convs = kpis.get("conversations", 0)
            if not spend and not convs:
                return questions.NO_DATA_TEXT
            delta = kpis.get("delta_spend_pct")
            delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
            text = f"En {ventana} llevás {_money(spend)} de inversión{delta_str}."
            if "account" in facts and acc.get("spend_cap", 0) > 0:
                text += f" Tu tope de gasto en la cuenta es {_money(acc['spend_cap'])} y te quedan {_money(acc['tope_restante'])}."
            return text

        if single_topic == "REACH":
            personas = alc.get("total_personas", 0)
            impresiones = alc.get("impresiones", 0)
            if not personas and not impresiones:
                return questions.NO_DATA_TEXT
            freq = alc.get("frecuencia", 0.0)
            return f"En {ventana} alcanzaste a {_fmt_int(personas)} personas con {_fmt_int(impresiones)} impresiones (frecuencia promedio de {freq:.2f})."

        if single_topic == "CTR":
            personas = alc.get("total_personas", 0)
            impresiones = alc.get("impresiones", 0)
            if not personas and not impresiones:
                return questions.NO_DATA_TEXT
            ctr = alc.get("ctr_promedio_pct", 0.0)
            return f"Tu CTR promedio es {ctr:.2f}% en {ventana}, sobre un alcance de {_fmt_int(personas)} personas."

        if single_topic == "LEADS":
            convs = kpis.get("conversations", 0)
            spend = kpis.get("spend", 0.0)
            if not spend and not convs:
                return questions.NO_DATA_TEXT
            delta = kpis.get("delta_conversations_pct")
            delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
            top_str = f" La que más generó fue {camps[0]['name']} con {camps[0]['conversations']} contactos." if camps else ""
            return f"En {ventana} lograste {convs} conversaciones iniciadas{delta_str}.{top_str}"

        if single_topic == "CPL":
            convs = kpis.get("conversations", 0)
            if not convs:
                return f"Todavía no hay conversaciones en {ventana}, así que no hay un costo por lead que calcular."
            cpl = kpis.get("cost_per_conv", 0.0)
            delta = kpis.get("delta_cpl_pct")
            delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
            best_cpl = camps[0] if camps else None
            best_str = f" La más eficiente es {best_cpl['name']} con {_money(best_cpl['cost_per_conv'])} por lead." if best_cpl else ""
            return f"Tu costo por conversación promedio en {ventana} es de {_money(cpl)}{delta_str}.{best_str}"

        if single_topic == "BEST_CAMPAIGN":
            if not camps:
                return f"No hay campañas activas con métricas registradas en {ventana}."
            best = camps[0]
            return f"Tu mejor campaña en {ventana} es {best['name']} con {best['conversations']} conversaciones a un costo de {_money(best['cost_per_conv'])} por contacto."

        if single_topic == "WORST_CAMPAIGN":
            if not camps:
                return f"No hay campañas registradas en {ventana}."
            worst = camps[-1]
            return f"La campaña con menor rendimiento en {ventana} es {worst['name']} con un costo por contacto de {_money(worst['cost_per_conv'])} ({worst['conversations']} conversaciones)."

        if single_topic == "CAMPAIGNS_OVERVIEW":
            total_act = facts.get("total_campanas_activas", 0)
            total = facts.get("total_campanas", 0)
            names = ", ".join(c["name"] for c in camps[:3])
            return f"Tenés {total_act} campañas activas (de {total} totales en {ventana}): {names}."

        if single_topic == "BUDGET_ADVICE":
            spend = kpis.get("spend", 0.0)
            daily = kpis.get("gasto_promedio_diario", 0.0)
            cpl = kpis.get("cost_per_conv", 0.0)
            return f"En {ventana} gastaste {_money(spend)} (promedio {_money(daily)}/día) con un CPL de {_money(cpl)}. Te sugiero mantener un ritmo similar o ajustar según tus objetivos de ventas."

        if single_topic == "SCHEDULE":
            con_fechas = [c for c in estructura if c.get("created_time") or c.get("start_time") or c.get("stop_time")]
            if not con_fechas:
                return _no_estructura_text(facts, reason="fechas")
            lineas = ["Fechas de tus campañas:"]
            for c in estructura[:RANKING_TOP_K]:
                creada = c.get("created_time") or c.get("start_time")
                creada_txt = f"creada el {creada}" if creada else "sin fecha de creación registrada"
                stop = c.get("stop_time")
                if stop:
                    fin_txt = f"finalizó el {stop}"
                elif c.get("status") == "ACTIVE":
                    dias = c.get("dias_corriendo")
                    fin_txt = f"sigue activa ({dias} días corriendo)" if dias is not None else "sigue activa, sin fecha de fin"
                else:
                    fin_txt = "sin fecha de finalización registrada"
                lineas.append(f"• {c['name']}: {creada_txt}, {fin_txt}.")
            return "\n".join(lineas)

        if single_topic == "OBJECTIVE":
            con_objetivo = [c for c in estructura if c.get("objective")]
            if not con_objetivo:
                return _no_estructura_text(facts, reason="objetivo")
            lineas = [f"Objetivos de tus campañas ({len(estructura)} en total):"]
            for c in estructura[:RANKING_TOP_K]:
                lineas.append(f"• {c['name']}: {_objective_label(c.get('objective'))} ({c.get('status')}).")
            return "\n".join(lineas)

    # Multi-tema o CUSTOM_PLAN: recorremos los bloques presentes en facts según BLOCK_PRIORITY
    parts: List[str] = []
    topics_set = set(topic) if isinstance(topic, (list, tuple, set)) else {topic}

    # Bloque 1: kpis
    if "kpis_del_periodo" in facts:
        spend = kpis.get("spend", 0.0)
        convs = kpis.get("conversations", 0)
        cpl = kpis.get("cost_per_conv", 0.0)
        delta = kpis.get("delta_spend_pct")
        delta_str = f" ({'+' if delta >= 0 else ''}{delta:.1f}% vs período anterior)" if delta is not None and delta != 0 else ""
        if "SPEND" in topics_set and "LEADS" not in topics_set and "CPL" not in topics_set:
            parts.append(f"En {ventana} llevás {_money(spend)} de inversión{delta_str}.")
        elif "LEADS" in topics_set and "SPEND" not in topics_set:
            parts.append(f"En {ventana} lograste {convs} conversaciones iniciadas.")
        elif "CPL" in topics_set and "SPEND" not in topics_set:
            parts.append(f"En {ventana} tu costo por conversación promedio es {_money(cpl)}.")
        else:
            parts.append(f"En {ventana} llevás {_money(spend)} invertidos y {convs} conversaciones (CPL promedio {_money(cpl)}){delta_str}.")

    # Bloque 2: reach
    if "alcance" in facts:
        personas = alc.get("total_personas", 0)
        impresiones = alc.get("impresiones", 0)
        freq = alc.get("frecuencia", 0.0)
        parts.append(f"Alcanzaste a {_fmt_int(personas)} personas con {_fmt_int(impresiones)} impresiones (frecuencia promedio de {freq:.2f}).")

    # Bloque 3: estructura (objetivo / fechas). Si el tema se pidió pero el
    # dato no está, lo decimos explícitamente en vez de dejar que otro bloque
    # (ej. ranking) quede como única respuesta y tape el "no sé" (punto 2).
    if "campanas_estructura" in facts:
        con_fechas = [c for c in estructura if c.get("created_time") or c.get("start_time") or c.get("stop_time")]
        con_objetivo = [c for c in estructura if c.get("objective")]
        if "SCHEDULE" in topics_set:
            if con_fechas:
                c = con_fechas[0]
                creada = c.get("created_time") or c.get("start_time")
                parts.append(f"{c['name']} se creó el {creada}." if creada else f"No tengo fecha de creación para {c['name']}.")
            else:
                parts.append(_no_estructura_text(facts, reason="fechas"))
        if "OBJECTIVE" in topics_set:
            if con_objetivo:
                c = con_objetivo[0]
                parts.append(f"El objetivo de {c['name']} es {_objective_label(c.get('objective'))}.")
            else:
                parts.append(_no_estructura_text(facts, reason="objetivo"))

    # Bloque 4: account (solo si el propio tema lo pidió — nunca de relleno)
    if "account" in facts and acc.get("spend_cap", 0) > 0 and ("SPEND_CAP" in topics_set or "BUDGET_ADVICE" in topics_set):
        parts.append(f"Tu tope de gasto en la cuenta es {_money(acc['spend_cap'])} y te quedan {_money(acc['tope_restante'])} disponibles.")

    # Bloque 5: ranking
    if "campaigns" in facts and camps:
        best = camps[0]
        parts.append(f"Tu mejor campaña es {best['name']} con {best['conversations']} conversaciones a {_money(best['cost_per_conv'])} por contacto.")

    # Bloque 6: destino
    if "destino" in facts and facts["destino"]:
        d = facts["destino"]
        parts.append(f"Destino: página {d.get('name')}.")

    if parts:
        return " ".join(parts)

    if "campanas_estructura" in facts:
        return _no_estructura_text(facts, reason="estructura")

    # Fallback si no hay bloques reconocidos
    spend = kpis.get("spend", 0.0)
    convs = kpis.get("conversations", 0)
    cpl = kpis.get("cost_per_conv", 0.0)
    return f"Resumen de {ventana}: {_money(spend)} invertidos, {convs} conversaciones y un costo promedio de {_money(cpl)} por lead."
