"""Tests de la Fase 6 del plan de creación de campañas: soporte de video/UGC
en CreativeBuilder (playbook §5 Paso 4 — "el video suele rendir mejor que la
imagen estática")."""
import pytest
from app.services.creative_builder import CreativeBuilder


def test_build_creative_payload_with_image_uses_link_data():
    spec = CreativeBuilder.build_creative_payload(
        name="Test Creative", page_id="page1", copy_text="Hola!", image_hash="hash123",
        whatsapp_number="51 926 325 027",
    )
    story = spec["object_story_spec"]
    assert "link_data" in story
    assert "video_data" not in story
    assert story["link_data"]["image_hash"] == "hash123"
    assert story["link_data"]["message"] == "Hola!"
    assert story["link_data"]["call_to_action"]["type"] == "WHATSAPP_MESSAGE"
    # CTWA: link obligatorio (Marketing API v25); se normaliza el número a dígitos.
    assert story["link_data"]["link"] == "https://api.whatsapp.com/send?phone=51926325027"


def test_build_creative_payload_link_falls_back_without_number():
    spec = CreativeBuilder.build_creative_payload(
        name="Test Creative", page_id="page1", copy_text="Hola!", image_hash="hash123",
    )
    # Sin número, el link sigue siendo válido (Meta exige el campo presente).
    assert spec["object_story_spec"]["link_data"]["link"] == "https://api.whatsapp.com/send"


def test_build_creative_payload_with_video_uses_video_data():
    spec = CreativeBuilder.build_creative_payload(
        name="Test Creative", page_id="page1", copy_text="Hola!",
        video_id="vid_123", thumbnail_url="https://cdn.example/thumb.jpg",
    )
    story = spec["object_story_spec"]
    assert "video_data" in story
    assert "link_data" not in story
    assert story["video_data"]["video_id"] == "vid_123"
    assert story["video_data"]["image_url"] == "https://cdn.example/thumb.jpg"
    assert story["video_data"]["message"] == "Hola!"
    assert story["video_data"]["call_to_action"]["type"] == "WHATSAPP_MESSAGE"
    assert "page_welcome_message" in story["video_data"]


def test_build_creative_payload_requires_video_or_image():
    with pytest.raises(ValueError):
        CreativeBuilder.build_creative_payload(name="X", page_id="p", copy_text="c")


def test_build_creative_payload_video_requires_thumbnail():
    with pytest.raises(ValueError):
        CreativeBuilder.build_creative_payload(
            name="X", page_id="p", copy_text="c", video_id="vid_123",
        )


def test_page_welcome_message_media_type_is_lowercase():
    """Con "TEXT" en mayúscula Meta rechaza el creativo entero con un genérico
    «An unknown error has occurred» (code 1, sin subcódigo, imposible de
    diagnosticar). Aislado con el preflight el 2026-07-17."""
    spec = CreativeBuilder.build_creative_payload(
        name="C", page_id="page1", copy_text="Hola", image_hash="h1",
    )
    pwm = spec["object_story_spec"]["link_data"]["page_welcome_message"]
    assert pwm["media_type"] == "text"
