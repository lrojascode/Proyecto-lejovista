"""Spec 13 — Observabilidad, interruptores operativos, feature flags e incidentes.

Un test por criterio de aceptación de
`docs/specs/13-observabilidad-kill-switches-e-incidentes.md`.

Tres decisiones de la spec sobre cómo se prueba, respetadas aquí:

* **Un interruptor se prueba en las DOS superficies**: la API y la tarea
  asíncrona. Probar solo la API deja fuera precisamente el camino que un
  incidente necesita cortar — la cola que ya salió.
* **El fallo del almacén se prueba comprobando el par completo**: qué queda
  bloqueado y qué sigue funcionando. Solo la primera mitad no distingue una
  política de fallo cerrado de un sistema roto.
* **Los umbrales se prueban inyectando el estado que debe dispararlos**, no
  esperando a que ocurra.
"""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operacion import (
    CAP_CREACION,
    CAP_IA,
    CAP_MEDIOS,
    CAP_SOLO_LECTURA,
    CAP_SYNC,
    CAP_WRITE_BACK,
    CAPACIDADES,
    OperationalChange,
    OperationalSwitch,
)
from app.models.organization import Organization
from app.services import feature_flags, incidente, interruptores, senales
from conftest import cuenta_de_prueba


@pytest.fixture(autouse=True)
def _cache_limpio():
    """El caché de resolución vive en memoria del proceso y sobrevive entre
    tests. Sin limpiarlo, el orden de ejecución decidiría qué ve cada uno."""
    interruptores.invalidar_cache()
    feature_flags.invalidar_cache()
    yield
    interruptores.invalidar_cache()
    feature_flags.invalidar_cache()


async def _agencia(async_client: AsyncClient, db_session: AsyncSession, slug: str):
    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@ejemplo.com", "password": "securepassword123",
        "full_name": "Operador", "organization_slug": slug, "organization_name": slug,
    })
    body = alta.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    org_id = body["user"]["organization_id"]
    return headers, org_id


# --------------------------------------------------------------------------
# Criterio 1 — cinco interruptores independientes.
# --------------------------------------------------------------------------

def test_c1_hay_un_interruptor_por_capacidad():
    assert set(CAPACIDADES) == {
        CAP_CREACION, CAP_WRITE_BACK, CAP_SYNC, CAP_IA, CAP_MEDIOS,
    }


@pytest.mark.asyncio
async def test_c1_apagar_uno_no_apaga_los_demas(db_session: AsyncSession):
    """Separados porque un incidente rara vez los afecta a todos. Apagar la
    creación porque Meta devuelve errores dejaría al cliente sin consultar su
    histórico, que no tiene nada que ver."""
    await interruptores.cambiar(db_session, CAP_CREACION, encendido=False)
    await db_session.commit()

    assert await interruptores.esta_encendida(db_session, CAP_CREACION) is False
    for otra in (CAP_WRITE_BACK, CAP_SYNC, CAP_IA, CAP_MEDIOS):
        assert await interruptores.esta_encendida(db_session, otra) is True, otra


@pytest.mark.asyncio
async def test_c1_una_capacidad_inventada_se_rechaza(db_session: AsyncSession):
    with pytest.raises(ValueError):
        await interruptores.cambiar(db_session, "apagar_todo", encendido=False)


# --------------------------------------------------------------------------
# Criterios 2 y 3 — se rechaza donde se ejecuta, y se nombra.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c2_apagar_la_creacion_la_rechaza_en_la_api(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    headers, org_id = await _agencia(async_client, db_session, "apaga-creacion")
    acc = await cuenta_de_prueba(db_session, org_id)
    await interruptores.cambiar(db_session, CAP_CREACION, encendido=False,
                                motivo="Incidente con Meta")
    await db_session.commit()

    res = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 100.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })

    # 503 y no 403: no falta permiso, la capacidad no está disponible ahora.
    assert res.status_code == 503, res.text
    detalle = res.json()["detail"]
    assert detalle["code"] == "CAPABILITY_DISABLED"
    assert detalle["capacidad"] == CAP_CREACION
    # El mensaje del cliente NO lleva el motivo interno.
    assert "Incidente con Meta" not in detalle["message"]


@pytest.mark.asyncio
async def test_c2_apagar_el_sync_corta_la_tarea_ya_encolada(
    db_session: AsyncSession, mocker,
):
    """El criterio que separa un interruptor de un botón oculto.

    Se comprueba llamando a la comprobación tal como la hace la tarea, después
    de apagar el interruptor: una tarea encolada antes sigue viva en la cola, y
    cortar solo la puerta de entrada la deja vaciándose sobre el problema que se
    acaba de intentar contener.
    """
    from app.models.user import User

    org = Organization(slug="apaga-sync", name="Apaga Sync")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)
    await db_session.commit()

    # Antes de apagar, la tarea seguiría.
    assert await interruptores.esta_encendida(
        db_session, CAP_SYNC, organization_id=org.id,
    ) is True

    await interruptores.cambiar(db_session, CAP_SYNC, encendido=False,
                                motivo="Meta devolviendo 500")
    await db_session.commit()

    estado = await interruptores.estado(
        db_session, CAP_SYNC, organization_id=org.id,
    )
    assert estado.encendida is False
    assert estado.motivo == "Meta devolviendo 500"


@pytest.mark.asyncio
async def test_c2_el_writeback_se_bloquea_en_su_punto_de_ejecucion(
    db_session: AsyncSession, mocker,
):
    """No en la API: en el limitador, que es por donde pasan TODAS las
    escrituras, incluidas las de las tareas asíncronas."""
    from app.services.meta_ads.exceptions import WritebackBlocked
    from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

    org = Organization(slug="apaga-wb", name="Apaga WB")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)
    await interruptores.cambiar(db_session, CAP_WRITE_BACK, encendido=False,
                                motivo="Contención")
    await db_session.commit()

    # El limitador abre su propia sesión a propósito: es un canal lateral que
    # no debe quedar enredado con la transacción de negocio. En los tests hay
    # que prestarle la del test para que vea las filas recién escritas.
    class _Prestada:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    # La compuerta re-importa `async_session` desde `app.database` dentro de la
    # función, así que hay que parchear el origen y no el módulo que lo usa.
    mocker.patch("app.database.async_session", _Prestada())

    limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
    with pytest.raises(WritebackBlocked) as e:
        await limiter._assert_writeback_allowed()
    assert "write_back" in str(e.value)


@pytest.mark.asyncio
async def test_c3_el_rechazo_nombra_el_interruptor_responsable(db_session: AsyncSession):
    """Un rechazo que no dice cuál obliga a revisar los cinco durante un
    incidente, y quien lo revisa está ocupado."""
    await interruptores.cambiar(db_session, CAP_MEDIOS, encendido=False,
                                motivo="Disco lleno")
    await db_session.commit()

    with pytest.raises(interruptores.CapacidadApagada) as e:
        await interruptores.assert_encendida(db_session, CAP_MEDIOS)

    assert e.value.capacidad == CAP_MEDIOS
    assert e.value.motivo == "Disco lleno"
    # Y el diagnóstico de la agencia lo lleva; el mensaje del cliente no.
    assert e.value.as_diagnostico()["capacidad"] == CAP_MEDIOS
    assert "Disco lleno" in e.value.as_diagnostico()["motivo"]


def test_c3_el_mensaje_del_cliente_no_expone_infraestructura():
    e = interruptores.CapacidadApagada(
        CAP_MEDIOS, motivo="El volumen /var está al 97 %", ambito="global",
    )
    mensaje = e.as_detail()["message"]
    assert "/var" not in mensaje and "97" not in mensaje
    # Y nombra lo que SIGUE funcionando.
    assert "resto del producto" in mensaje


# --------------------------------------------------------------------------
# Criterios 4 y 5 — efecto inmediato, registrado, y por Organización.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c4_el_cambio_queda_registrado_con_actor_y_momento(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, _org_id = await _agencia(async_client, db_session, "registro-op")

    res = await async_client.put(
        f"/api/v1/operacion/interruptores/{CAP_IA}", headers=headers,
        json={"encendido": False, "motivo": "El proveedor está caído"},
    )
    assert res.status_code == 200, res.text

    cambio = (await db_session.execute(
        select(OperationalChange).where(OperationalChange.clave == CAP_IA)
    )).scalars().one()
    assert cambio.valor_nuevo == "apagado"
    assert cambio.motivo == "El proveedor está caído"
    assert cambio.actor_email == "registro-op@ejemplo.com"
    assert cambio.created_at is not None


@pytest.mark.asyncio
async def test_c4_el_efecto_es_inmediato_sin_desplegar(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """El caché tiene TTL corto y se invalida al cambiar: un interruptor que
    tarda en hacer efecto no sirve en el único momento en que se usa."""
    headers, _org_id = await _agencia(async_client, db_session, "inmediato-op")

    antes = (await async_client.get(
        "/api/v1/operacion/interruptores", headers=headers,
    )).json()
    assert antes["hay_algo_apagado"] is False

    await async_client.put(
        f"/api/v1/operacion/interruptores/{CAP_SYNC}", headers=headers,
        json={"encendido": False, "motivo": "x"},
    )

    despues = (await async_client.get(
        "/api/v1/operacion/interruptores", headers=headers,
    )).json()
    assert despues["hay_algo_apagado"] is True
    apagada = next(c for c in despues["capacidades"] if c["capacidad"] == CAP_SYNC)
    assert apagada["encendida"] is False


@pytest.mark.asyncio
async def test_c5_apagar_para_una_organizacion_no_toca_a_las_demas(
    db_session: AsyncSession,
):
    una = Organization(slug="una-op", name="Una")
    otra = Organization(slug="otra-op", name="Otra")
    db_session.add_all([una, otra])
    await db_session.flush()

    await interruptores.cambiar(
        db_session, CAP_CREACION, encendido=False, organization_id=una.id,
        motivo="Solo esta",
    )
    await db_session.commit()

    assert await interruptores.esta_encendida(
        db_session, CAP_CREACION, organization_id=una.id,
    ) is False
    assert await interruptores.esta_encendida(
        db_session, CAP_CREACION, organization_id=otra.id,
    ) is True
    assert await interruptores.esta_encendida(db_session, CAP_CREACION) is True


@pytest.mark.asyncio
async def test_c5_lo_especifico_gana_sobre_lo_global(db_session: AsyncSession):
    """Apagar globalmente y dejar una organización encendida tiene que
    funcionar: es como se excluye a la cuenta piloto de una contención."""
    org = Organization(slug="excepcion-op", name="Excepción")
    db_session.add(org)
    await db_session.flush()

    await interruptores.cambiar(db_session, CAP_IA, encendido=False)
    await interruptores.cambiar(
        db_session, CAP_IA, encendido=True, organization_id=org.id,
    )
    await db_session.commit()

    assert await interruptores.esta_encendida(db_session, CAP_IA) is False
    assert await interruptores.esta_encendida(
        db_session, CAP_IA, organization_id=org.id,
    ) is True


# --------------------------------------------------------------------------
# Criterio 6 — modo de solo lectura.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c6_el_modo_de_solo_lectura_apaga_todas_las_capacidades(
    db_session: AsyncSession,
):
    """Se activa de una vez, que es lo que hace falta cuando todavía no se sabe
    cuál es la causa: ir apagando de a una mientras se investiga deja huecos."""
    await interruptores.cambiar(
        db_session, CAP_SOLO_LECTURA, encendido=True, motivo="Causa desconocida",
    )
    await db_session.commit()

    for capacidad in CAPACIDADES:
        estado = await interruptores.estado(db_session, capacidad)
        assert estado.encendida is False, capacidad
        assert estado.ambito == "solo_lectura"


@pytest.mark.asyncio
async def test_c6_el_modo_de_solo_lectura_no_es_la_suma_de_los_cinco(
    db_session: AsyncSession,
):
    """Su `encendido` significa lo CONTRARIO que el de las capacidades:
    encendido = está bloqueando. Se resuelve con su propia función para que un
    `not` en el sitio equivocado no decida si se puede escribir."""
    activo, motivo = await interruptores.solo_lectura_activo(db_session)
    assert activo is False and motivo is None

    await interruptores.cambiar(
        db_session, CAP_SOLO_LECTURA, encendido=True, motivo="Mantenimiento",
    )
    await db_session.commit()

    activo, motivo = await interruptores.solo_lectura_activo(db_session)
    assert activo is True
    assert motivo == "Mantenimiento"


@pytest.mark.asyncio
async def test_c6_con_solo_lectura_el_historico_sigue_consultable(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """La otra mitad del criterio. Un modo de solo lectura que también quita la
    lectura no es un modo de solo lectura."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    headers, org_id = await _agencia(async_client, db_session, "lectura-op")
    await cuenta_de_prueba(db_session, org_id)
    await interruptores.cambiar(db_session, CAP_SOLO_LECTURA, encendido=True)
    await db_session.commit()

    assert (await async_client.get(
        "/api/v1/organizations/my", headers=headers,
    )).status_code == 200
    assert (await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers,
    )).status_code == 200


# --------------------------------------------------------------------------
# Criterio 7 — fallo del almacén de estado.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_con_el_almacen_caido_las_escrituras_se_apagan(
    db_session: AsyncSession, mocker,
):
    """Fallo cerrado en el plano operativo: no poder comprobar qué está apagado
    es, a efectos de contención, indistinguible de estar apagado."""
    mocker.patch(
        "app.services.interruptores._cargar",
        side_effect=RuntimeError("la base no responde"),
    )

    for capacidad in CAPACIDADES:
        estado = await interruptores.estado(db_session, capacidad)
        assert estado.encendida is False, capacidad
        assert estado.ambito == "fallo"
        assert "precaución" in (estado.motivo or "")


@pytest.mark.asyncio
async def test_c7_con_el_almacen_caido_la_lectura_del_historico_sigue(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """El par completo. Solo la primera mitad no distingue una política de
    fallo cerrado de un sistema roto."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    headers, org_id = await _agencia(async_client, db_session, "fallo-op")
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    mocker.patch(
        "app.services.interruptores._cargar",
        side_effect=RuntimeError("la base no responde"),
    )

    # La consulta del histórico NO pasa por los interruptores: no es que su
    # interruptor esté encendido, es que no tiene.
    assert (await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers,
    )).status_code == 200


@pytest.mark.asyncio
async def test_c7_con_el_almacen_caido_el_modo_de_solo_lectura_se_asume_activo(
    db_session: AsyncSession, mocker,
):
    mocker.patch(
        "app.services.interruptores._cargar",
        side_effect=RuntimeError("la base no responde"),
    )
    activo, motivo = await interruptores.solo_lectura_activo(db_session)
    assert activo is True
    assert "precaución" in (motivo or "")


# --------------------------------------------------------------------------
# Criterios 8 y 9 — feature flags.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c8_dos_organizaciones_resuelven_distinto_en_la_misma_ejecucion(
    db_session: AsyncSession,
):
    una = Organization(slug="flag-una", name="Una")
    otra = Organization(slug="flag-otra", name="Otra")
    db_session.add_all([una, otra])
    await db_session.flush()

    await feature_flags.fijar(
        db_session, "meta_version_candidata", activo_=True, organization_id=una.id,
    )
    await db_session.commit()

    assert await feature_flags.activo(
        db_session, "meta_version_candidata", organization_id=una.id,
    ) is True
    assert await feature_flags.activo(
        db_session, "meta_version_candidata", organization_id=otra.id,
    ) is False


@pytest.mark.asyncio
async def test_c9_un_flag_sin_configurar_toma_su_valor_predeterminado(
    db_session: AsyncSession,
):
    """Explícito y declarado junto al flag, no un NULL que cada consumidor
    interprete a su manera."""
    assert await feature_flags.activo(db_session, "chat_resumen_proactivo") is True
    assert await feature_flags.activo(db_session, "meta_version_candidata") is False


@pytest.mark.asyncio
async def test_c9_un_flag_inventado_falla_en_vez_de_devolver_apagado(
    db_session: AsyncSession,
):
    """Un flag mal escrito que responde "apagado" produce una capacidad que
    nunca se enciende y nadie entiende por qué."""
    with pytest.raises(feature_flags.FlagDesconocido):
        await feature_flags.activo(db_session, "flag_que_no_existe")


@pytest.mark.asyncio
async def test_la_cuenta_piloto_gana_sobre_la_organizacion(db_session: AsyncSession):
    """Si no ganara, no serviría para pilotar."""
    org = Organization(slug="piloto-org", name="Piloto")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    await feature_flags.fijar(
        db_session, "meta_version_candidata", activo_=False, organization_id=org.id,
    )
    await feature_flags.fijar(
        db_session, "meta_version_candidata", activo_=True, ad_account_id=acc.id,
    )
    await db_session.commit()

    assert await feature_flags.activo(
        db_session, "meta_version_candidata",
        organization_id=org.id, ad_account_id=acc.id,
    ) is True
    assert await feature_flags.activo(
        db_session, "meta_version_candidata", organization_id=org.id,
    ) is False


@pytest.mark.asyncio
async def test_un_flag_no_se_fija_en_dos_ambitos_a_la_vez(db_session: AsyncSession):
    """La fila existiría y la resolución no la consultaría nunca, que es peor
    que un error."""
    org = Organization(slug="ambito-org", name="Ámbito")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    with pytest.raises(ValueError):
        await feature_flags.fijar(
            db_session, "meta_version_candidata", activo_=True,
            organization_id=org.id, ad_account_id=acc.id,
        )


@pytest.mark.asyncio
async def test_con_el_almacen_de_flags_caido_se_usa_el_valor_predeterminado(
    db_session: AsyncSession, mocker,
):
    """Distinto de los interruptores, y a propósito: un interruptor apagado
    contiene un incidente; un flag apagado por accidente apaga una capacidad
    que llevaba meses funcionando bien. El default es lo que el sistema hacía
    ayer."""
    mocker.patch(
        "app.services.feature_flags._cargar",
        side_effect=RuntimeError("la base no responde"),
    )
    assert await feature_flags.activo(db_session, "chat_resumen_proactivo") is True
    assert await feature_flags.activo(db_session, "meta_version_candidata") is False


@pytest.mark.asyncio
async def test_el_origen_del_valor_viaja_con_el_valor(db_session: AsyncSession):
    """"Está encendido" no dice si lo está por su default, por una decisión
    global o porque esta cuenta es la piloto — y apagarlo exige saber cuál."""
    org = Organization(slug="origen-org", name="Origen")
    db_session.add(org)
    await db_session.flush()
    await feature_flags.fijar(
        db_session, "meta_version_candidata", activo_=True, organization_id=org.id,
    )
    await db_session.commit()

    todos = await feature_flags.resolver_todos(db_session, organization_id=org.id)
    assert todos["meta_version_candidata"]["origen"] == "organizacion"
    assert todos["chat_resumen_proactivo"]["origen"] == "por_defecto"


# --------------------------------------------------------------------------
# Criterios 10, 11 y 12 — señales, umbrales y el vigilante externo.
# --------------------------------------------------------------------------

# La lista literal de la spec. El test falla si se añade una señal a la lista y
# no al código, o al revés.
SENALES_EXIGIDAS = {
    "sync_frescura",          # frescura de sync
    "colas_profundidad",      # colas
    "colas_latencia",         # colas
    "meta_errores",           # errores de Meta
    "grounding_descartes",    # descartes de grounding
    "ia_consumo",             # uso y costo de IA
    "escrituras_inciertas",   # escrituras inciertas
    "respaldo",               # salud del respaldo
    "disco",
    "memoria",
    "dependencias",           # disponibilidad de dependencias
}


@pytest.mark.asyncio
async def test_c10_existe_una_senal_por_cada_elemento_de_la_lista(
    db_session: AsyncSession,
):
    todas = await senales.todas(db_session)
    claves = {s.clave for s in todas}
    assert SENALES_EXIGIDAS <= claves, SENALES_EXIGIDAS - claves


@pytest.mark.asyncio
async def test_c10_cada_senal_declara_su_umbral(db_session: AsyncSession):
    """Un número sin umbral no se puede vigilar, solo mirar."""
    for senal in await senales.todas(db_session):
        assert senal.umbral is not None, senal.clave
        assert senal.titulo, senal.clave


@pytest.mark.asyncio
async def test_c11_superar_el_umbral_genera_alerta(db_session: AsyncSession):
    """Se inyecta el estado que debe dispararla en vez de esperar a que ocurra:
    un sync envejecido más allá del umbral."""
    from app.models.sync_job import SyncJob

    org = Organization(slug="senal-org", name="Señal")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    viejo = datetime.datetime.utcnow() - datetime.timedelta(
        hours=senales.HORAS_SIN_SYNC + 5,
    )
    db_session.add(SyncJob(
        ad_account_id=acc.id, organization_id=org.id,
        status="COMPLETED", sync_type="INCREMENTAL",
        started_at=viejo, completed_at=viejo,
    ))
    await db_session.commit()

    senal = await senales.frescura_del_sync(db_session)
    assert senal.estado == senales.ALERTA
    assert senal.valor > senales.HORAS_SIN_SYNC


@pytest.mark.asyncio
async def test_c11_por_debajo_del_umbral_no_alerta(db_session: AsyncSession):
    """El contracaso. Sin él, la señal se aprueba alertando siempre — y una
    señal que siempre alerta se deja de mirar."""
    from app.models.sync_job import SyncJob

    org = Organization(slug="senal-ok-org", name="Señal OK")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    reciente = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    db_session.add(SyncJob(
        ad_account_id=acc.id, organization_id=org.id,
        status="COMPLETED", sync_type="INCREMENTAL",
        started_at=reciente, completed_at=reciente,
    ))
    await db_session.commit()

    senal = await senales.frescura_del_sync(db_session)
    assert senal.estado == senales.OK


@pytest.mark.asyncio
async def test_una_instalacion_nueva_no_arranca_con_una_alerta_falsa(
    db_session: AsyncSession,
):
    """Alertar el primer día de cada despliegue es la forma más rápida de que
    las alertas se dejen de mirar."""
    senal = await senales.frescura_del_sync(db_session)
    assert senal.estado == senales.OK
    assert "Todavía no hay ningún sync" in (senal.detalle or "")


@pytest.mark.asyncio
async def test_una_senal_que_no_se_pudo_medir_es_desconocida_no_ok(
    db_session: AsyncSession, mocker,
):
    """`desconocido` no es `ok`. Una señal que falla al medirse y se reporta
    como sana es la peor de las tres opciones."""
    mocker.patch(
        "app.models.sync_job.SyncJob", side_effect=RuntimeError("boom"),
    )
    senal = senales.salud_del_respaldo()
    assert senal.estado == senales.DESCONOCIDO
    assert senal.alerta is False, "desconocido no dispara la alerta, pero no es ok"


@pytest.mark.asyncio
async def test_c11_la_escritura_incierta_alerta_desde_la_primera(
    db_session: AsyncSession,
):
    """Una sola ya significa que se mandó algo y no se sabe si llegó."""
    from app.models.campaign_proposal import WRITE_VERIFYING, MetaWriteIntent

    org = Organization(slug="incierta-org", name="Incierta")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    assert (await senales.escrituras_inciertas(db_session)).estado == senales.OK

    db_session.add(MetaWriteIntent(
        idempotency_key="k1", organization_id=org.id, ad_account_id=acc.id,
        operation="create_campaign", status=WRITE_VERIFYING,
    ))
    await db_session.commit()

    assert (await senales.escrituras_inciertas(db_session)).estado == senales.ALERTA


@pytest.mark.asyncio
async def test_c12_las_senales_se_publican_sin_autenticacion(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Un endpoint que exige credenciales no lo puede sondear un monitor de
    disponibilidad simple, y un sistema que solo alerta desde dentro no puede
    informar de su propia muerte."""
    res = await async_client.get("/api/v1/operacion/senales")
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    assert cuerpo["estado"] in (senales.OK, senales.ALERTA)
    assert cuerpo["senales"]
    # La marca de tiempo es lo que hace posible el criterio: el vigilante mira
    # que AVANCE. Con el VPS apagado no hay respuesta, y esa ausencia es la
    # alerta.
    assert cuerpo["generado_en"]


@pytest.mark.asyncio
async def test_c12_las_senales_no_exponen_datos_de_ningun_cliente(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Va sin autenticación, así que lo que publica tiene que poder verlo
    cualquiera: estados, umbrales y una marca de tiempo."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    _headers, org_id = await _agencia(async_client, db_session, "secreto-org")
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    cuerpo = (await async_client.get("/api/v1/operacion/senales")).json()
    texto = str(cuerpo)

    assert "secreto-org" not in texto
    assert org_id not in texto
    assert "@ejemplo.com" not in texto


# --------------------------------------------------------------------------
# Criterios 13 y 14 — dos audiencias, y la lectura se conserva.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c13_la_agencia_ve_el_diagnostico_tecnico(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, _org_id = await _agencia(async_client, db_session, "diag-org")
    await interruptores.cambiar(
        db_session, CAP_SYNC, encendido=False, motivo="Meta devolviendo 500",
        actor_email="operador@ejemplo.com",
    )
    await db_session.commit()

    res = await async_client.get("/api/v1/operacion/diagnostico", headers=headers)
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    assert cuerpo["senales"], "las señales funcionales"
    assert cuerpo["interruptores"]["hay_algo_apagado"] is True
    # Quién movió qué: durante un incidente esa persona está ocupada.
    assert cuerpo["cambios_recientes"][0]["clave"] == CAP_SYNC
    assert cuerpo["cambios_recientes"][0]["motivo"] == "Meta devolviendo 500"
    assert cuerpo["cambios_recientes"][0]["actor"] == "operador@ejemplo.com"


@pytest.mark.asyncio
async def test_c13_el_cliente_no_ve_el_diagnostico_tecnico(
    async_client: AsyncClient, db_session: AsyncSession,
):
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    headers, org_id = await _agencia(async_client, db_session, "diag-cliente-org")
    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org_id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    assert (await async_client.get(
        "/api/v1/operacion/diagnostico", headers=headers,
    )).status_code == 403


@pytest.mark.asyncio
async def test_c13_el_aviso_del_cliente_no_expone_infraestructura(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, _org_id = await _agencia(async_client, db_session, "aviso-org")
    await interruptores.cambiar(
        db_session, CAP_MEDIOS, encendido=False,
        motivo="El volumen /var/lib/lejovista está al 97 %",
    )
    await db_session.commit()

    cuerpo = (await async_client.get(
        "/api/v1/operacion/aviso", headers=headers,
    )).json()

    assert cuerpo["hay_incidente"] is True
    texto = str(cuerpo)
    assert "/var" not in texto and "97" not in texto
    assert "medios" not in texto, "ni la clave interna de la capacidad"
    # Y nombra lo que SIGUE disponible.
    assert cuerpo["sigue_disponible"]


@pytest.mark.asyncio
async def test_sin_incidente_el_aviso_no_dice_nada(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Una interfaz que siempre muestra algo sobre el estado del sistema entrena
    a ignorar ese sitio, y el día que importe no se va a mirar."""
    headers, _org_id = await _agencia(async_client, db_session, "sin-incidente-org")
    cuerpo = (await async_client.get(
        "/api/v1/operacion/aviso", headers=headers,
    )).json()
    assert cuerpo == {"hay_incidente": False}


@pytest.mark.asyncio
async def test_c14_durante_un_incidente_de_escritura_la_lectura_sigue(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Es justo cuando el cliente más quiere mirar qué pasa con su dinero."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    headers, org_id = await _agencia(async_client, db_session, "lectura-incidente")
    await cuenta_de_prueba(db_session, org_id)

    for capacidad in (CAP_CREACION, CAP_WRITE_BACK, CAP_MEDIOS):
        await interruptores.cambiar(db_session, capacidad, encendido=False)
    await db_session.commit()

    assert (await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers,
    )).status_code == 200
    assert (await async_client.get(
        "/api/v1/organizations/my", headers=headers,
    )).status_code == 200


# --------------------------------------------------------------------------
# Criterios 15, 16 y 17 — las métricas de producto.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c15_c16_c17_las_seis_metricas_se_pueden_consultar(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, _org_id = await _agencia(async_client, db_session, "metricas-org")

    res = await async_client.get(
        "/api/v1/operacion/metricas?dias=30", headers=headers,
    )
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    for clave in (
        "tiempo_hasta_borrador", "tiempo_de_revision", "aprobacion_sin_cambios",
        "errores_bloqueados_antes_de_meta", "diferencia_frente_al_baseline",
        "uso_del_chat",
    ):
        assert clave in cuerpo, clave


@pytest.mark.asyncio
async def test_c15_sin_recorrido_las_metricas_son_nulas_y_no_cero(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """"Sin datos" no es "cero minutos": un cero se leería como que el sistema
    propone al instante."""
    headers, _org_id = await _agencia(async_client, db_session, "vacias-org")
    cuerpo = (await async_client.get(
        "/api/v1/operacion/metricas", headers=headers,
    )).json()

    assert cuerpo["tiempo_hasta_borrador"]["muestra"] == 0
    assert cuerpo["tiempo_hasta_borrador"]["mediana"] is None
    assert cuerpo["aprobacion_sin_cambios"]["proporcion"] is None
    assert cuerpo["diferencia_frente_al_baseline"]["diferencia_mediana"] is None


@pytest.mark.asyncio
async def test_c17_el_uso_del_chat_se_puede_consultar(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    from app.models.campaign_chat_session import CampaignChatSession

    mocker.patch("app.api.v1.organizations.sync_ad_account")
    headers, org_id = await _agencia(async_client, db_session, "chat-metrica-org")
    acc = await cuenta_de_prueba(db_session, org_id)
    db_session.add(CampaignChatSession(
        organization_id=org_id, ad_account_id=acc.id,
        state="BROWSING", status="OPEN", mode="ASK", turn_count=4,
    ))
    await db_session.commit()

    cuerpo = (await async_client.get(
        "/api/v1/operacion/metricas", headers=headers,
    )).json()

    assert cuerpo["uso_del_chat"]["sesiones"] == 1
    assert cuerpo["uso_del_chat"]["turnos_totales"] == 4
    assert cuerpo["uso_del_chat"]["por_modo"] == {"ASK": 1}


@pytest.mark.asyncio
async def test_c16_la_diferencia_se_mide_contra_el_baseline_congelado(
    db_session: AsyncSession,
):
    """Contra el congelado y no el vigente: juzgar una campaña de hace dos meses
    con la referencia de hoy la premia o la castiga por lo que hicieron OTRAS
    campañas después."""
    from app.models.campaign_draft import CampaignDraft
    from app.models.campaign_learning_log import CampaignLearningLog
    from app.services import metricas_producto

    org = Organization(slug="dif-org", name="Dif")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)

    # `draft_id` es una clave foránea. Un identificador inventado pasa contra el
    # motor embebido, que no las comprueba por defecto, y rompe contra PostgreSQL.
    borrador = CampaignDraft(organization_id=org.id, name="Borrador", payload={})
    db_session.add(borrador)
    await db_session.flush()

    db_session.add(CampaignLearningLog(
        organization_id=org.id, ad_account_id=acc.id, draft_id=borrador.id,
        status="EVALUATED",
        outcome_json={
            "verdict": "WIN", "cost_per_conv": 8.0,
            "baseline_congelado": {"mediana": 10.0},
        },
    ))
    await db_session.commit()

    resultado = await metricas_producto.diferencia_frente_al_baseline(
        db_session, org.id, datetime.datetime.utcnow() - datetime.timedelta(days=30),
    )
    assert resultado["muestra"] == 1
    # Negativo es bueno: costó menos que la referencia.
    assert resultado["diferencia_mediana"] == -0.2
    assert resultado["mejores_que_el_baseline"] == 1


@pytest.mark.asyncio
async def test_el_cliente_no_ve_las_metricas_de_producto(
    async_client: AsyncClient, db_session: AsyncSession,
):
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    headers, org_id = await _agencia(async_client, db_session, "metricas-cliente")
    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org_id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    assert (await async_client.get(
        "/api/v1/operacion/metricas", headers=headers,
    )).status_code == 403


@pytest.mark.asyncio
async def test_c2_la_tarea_de_sync_se_detiene_de_verdad_con_el_interruptor_apagado(
    db_session: AsyncSession, mocker,
):
    """El criterio 2 en su forma completa: no que la comprobación devuelva
    `False`, sino que la tarea NO llegue a llamar a Meta.

    Se ejerce el cuerpo real de la tarea con el servicio de sync espiado. Probar
    solo la resolución del interruptor dejaría pasar el fallo que importa —que
    alguien lo consulte y siga adelante igual.
    """
    from app.workers import tasks as tareas

    org = Organization(slug="tarea-sync-org", name="Tarea Sync")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)
    await interruptores.cambiar(db_session, CAP_SYNC, encendido=False,
                                motivo="Contención")
    await db_session.commit()

    class _Prestada:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    mocker.patch.object(tareas, "async_session", _Prestada())
    mocker.patch.object(tareas, "_acquire_sync_lock", return_value=True)
    mocker.patch.object(tareas, "_release_sync_lock")
    corrio = mocker.patch.object(
        tareas.MetaAdsSyncService, "run_sync", autospec=True,
    )

    # `run_async` crea su propio event loop con `asyncio.run`, que no se puede
    # llamar desde dentro de uno. Se captura la corrutina y se ejecuta en el
    # loop del test: así se ejerce el CUERPO REAL de la tarea, que es lo que el
    # criterio 2 exige comprobar.
    capturada = {}
    mocker.patch.object(
        tareas, "run_async", lambda coro: capturada.setdefault("coro", coro),
    )

    tareas.sync_ad_account(acc.id, "INCREMENTAL")
    await capturada["coro"]

    assert corrio.await_count == 0 and corrio.call_count == 0, (
        "la tarea llamó a Meta con el interruptor apagado"
    )


@pytest.mark.asyncio
async def test_c2_con_el_interruptor_encendido_la_tarea_si_sincroniza(
    db_session: AsyncSession, mocker,
):
    """El contracaso. Sin él, el test de arriba pasaría con una tarea rota."""
    from app.workers import tasks as tareas

    org = Organization(slug="tarea-ok-org", name="Tarea OK")
    db_session.add(org)
    await db_session.flush()
    acc = await cuenta_de_prueba(db_session, org.id)
    await db_session.commit()

    class _Prestada:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    mocker.patch.object(tareas, "async_session", _Prestada())
    mocker.patch.object(tareas, "_acquire_sync_lock", return_value=True)
    mocker.patch.object(tareas, "_release_sync_lock")
    # Todo lo que hace la tarea DESPUÉS de sincronizar pasa por Redis, y la
    # suite lo apunta a un puerto muerto. Lo que este test mira es si llegó a
    # sincronizar, no lo que hace después.
    mocker.patch.object(tareas, "_schedule_org_analysis_debounced", return_value=False)
    mocker.patch(
        "app.services.notification_service.NotificationService.notify_job_status",
        return_value=None,
    )
    corrio = mocker.patch.object(
        tareas.MetaAdsSyncService, "run_sync", autospec=True,
    )
    corrio.return_value = mocker.MagicMock(
        id="j1", status="COMPLETED", records_synced=1,
    )

    capturada = {}
    mocker.patch.object(
        tareas, "run_async", lambda coro: capturada.setdefault("coro", coro),
    )

    tareas.sync_ad_account(acc.id, "INCREMENTAL")
    await capturada["coro"]

    assert corrio.await_count == 1, "con el interruptor encendido tiene que sincronizar"
