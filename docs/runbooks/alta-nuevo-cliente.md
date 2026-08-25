# Runbook: Alta y Onboarding de un Nuevo Cliente (Tenant)

Paso a paso reproducible para dar de alta una organización en **Agente DX IA**,
con los módulos que ese cliente contrató y sin que nadie escriba su contraseña.

> **Cambió en 2026-08.** Antes el cliente se auto-registraba desde `/signup` y
> quedaba como ADMIN de su propia organización. Ahora el alta la hace un
> **administrador de plataforma** desde `/admin`, que además elige qué módulos
> ve ese cliente. El signup público sigue existiendo y no cambió.

---

## 0. Requisito previo (una sola vez): ser admin de plataforma

`is_platform_admin` es una columna de `users` con default `false`. **Nadie la
tiene hasta que se marque a mano**, y no hay UI para dárselo a nadie — es
deliberado: si el panel pudiera crear admins de plataforma, cualquier toma de
esa cuenta escalaría a todos los clientes.

Se hace una vez, contra la base:

```sql
UPDATE users SET is_platform_admin = true WHERE email = 'tu@correo.com';
```

En local:

```bash
docker compose exec db psql -U lejovista -d lejovista -c "UPDATE users SET is_platform_admin = true WHERE email = 'tu@correo.com';"
```

En producción (Coolify), abrir una terminal contra el contenedor de Postgres del
recurso y correr el mismo `UPDATE`.

**Qué NO da este permiso:** acceso a los datos de ningún cliente. Un admin de
plataforma crea organizaciones, invita a su primer ADMIN y prende o apaga
módulos. No puede leer campañas, gastos ni el chat de nadie. Ese límite es lo
que mantiene en pie el aislamiento por organización.

---

## 1. Crear el cliente y elegir qué ve

1. Entrar a `/admin` (no está en el menú: se escribe la URL). Si no sos admin de
   plataforma, la pantalla lo dice; la API responde 403.
2. **Nuevo cliente**:
   - **Subdominio (slug)**: `cliente-nuevo`. Es el subdominio del tenant y
     **no se puede cambiar después** — renombrarlo dejaría fuera a todos sus
     usuarios. Minúsculas, números y guiones; `www`, `api`, `admin` y demás
     reservados se rechazan.
   - **Nombre**: la razón social o el nombre comercial.
   - **Email del primer ADMIN**: se le manda una invitación de un solo uso.
     Es opcional: se puede crear la organización e invitar después.
   - **Acento**: color de la marca del cliente.
3. **Crear cliente.**

### El enlace de invitación

Nadie escribe la contraseña del cliente: la elige él desde el enlace.

`send_email` es *best-effort*. Si `BREVO_API_KEY` no está configurada, la
invitación se crea igual pero **el correo no sale**. El panel distingue los dos
casos: cuando no salió, muestra el enlace con un botón de copiar para pasarlo
por WhatsApp o por donde sea. Vence en 7 días y se usa una sola vez.

### Elegir los módulos

Recién creado, el cliente tiene **todos** los módulos. En su fila del panel,
cada módulo es un botón: se apagan los que no contrató.

| Módulo | Clave | Pantalla |
|---|---|---|
| Resumen / dashboard | `resumen` | `/` |
| Campañas | `campanas` | `/campaigns` |
| Chat analista | `chat` | `/chat` |
| Creación de campañas por chat | `chat_creacion` | modo BUILD del chat |
| Reporte ejecutivo | `reporte` | `/reporte` |
| Diagnósticos de IA | `diagnosticos` | `/diagnostics` |
| Acciones sugeridas | `acciones` | `/actions` |
| Consumo de IA | `ai_usage` | `/ai-usage` |
| Configuración | `settings` | `/settings` — **no se puede apagar** |

`settings` no se apaga porque sin ella el cliente no puede conectar Meta ni
administrarse: quedaría sin forma de repararse solo.

**Para la vista recortada de cliente** (ocultar Resumen, Reporte, Diagnósticos y
Uso de IA), dejar encendidos: `campanas`, `chat`, `chat_creacion`, `acciones`.

Ocultar funciona en tres capas, y **solo la tercera es seguridad**:

1. El ítem desaparece del menú.
2. Escribir la URL a mano redirige a la primera pantalla habilitada — incluida
   la raíz `/`, que es el Resumen.
3. La API responde **403**. Sin esto el módulo estaría escondido, no apagado.

> Los módulos se cambian **solo desde `/admin`**. El ADMIN de la organización no
> puede tocarlos ni desde la UI ni por la API: si pudiera, el módulo dejaría de
> ser una decisión comercial y pasaría a ser una preferencia del cliente.

---

## 1b. Qué hace el cliente al recibir el enlace

1. Abre el enlace → elige su contraseña → entra ya como `ADMIN` de **su**
   organización.
2. Ve únicamente los módulos habilitados. Si el Resumen está apagado, la raíz lo
   lleva a Campañas.
3. Desde **Configuración** conecta su cuenta de Meta Ads (paso 3) y su clave de
   IA (paso 4).

---

## 2. Configuración de Marca y Personalización

1. Dirigirse a **Configuración** (`/settings`) → **Marca y Personalización**:
   - **Nombre comercial para mostrar**: Asignar el nombre público.
   - **Color de acento**: Elegir el color corporativo (ej. `#2563EB`).
   - **Logo de la empresa**: Subir archivo (PNG/JPG/SVG, máx 5MB).
2. Hacer clic en **Guardar Cambios**.
3. Verificar que el logo y nombre se reflejen de inmediato en el Sidebar y en la pantalla de Login del subdominio.

---

## 3. Conexión de Cuenta Publicitaria de Meta Ads

1. En **Configuración** → **Conectar Cuenta de Meta Ads**:
   - **Nombre Descriptivo**: Identificador claro (ej. `Campaña Principal 2026`).
   - **ID de Cuenta**: ID de Meta Ads (`act_123456789012345`). Si se ingresan solo dígitos, el sistema añade `act_` automáticamente.
   - **Access Token**: Token de larga duración generado desde Meta Graph API Explorer o Usuario del Sistema en Meta Business Manager con los scopes requeridos:
     `ads_read`, `ads_management`, `business_management`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`, `read_insights`, `whatsapp_business_management`.
2. Presionar **Conectar Cuenta**:
   - El sistema valida el token contra Meta Graph API antes de guardarlo.
   - Si es válido, se cifra con AES-256 y se encola la primera sincronización completa (`FULL`).

---

## 4. Carga de Credencial de Inteligencia Artificial (LLM)

1. En **Configuración** → **Proveedor de IA (LLM)**:
   - Elegir el proveedor del cliente: `OpenRouter`, `OpenAI` o `Gemini Direct`.
   - Pegar la API Key correspondiente (`sk-or-v1-...`, `sk-...`, etc.).
   - Hacer clic en **Probar API Key** para validar saldo y conectividad.
2. Tras la validación exitosa, presionar **Guardar Cambios**.
3. Seleccionar el modelo deseado para la cuenta (ej. `google/gemini-2.5-flash` o `openai/gpt-4o-mini`).

---

## 5. Reglas de Negocio, Políticas y Umbrales

1. En **Configuración**:
   - **Rubro del negocio**: Seleccionar la vertical (ej. `Inmobiliario`, `Educación`, `Salud`).
   - **Límite financiero mensual de seguridad**: Definir presupuesto máximo mensual (ej. `$1,000`).
   - **Reglas de campaña / Umbrales**: Ajustar si la cuenta opera en un geo específico, requiere objetivo de `CONVERSATIONS` o `LEAD_GENERATION`, y configurar límites de creación por ventana.
2. Hacer clic en **Guardar Cambios**.

---

## 6. Primer Sync y Verificación en Diagnósticos

1. En **Configuración** → **Cuentas Publicitarias Conectadas**, verificar el estado `ACTIVE` y el icono de sincronización.
2. Monitorear el progreso en el indicador superior o WebSocket.
3. Al completar el sync, verificar desde **tu propia** organización de operador
   (el cliente puede tener Resumen y Diagnósticos apagados):
   - **Resumen** (`/`): KPIs de gasto, impresiones, CPL y resultados.
   - **Diagnósticos IA** (`/diagnostics`): narrativa del motor, clasificación de
     campañas y anomalías.

### El primer sync trae TODO el historial

No son 30 días: el `BACKFILL` baja mes por mes hasta donde llegue la cuenta en
Meta. Mientras corre, un aviso en la app muestra cuántos meses faltan y una
estimación de tiempo — derivada del ritmo real de llamadas de esa cuenta, no de
una constante. Los sincronizados siguientes solo traen lo reciente y extienden
el histórico; nunca lo pisan.

Una cuenta con años de antigüedad puede tardar entre 3 y 25 minutos. El cliente
puede usar la app mientras tanto: el chat responde sobre lo que ya bajó y **lo
aclara** en vez de dar por ceros lo que todavía no tiene.
   - Abrir **Chat Analista** (`/chat`): Realizar preguntas en lenguaje natural sobre el rendimiento de la cuenta.

---

## 7. Invitación del Equipo de Trabajo

1. En **Configuración** → **Equipo y Miembros**:
   - Ingresar correo electrónico del colaborador.
   - Seleccionar rol: `Operador` (gestión y creación de campañas) o `Consultor (Viewer)` (solo lectura y chat analista).
   - **Marcar a qué cuentas publicitarias accede.** Sin marcar ninguna entra con
     todas las de la organización. Marcar acota.
   - Presionar **Invitar Usuario**.
2. El colaborador recibirá un correo con el link de un solo uso para establecer su contraseña e ingresar al espacio.

### Cambiar el rol o dar de baja a alguien

Desde **Equipo y Miembros**: el rol es un desplegable en la fila del miembro, y
**Quitar** lo saca de la organización (se lleva también sus concesiones de
cuentas). Dos protecciones: nadie puede quitarse a sí mismo, y no se puede dejar
la organización sin ningún ADMIN.

### Si el cliente pierde la contraseña

Ya no hace falta tocar la base: **¿Olvidaste tu contraseña?** en el login manda
un enlace de un solo uso que vence a los 30 minutos. Y con la sesión abierta se
cambia desde **Configuración → Mi cuenta**.

> Las dos cosas dependen del correo: sin `BREVO_API_KEY` el backend no envía
> nada. Ver §Correo abajo.

### El rol dice qué puede hacer; las cuentas dicen sobre qué

Son dos ejes distintos, y hasta 2026-08 solo existía el primero: cualquier
miembro veía **todas** las cuentas publicitarias de la organización. En una
organización que aloja las cuentas de varios clientes eso es un leak entre
clientes.

El acceso es una **concesión explícita por usuario**, y es fail-closed: un
usuario sin ninguna cuenta concedida no ve datos, y la app se lo dice en vez de
mostrarle un panel vacío. Se cambia en cualquier momento desde **Equipo y
Miembros** → **Cambiar**, en la fila del miembro.

> Al migrar, cada miembro conservó las cuentas que ya veía. Si una organización
> aloja cuentas de más de un cliente, hay que **quitar** las ajenas a mano — no
> se quitaron solas a propósito, para no dejar a nadie sin acceso en el
> despliegue.

### Páginas y números de WhatsApp

Van por el mismo eje: cada página y cada número pertenecen a una o más **cuentas
publicitarias**, no a la organización entera. El dropdown de "Crear campaña"
muestra solo los de la cuenta activa.

- **Páginas**: el enlace lo mantiene el sync y lo **poda** — no hay que tocar
  nada. Tras un ciclo de sync por cuenta, cada página queda con las cuentas que
  de verdad pueden promocionarla.
- **Números MANUAL**: no hay nada en Meta contra qué reconciliarlos, así que la
  migración los dejó alcanzables desde **todas** las cuentas de su organización.
  Se acotan en **Configuración → Páginas y WhatsApp** → *Editar* en el número,
  marcando las cuentas que corresponden.

---

## Correo transaccional (requisito, no opcional)

`BREVO_API_KEY` alimenta tres cosas: las invitaciones, los avisos de token de
Meta vencido y la recuperación de contraseña. Sin ella el backend **no envía
nada**.

Hasta 2026-08-25 fallaba en silencio por partida doble: `send_email` devolvía
`True` sin API key —igual que en modo mock— y la variable ni siquiera figuraba en
el `x-app-env` del compose de Coolify, así que aunque se cargara en el panel no
llegaba al contenedor. Las dos cosas están arregladas: la variable se pasa, y si
no está configurada el envío devuelve `False`, lo loguea como WARNING y la
pantalla de invitaciones lo dice en vez de afirmar que salió.

Para activarlo: cargar `BREVO_API_KEY` (y opcionalmente `EMAIL_FROM` /
`EMAIL_FROM_NAME`) en las Environment Variables del recurso de Coolify.
