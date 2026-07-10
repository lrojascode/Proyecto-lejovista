"""P-2.4 — Agregador del Master Brief.

Construye, desde la base de datos, el reporte estructurado con todas las secciones
del informe: KPIs, ranking de campañas, adsets, anuncios/creativos, segmentaciones
(placement/edad/género/dispositivo/geografía), audiencias, inventario de creativos,
alertas y parámetros óptimos. Los números salen de los datos sincronizados (nunca
inventados por el LLM).
"""
import datetime
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.ad_snapshot import AdSnapshot
from app.models.meta_creative import MetaCreative
from app.models.custom_audience import CustomAudience
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.campaign_draft import CampaignDraft
from app.services.ai.thresholds import classify_campaign, compute_target_cpl
from app.services.kpi_period import get_window_kpis, pct_change


class ReportBuilder:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(self, ad_account: AdAccount, window_days: int = 30) -> Dict[str, Any]:
        today = datetime.date.today()
        since = today - datetime.timedelta(days=window_days)

        async def _load(model):
            q = select(model).where(model.ad_account_id == ad_account.id, model.date >= since)
            return (await self.db.execute(q)).scalars().all()

        camp_snaps = await _load(CampaignSnapshot)
        adset_snaps = await _load(AdsetSnapshot)
        ad_snaps = await _load(AdSnapshot)
        creatives = (await self.db.execute(
            select(MetaCreative).where(MetaCreative.ad_account_id == ad_account.id))).scalars().all()
        audiences = (await self.db.execute(
            select(CustomAudience).where(CustomAudience.ad_account_id == ad_account.id))).scalars().all()

        total_spend = sum(s.spend for s in camp_snaps)
        total_reach = sum(s.reach for s in camp_snaps)
        total_impr = sum(s.impressions for s in camp_snaps)
        total_clicks = sum(s.clicks for s in camp_snaps)
        total_conv = sum((s.conversations or s.conversions) for s in camp_snaps)
        avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0

        # C-2: target_cpl de la ventana del reporte — la MISMA fórmula que usa
        # rules.py (piso/techo de thresholds.py), para que la clasificación de
        # cada campaña (GANADORA/BUENA/REGULAR/CARA/PAUSAR) sea relativa al
        # desempeño real de la cuenta, no un umbral absoluto en soles igual
        # para cualquier cliente.
        target_cpl = compute_target_cpl(total_spend, total_conv)

        campaigns = self._agg_campaigns(camp_snaps, target_cpl)
        adsets = self._agg_adsets(adset_snaps)
        ads = self._agg_ads(ad_snaps)
        segmentation = self._segmentation(camp_snaps)

        best_cost = min([c["cost_per_conv"] for c in campaigns if c["conversations"] > 0], default=0)

        # 5.2 (F-3.1): comparativa temporal — la ventana anterior del MISMO
        # tamaño (ej. 30d actuales vs los 30d previos a esos), para que el Δ%
        # sea comparable sin importar qué ventana eligió el usuario (7/30/90).
        prev_since = since - datetime.timedelta(days=window_days)
        kpis_prev = await get_window_kpis(self.db, ad_account.id, prev_since, since)
        cost_per_conv = round(total_spend / total_conv, 2) if total_conv > 0 else round(total_spend, 2)
        deltas_pct = {
            "spend": pct_change(kpis_prev["spend"], total_spend),
            "conversations": pct_change(kpis_prev["conversations"], total_conv),
            "cost_per_conv": pct_change(kpis_prev["cost_per_conv"], cost_per_conv),
        }

        q_prev_camp = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id,
            CampaignSnapshot.date >= prev_since, CampaignSnapshot.date < since,
        )
        prev_camp_snaps = (await self.db.execute(q_prev_camp)).scalars().all()
        prev_by_id = {c["campaign_id"]: c for c in self._agg_campaigns(prev_camp_snaps, target_cpl)}
        for c in campaigns:
            prev = prev_by_id.get(c["campaign_id"])
            if prev and prev["spend"] > 0:
                c["spend_delta_pct"] = pct_change(prev["spend"], c["spend"])
                c["cost_per_conv_delta_pct"] = pct_change(prev["cost_per_conv"], c["cost_per_conv"])
            else:
                c["spend_delta_pct"] = None
                c["cost_per_conv_delta_pct"] = None

        # 5.4(d): marcar campañas creadas por la IA (cruzando con el historial
        # de aprendizaje de creación, Ajuste 2A) — antes el reporte no
        # distinguía una campaña que el usuario armó a mano de una que la IA
        # propuso y creó sola.
        ai_campaign_ids = await self._get_ai_created_campaign_ids(ad_account.id)
        for c in campaigns:
            c["created_by_ai"] = c["campaign_id"] in ai_campaign_ids

        return {
            "window_days": window_days,
            "generated_at": datetime.datetime.utcnow().isoformat(),
            "account": {
                "name": ad_account.name,
                "external_account_id": ad_account.external_account_id,
                "spend_cap": ad_account.spend_cap,
                "amount_spent": ad_account.amount_spent,
                "status": ad_account.status,
            },
            "kpis": {
                "total_spend": round(total_spend, 2),
                "total_reach": total_reach,
                "total_conversations": total_conv,
                "avg_ctr": round(avg_ctr, 2),
                "best_cost_per_conv": round(best_cost, 2),
                "target_cpl": round(target_cpl, 2),
                "deltas_pct": deltas_pct,
            },
            "campaigns": campaigns,
            "adsets": adsets,
            "ads": ads,
            "segmentation": segmentation,
            "audiences": [{
                "name": a.name, "subtype": a.subtype, "description": a.description,
                "approximate_count": a.approximate_count,
            } for a in audiences],
            "creatives": [{
                "image_hash": c.image_hash, "name": c.name, "url": c.url,
                "width": c.width, "height": c.height,
            } for c in creatives],
            "alerts": self._alerts(campaigns),
            "optimal_params": self._optimal_params(adsets, segmentation, campaigns),
            "findings": self._findings(campaigns, adsets, segmentation),
            "recommendations": self._recommendations(campaigns, adsets, segmentation),
        }

    async def _get_ai_created_campaign_ids(self, ad_account_id: str) -> set:
        """5.4(d): campaign_id reales de Meta que se originaron en una
        propuesta de la IA — el resultado (`outcome_json.campaign_id`) si ya
        se evaluó, o si no `draft.payload.result.campaign_id` (mismo campo
        que usa `campaign_outcome.py` para localizar la campaña creada)."""
        q = (
            select(CampaignLearningLog.outcome_json, CampaignDraft.payload)
            .outerjoin(CampaignDraft, CampaignLearningLog.draft_id == CampaignDraft.id)
            .where(CampaignLearningLog.ad_account_id == ad_account_id)
        )
        rows = (await self.db.execute(q)).all()
        ids = set()
        for outcome_json, draft_payload in rows:
            cid = (outcome_json or {}).get("campaign_id")
            if not cid:
                cid = ((draft_payload or {}).get("result") or {}).get("campaign_id")
            if cid:
                ids.add(cid)
        return ids

    # --- agregaciones ---
    def _agg_campaigns(self, snaps, target_cpl: float) -> List[Dict[str, Any]]:
        agg: Dict[str, Any] = {}
        for s in snaps:
            a = agg.setdefault(s.campaign_id, {
                "name": s.campaign_name, "status": s.status, "budget": s.budget,
                "spend": 0.0, "impressions": 0, "reach": 0, "clicks": 0,
                "conversions": 0, "conversations": 0, "freqs": [],
            })
            a["name"] = s.campaign_name
            a["status"] = s.status
            a["spend"] += s.spend
            a["impressions"] += s.impressions
            a["reach"] += s.reach
            a["clicks"] += s.clicks
            a["conversions"] += s.conversions
            a["conversations"] += s.conversations
            if s.frequency:
                a["freqs"].append(s.frequency)

        out = []
        for cid, a in agg.items():
            conv = a["conversations"] or a["conversions"]
            cost = a["spend"] / conv if conv > 0 else a["spend"]
            ctr = (a["clicks"] / a["impressions"] * 100) if a["impressions"] else 0
            freq = (sum(a["freqs"]) / len(a["freqs"])) if a["freqs"] else 0
            out.append({
                "campaign_id": cid, "name": a["name"], "status": a["status"], "budget": a["budget"],
                "spend": round(a["spend"], 2), "reach": a["reach"], "impressions": a["impressions"],
                "frequency": round(freq, 2), "ctr": round(ctr, 2), "conversations": conv,
                "cost_per_conv": round(cost, 2), "label": classify_campaign(cost, conv, target_cpl),
            })
        out.sort(key=lambda x: x["cost_per_conv"])
        return out

    def _agg_adsets(self, snaps) -> List[Dict[str, Any]]:
        agg: Dict[str, Any] = {}
        for s in snaps:
            a = agg.setdefault(s.adset_id, {
                "name": s.name, "campaign_id": s.campaign_id, "targeting": s.targeting_summary or {},
                "spend": 0.0, "reach": 0, "impressions": 0, "clicks": 0,
                "conversions": 0, "conversations": 0,
            })
            a["name"] = s.name
            a["targeting"] = s.targeting_summary or a["targeting"]
            a["spend"] += s.spend
            a["reach"] += s.reach
            a["impressions"] += s.impressions
            a["clicks"] += s.clicks
            a["conversions"] += s.conversions
            a["conversations"] += s.conversations

        out = []
        for aid, a in agg.items():
            conv = a["conversations"] or a["conversions"]
            cost = a["spend"] / conv if conv > 0 else a["spend"]
            ctr = (a["clicks"] / a["impressions"] * 100) if a["impressions"] else 0
            out.append({
                "adset_id": aid, "name": a["name"], "campaign_id": a["campaign_id"],
                "targeting": a["targeting"], "spend": round(a["spend"], 2), "reach": a["reach"],
                "ctr": round(ctr, 2), "conversations": conv, "cost_per_conv": round(cost, 2),
            })
        out.sort(key=lambda x: x["cost_per_conv"])
        return out

    def _agg_ads(self, snaps) -> List[Dict[str, Any]]:
        agg: Dict[str, Any] = {}
        for s in snaps:
            a = agg.setdefault(s.ad_id, {
                "name": s.name, "adset_id": s.adset_id, "creative_id": s.creative_id,
                "spend": 0.0, "reach": 0, "impressions": 0, "clicks": 0,
                "conversions": 0, "conversations": 0,
                "creative_title": None, "creative_body": None, "creative_cta_type": None,
            })
            a["name"] = s.name
            a["spend"] += s.spend
            a["reach"] += s.reach
            a["impressions"] += s.impressions
            a["clicks"] += s.clicks
            a["conversions"] += s.conversions
            a["conversations"] += s.conversations
            # El copy no cambia día a día — nos quedamos con el más reciente
            # que traiga texto (algunos días pueden venir sin este dato).
            if s.creative_body:
                a["creative_title"] = s.creative_title
                a["creative_body"] = s.creative_body
                a["creative_cta_type"] = s.creative_cta_type

        out = []
        for adid, a in agg.items():
            conv = a["conversations"] or a["conversions"]
            cost = a["spend"] / conv if conv > 0 else a["spend"]
            ctr = (a["clicks"] / a["impressions"] * 100) if a["impressions"] else 0
            out.append({
                "ad_id": adid, "name": a["name"], "adset_id": a["adset_id"],
                "creative_id": a["creative_id"], "spend": round(a["spend"], 2), "reach": a["reach"],
                "impressions": a["impressions"], "ctr": round(ctr, 2), "conversations": conv,
                "cost_per_conv": round(cost, 2),
                "creative_title": a["creative_title"], "creative_body": a["creative_body"],
                "creative_cta_type": a["creative_cta_type"],
            })
        out.sort(key=lambda x: x["cost_per_conv"])
        return out

    def _segmentation(self, snaps) -> Dict[str, Any]:
        buckets = {"placements": {}, "age": {}, "gender": {}, "device": {}, "region": {}}

        def add(target, key, d):
            e = target.setdefault(key, {"spend": 0.0, "conversions": 0})
            e["spend"] += float(d.get("spend", 0) or 0)
            e["conversions"] += int(d.get("conversions", 0) or 0)

        for s in snaps:
            for k, v in (s.placements_breakdown or {}).items():
                add(buckets["placements"], k, v)
            for k, v in (s.demographics_breakdown or {}).items():
                add(buckets["age"], k, v)
            extra = s.extra_breakdowns or {}
            for k, v in (extra.get("gender") or {}).items():
                add(buckets["gender"], k, v)
            for k, v in (extra.get("device") or {}).items():
                add(buckets["device"], k, v)
            for k, v in (extra.get("region") or {}).items():
                add(buckets["region"], k, v)

        # redondear
        for grp in buckets.values():
            for e in grp.values():
                e["spend"] = round(e["spend"], 2)
        return buckets

    def _alerts(self, campaigns) -> Dict[str, List[Dict[str, str]]]:
        red, green = [], []
        for c in campaigns:
            if c["label"] in ("CARA", "PAUSAR"):
                reason = ("0 conversaciones" if c["conversations"] == 0
                          else f"costo/conversación S/.{c['cost_per_conv']:.2f} (alto)")
                red.append({"name": c["name"], "reason": reason})
            elif c["label"] in ("GANADORA", "BUENA"):
                green.append({"name": c["name"], "reason": f"S/.{c['cost_per_conv']:.2f}/conv, CTR {c['ctr']:.2f}%"})
        return {"red": red, "green": green}

    @staticmethod
    def _is_advantage(targeting: dict) -> bool:
        t = targeting or {}
        return bool(t.get("advantage_audience")) or t.get("audience_type") == "ADVANTAGE_PLUS"

    def _findings(self, campaigns, adsets, segmentation) -> List[str]:
        """Hallazgos ('lo más importante') derivados de los números agregados —
        nunca inventados por el LLM. Cada uno es una frase lista para mostrar."""
        out: List[str] = []
        winners = [c for c in campaigns if c["conversations"] > 0]  # ya ordenados por costo asc
        if winners:
            b = winners[0]
            out.append(f"{b['name']} es la campaña más eficiente: S/.{b['cost_per_conv']:.2f} por "
                       f"conversación con CTR {b['ctr']:.2f}% (el mejor de la cuenta).")

        expensive = [c for c in campaigns if c["label"] in ("CARA", "PAUSAR") and c["conversations"] > 0]
        if expensive:
            names = " y ".join(c["name"] for c in expensive[:2])
            hi = max(c["cost_per_conv"] for c in expensive)
            out.append(f"Las campañas más caras ({names}) llegan a S/.{hi:.2f} por conversación, "
                       f"casi el doble de las ganadoras.")

        adv = [a for a in adsets if self._is_advantage(a.get("targeting", {})) and a["conversations"] > 0]
        intr = [a for a in adsets if not self._is_advantage(a.get("targeting", {})) and a["conversations"] > 0]
        if adv and intr:
            adv_cost = sum(a["cost_per_conv"] for a in adv) / len(adv)
            int_cost = sum(a["cost_per_conv"] for a in intr) / len(intr)
            lo, hi = sorted([("Advantage+", adv_cost), ("intereses", int_cost)], key=lambda x: x[1])
            if hi[1] > 0:
                pct = round((1 - lo[1] / hi[1]) * 100)
                out.append(f"{lo[0].capitalize()} rinde mejor que {hi[0]}: S/.{lo[1]:.2f} vs "
                           f"S/.{hi[1]:.2f} por conversación ({pct}% más barato).")

        placements = segmentation.get("placements", {})
        total_pl = sum(v["spend"] for v in placements.values())
        if total_pl > 0:
            top_key, top_val = max(placements.items(), key=lambda kv: kv[1]["spend"])
            share = round(top_val["spend"] / total_pl * 100)
            out.append(f"{top_key.replace('_', ' ')} concentra el {share}% de la inversión — "
                       f"es el canal principal de la cuenta.")
        return out

    def _recommendations(self, campaigns, adsets, segmentation) -> List[str]:
        """Recomendaciones accionables ('qué hacer con las nuevas campañas')."""
        out: List[str] = []
        winners = [a for a in adsets if a["conversations"] > 0]
        if winners:
            if self._is_advantage(winners[0].get("targeting", {})):
                out.append("Usar Advantage+ (audiencia amplia) en las nuevas campañas — fue lo más "
                           "eficiente en el período y simplifica la gestión.")
            else:
                out.append("Mantener segmentación por intereses en las nuevas campañas — superó a "
                           "Advantage+ en costo por conversación.")

        placements = segmentation.get("placements", {})
        dead = [(k, v) for k, v in placements.items() if v["spend"] > 0 and v.get("conversions", 0) == 0]
        if dead:
            worst = max(dead, key=lambda kv: kv[1]["spend"])[0]
            out.append(f"Limitar o excluir {worst.replace('_', ' ')}: recibió inversión sin generar "
                       f"ninguna conversión.")

        costs = [c["cost_per_conv"] for c in campaigns if c["conversations"] > 0]
        if costs:
            lo, hi = round(min(costs), 2), round(sorted(costs)[len(costs) // 2], 2)
            out.append(f"Fijar una meta de costo de S/.{lo:.2f}–S/.{hi:.2f} por conversación, "
                       f"basada en el rango de las campañas ganadoras.")

        out.append("Crear anuncios con objetivo de conversaciones directas a WhatsApp, no boost de "
                   "publicaciones, que históricamente convierten más caro.")
        return out

    def _optimal_params(self, adsets, segmentation, campaigns) -> List[Dict[str, str]]:
        # Mejor adset con conversiones
        winners = [a for a in adsets if a["conversations"] > 0]
        best = winners[0] if winners else None
        best_targeting = (best or {}).get("targeting", {}) if best else {}

        def _best_key(bucket):
            items = [(k, v) for k, v in bucket.items() if v.get("conversions", 0) > 0]
            if not items:
                return None
            return min(items, key=lambda kv: (kv[1]["spend"] / kv[1]["conversions"]))[0]

        best_device = _best_key(segmentation.get("device", {}))
        best_placement = _best_key(segmentation.get("placements", {}))
        costs = [c["cost_per_conv"] for c in campaigns if c["conversations"] > 0]
        cost_lo = round(min(costs), 2) if costs else 1.5
        cost_hi = round(sorted(costs)[len(costs) // 2], 2) if costs else 2.5

        rows = [
            {"param": "Objetivo", "value": "Conversaciones → WhatsApp",
             "justification": "Las campañas de mensajes convierten mejor que boost de posts."},
            {"param": "Tipo de audiencia",
             "value": "Advantage+" if best_targeting.get("advantage_audience") else "Intereses",
             "justification": "Derivado del adset con menor costo por conversación."},
            {"param": "Segmentación geo",
             "value": ", ".join(best_targeting.get("geo", [])) or "Lima Región",
             "justification": "Zona del adset ganador."},
            {"param": "Placement óptimo", "value": best_placement or "facebook_feed",
             "justification": "Placement con menor costo por conversión en el período."},
            {"param": "Dispositivo óptimo", "value": best_device or "mobile",
             "justification": "Dispositivo con menor costo por conversión."},
            {"param": "Meta de costo", "value": f"S/.{cost_lo:.2f} – S/.{cost_hi:.2f} por conversación",
             "justification": "Rango observado en campañas ganadoras."},
        ]
        return rows
