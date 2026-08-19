# Runbook: Alta y Onboarding de un Nuevo Cliente (Tenant)

Este procedimiento describe el paso a paso reproducible para configurar y habilitar una nueva organización en LejoVista.

---

## 1. Creación de la Organización y Usuario Administrador

1. Acceder a `http://<slug>.localhost:3000/login` (o en producción `https://<slug>.lejovista.com/login`).
2. Seleccionar **Regístrate aquí**:
   - Nombre de la Organización: `Nombre Comercial S.A.C.`
   - Identificador (Slug): `nombre-cliente` (solo letras minúsculas, números y guiones; no usar palabras reservadas como `api`, `admin`, `www`).
   - Nombre del Administrador y correo electrónico corporativo.
   - Contraseña segura (mínimo 8 caracteres).
3. Tras registrarse, el usuario queda automáticamente como `ADMIN` de la nueva organización.

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
3. Al completar el sync:
   - Revisar **Resumen** (`/`): KPIs de gasto, impresiones, CPL y conversiones.
   - Revisar **Diagnósticos IA** (`/diagnostics`): Narrativa generada por el motor, clasificación de campañas y análisis de anomalías.
   - Abrir **Chat Analista** (`/chat`): Realizar preguntas en lenguaje natural sobre el rendimiento de la cuenta.

---

## 7. Invitación del Equipo de Trabajo

1. En **Configuración** → **Equipo y Miembros**:
   - Ingresar correo electrónico del colaborador.
   - Seleccionar rol: `Operador` (gestión y creación de campañas) o `Consultor (Viewer)` (solo lectura y chat analista).
   - Presionar **Invitar Usuario**.
2. El colaborador recibirá un correo con el link de un solo uso para establecer su contraseña e ingresar al espacio.
