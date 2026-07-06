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
            "media_type": "TEXT",
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
