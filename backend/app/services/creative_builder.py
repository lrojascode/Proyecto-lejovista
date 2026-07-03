from typing import Dict, Any

class CreativeBuilder:
    """
    Construye el pesado payload JSON (object_story_spec) necesario para
    crear el AdCreative en Meta.
    """
    @staticmethod
    def build_creative_payload(name: str, page_id: str, image_hash: str, copy_text: str) -> Dict[str, Any]:
        """
        Paso 5 del documento: Crear Creativo (object_story_spec)
        """
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

        # Estructura del link_data
        link_data = {
            "image_hash": image_hash,
            "message": copy_text,
            "call_to_action": {
                "type": "WHATSAPP_MESSAGE",
                "value": {"app_destination": "WHATSAPP"}
            },
            "page_welcome_message": page_welcome_message
        }

        return {
            "name": name,
            "object_story_spec": {
                "page_id": page_id,
                "link_data": link_data
            }
        }
