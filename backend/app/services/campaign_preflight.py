"""Validación de los payloads de la Saga contra Meta SIN crear nada
(`execution_options=['validate_only']`).

Motivación (sesión de depuración del 2026-07-17): cada regla de Meta se
descubría de a una por intento, y cada intento fallido dejaba una campaña
huérfana en la cuenta del cliente (presupuesto mínimo, luego Advantage+ vs
edad, luego el flag obligatorio...). Con `validate_only`, Meta evalúa el
payload REAL que produce la app y devuelve sus errores sin escribir.

Se validan los tres pasos que fallan de verdad — campaña, adset y creativo —
reutilizando los MISMOS constructores que usa la Saga (`build_targeting`,
`CreativeBuilder`, las reglas del tenant), no un payload escrito a mano: lo
que se valida es exactamente lo que se va a enviar.

Límite conocido: `validate_only` sobre la campaña no devuelve un id, así que
el adset se valida contra una campaña YA EXISTENTE de la cuenta (solo como
padre; no se modifica). Si el objetivo de esa campaña difiere del que usaría
la Saga, la validación del adset puede no ser representativa — por eso se
informa cuál se usó. Si la cuenta no tiene campañas, ese chequeo se omite y se
dice explícitamente.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.models.ad_account import AdAccount
from app.services.brand_code import derive_brand_code
from app.services.campaign_targeting import build_targeting
from app.services.creative_builder import CreativeBuilder
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

logger = logging.getLogger(__name__)

_VALIDATE_ONLY = json.dumps(["validate_only"])


def _describe_meta_error(res: Dict[str, Any]) -> Dict[str, Any]:
    """Extrae el mensaje legible que Meta ya trae (`error_user_msg`) en vez del
    JSON crudo que hoy se le muestra al usuario."""
    err = (res or {}).get("error", {}) if isinstance(res, dict) else {}
    return {
        "title": err.get("error_user_title") or err.get("type") or "Error de Meta",
        "message": err.get("error_user_msg") or err.get("message") or json.dumps(res)[:400],
        "subcode": err.get("error_subcode"),
        "blame_fields": err.get("error_data"),
    }


class CampaignPreflight:
    """Corre los payloads de la Saga por `validate_only` y reporta qué rechazaría
    Meta, sin crear campañas, adsets ni creativos."""

    def __init__(self, ad_account: AdAccount, rules: Dict[str, Any], limiter: Optional[MetaAPIRateLimiter] = None):
        self.acc = ad_account
        self.rules = rules
        self.limiter = limiter or MetaAPIRateLimiter(ad_account_id=ad_account.id, external_account_id=ad_account.external_account_id)

    async def run(
        self,
        *,
        product_name: str,
        page_id: str,
        whatsapp_number: Optional[str],
        budget: float,
        days: int,
        copy_text: str,
        image_hash: Optional[str] = None,
        best_age_range: Optional[str] = None,
        best_device: Optional[str] = None,
        excluded_placements: Optional[List[str]] = None,
        budget_type: str = "LIFETIME",
        headline: Optional[str] = None,
        description: Optional[str] = None,
        instagram_user_id: Optional[str] = None,
        campaign_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = self.acc.get_access_token()
        checks: List[Dict[str, Any]] = []

        campaign_name = campaign_name or (
            f"{derive_brand_code(self.acc)}_MSJ_{product_name}_"
            f"{datetime.now().strftime('%b%Y').upper()}"
        ).replace(" ", "")

        # --- 1. Campaña ---
        checks.append(await self._check(
            "campaign", f"{self.acc.external_account_id}/campaigns", token,
            {
                "name": campaign_name,
                "objective": self.rules["objective"],
                "buying_type": "AUCTION",
                "status": self.rules["default_status"],
                "special_ad_categories": "[]",
                "is_adset_budget_sharing_enabled": "false",
            },
        ))

        # --- 2. Adset (necesita una campaña padre; se usa una existente) ---
        parent_id, parent_name = await self._find_parent_campaign(token)
        if not parent_id:
            checks.append({
                "step": "adset",
                "ok": None,
                "skipped": "La cuenta no tiene campañas existentes que usar como padre para "
                           "validar el adset sin crear nada.",
            })
        else:
            start = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0000")
            end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+0000")
            adset_payload = {
                "name": f"{campaign_name}_AS01",
                "campaign_id": parent_id,
                "optimization_goal": self.rules["optimization_goal"],
                "destination_type": self.rules["destination_type"],
                "billing_event": "IMPRESSIONS",
                "bid_strategy": self.rules["bid_strategy"],
                "start_time": start,
                "end_time": end,
                "status": self.rules["default_status"],
                "targeting": json.dumps(build_targeting(
                    best_age_range=best_age_range,
                    best_device=best_device,
                    excluded_placements=excluded_placements or [],
                    rules=self.rules,
                )),
                "promoted_object": json.dumps({"page_id": page_id}),
            }
            # Mismo criterio que la Saga: diario y total son excluyentes.
            budget_cents = int(round(budget * 100))
            if budget_type == "DAILY":
                adset_payload["daily_budget"] = budget_cents
            else:
                adset_payload["lifetime_budget"] = budget_cents

            adset_check = await self._check(
                "adset", f"{self.acc.external_account_id}/adsets", token, adset_payload,
            )
            adset_check["parent_campaign"] = {"id": parent_id, "name": parent_name}
            checks.append(adset_check)

        # --- 3. Creativo ---
        if not image_hash:
            checks.append({
                "step": "creative",
                "ok": None,
                "skipped": "Sin image_hash no se puede validar el creativo (sube la imagen primero).",
            })
        else:
            spec = CreativeBuilder.build_creative_payload(
                name=f"{campaign_name}_CREATIVE",
                page_id=page_id,
                copy_text=copy_text,
                image_hash=image_hash,
                whatsapp_number=whatsapp_number,
                headline=headline,
                description=description,
                instagram_user_id=instagram_user_id,
            )
            checks.append(await self._check(
                "creative", f"{self.acc.external_account_id}/adcreatives", token,
                {
                    "name": spec["name"],
                    "object_story_spec": json.dumps(spec["object_story_spec"]),
                },
            ))

        failed = [c for c in checks if c.get("ok") is False]
        return {"ok": not failed, "checks": checks}

    async def _check(self, step: str, endpoint: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Un POST con validate_only: Meta valida y responde sin crear el objeto."""
        try:
            res = await self.limiter.call_api(
                endpoint, {**payload, "execution_options": _VALIDATE_ONLY},
                method="POST", access_token=token,
            )
        except Exception as exc:
            logger.exception("Preflight: fallo al validar el paso %s", step)
            return {"step": step, "ok": False, "error": {"title": "No se pudo validar contra Meta",
                                                          "message": str(exc)}}

        if isinstance(res, dict) and res.get("mock"):
            # Gating de simulación (MOCK_SERVICES / write-back apagado): no hay
            # validación real que reportar; no se finge un OK.
            return {"step": step, "ok": None, "skipped": "Escrituras simuladas: Meta no validó nada."}
        if isinstance(res, dict) and ("error" in res or "ERROR" in res):
            return {"step": step, "ok": False, "error": _describe_meta_error(res)}
        return {"step": step, "ok": True}

    async def _find_parent_campaign(self, token: str) -> tuple[Optional[str], Optional[str]]:
        """Campaña existente de la cuenta para colgar el adset a validar. No se
        modifica: `validate_only` no escribe."""
        res = await self.limiter.call_api(
            f"{self.acc.external_account_id}/campaigns",
            {"fields": "id,name,objective", "limit": "10"},
            access_token=token,
        )
        if not isinstance(res, dict) or "error" in res:
            return None, None
        campaigns = res.get("data", []) or []
        # Preferir una con el mismo objetivo: si difiere, Meta podría rechazar el
        # adset por el objetivo del padre y no por nuestro payload.
        for c in campaigns:
            if c.get("objective") == self.rules["objective"]:
                return c.get("id"), c.get("name")
        if campaigns:
            return campaigns[0].get("id"), campaigns[0].get("name")
        return None, None
