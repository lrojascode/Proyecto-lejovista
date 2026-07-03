import logging
from typing import Dict, Any
from app.workers.celery_app import celery_app
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES
from app.services.creative_builder import CreativeBuilder
from datetime import datetime, timedelta

# En Celery, usamos el argumento previo que devuelve la tarea en el chain.
# Por lo tanto, cada tarea recibe `context` como primer argumento (excepto la primera, si no se le pasa)
# y debe retornar el contexto actualizado.

logger = logging.getLogger("celery.task")

@celery_app.task(bind=True, max_retries=3)
def task_upload_image(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 2: Subir imagen o reutilizar hash
    """
    logger.info(f"Task 1 (Upload Image) - context: {context}")
    # Aquí instanciaríamos MetaAssetRepository
    # Para el chain, si viene image_hash lo usamos, si no, lo subimos.
    image_hash = context.get("image_hash")
    if not image_hash:
        image_url = context.get("image_url")
        # rep = MetaAssetRepository(api_client)
        # image_hash = async_to_sync(rep.get_or_upload_image(..., image_url))
        image_hash = "mock_uploaded_hash_123"
        
    context["image_hash"] = image_hash
    return context

@celery_app.task(bind=True, max_retries=3)
def task_create_campaign(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 3: Crear campaña (PAUSED)
    """
    logger.info(f"Task 2 (Create Campaign) - context: {context}")
    ad_account_id = context["ad_account_id"]
    product_name = context["product_name"]
    zone = context["zone"]
    # Generar nombre (e.g. MTV_MSJ_Cable_Surquillo_Jun2026)
    month_year = datetime.now().strftime("%b%Y")
    campaign_name = f"MTV_MSJ_{product_name}_{zone}_{month_year}".replace(" ", "")
    
    payload = {
        "name": campaign_name,
        "objective": TENANT_CAMPAIGN_RULES["objective"],
        "buying_type": "AUCTION",
        "status": TENANT_CAMPAIGN_RULES["default_status"],
        "special_ad_categories": []
    }
    
    # POST /act_{id}/campaigns con payload
    # api_client.post(..., data=payload)
    mock_campaign_id = f"camp_{int(datetime.now().timestamp())}"
    
    context["campaign_id"] = mock_campaign_id
    context["campaign_name"] = campaign_name
    return context

@celery_app.task(bind=True, max_retries=3)
def task_create_adset(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 4: Crear AdSet (Geo, placements, WhatsApp)
    """
    logger.info(f"Task 3 (Create AdSet) - context: {context}")
    campaign_id = context["campaign_id"]
    
    start_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    # Si no hay timezone, añadimos +0000 para cumplir con ISO8601 de Meta
    end_time = (datetime.now() + timedelta(days=context["days"])).strftime("%Y-%m-%dT%H:%M:%S+0000")
    
    payload = {
        "name": f"{context['campaign_name']}_AS01",
        "campaign_id": campaign_id,
        "optimization_goal": TENANT_CAMPAIGN_RULES["optimization_goal"],
        "destination_type": TENANT_CAMPAIGN_RULES["destination_type"],
        "billing_event": "IMPRESSIONS",
        "bid_strategy": TENANT_CAMPAIGN_RULES["bid_strategy"],
        "lifetime_budget": int(context["budget"] * 1000000),  # Meta requiere montos en centavos/multiplicador
        "start_time": start_time + "+0000",
        "end_time": end_time,
        "status": "ACTIVE", # El adset va activo, la campaña en sí está pausada
        "targeting": {
            "geo_locations": {"regions": [{"key": TENANT_CAMPAIGN_RULES["default_geo_region_key"]}]},
            "age_min": 25,
            "age_max": 65,
            "publisher_platforms": ["facebook"],
            "facebook_positions": ["feed", "story"] + TENANT_CAMPAIGN_RULES["required_placements"],
            "targeting_automation": {"advantage_audience": 1}
        },
        "promoted_object": {
            "page_id": context["page_id"],
            "whatsapp_phone_number": context["whatsapp_number"]
        }
    }
    
    # POST /act_{id}/adsets
    mock_adset_id = f"adset_{int(datetime.now().timestamp())}"
    
    context["adset_id"] = mock_adset_id
    return context

@celery_app.task(bind=True, max_retries=3)
def task_create_creative(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 5: Crear Creativo
    """
    logger.info(f"Task 4 (Create Creative) - context: {context}")
    
    creative_name = f"MTV_CREATIVE_{context['product_name']}_{datetime.now().strftime('%b%Y')}"
    payload = CreativeBuilder.build_creative_payload(
        name=creative_name,
        page_id=context["page_id"],
        image_hash=context["image_hash"],
        copy_text=context["copy_text"]
    )
    
    # POST /act_{id}/adcreatives
    mock_creative_id = f"creative_{int(datetime.now().timestamp())}"
    
    context["creative_id"] = mock_creative_id
    return context

@celery_app.task(bind=True, max_retries=3)
def task_create_ad(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 6: Crear Ad y finalizar
    """
    logger.info(f"Task 5 (Create Ad) - context: {context}")
    
    ad_name = f"MTV_AD_{context['product_name']}{context['zone']}_{datetime.now().strftime('%b%Y')}"
    payload = {
        "name": ad_name,
        "adset_id": context["adset_id"],
        "creative": {"creative_id": context["creative_id"]},
        "status": "ACTIVE"
    }
    
    # POST /act_{id}/ads
    mock_ad_id = f"ad_{int(datetime.now().timestamp())}"
    context["ad_id"] = mock_ad_id
    context["status"] = "COMPLETED"
    
    logger.info(f"Campaign creation Saga completed successfully: {context}")
    
    # Notificar finalización
    from app.services.notification_service import NotificationService
    # Recuperar tenant_id del contexto si es posible, por ahora mock
    notifier = NotificationService()
    notifier.notify_job_status("mock-tenant-id", context["draft_id"], "READY", event_type="campaign_creation")
    
    return context

@celery_app.task
def task_handle_chain_error(request, exc, traceback, draft_id):
    """
    Callback de error para el chain. Si alguna tarea falla, cae aquí.
    """
    logger.error(f"Saga Failed for draft_id {draft_id}: {exc}")
    # Aquí actualizaríamos el CampaignDraft a status="FAILED"
    from app.services.notification_service import NotificationService
    notifier = NotificationService()
    notifier.notify_job_status("mock-tenant-id", draft_id, "FAILED", event_type="campaign_creation")
