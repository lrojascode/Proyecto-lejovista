# 📘 PLAYBOOK MAESTRO — Gestión de Meta Ads por IA
### Descarga de información · Diagnóstico · Creación de campañas · Anti-bloqueo
**Versión:** 1.0 · **Fecha:** 2026-07-05 · **Autor:** Alejandro Victorio (gestión de agencia)

> **Para quién es este documento:** cualquier IA (o persona) que vaya a operar cuentas de Meta Ads
> de este operador. Léelo COMPLETO antes de tocar nada. Está diseñado para que puedas
> **descargar info → diagnosticar → crear campañas** de una **marca nueva** sin errores y sin
> arriesgar la cuenta. Sigue el orden de las fases.

---

## 0. REGLA DE ORO (leer primero, siempre)

1. **Pide permiso explícito antes de usar el token o llamar a la API.** Nunca actúes sin luz verde.
2. **Antes de cualquier ESCRITURA (POST) informa el nivel de riesgo** (bajo / medio / alto) y espera aprobación.
3. **Crea todo en `status=PAUSED`.** La **activación la hace el humano manualmente** en Ads Manager.
   Nunca actives campañas/adsets/ads vía API (activar por API = acción visible automatizada = riesgo de bloqueo).
4. **Revisa siempre las campañas anteriores antes de crear** (aprende de la data real).
5. **Si una acción puede inhabilitar la cuenta, NO la hagas.** Dile al humano que la haga manual.
6. **Ante la duda, pausa y pregunta.**

---

## 1. PERMISOS Y ACCESOS NECESARIOS

### 1.1 Token de acceso (User Access Token de Meta / Facebook)
- El operador entrega un **token de usuario** al inicio de la sesión.
- **Caduca en ~1–2 horas.** Si la sesión tiene más de un par de horas, **pide uno nuevo**.
- El token va en cada request como parámetro `access_token=...` (o header `Authorization: Bearer`).
- **Nunca** lo publiques, lo subas a un repo, ni lo mandes a servicios externos.

### 1.2 Permisos (scopes) que debe tener el token
Para el flujo completo (leer + crear campañas) el token necesita:
| Scope | Para qué |
|---|---|
| `ads_read` | Descargar campañas, adsets, ads, insights, imágenes |
| `ads_management` | Crear/editar campañas, adsets, creativos, ads; subir imágenes |
| `business_management` | Leer Business Manager, páginas y activos del negocio |
| `pages_show_list` / `pages_read_engagement` | Listar y leer páginas conectadas (para CTWA / WhatsApp) |
| `pages_manage_ads` | Que la página pueda ser promocionada |
| `read_insights` | Métricas de página/anuncios |

Solo lectura/diagnóstico basta con `ads_read` + `pages_show_list` + `read_insights`.

### 1.3 Roles en Business Manager (los tiene el operador, no la IA)
- El **operador** debe tener rol **Admin o Anunciante** sobre la cuenta publicitaria y la página.
- La cuenta y la página deben estar en el **mismo Business Manager** (o compartidas) para CTWA.

### 1.4 Cómo verificar acceso antes de empezar (solo lectura, riesgo bajo)
```bash
TOKEN="EAAxxxx..."
# ¿Quién soy?
curl -s -G "https://graph.facebook.com/v21.0/me" \
  --data-urlencode "fields=id,name" --data-urlencode "access_token=$TOKEN"

# ¿A qué cuentas publicitarias tengo acceso?
curl -s -G "https://graph.facebook.com/v21.0/me/adaccounts" \
  --data-urlencode "fields=name,account_id,account_status,currency,business{id,name}" \
  --data-urlencode "limit=100" --data-urlencode "access_token=$TOKEN"

# ¿Qué páginas administro? (para conseguir page_id + WhatsApp)
curl -s -G "https://graph.facebook.com/v21.0/me/accounts" \
  --data-urlencode "fields=name,id,category,connected_instagram_account" \
  --data-urlencode "access_token=$TOKEN"
```
> `account_status`: **1 = Activa** · **2 = Inhabilitada** · 3 = No liquidada · 7 = En revisión · 9 = Gracia.

---

## 2. PARÁMETROS ANTI-BLOQUEO (obligatorios en TODA cuenta)

Meta puede **inhabilitar cuentas** si detecta comportamiento automatizado o inusual. Reglas:

1. **Consultar antes de usar el token.**
2. **Simular comportamiento humano:** no ráfagas. **Espaciar 20–30 s entre POSTs.** Máx **2–3 POSTs seguidos** sin pausa.
3. **Orden natural:** campaña → (espera) → adset → (espera) → creativo → (espera) → ad.
4. **Máx ~5 campañas nuevas por sesión.**
5. **Todo en PAUSED; activación manual por el humano** desde Ads Manager.
6. **Informar nivel de riesgo antes de cada acción sensible.**
7. **No apelar rechazos en bucle:** máximo **1 “Solicitar revisión”** por anuncio (apelar repetido levanta banderas).
8. **Subir el spend cap lo hace el humano** en Business Manager, no la IA.

**Niveles de riesgo de referencia:**
- 🟢 **Bajo:** GET de lectura (descargar info, insights, diagnóstico).
- 🟡 **Medio:** crear campaña/adset/creativo/ad en PAUSED, subir imagen.
- 🔴 **Alto:** activar entregas, cambiar presupuesto de campaña en marcha, editar público de campaña activa, apelar rechazos, eliminar objetos. → **preferir que lo haga el humano.**

---

## 3. FASE 1 — DESCARGA DE INFORMACIÓN (diagnóstico, riesgo 🟢 bajo)

Objetivo: bajar TODO lo necesario para entender la cuenta antes de crear nada.
Sustituye `ACT="act_XXXXXXXXXX"` y `TOKEN`. Guarda cada salida como `.json` (ej. en `gestion-ads-alejo\<Marca>\`).

### 3.1 Estado de la cuenta (¿congelada? ¿spend cap topado?)
```bash
curl -s -G "https://graph.facebook.com/v21.0/$ACT" \
  --data-urlencode "fields=name,account_status,disable_reason,balance,amount_spent,spend_cap,currency,funding_source_details" \
  --data-urlencode "access_token=$TOKEN"
```
> ⚠️ Si `spend_cap == amount_spent` → **cuenta topada, no entregará**. El humano debe subir el spend cap.

### 3.2 Campañas
```bash
curl -s -G "https://graph.facebook.com/v21.0/$ACT/campaigns" \
  --data-urlencode "fields=name,objective,status,effective_status,daily_budget,lifetime_budget,created_time,start_time,stop_time" \
  --data-urlencode "limit=200" --data-urlencode "access_token=$TOKEN"
```

### 3.3 Adsets (config y público)
```bash
curl -s -G "https://graph.facebook.com/v21.0/$ACT/adsets" \
  --data-urlencode "fields=name,campaign_id,optimization_goal,billing_event,bid_strategy,destination_type,daily_budget,lifetime_budget,end_time,status,promoted_object,targeting{age_min,age_max,geo_locations,flexible_spec,publisher_platforms,facebook_positions,instagram_positions,targeting_automation}" \
  --data-urlencode "limit=200" --data-urlencode "access_token=$TOKEN"
```

### 3.4 Ads + creativos (copy, CTA, imágenes que ya funcionan)
```bash
curl -s -G "https://graph.facebook.com/v21.0/$ACT/ads" \
  --data-urlencode "fields=name,status,adset_id,creative{name,object_story_spec,title,body,call_to_action_type,image_hash,effective_object_story_id}" \
  --data-urlencode "limit=100" --data-urlencode "access_token=$TOKEN"
```

### 3.5 Insights (rendimiento real = base del diagnóstico)
```bash
# A nivel campaña, todo el historial
curl -s -G "https://graph.facebook.com/v21.0/$ACT/insights" \
  --data-urlencode "level=campaign" --data-urlencode "date_preset=maximum" \
  --data-urlencode "fields=campaign_name,objective,spend,impressions,reach,clicks,ctr,cpc,cpm,actions,cost_per_action_type" \
  --data-urlencode "limit=100" --data-urlencode "access_token=$TOKEN"
```
Breakdowns útiles (añadir `--data-urlencode "breakdowns=..."`):
- `age,gender` — quién convierte más barato
- `publisher_platform,platform_position` — qué placement rinde (FB feed vs IG stories vs audience_network)
- `region` / `country` — dónde
- `impression_device` — mobile vs desktop

### 3.6 Imágenes en la cuenta (hashes ya subidos)
```bash
curl -s -G "https://graph.facebook.com/v21.0/$ACT/adimages" \
  --data-urlencode "fields=hash,name,url,width,height" --data-urlencode "access_token=$TOKEN"
```

---

## 4. FASE 2 — DIAGNÓSTICO (qué mirar en la data)

1. **¿La cuenta entrega?** Revisa `account_status` y spend cap (§3.1). Si está topada/congelada → avisar antes de crear.
2. **Detecta la campaña ganadora:** ordena por **costo por resultado** (para WhatsApp: `onsite_conversion.messaging_conversation_started_7d`).
   Métricas clave a comparar entre campañas:
   - **Costo/conversación** (o costo/lead) → el KPI principal.
   - **CPM** (más bajo = mejor eficiencia de entrega).
   - **CTR** (>2% bien; >5% excelente).
   - **Volumen** (conversaciones, leads).
3. **Extrae el patrón ganador:** objetivo, optimización, público (amplio vs segmentado), placements, edad, formato (imagen vs **video/UGC** — el video suele ganar).
4. **Detecta desperdicio:** placements/edades/dispositivos con S/0 conversiones → excluir en la nueva.
5. **Copy/creativo ganador:** guarda el `body`, `title` y CTA de la mejor campaña para replicar el estilo.
6. **Conclusión accionable:** “replico X estructura con Y creativo, cambio Z”. **La data manda sobre las hipótesis.**

> Ejemplo real (Althea Pack, jul-2026): público **amplio + Advantage+, solo Lima, 18-65, sin intereses**
> ganó (S/0.53/conversación, CPM S/8.68) a la versión con más CPM. Se descartó segmentar por intereses.
> Ejemplo real (Expert Farma): excluir `audience_network` y `facebook_reels`, solo mobile → bajó el costo.

---

## 5. FASE 3 — CREACIÓN DE CAMPAÑA (flujo 6 pasos, riesgo 🟡 medio)

> **Antes de empezar:** informa riesgo medio, confirma con el humano. Todo en **PAUSED**.
> **Espacia 20–30 s entre pasos.** Usa **siempre `--data-urlencode`** (no `-d`) para JSON anidado.

### Paso 0 — Subir la imagen (si es creativo nuevo con imagen local)
```bash
curl -s -F "filename=@/ruta/a/la/imagen.jpg" -F "access_token=$TOKEN" \
  "https://graph.facebook.com/v21.0/$ACT/adimages"
# → devuelve {"images":{"nombre.jpg":{"hash":"HASH_IMAGEN", ...}}}
```
> En Git Bash (Windows) la ruta va como `/c/Users/User/Desktop/.../imagen.jpg`. Rutas con espacios: entre comillas.
> Recomendado 1080×1080 o 1350×1350 (feed) — el arte cuadrado/vertical rinde mejor en mobile.

### Paso 1 — Crear campaña (PAUSED)
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/$ACT/campaigns" \
  --data-urlencode "name=MARCA_MSJ_PRODUCTO_MESAA" \
  --data-urlencode "objective=OUTCOME_LEADS" \
  --data-urlencode "status=PAUSED" \
  --data-urlencode "special_ad_categories=[]" \
  --data-urlencode "is_adset_budget_sharing_enabled=false" \
  --data-urlencode "access_token=$TOKEN"
# → {"id":"CAMPAIGN_ID"}
```
> **Objetivo:** `OUTCOME_LEADS` (para WhatsApp/mensajes) u `OUTCOME_ENGAGEMENT` (mensajes clásico). Ambos válidos según cuenta.
> ⚠️ `is_adset_budget_sharing_enabled=false` es **obligatorio** si NO usas presupuesto a nivel campaña (CBO), o da error 4834011.

### Paso 2 — Crear adset (PAUSED)
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/$ACT/adsets" \
  --data-urlencode "name=AD SET — MARCA_PRODUCTO_GEO" \
  --data-urlencode "campaign_id=CAMPAIGN_ID" \
  --data-urlencode "optimization_goal=CONVERSATIONS" \
  --data-urlencode "destination_type=WHATSAPP" \
  --data-urlencode "billing_event=IMPRESSIONS" \
  --data-urlencode "bid_strategy=LOWEST_COST_WITHOUT_CAP" \
  --data-urlencode "daily_budget=1000" \
  --data-urlencode "status=PAUSED" \
  --data-urlencode 'promoted_object={"page_id":"PAGE_ID"}' \
  --data-urlencode 'targeting={"age_min":18,"age_max":65,"geo_locations":{"cities":[{"key":"1696855","radius":40,"distance_unit":"kilometer"}],"location_types":["home","recent"]},"targeting_automation":{"advantage_audience":1}}' \
  --data-urlencode "access_token=$TOKEN"
# → {"id":"ADSET_ID"}
```
> `daily_budget=1000` = **S/10/día** (en centavos). Para presupuesto total usa `lifetime_budget` + `end_time` (ISO `2026-08-05T23:59:59-0500`).
> `geo_locations`: para todo Perú usa `{"countries":["PE"]}`. Lima ciudad = `cities` key `1696855`.
> **Advantage+** (`advantage_audience:1`) obliga `age_max:65`.

### Paso 3 — (Ya tienes el hash del Paso 0)

### Paso 4 — Crear creativo
```bash
SPEC='{
  "page_id":"PAGE_ID",
  "instagram_user_id":"IG_USER_ID",
  "link_data":{
    "image_hash":"HASH_IMAGEN",
    "name":"TÍTULO CORTO 🔥",
    "message":"COPY con emojis y saltos de línea \n...",
    "description":"Descripción corta · con puntos",
    "call_to_action":{"type":"WHATSAPP_MESSAGE","value":{"app_destination":"WHATSAPP","link":"https://api.whatsapp.com/send"}}
  }
}'
curl -s -X POST "https://graph.facebook.com/v21.0/$ACT/adcreatives" \
  --data-urlencode "name=CREAT_PRODUCTO_MESAA" \
  --data-urlencode "object_story_spec=${SPEC}" \
  --data-urlencode "access_token=$TOKEN"
# → {"id":"CREATIVE_ID"}
```
> Para **video**: usa `video_data` con `video_id` e `image_url` (thumbnail) en vez de `link_data`.
> El WhatsApp de destino lo define el **número conectado a la página**; `link` puede ser `https://api.whatsapp.com/send`.

### Paso 5 — Crear anuncio (PAUSED)
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/$ACT/ads" \
  --data-urlencode "name=AD_PRODUCTO_MESAA" \
  --data-urlencode "adset_id=ADSET_ID" \
  --data-urlencode 'creative={"creative_id":"CREATIVE_ID"}' \
  --data-urlencode "status=PAUSED" \
  --data-urlencode "access_token=$TOKEN"
# → {"id":"AD_ID"}
```

### Paso 6 — Corregir copy (si hace falta)
Crea un creativo nuevo con el copy corregido y reasígnalo:
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/AD_ID" \
  --data-urlencode 'creative={"creative_id":"NUEVO_CREATIVE_ID"}' \
  --data-urlencode "access_token=$TOKEN"
# → {"success":true}
```

### Entrega final al humano (SIEMPRE)
- Reporta los 4 IDs (campaña/adset/creativo/ad) y que todo quedó **PAUSED**.
- Recuérdale: **1) subir spend cap si está topado, 2) revisar en Ads Manager, 3) activar manualmente.**
- Link directo: `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=NUMERO_SIN_act&selected_campaign_ids=CAMPAIGN_ID`

---

## 6. PARÁMETROS ÓPTIMOS VERIFICADOS (baseline WhatsApp Perú)

| Parámetro | Valor recomendado | Nota |
|---|---|---|
| Objetivo campaña | `OUTCOME_LEADS` (o `OUTCOME_ENGAGEMENT`) | según lo que use la cuenta |
| Optimización adset | `CONVERSATIONS` | conversaciones de WhatsApp |
| destination_type | `WHATSAPP` | click-to-WhatsApp |
| billing_event | `IMPRESSIONS` | |
| bid_strategy | `LOWEST_COST_WITHOUT_CAP` | dejar que Meta optimice |
| Público | Amplio + Advantage+ (`advantage_audience:1`), sin intereses | ganó en B2C y B2B |
| Edad | 18–65 (o 25–65) | Advantage+ obliga max 65 |
| Geo | ciudad clave (Lima `1696855`) o país `PE` | según alcance del negocio |
| Placements | Automáticos (Advantage+) | excluir `audience_network`/`facebook_reels` si data muestra S/0 conv |
| Dispositivo | mobile | desktop suele dar 0 conv |
| Presupuesto | S/10/día ó S/300/30 días | arrancar controlado |
| Formato | **Video/UGC** > imagen estática | el video suele ganar |
| CTA | `WHATSAPP_MESSAGE` | |
| Status al crear | `PAUSED` | siempre |

**Estructura de copy ganadora:** DOLOR → PROMESA → BENEFICIOS (✅) → URGENCIA → CTA (📲) + hashtags.

---

## 7. ERRORES CONOCIDOS Y CÓMO EVITARLOS

| Síntoma / error | Causa | Solución |
|---|---|---|
| `is_adset_budget_sharing_enabled` requerido (subcode 4834011) | Falta el flag al crear campaña sin CBO | Añadir `is_adset_budget_sharing_enabled=false` |
| “object_story_spec inválido” | JSON mal escapado con `-d` | Usar **`--data-urlencode`**, no `-d` |
| Campaña no entrega (0 impresiones) | **Spend cap topado** o cuenta sin saldo | Humano sube spend cap / carga saldo |
| Anuncio rechazado por “medicamentos/farmacéuticos” | Copy con “fórmula magistral / receta médica / medicamento / dosis / tratamiento” | Reframear como cuidado personal/cosmético o (si vendes el ENVASE) hablar del producto físico, no del fármaco. Apelar máx 1 vez |
| Advantage+ ignora edad <65 | Advantage+ activo | Usar `age_max:65` |
| Routing a WhatsApp equivocado | promoted_object sin número | Especificar `whatsapp_phone_number` en promoted_object desde la creación |
| Token expira a mitad | Tokens duran ~1–2 h | Pedir token nuevo |
| Cuenta inhabilitada tras muchas acciones | Ráfaga de POSTs / activar por API | Espaciar, PAUSED, activar manual |

---

## 8. NOMENCLATURA

`MARCA_TIPO_PUBLICO_PRODUCTO_MESAA`
- **MARCA:** ALT (Althea), EF (Expert Farma), MTV, etc.
- **TIPO:** `MSJ` = mensajes/WhatsApp · `PRB` = prueba · `TRAF` = tráfico
- **PÚBLICO:** `PA`/`ABIERTA` = amplio · `SGMT` = segmentado
- **PRODUCTO/GEO:** nombre corto (PLATE46, BP=bajar de peso, LIMA)
- **MESAA:** mes+año (JUL26)

Ejemplo: `ALT_MSJ_PLATE46_JUL26`.

---

## 9. CHECKLIST PARA UNA MARCA NUEVA (de cero)

- [ ] 0. Pedir **token** y **permiso** para consultar.
- [ ] 1. Verificar acceso: `/me`, `/me/adaccounts`, `/me/accounts` (§1.4).
- [ ] 2. Identificar **cuenta publicitaria** (`act_...`) y **página** (`page_id`) + **WhatsApp** de la marca.
- [ ] 3. **Descargar info** (Fase 1): estado cuenta, campañas, adsets, ads+creativos, insights, imágenes.
- [ ] 4. **Diagnosticar** (Fase 2): ganadora, patrón, desperdicio, copy ganador, ¿cuenta topada?
- [ ] 5. Proponer plan al humano + **nivel de riesgo**; esperar OK.
- [ ] 6. **Crear** (Fase 3, PAUSED): imagen → campaña → adset → creativo → ad. Espaciar.
- [ ] 7. **Reportar IDs** + link Ads Manager + pendientes manuales (spend cap, activar).
- [ ] 8. **Guardar en memoria** los datos de la marca (cuenta, página, WhatsApp, data ganadora, IDs).
- [ ] 9. **No activar por API.** El humano revisa y activa.

---

## 10. DATOS DE CUENTAS CONOCIDAS (referencia rápida)

| Marca | Cuenta | Página / WhatsApp | Notas |
|---|---|---|---|
| Althea Pack | `act_1317785970189038` (Business Agencia Kibo) | Página `1040709642462236` · WA +51 923 882 598 · IG `17841429053503321` | B2B envases premium. Ganó amplio+Advantage+ Lima. Spend cap topado (jul-05) |
| Expert Farma | `act_852821811257195` | Farmacias `912702325256388` (WA 932 577 001 / 987 868 909) · Preparados `845080588695600` (WA 908 884 275) | B2C. Ojo: rechazos por lenguaje “fórmula magistral” |
| MTV Cables | (ver memoria MTV) | — | Conductores de cobre. Cuenta congelada por spend cap |

> Mantén esta tabla y las memorias `project_*` actualizadas cada vez que operes una marca.

---

**Fin del playbook.** Ante cualquier acción no cubierta aquí que pueda parecer automatización o violar políticas de Meta: **pausa y pregunta al humano.**
