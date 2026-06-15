PASO 0 — ANTES DE TOCAR LA API
  ════════════════════════════════════════
  1. Lee el brief master del cliente:
     [RUTA AL HTML — ejemplo: C:\Users\User\gestion-ads-alejo\MTV Cables\master-brief-mtv-jun2026.html]
  2. Del brief extrae y confirma conmigo ANTES de ejecutar:
     - Account ID y Page ID
     - Parámetros ganadores (CPL histórico, placements, audiencia)
     - Números WhatsApp disponibles por zona
     - Hashes de imágenes activas en la cuenta
     - Errores conocidos a evitar
     - Estado del spend cap (si está agotado, avisar antes de continuar)
  3. Presenta un resumen de la campaña que vas a crear con este formato:
     ┌─────────────────────────────────────────┐
     │ CAMPAÑA A CREAR — RESUMEN PARA APROBAR  │
     ├─────────────────────────────────────────┤
     │ Nombre campaña: ...                     │
     │ Producto/Ángulo: ...                    │
     │ Zona / WhatsApp: ...                    │
     │ Presupuesto: S/... por ... días         │
     │ Imagen: [nueva / hash existente]        │
     │ Placement: Feed + Story + Reels         │
     │ Copy propuesto: [primeras 2 líneas]     │
     │ CPL esperado basado en historial: S/... │
     │ Riesgo: 🟢/🟡/🔴                       │
     └─────────────────────────────────────────┘
     Esperar confirmación explícita antes de continuar.
  ════════════════════════════════════════
  DATOS DE ENTRADA (yo te proporciono)
  ════════════════════════════════════════
  - Token de acceso: [PEGA TOKEN AQUI]
  - Imagen: [RUTA LOCAL o URL de la imagen]
  - Producto a promocionar: [ej: Cable GPT / Vulcanizado / THHN / etc.]
  - Zona de la tienda: [Cercado / Surquillo / ambas]
  - Presupuesto: S/[MONTO] por [DIAS] días
    (si no lo especifico, usar el valor ganador del brief)
  ════════════════════════════════════════
  PROTOCOLO DE SEGURIDAD — ESCRITURA
  ════════════════════════════════════════
  Para operaciones POST (escritura), reglas más estrictas que en lectura:
  CONFIRMACIÓN OBLIGATORIA antes de cada POST:
    Acción: [qué se va a crear]
    Endpoint: [URL exacta]
    Riesgo: 🟡 POST — escritura en la cuenta
    Parámetros clave: [nombre, presupuesto, WhatsApp, imagen]
    ¿Confirmas? → Esperar "sí" explícito
  TIMING PARA POSTS (más conservador):
    - Entre POST y POST: random.randint(30, 50) segundos
    - Después de un error en POST: esperar mínimo 90 segundos
    - Máximo 3 POSTs por sesión sin importar qué
    - Si hay error en cualquier POST: pausar toda la sesión y reportar
  CÓDIGO BASE PARA POSTS:
  import urllib.request, urllib.parse, json, sys, time, random
  def post_api(endpoint, data, descripcion):
      TOKEN = data.get('access_token', '')
      headers = {
          'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/124.0.0.0 Safari/537.36'),
          'Accept': 'application/json',
          'Accept-Language': 'es-PE,es;q=0.9,en;q=0.8',
          'Content-Type': 'application/x-www-form-urlencoded',
          'Connection': 'keep-alive'
      }
      url = f'https://graph.facebook.com/v19.0/{endpoint}'
      try:
          req = urllib.request.Request(
              url,
              data=urllib.parse.urlencode(data).encode('utf-8'),
              headers=headers,
              method='POST'
          )
          with urllib.request.urlopen(req, timeout=30) as r:
              result = json.loads(r.read().decode('utf-8'))
              print(f'✅ {descripcion}: ID = {result.get("id", result)}')
              return result
      except urllib.error.HTTPError as e:
          err = json.loads(e.read().decode('utf-8'))
          print(f'❌ Error en {descripcion}: {err}')
          return {'ERROR': e.code, 'detalle': err}
  ════════════════════════════════════════
  FLUJO DE CREACIÓN — 6 PASOS
  (cada paso requiere confirmación + pausa)
  ════════════════════════════════════════
  PASO 1 — Verificar cuenta antes de crear nada
    GET /act_[ID]/adaccounts?fields=amount_spent,spend_cap,account_status
    → Si spend_cap = amount_spent: DETENER y avisar. Sin balance no tiene sentido crear.
    → Si account_status ≠ 1: DETENER. Cuenta suspendida o con restricciones.
  PASO 2 — Subir imagen (si es nueva)
    POST /act_[ID]/adimages
    → Leer la imagen del path local que el usuario indicó
    → Guardar el hash resultado para usarlo en el creativo
    → Si la imagen ya existe en la cuenta (mismo nombre o hash), usar el existente
    → Formatos aceptados: JPG, PNG — máx 30MB
    → Dimensión recomendada: 1620×1620 (1:1) para feed
      También subir versión 4:5 (1620×2025) si está disponible
    pausa: random.randint(30, 50) segundos
  PASO 3 — Crear campaña
    POST /act_[ID]/campaigns
    Parámetros fijos (validados por el brief):
    {
      "name": "MTV_MSJ_[PRODUCTO][ZONA][MES][AÑO]",
      "objective": "OUTCOME_LEADS",
      "buying_type": "AUCTION",
      "status": "PAUSED",
      "special_ad_categories": []
    }
    Regla: SIEMPRE crear en PAUSED. Nunca crear activa.
    Regla: Nombre sin tildes, ñ, emojis ni caracteres especiales.
    pausa: random.randint(30, 50) segundos
  PASO 4 — Crear Ad Set
    POST /act_[ID]/adsets
    Parámetros fijos (ganadores del brief):
    {
      "name": "[CAMPAÑA]_AS01",
      "campaign_id": "[ID_DEL_PASO_3]",
      "optimization_goal": "CONVERSATIONS",
      "destination_type": "WHATSAPP",
      "billing_event": "IMPRESSIONS",
      "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
      "lifetime_budget": [MONTO × 1000000],
      "start_time": "[FECHA_HOY]",
      "end_time": "[FECHA_HOY + DIAS]",
      "status": "ACTIVE",
      "targeting": {
        "geo_locations": {"regions": [{"key": "2771"}]},
        "age_min": 25,
        "age_max": 65,
        "publisher_platforms": ["facebook"],
        "facebook_positions": ["feed", "story", "facebook_reels"],
        "targeting_automation": {"advantage_audience": 1}
      },
      "promoted_object": {
        "page_id": "[PAGE_ID]",
        "whatsapp_phone_number": "[NUMERO_SIN_+_NI_ESPACIOS]"
      }
    }
    Regla: Geo SIEMPRE Lima Región key:2771. NUNCA Perú entero.
    Regla: SIEMPRE incluir facebook_reels (S/1.48/conv vs S/2.80 feed).
    Regla: NUNCA incluir instagram_positions (S/6-7/conv, ineficiente).
    Regla: whatsapp_phone_number va aquí en el adset, NO en el creative.
    pausa: random.randint(30, 50) segundos
  PASO 5 — Crear Creativo
    POST /act_[ID]/adcreatives
    Usar el copy optimizado generado en PASO 0 (formato validado):
    - Línea 1: hook con problema/necesidad (sin tildes es OK aquí)
    - Líneas 2-6: bullets con beneficios concretos del producto
    - Línea 7: dirección de la tienda
    - Línea 8: CTA al WhatsApp
    {
      "name": "MTV_CREATIVE_[PRODUCTO]_[MES][AÑO]",
      "object_story_spec": {
        "page_id": "[PAGE_ID]",
        "link_data": {
          "image_hash": "[HASH_DEL_PASO_2]",
          "message": "[COPY COMPLETO]",
          "call_to_action": {
            "type": "WHATSAPP_MESSAGE",
            "value": {"app_destination": "WHATSAPP"}
          },
          "page_welcome_message": {
            "type": "VISUAL_EDITOR",
            "version": 2,
            "landing_screen_type": "welcome_message",
            "media_type": "TEXT",
            "text_format": {
              "customer_action_type": "AUTOFILL_MESSAGE",
              "message": {
                "autofill_message": {"content": "Hola, quiero cotizar"},
                "text": "Hola! Con gusto te brindamos informacion y precios."
              }
            }
          }
        }
      }
    }
    Regla: page_welcome_message es OBLIGATORIO. Sin él la API rechaza el creativo.
    Regla: call_to_action value = {"app_destination": "WHATSAPP"}, no un link.
    pausa: random.randint(30, 50) segundos
  PASO 6 — Crear Ad
    POST /act_[ID]/ads
    {
      "name": "MTV_AD_[PRODUCTO][ZONA][MES][AÑO]",
      "adset_id": "[ID_DEL_PASO_4]",
      "creative": {"creative_id": "[ID_DEL_PASO_5]"},
      "status": "ACTIVE"
    }
  ════════════════════════════════════════
  REPORTE FINAL (obligatorio al terminar)
  ════════════════════════════════════════
  Al completar los 6 pasos, presentar:
  ┌──────────────────────────────────────────────────┐
  │ CAMPAÑA CREADA — RESUMEN FINAL                   │
  ├──────────────────────────────────────────────────┤
  │ Campaign ID:  [ID]  → [NOMBRE]                   │
  │ Ad Set ID:    [ID]  → [NOMBRE]                   │
  │ Creative ID:  [ID]  → [NOMBRE]                   │
  │ Ad ID:        [ID]  → [NOMBRE]                   │
  │                                                  │
  │ Imagen hash: [HASH]                              │
  │ WhatsApp vinculado: +51 [NUMERO]                 │
  │ Presupuesto: S/[MONTO] hasta [FECHA]             │
  │ Placements: Feed + Story + Reels                 │
  │ Estado: PAUSADA — activar manualmente            │
  │                                                  │
  │ PRÓXIMOS PASOS:                                  │
  │ 1. Verificar en Ads Manager que todo se ve bien  │
  │ 2. Confirmar WhatsApp vinculado en el adset      │
  │ 3. Activar campaña manualmente desde el Manager  │
  │ 4. Monitorear las primeras 48h (CPL objetivo)    │
  └──────────────────────────────────────────────────┘
  Guardar los IDs creados en:
  [RUTA_CLIENTE]\campanas-creadas-log.txt
  (para poder hacer seguimiento y modificaciones futuras)
  ════════════════════════════════════════
  QUÉ NO HACER — LÍMITES DE ESTA SESIÓN
  ════════════════════════════════════════
  ❌ No activar campañas vía API (siempre manual desde Ads Manager)
  ❌ No crear más de 3 campañas en una misma sesión
  ❌ No modificar campañas existentes sin confirmación explícita
  ❌ No aumentar presupuestos sin que yo lo pida
  ❌ No duplicar adsets o ads sin confirmación
  ❌ No cambiar el estado de campañas que ya existían antes