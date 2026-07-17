import re
from typing import Dict, Any, Optional

class CreativeBuilder:
    """
    Construye el pesado payload JSON (object_story_spec) necesario para
    crear el AdCreative en Meta.
    """
    @staticmethod
    def build_creative_payload(
        name: str,
        page_id: str,
        copy_text: str,
        image_hash: Optional[str] = None,
        video_id: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        whatsapp_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Paso 5 del documento: Crear Creativo (object_story_spec).

        Soporta imagen (`link_data` + `image_hash`) o video/UGC (`video_data` +
        `video_id` + `thumbnail_url`, playbook §5 Paso 4 — "el video suele
        rendir mejor que la imagen estática"). Exactamente uno de los dos debe
        proveerse.
        """
        if not image_hash and not video_id:
            raise ValueError("Debe proveerse image_hash (imagen) o video_id (video).")
        if video_id and not thumbnail_url:
            raise ValueError("video_id requiere thumbnail_url (miniatura) para el creativo de video.")

        # Según el protocolo, page_welcome_message es OBLIGATORIO para campañas de destino WHATSAPP.
        page_welcome_message = {
            "type": "VISUAL_EDITOR",
            "version": 2,
            "landing_screen_type": "welcome_message",
            # OJO: en MINÚSCULA. Con "TEXT" Meta rechaza el creativo entero con
            # un inútil «An unknown error has occurred» (code 1, sin subcódigo).
            # Aislado con el preflight (validate_only) el 2026-07-17: el mismo
            # creativo sin page_welcome_message validaba, y con "text" también.
            "media_type": "text",
            "text_format": {
                "customer_action_type": "AUTOFILL_MESSAGE",
                "message": {
                    "autofill_message": {"content": "Hola, quiero cotizar"},
                    "text": "¡Hola! Con gusto te brindamos informacion y precios."
                }
            }
        }

        call_to_action = {
            "type": "WHATSAPP_MESSAGE",
            "value": {"app_destination": "WHATSAPP"}
        }

        # CTWA: `link` es OBLIGATORIO en link_data (Marketing API v25 — el doc de
        # click-to-whatsapp lo marca Required). El enrutado real a WhatsApp lo
        # hace el call_to_action WHATSAPP_MESSAGE + promoted_object.page_id, pero
        # Meta exige el campo presente y válido. Se usa el deep link de WhatsApp
        # con el número de la página.
        wa_digits = re.sub(r"\D", "", whatsapp_number or "")
        wa_link = (
            f"https://api.whatsapp.com/send?phone={wa_digits}"
            if wa_digits else "https://api.whatsapp.com/send"
        )

        if video_id:
            story_spec_extra = {
                "video_data": {
                    "video_id": video_id,
                    "image_url": thumbnail_url,
                    "message": copy_text,
                    "call_to_action": call_to_action,
                    "page_welcome_message": page_welcome_message,
                }
            }
        else:
            story_spec_extra = {
                "link_data": {
                    "image_hash": image_hash,
                    "link": wa_link,
                    "message": copy_text,
                    "call_to_action": call_to_action,
                    "page_welcome_message": page_welcome_message,
                }
            }

        return {
            "name": name,
            "object_story_spec": {
                "page_id": page_id,
                **story_spec_extra,
            }
        }
