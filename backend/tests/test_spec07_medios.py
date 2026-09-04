"""Spec 07 — Medios: carga, validación, almacenamiento y ciclo de vida.

Un test por criterio de aceptación de
`docs/specs/07-medios-almacenamiento-y-ciclo-de-vida.md`.

**Los casos hostiles son el núcleo de la suite**, y la spec los nombra uno a uno:
contenido incompatible con el tipo declarado, archivo truncado, archivo vacío,
tamaño en el límite exacto y justo por encima, y carga concurrente. Un test que
solo comprobara la carga feliz no distingue un validador de un `pass`.

El comportamiento de memoria se **mide sobre el proceso**, no se razona sobre el
código.
"""
import asyncio
import datetime
import io
import os
import resource

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media_asset import (
    ASOCIADO, ELIMINADO, EN_USO, TEMPORAL, MediaAsset,
)
from app.services.media_service import limpiar_vencidos, uso_de_la_organizacion
from app.services.media_storage import firmar_acceso, verificar_acceso
from app.services.media_validation import MAX_IMAGEN_BYTES, detectar_tipo


@pytest.fixture(autouse=True)
def _almacenamiento_en_tmp(tmp_path, monkeypatch):
    """Los medios de los tests no tocan el MEDIA_ROOT real."""
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DRIVER", "local")
    return tmp_path / "media"


async def _signup(client: AsyncClient, slug: str):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec07", "organization_slug": slug, "organization_name": "Spec07 Org",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def _png(ancho=1080, alto=1080, ruido=False) -> bytes:
    """PNG de prueba.

    Con `ruido=True` el contenido es aleatorio: un PNG de color plano comprime a
    unos pocos KB por grande que sea, y un test que mida bytes sobre eso no
    estaría midiendo lo que cree.
    """
    if ruido:
        datos = os.urandom(ancho * alto * 3)
        img = Image.frombytes("RGB", (ancho, alto), datos)
    else:
        img = Image.new("RGB", (ancho, alto), "red")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _mp4_falso() -> bytes:
    """Cabecera MP4 válida con basura detrás: sirve para probar la detección de
    tipo, no para que `ffprobe` lo acepte."""
    return b"\x00\x00\x00\x20ftypisom" + b"\x00" * 512


async def _subir(client: AsyncClient, headers, datos: bytes, nombre="foto.png",
                 tipo="image/png"):
    return await client.post(
        "/api/v1/media", headers=headers,
        files={"file": (nombre, io.BytesIO(datos), tipo)},
    )


# --------------------------------------------------------------------------
# Criterios 1-3 · el archivo no se materializa entero, y el límite se aplica
#                 durante la transferencia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_la_memoria_no_crece_con_el_tamano_del_archivo(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Se MIDE sobre el proceso. El código anterior hacía `await file.read()`:
    un video de 100 MB eran 100 MB de RSS en el proceso que atiende a todos los
    clientes."""
    body, headers = await _signup(async_client, "c1-memoria")

    grande = _png(900, 900, ruido=True)   # ~2 MB de PNG incompresible
    assert len(grande) > 200_000, "el archivo tiene que ser grande para que la medida signifique algo"

    antes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for _ in range(3):
        res = await _subir(async_client, headers, grande)
        assert res.status_code == 201, res.text
    despues = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # `ru_maxrss` es un máximo histórico y nunca baja; lo que se comprueba es que
    # tres cargas seguidas no lo empujen en múltiplos del archivo.
    crecimiento = despues - antes
    assert crecimiento < len(grande) * 3, (
        f"la memoria creció {crecimiento} para {len(grande) * 3} bytes cargados"
    )


@pytest.mark.asyncio
async def test_c2_cargas_simultaneas_no_rompen_ni_se_pisan(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c2-concurrencia")
    datos = _png(1200, 1200)

    respuestas = await asyncio.gather(
        *[_subir(async_client, headers, datos, nombre=f"f{i}.png") for i in range(6)]
    )
    assert all(r.status_code == 201 for r in respuestas), [r.text for r in respuestas]

    ids = {r.json()["id"] for r in respuestas}
    assert len(ids) == 6, "cada carga tiene su propio medio"

    filas = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.organization_id == body["user"]["organization_id"])
    )).scalars().all()
    claves = {f.storage_key for f in filas}
    assert len(claves) == len(filas), "dos medios no pueden compartir clave"


@pytest.mark.asyncio
async def test_c3_un_archivo_por_encima_del_limite_se_rechaza_sin_escribirse_entero(
    async_client: AsyncClient, tmp_path, monkeypatch
):
    """El límite se aplica DURANTE la transferencia: comprobarlo al final obliga
    a haber materializado el archivo primero, que es lo que hay que evitar."""
    from app.services import media_validation

    monkeypatch.setattr(media_validation, "MAX_IMAGEN_BYTES", 50_000)
    import app.services.media_service as ms
    monkeypatch.setattr(ms, "MAX_IMAGEN_BYTES", 50_000)

    body, headers = await _signup(async_client, "c3-limite")
    grande = _png(300, 300, ruido=True)
    assert len(grande) > 50_000

    res = await _subir(async_client, headers, grande)
    assert res.status_code == 413
    assert res.json()["detail"]["code"] == "MEDIA_TOO_LARGE"

    # Y no queda ningún archivo completo tirado en el almacenamiento.
    raiz = tmp_path / "media"
    sobrantes = [p for p in raiz.rglob("*") if p.is_file()] if raiz.exists() else []
    assert not sobrantes, f"quedaron archivos tras el rechazo: {sobrantes}"


# --------------------------------------------------------------------------
# Criterios 4-7 · el tipo declarado no es evidencia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c4_un_ejecutable_renombrado_como_imagen_se_rechaza(
    async_client: AsyncClient
):
    body, headers = await _signup(async_client, "c4-ejecutable")
    elf = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 2048

    res = await _subir(async_client, headers, elf, nombre="inocente.png", tipo="image/png")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "MEDIA_UNSUPPORTED_TYPE"


@pytest.mark.asyncio
async def test_c5_una_imagen_truncada_se_rechaza(async_client: AsyncClient):
    """Los primeros bytes de una imagen cortada por la mitad son exactamente los
    de una imagen entera: las firmas no lo ven, abrirla sí."""
    body, headers = await _signup(async_client, "c5-truncada")
    entera = _png(1080, 1080)
    truncada = entera[: len(entera) // 3]

    res = await _subir(async_client, headers, truncada)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "MEDIA_CORRUPT"


@pytest.mark.asyncio
async def test_c6_un_archivo_vacio_se_rechaza(async_client: AsyncClient):
    body, headers = await _signup(async_client, "c6-vacio")
    res = await _subir(async_client, headers, b"")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "MEDIA_EMPTY"


def test_c4b_la_deteccion_va_por_contenido_no_por_extension():
    assert detectar_tipo(_png()[:32]) == ("image/png", "IMAGE")
    assert detectar_tipo(_mp4_falso()[:32]) == ("video/mp4", "VIDEO")
    assert detectar_tipo(b"no soy nada" + b"\x00" * 32) == (None, None)


@pytest.mark.asyncio
async def test_c7_un_video_en_el_endpoint_de_imagen_se_rechaza(async_client: AsyncClient):
    """Subir un video a `/upload-image` produciría una Saga que le manda a Meta
    un creativo que no puede construir."""
    body, headers = await _signup(async_client, "c7-tipo-cruzado")

    res = await async_client.post(
        "/api/v1/campaigns/upload-image", headers=headers,
        files={"file": ("video.mp4", io.BytesIO(_mp4_falso()), "video/mp4")},
    )
    # El mp4 sintético no pasa `ffprobe`, así que puede caer por corrupto o por
    # tipo cruzado. Las dos son rechazos correctos; lo que no puede es entrar.
    assert res.status_code == 400
    assert res.json()["detail"]["code"] in ("MEDIA_WRONG_KIND", "MEDIA_CORRUPT")


# --------------------------------------------------------------------------
# Criterios 8-9 · verificación técnica: avisa, no promete
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c8_una_relacion_de_aspecto_fuera_de_rango_produce_un_aviso(
    async_client: AsyncClient
):
    body, headers = await _signup(async_client, "c8-aspecto")
    panoramica = _png(3000, 500)      # 6:1, muy fuera del rango de Meta

    res = await _subir(async_client, headers, panoramica)
    assert res.status_code == 201, res.text
    codigos = {a["code"] for a in res.json()["avisos"]}
    assert "ASPECT_RATIO_OUT_OF_RANGE" in codigos


@pytest.mark.asyncio
async def test_c8b_una_resolucion_baja_produce_un_aviso(async_client: AsyncClient):
    body, headers = await _signup(async_client, "c8b-resolucion")
    res = await _subir(async_client, headers, _png(200, 200))
    assert res.status_code == 201, res.text
    codigos = {a["code"] for a in res.json()["avisos"]}
    assert "RESOLUTION_TOO_LOW" in codigos


@pytest.mark.asyncio
async def test_c8c_una_creatividad_correcta_no_produce_avisos(async_client: AsyncClient):
    """El par de los dos anteriores: un verificador que siempre avisa no sirve
    para distinguir nada."""
    body, headers = await _signup(async_client, "c8c-correcta")
    res = await _subir(async_client, headers, _png(1080, 1080))
    assert res.status_code == 201, res.text
    assert res.json()["avisos"] == []


@pytest.mark.asyncio
async def test_c9_ninguna_respuesta_promete_rendimiento(async_client: AsyncClient):
    """El rendimiento de un creativo no se lee en sus píxeles, y prometerlo
    estaría además prohibido (spec 10)."""
    body, headers = await _signup(async_client, "c9-sin-promesas")
    res = await _subir(async_client, headers, _png(1080, 1080))
    texto = res.text.lower()
    for palabra in ("exitosa", "éxito", "funcionará", "garantiza", "mejor rendimiento"):
        assert palabra not in texto


# --------------------------------------------------------------------------
# Criterios 10-12 · almacenamiento, URL firmadas y pertenencia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_el_medio_va_al_almacenamiento_y_no_a_una_ruta_adivinable(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c10-almacen")
    res = await _subir(async_client, headers, _png())
    asset_id = res.json()["id"]

    fila = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )).scalar_one()
    assert fila.storage_key.startswith(body["user"]["organization_id"] + "/")
    assert fila.checksum_sha256 and len(fila.checksum_sha256) == 64
    assert fila.size_bytes > 0

    # La URL que se entrega lleva firma, no es la ruta.
    assert "token=" in res.json()["url"]


@pytest.mark.asyncio
async def test_c11_una_url_firmada_caducada_no_da_acceso(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c11-firma")
    res = await _subir(async_client, headers, _png())
    asset_id = res.json()["id"]

    # Firma válida: entra.
    vigente = firmar_acceso(asset_id)
    r = await async_client.get(f"/api/v1/media/{asset_id}/contenido?token={vigente}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")

    # Firma vencida: no.
    vencida = firmar_acceso(asset_id, ttl=-10)
    r = await async_client.get(f"/api/v1/media/{asset_id}/contenido?token={vencida}")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "MEDIA_SIGNATURE_INVALID"

    # Firma de OTRO medio: tampoco. Una firma es de un recurso, no un pase.
    ajena = firmar_acceso("otro-medio-cualquiera")
    r = await async_client.get(f"/api/v1/media/{asset_id}/contenido?token={ajena}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_c12_no_se_emite_una_firma_para_un_medio_de_otra_organizacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Aunque se conozca su identificador. La comprobación es una consulta con la
    organización en el WHERE, no una lectura seguida de comparar la ruta."""
    body_a, headers_a = await _signup(async_client, "c12-org-a")
    res = await _subir(async_client, headers_a, _png())
    asset_de_a = res.json()["id"]

    body_b, headers_b = await _signup(async_client, "c12-org-b")
    r = await async_client.get(f"/api/v1/media/{asset_de_a}", headers=headers_b)
    assert r.status_code == 404, "ni se filtra que existe"


# --------------------------------------------------------------------------
# Criterio 13 · cuotas por Organización
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c13_al_alcanzar_la_cuota_se_rechaza_con_uso_y_limite(
    async_client: AsyncClient, monkeypatch
):
    """Sin cuota, un solo cliente puede agotar el disco de todos — y en un VPS de
    100 GB compartido con PostgreSQL eso tumba el producto entero."""
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_QUOTA_ARCHIVOS_POR_ORG", 2)
    body, headers = await _signup(async_client, "c13-cuota")

    assert (await _subir(async_client, headers, _png(), "a.png")).status_code == 201
    assert (await _subir(async_client, headers, _png(), "b.png")).status_code == 201

    res = await _subir(async_client, headers, _png(), "c.png")
    assert res.status_code == 413
    detalle = res.json()["detail"]
    assert detalle["code"] == "MEDIA_QUOTA_EXCEEDED"
    assert detalle["archivos"] == 2
    assert detalle["limite_archivos"] == 2


@pytest.mark.asyncio
async def test_c13b_la_cuota_es_por_organizacion_y_no_global(
    async_client: AsyncClient, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_QUOTA_ARCHIVOS_POR_ORG", 1)
    _, headers_a = await _signup(async_client, "c13b-a")
    _, headers_b = await _signup(async_client, "c13b-b")

    assert (await _subir(async_client, headers_a, _png())).status_code == 201
    assert (await _subir(async_client, headers_a, _png())).status_code == 413
    # La organización B no paga por lo que consumió A.
    assert (await _subir(async_client, headers_b, _png())).status_code == 201


# --------------------------------------------------------------------------
# Criterios 14-15 · ciclo de vida
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c14_un_medio_no_asociado_desaparece_al_vencer(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c14-caducidad")
    res = await _subir(async_client, headers, _png())
    asset_id = res.json()["id"]

    fila = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )).scalar_one()
    assert fila.status == TEMPORAL
    assert fila.expires_at is not None, "todo medio nace con caducidad"

    from app.services.media_storage import obtener_almacenamiento
    assert await obtener_almacenamiento().existe(fila.storage_key)

    # El reloj se controla, no se espera.
    futuro = datetime.datetime.utcnow() + datetime.timedelta(days=365)
    borrados = await limpiar_vencidos(db_session, ahora=futuro)
    await db_session.commit()

    assert borrados == 1
    await db_session.refresh(fila)
    assert fila.status == ELIMINADO
    assert not await obtener_almacenamiento().existe(fila.storage_key)


@pytest.mark.asyncio
async def test_c15_la_limpieza_es_idempotente_y_no_toca_los_medios_en_uso(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c15-idempotente")
    vencido = (await _subir(async_client, headers, _png(), "vence.png")).json()["id"]
    en_uso = (await _subir(async_client, headers, _png(), "usada.png")).json()["id"]

    # El segundo sostiene una campaña viva: no caduca por reloj.
    await db_session.execute(
        update(MediaAsset).where(MediaAsset.id == en_uso)
        .values(status=EN_USO, expires_at=None)
    )
    await db_session.commit()

    futuro = datetime.datetime.utcnow() + datetime.timedelta(days=365)
    primera = await limpiar_vencidos(db_session, ahora=futuro)
    await db_session.commit()
    segunda = await limpiar_vencidos(db_session, ahora=futuro)
    await db_session.commit()

    assert primera == 1
    assert segunda == 0, "correrla dos veces da el mismo resultado"

    fila_en_uso = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.id == en_uso)
    )).scalar_one()
    assert fila_en_uso.status == EN_USO

    from app.services.media_storage import obtener_almacenamiento
    assert await obtener_almacenamiento().existe(fila_en_uso.storage_key)


@pytest.mark.asyncio
async def test_c15b_los_medios_eliminados_dejan_de_consumir_cuota(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c15b-cuota-libera")
    org_id = body["user"]["organization_id"]
    await _subir(async_client, headers, _png())

    usado_antes, archivos_antes = await uso_de_la_organizacion(db_session, org_id)
    assert archivos_antes == 1 and usado_antes > 0

    futuro = datetime.datetime.utcnow() + datetime.timedelta(days=365)
    await limpiar_vencidos(db_session, ahora=futuro)
    await db_session.commit()

    usado, archivos = await uso_de_la_organizacion(db_session, org_id)
    assert (usado, archivos) == (0, 0)


# --------------------------------------------------------------------------
# Criterio 16 · las URL firmadas no aparecen en los registros
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c16_ninguna_url_firmada_aparece_en_los_registros(
    async_client: AsyncClient, caplog
):
    """Una URL firmada es una credencial de acceso temporal; se trata como tal."""
    import logging

    from app.utils.log_sanitizer import instalar_saneador

    # El saneado es una FÁBRICA de registros, así que alcanza a todo lo que se
    # registre — incluido lo que emiten httpx y uvicorn, que es justo por donde
    # se escapaba la URL firmada.
    instalar_saneador()

    body, headers = await _signup(async_client, "c16-logs")
    with caplog.at_level(logging.DEBUG):
        res = await _subir(async_client, headers, _png())
        asset_id = res.json()["id"]
        token = firmar_acceso(asset_id)
        await async_client.get(f"/api/v1/media/{asset_id}/contenido?token={token}")

    registros = "\n".join(r.getMessage() for r in caplog.records)

    # Lo que no puede aparecer es el VALOR de la firma. El literal `token=`
    # sobrevive a la redacción —queda `token=[REDACTADO]`— y eso es deseable: el
    # log sigue diciendo que hubo una firma, sin decir cuál.
    assert token not in registros
    firma = token.split(".", 1)[1]
    assert firma not in registros
    assert "token=[REDACTADO]" in registros, (
        "el saneador tiene que haber pasado por esta línea"
    )


def test_la_firma_no_se_puede_falsificar():
    """`compare_digest` y una clave: sin la clave no se produce una firma válida."""
    valido = firmar_acceso("asset-1")
    assert verificar_acceso("asset-1", valido) is True
    assert verificar_acceso("asset-2", valido) is False
    assert verificar_acceso("asset-1", "9999999999.firmainventada") is False
    assert verificar_acceso("asset-1", "no-es-un-token") is False
