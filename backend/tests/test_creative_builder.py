"""Tests de la Fase 6 del plan de creación de campañas: soporte de video/UGC
en CreativeBuilder (playbook §5 Paso 4 — "el video suele rendir mejor que la
imagen estática")."""
import pytest
from app.services.creative_builder import CreativeBuilder


def test_build_creative_payload_with_image_uses_link_data():
    spec = CreativeBuilder.build_creative_payload(
        name="Test Creative", page_id="page1", copy_text="Hola!", image_hash="hash123",
    )
    story = spec["object_story_spec"]
    assert "link_data" in story
    assert "video_data" not in story
    assert story["link_data"]["image_hash"] == "hash123"
    assert story["link_data"]["message"] == "Hola!"
    assert story["link_data"]["call_to_action"]["type"] == "WHATSAPP_MESSAGE"


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
