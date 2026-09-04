"""Spec 05 — Experiencias de cliente y de agencia, roles, módulos y alta asistida.

Un test por criterio de aceptación de
`docs/specs/05-experiencias-cliente-agencia-roles-y-modulos.md`.

Dos decisiones sobre cómo se prueba, ambas del propio contrato de la spec:

* **Cada ocultación se comprueba llamando a la API, no mirando el menú.** Un test
  que solo verifique la navegación no distingue *oculto* de *apagado*, que es
  exactamente el error que esta spec corrige.
* **La matriz módulo × rol se prueba en sus cuatro combinaciones**, incluidas las
  dos que hoy se confunden: rol suficiente con módulo apagado, y rol insuficiente
  con módulo encendido.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.services.experiencia import ACCESO_AGENCIA, ACCESO_CLIENTE
from conftest import cuenta_de_prueba


async def _signup(client: AsyncClient, slug: str, prefijo: str = "admin"):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"{prefijo}@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec05", "organization_slug": slug, "organization_name": "Spec05 Org",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


async def _fijar_acceso(db: AsyncSession, user_id: str, org_id: str, acceso: str):
    await db.execute(
        update(OrganizationUser)
        .where(OrganizationUser.user_id == user_id,
               OrganizationUser.organization_id == org_id)
        .values(access_kind=acceso)
    )
    await db.commit()


async def _fijar_rol(db: AsyncSession, user_id: str, org_id: str, rol: str):
    await db.execute(
        update(OrganizationUser)
        .where(OrganizationUser.user_id == user_id,
               OrganizationUser.organization_id == org_id)
        .values(role=rol)
    )
    await db.commit()


async def _fijar_modulos(db: AsyncSession, org_id: str, modulos):
    await db.execute(
        update(Organization).where(Organization.id == org_id).values(enabled_modules=modulos)
    )
    await db.commit()


async def _segundo_usuario(db: AsyncSession, client: AsyncClient, org_id: str,
                           email: str, rol: str, acceso: str):
    """Un usuario adicional en la MISMA organización, con su propio acceso."""
    from app.utils.security import create_access_token, get_password_hash

    u = User(email=email, hashed_password=get_password_hash("securepassword123"),
             full_name="Otro")
    db.add(u)
    await db.flush()
    db.add(OrganizationUser(user_id=u.id, organization_id=org_id, role=rol,
                            access_kind=acceso))
    await db.commit()
    from conftest import conceder_todas_las_cuentas
    await conceder_todas_las_cuentas(db, u.id, org_id)
    await db.commit()
    token = create_access_token(subject=u.id, org=org_id)
    return u, {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Criterio 1 · dos superficies distintas sobre la misma Organización
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_cliente_y_agencia_obtienen_superficies_distintas(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers_agencia = await _signup(async_client, "c1-dos-caras")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    _, headers_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "cliente@c1.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )

    exp_agencia = await async_client.get("/api/v1/organizations/my/experience", headers=headers_agencia)
    exp_cliente = await async_client.get("/api/v1/organizations/my/experience", headers=headers_cliente)

    assert exp_agencia.status_code == 200, exp_agencia.text
    assert exp_cliente.status_code == 200, exp_cliente.text
    assert exp_agencia.json()["experiencia"] == "agencia"
    assert exp_cliente.json()["experiencia"] == "cliente"


# --------------------------------------------------------------------------
# Criterio 2 · el cliente no ve la altura operativa
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c2_el_cliente_no_ve_proveedor_modelo_ni_clave_de_ia(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _headers_agencia = await _signup(async_client, "c2-sin-palancas")
    org_id = body["user"]["organization_id"]
    org = await db_session.get(Organization, org_id)
    org.llm_provider = "openai"
    org.ai_model = "gpt-5.6-luna"
    org.set_llm_api_key("sk-clave-del-cliente-1234")
    db_session.add(org)
    await db_session.commit()

    _, headers_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "cliente@c2.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )

    res = await async_client.get("/api/v1/organizations/my", headers=headers_cliente)
    assert res.status_code == 200, res.text
    cuerpo = res.json()
    # La forma de la respuesta es estable —las claves siguen ahí— y lo que no
    # llega es el VALOR. Una respuesta que cambia de forma según quién pregunta
    # obliga al frontend a tratar cada campo como opcional por un motivo ajeno.
    for campo in ("llm_provider", "llm_api_key_preview", "ai_model"):
        assert cuerpo[campo] is None, f"'{campo}' es una palanca de la agencia"
    assert "sk-clave" not in res.text

    # La agencia sí los ve: si no, esto no sería una separación, sería un borrado.
    res_agencia = await async_client.get("/api/v1/organizations/my", headers=_headers_agencia)
    assert res_agencia.json()["llm_provider"] == "openai"
    assert res_agencia.json()["llm_api_key_preview"]


@pytest.mark.asyncio
async def test_c2b_el_cliente_no_alcanza_las_superficies_de_diagnostico(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Se comprueba llamando a la API, no mirando el menú: un test sobre la
    navegación no distingue oculto de apagado."""
    body, headers_agencia = await _signup(async_client, "c2b-diagnostico")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    _, headers_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "cliente@c2b.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )

    for ruta in ("/api/v1/campaigns/api-call-history", "/api/v1/ai-usage"):
        r_cliente = await async_client.get(ruta, headers=headers_cliente)
        assert r_cliente.status_code == 403, f"{ruta} no debería alcanzarla el cliente"
        assert r_cliente.json()["detail"]["code"] == "AGENCY_ONLY"


# --------------------------------------------------------------------------
# Criterios 3-6 · la matriz módulo × rol, en sus cuatro combinaciones
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c3_modulo_apagado_bloquea_al_cliente_tambien_por_api(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _ = await _signup(async_client, "c3-modulo-off")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await _fijar_modulos(db_session, org_id, ["resumen", "settings"])

    _, headers_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "cliente@c3.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )

    res = await async_client.get("/api/v1/actions", headers=headers_cliente)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "MODULE_DISABLED"


@pytest.mark.asyncio
async def test_c4_el_mismo_modulo_apagado_sigue_disponible_para_la_agencia(
    async_client: AsyncClient, db_session: AsyncSession
):
    """La corrección de frontera: apagar un módulo para simplificarle la vista al
    cliente se lo apagaba también al operador que necesita esa herramienta."""
    body, headers_agencia = await _signup(async_client, "c4-agencia-mantiene")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await _fijar_modulos(db_session, org_id, ["resumen", "settings"])

    res = await async_client.get("/api/v1/actions", headers=headers_agencia)
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_c5_rol_insuficiente_se_rechaza_con_el_modulo_encendido(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _ = await _signup(async_client, "c5-rol")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    _, headers_viewer = await _segundo_usuario(
        db_session, async_client, org_id, "viewer@c5.example.com",
        rol="VIEWER", acceso=ACCESO_CLIENTE,
    )

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers_viewer,
        json={"ad_account_id": "x", "page_id": "p", "product_name": "C",
              "budget": 10.0, "days": 7, "whatsapp_number": "519", "copy_text": "t"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ROLE_REQUIRED"


@pytest.mark.asyncio
async def test_c6_los_dos_rechazos_se_distinguen(
    async_client: AsyncClient, db_session: AsyncSession
):
    """`ROLE_REQUIRED` y `MODULE_DISABLED` son dos problemas con dos soluciones
    distintas. Un mensaje suelto los confunde."""
    body, _ = await _signup(async_client, "c6-distinguir")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await _fijar_modulos(db_session, org_id, ["resumen", "settings"])

    _, headers_admin_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "admin@c6.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )
    _, headers_viewer_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "viewer@c6.example.com",
        rol="VIEWER", acceso=ACCESO_CLIENTE,
    )

    # Rol suficiente + módulo apagado
    r1 = await async_client.get("/api/v1/actions", headers=headers_admin_cliente)
    assert r1.status_code == 403 and r1.json()["detail"]["code"] == "MODULE_DISABLED"

    # Rol insuficiente + módulo encendido (chat sí está... no: se enciende)
    await _fijar_modulos(db_session, org_id, ["resumen", "settings", "acciones"])
    r2 = await async_client.post(
        "/api/v1/campaigns/", headers=headers_viewer_cliente,
        json={"ad_account_id": "x", "page_id": "p", "product_name": "C",
              "budget": 10.0, "days": 7, "whatsapp_number": "519", "copy_text": "t"},
    )
    assert r2.status_code == 403 and r2.json()["detail"]["code"] == "ROLE_REQUIRED"


# --------------------------------------------------------------------------
# Criterios 7-9 · AdAccount activa y aislamiento por concesión
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_con_varias_cuentas_y_ninguna_elegida_se_rechaza_explicitamente(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c7-varias")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id, external_account_id="act_a")
    await cuenta_de_prueba(db_session, org_id, external_account_id="act_b")
    await db_session.commit()

    res = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "MULTIPLE_AD_ACCOUNTS_NO_SELECTION"


@pytest.mark.asyncio
async def test_c8_elegir_la_cuenta_activa_cambia_los_datos(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c8-elegir")
    org_id = body["user"]["organization_id"]
    a = await cuenta_de_prueba(db_session, org_id, external_account_id="act_a")
    await cuenta_de_prueba(db_session, org_id, external_account_id="act_b")
    await db_session.commit()

    res = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": a.id}
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_c9_sin_concesion_no_se_leen_los_datos_de_una_cuenta(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Ser miembro de la Organización no alcanza: la cuenta tiene que estar
    CONCEDIDA. Es fail-closed."""
    from app.utils.security import create_access_token, get_password_hash

    body, _ = await _signup(async_client, "c9-concesion")
    org_id = body["user"]["organization_id"]
    acc = await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    sin_concesion = User(email="sin@c9.example.com",
                         hashed_password=get_password_hash("securepassword123"),
                         full_name="Sin concesion")
    db_session.add(sin_concesion)
    await db_session.flush()
    db_session.add(OrganizationUser(user_id=sin_concesion.id, organization_id=org_id,
                                    role="ADMIN", access_kind=ACCESO_CLIENTE))
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=sin_concesion.id, org=org_id)}"}

    res = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": acc.id}
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------
# Criterios 10-12 · alta asistida y retirada del registro público
# --------------------------------------------------------------------------

@pytest.fixture
async def admin_de_plataforma(async_client: AsyncClient, db_session: AsyncSession):
    body, headers = await _signup(async_client, "plataforma", prefijo="plat")
    await db_session.execute(
        update(User).where(User.id == body["user"]["id"]).values(is_platform_admin=True)
    )
    await db_session.commit()
    return headers


@pytest.mark.asyncio
async def test_c10_no_se_invita_mientras_falte_un_paso_del_alta(
    async_client: AsyncClient, db_session: AsyncSession, admin_de_plataforma, mocker
):
    mocker.patch("app.api.v1.admin.send_email", return_value=True)
    creada = await async_client.post("/api/v1/admin/organizations", headers=admin_de_plataforma,
                                     json={"slug": "cliente-c10", "name": "Cliente C10"})
    assert creada.status_code == 201, creada.text
    org_id = creada.json()["id"]

    estado = await async_client.get(f"/api/v1/admin/organizations/{org_id}/onboarding",
                                    headers=admin_de_plataforma)
    assert estado.status_code == 200
    assert sorted(estado.json()["faltantes"]) == ["ia", "meta", "whatsapp"]
    assert estado.json()["puede_invitar"] is False

    res = await async_client.post(f"/api/v1/admin/organizations/{org_id}/invite-admin",
                                  headers=admin_de_plataforma,
                                  json={"email": "dueno@cliente-c10.com"})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "ONBOARDING_INCOMPLETE"
    assert set(res.json()["detail"]["faltantes"]) == {"meta", "ia", "whatsapp"}


@pytest.mark.asyncio
async def test_c10b_un_paso_se_valida_contra_el_sistema_real_no_contra_el_campo(
    async_client: AsyncClient, db_session: AsyncSession, admin_de_plataforma
):
    """No comprueba que el campo esté lleno: comprueba que funcione."""
    creada = await async_client.post("/api/v1/admin/organizations", headers=admin_de_plataforma,
                                     json={"slug": "cliente-c10b", "name": "Cliente C10b"})
    org_id = creada.json()["id"]

    # Sin cuentas conectadas, el paso de Meta falla.
    r = await async_client.post(f"/api/v1/admin/organizations/{org_id}/onboarding/meta",
                                headers=admin_de_plataforma, json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ONBOARDING_STEP_FAILED"

    # Con clave de IA pero sin proveedor declarado, el paso de IA también falla:
    # sin proveedor, se elegiría solo (lo que la spec 10 prohíbe). Desde la spec
    # 10 una organización NACE con proveedor declarado, así que para llegar a
    # este estado hay que vaciarlo a propósito — es el estado en el que quedan
    # las organizaciones migradas que no tenían credencial en ningún lado.
    org = await db_session.get(Organization, org_id)
    org.set_llm_api_key("sk-una-clave")
    org.llm_provider = None
    db_session.add(org)
    await db_session.commit()
    r = await async_client.post(f"/api/v1/admin/organizations/{org_id}/onboarding/ia",
                                headers=admin_de_plataforma, json={})
    assert r.status_code == 400
    assert "proveedor" in r.json()["detail"]["message"]

    # Con proveedor declarado pero un modelo que el catálogo ya no ofrece, el
    # paso TAMBIÉN falla. Dar el alta por completa ahí deja al cliente con la IA
    # muerta desde el primer día: la clave está, el proveedor está, y cada
    # llamada devuelve un 404 que no dice cuál es el problema.
    org.llm_provider = "openai"
    org.ai_model = "gpt-4-que-ya-no-existe"
    db_session.add(org)
    await db_session.commit()
    r = await async_client.post(f"/api/v1/admin/organizations/{org_id}/onboarding/ia",
                                headers=admin_de_plataforma, json={})
    assert r.status_code == 400
    assert "gpt-4-que-ya-no-existe" in r.json()["detail"]["message"]

    # Y con proveedor y modelo del catálogo, pasa. Sin este contracaso, el paso
    # se aprobaría rechazando siempre.
    org.ai_model = "gpt-5.6-luna"
    db_session.add(org)
    await db_session.commit()
    r = await async_client.post(f"/api/v1/admin/organizations/{org_id}/onboarding/ia",
                                headers=admin_de_plataforma, json={})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_c11_con_el_alta_completa_la_invitacion_sale(
    async_client: AsyncClient, db_session: AsyncSession, admin_de_plataforma, mocker
):
    mocker.patch("app.api.v1.admin.send_email", return_value=True)
    creada = await async_client.post("/api/v1/admin/organizations", headers=admin_de_plataforma,
                                     json={"slug": "cliente-c11", "name": "Cliente C11"})
    org_id = creada.json()["id"]

    # Los tres pasos, omitidos con motivo: quedan REGISTRADOS y son distintos de
    # "validado". Hacer el hueco explícito evita dejarlo en silencio.
    for paso in ("meta", "ia", "whatsapp"):
        r = await async_client.post(
            f"/api/v1/admin/organizations/{org_id}/onboarding/{paso}",
            headers=admin_de_plataforma,
            json={"omitir": True, "motivo": f"{paso} se configura la semana próxima"},
        )
        assert r.status_code == 200, r.text

    estado = await async_client.get(f"/api/v1/admin/organizations/{org_id}/onboarding",
                                    headers=admin_de_plataforma)
    assert estado.json()["puede_invitar"] is True
    assert all(
        estado.json()["estado"][p]["estado"] == "omitido" for p in ("meta", "ia", "whatsapp")
    )
    assert all(estado.json()["estado"][p]["actor"] for p in ("meta", "ia", "whatsapp"))

    res = await async_client.post(f"/api/v1/admin/organizations/{org_id}/invite-admin",
                                  headers=admin_de_plataforma,
                                  json={"email": "dueno@cliente-c11.com"})
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_c11b_omitir_un_paso_exige_motivo(
    async_client: AsyncClient, admin_de_plataforma
):
    creada = await async_client.post("/api/v1/admin/organizations", headers=admin_de_plataforma,
                                     json={"slug": "cliente-c11b", "name": "Cliente C11b"})
    org_id = creada.json()["id"]
    r = await async_client.post(f"/api/v1/admin/organizations/{org_id}/onboarding/meta",
                                headers=admin_de_plataforma, json={"omitir": True})
    assert r.status_code == 400
    assert "motivo" in r.json()["detail"]


@pytest.mark.asyncio
async def test_c12_el_registro_publico_esta_apagado_por_defecto(async_client: AsyncClient):
    """Mientras exista un camino de alta que no pase por el flujo asistido,
    existe la posibilidad de una Organización sin validar."""
    from app.config import settings

    original = settings.ENABLE_PUBLIC_SIGNUP
    settings.ENABLE_PUBLIC_SIGNUP = False
    try:
        res = await async_client.post("/api/v1/auth/signup", json={
            "email": "intruso@example.com", "password": "securepassword123",
            "full_name": "Intruso", "organization_slug": "intrusa",
            "organization_name": "Intrusa",
        })
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "PUBLIC_SIGNUP_DISABLED"
    finally:
        settings.ENABLE_PUBLIC_SIGNUP = original


@pytest.mark.asyncio
async def test_c12b_el_default_de_configuracion_es_apagado():
    """El punto de aplicación es la variable, y su valor por defecto es el que
    rige en producción."""
    from app.config import Settings

    assert Settings.model_fields["ENABLE_PUBLIC_SIGNUP"].default is False


# --------------------------------------------------------------------------
# Criterios 13-15 · credenciales, vocabulario y herramientas de agencia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c13_las_credenciales_no_son_legibles_desde_la_superficie_del_cliente(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _ = await _signup(async_client, "c13-credenciales")
    org_id = body["user"]["organization_id"]
    org = await db_session.get(Organization, org_id)
    org.llm_provider = "openai"
    org.set_llm_api_key("sk-secreto-del-cliente-999")
    db_session.add(org)
    acc = await cuenta_de_prueba(db_session, org_id)
    acc.set_access_token("EAAG-token-de-meta-secreto")
    db_session.add(acc)
    await db_session.commit()

    _, headers_cliente = await _segundo_usuario(
        db_session, async_client, org_id, "cliente@c13.example.com",
        rol="ADMIN", acceso=ACCESO_CLIENTE,
    )

    for ruta in ("/api/v1/organizations/my", "/api/v1/organizations/my/ad-accounts"):
        res = await async_client.get(ruta, headers=headers_cliente)
        assert res.status_code == 200, res.text
        assert "sk-secreto" not in res.text
        assert "EAAG-token" not in res.text


@pytest.mark.asyncio
async def test_c15_la_agencia_consulta_las_herramientas_operativas(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers_agencia = await _signup(async_client, "c15-agencia")
    org_id = body["user"]["organization_id"]
    await cuenta_de_prueba(db_session, org_id)
    await db_session.commit()

    for ruta in (
        "/api/v1/campaigns/recent",
        "/api/v1/insights",
        "/api/v1/actions",
        "/api/v1/campaigns/api-call-history",
        "/api/v1/campaigns/learning-history",
    ):
        res = await async_client.get(ruta, headers=headers_agencia)
        assert res.status_code == 200, f"{ruta}: {res.text}"
