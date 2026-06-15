Tengo una cuenta de Meta Ads y necesito que descargues TODA la información disponible
  vía API y construyas un HTML master brief de análisis y optimización.
  ════════════════════════════════════════
  PROTOCOLO DE SEGURIDAD — LEER PRIMERO
  ════════════════════════════════════════
  ANTES de ejecutar cualquier llamada API, informar:
    • Acción exacta que se va a realizar
    • Endpoint que se usará
    • Nivel de riesgo: 🟢 lectura / 🟡 escritura simple / 🔴 escritura crítica
    • Precauciones que se tomarán
    • Esperar confirmación antes de ejecutar
  REGLAS DE COMPORTAMIENTO HUMANO (anti-detección):
  1. TIMING VARIABLE — no usar pausas fijas (las pausas idénticas son un patrón de bot):
     - Entre lecturas GET: esperar entre 12 y 22 segundos (usar random.randint(12, 22))
     - Entre escrituras POST: esperar entre 25 y 40 segundos
     - Cada 3 llamadas: pausa larga de 45 a 75 segundos
     - Nunca hacer más de 5 llamadas seguidas sin pausa larga
  2. ORDEN ALEATORIO — no consultar endpoints siempre en el mismo orden:
     - Variar el orden de las queries cuando no hay dependencia entre ellas
     - Mezclar llamadas de distintos endpoints (no agrupar todas las insights juntas)
  3. LÍMITES POR SESIÓN:
     - Máximo 15 llamadas GET por sesión
     - Máximo 3 llamadas POST por sesión
     - Si se superan: pausar la sesión y continuar en una nueva conversación
     - Meta permite ~200 calls/hora por token, pero mantenerse muy por debajo
  4. SIN REINTENTOS AGRESIVOS:
     - Si una llamada falla con error 400/403/429: NO reintentar de inmediato
     - Esperar al menos 60 segundos antes de cualquier reintento
     - Si falla 2 veces: documentar el error, continuar con la siguiente query
     - Nunca usar loops de retry automáticos
  5. SOLO LECTURA para recopilación de datos (GET únicamente):
     - Prohibido POST/PUT/DELETE durante la fase de análisis
     - Las escrituras (crear campañas) van en una sesión separada y posterior
     - Máximo 2–3 POSTs por sesión de creación, con confirmación entre cada uno
  6. HEADERS QUE IMITAN NAVEGADOR (en urllib):
     headers = {
       'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/124.0.0.0 Safari/537.36',
       'Accept': 'application/json',
       'Accept-Language': 'es-PE,es;q=0.9,en;q=0.8',
       'Connection': 'keep-alive'
     }
     # Aplicar estos headers a CADA llamada urllib
  7. TOKENS Y SESIONES:
     - Los tokens de acceso expiran cada ~2 horas
     - Si el token expira a mitad de sesión: pausar, pedir token nuevo, esperar 30s antes de continuar
     - No usar el mismo token en dos sesiones simultáneas
     - Nunca loguear ni guardar tokens en archivos de texto plano
  8. HORARIOS SEGUROS:
     - Preferir consultas en horario laboral Lima (9am–6pm hora Perú)
     - Evitar hacer muchas llamadas de madrugada (patrón de bot evidente)
     - Esta regla aplica especialmente para sesiones de escritura (crear campañas)
  9. CÓDIGO BASE PARA TODAS LAS LLAMADAS:
  import urllib.request, urllib.parse, json, sys, time, random
  def llamada_api(endpoint, params, es_post=False, es_critica=False):
      """
      Wrapper seguro para llamadas a Meta Graph API.
      Incluye headers de navegador, encoding UTF-8 y manejo de errores.
      """
      TOKEN = params.get('access_token', '')
      params_encoded = urllib.parse.urlencode(params)
      url = f'https://graph.facebook.com/v19.0/{endpoint}'
      headers = {
          'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/124.0.0.0 Safari/537.36'),
          'Accept': 'application/json',
          'Accept-Language': 'es-PE,es;q=0.9,en;q=0.8',
          'Connection': 'keep-alive'
      }
      try:
          if es_post:
              req = urllib.request.Request(
                  url,
                  data=params_encoded.encode('utf-8'),
                  headers=headers,
                  method='POST'
              )
          else:
              req = urllib.request.Request(
                  f'{url}?{params_encoded}',
                  headers=headers,
                  method='GET'
              )
          with urllib.request.urlopen(req, timeout=30) as r:
              data = json.loads(r.read().decode('utf-8'))
              return data
      except urllib.error.HTTPError as e:
          error_body = e.read().decode('utf-8')
          return {'ERROR': e.code, 'detalle': json.loads(error_body)}
      except Exception as e:
          return {'ERROR': 'excepcion', 'detalle': str(e)}
  def pausa_humana(tipo='get', num_llamada=0):
      """
      Pausa con variación aleatoria para imitar comportamiento humano.
      tipo: 'get' | 'post' | 'larga'
      """
      if tipo == 'post':
          segundos = random.randint(25, 40)
          print(f'  ⏳ Pausa POST: {segundos}s...')
      elif tipo == 'larga' or num_llamada % 3 == 0:
          segundos = random.randint(45, 75)
          print(f'  ⏳ Pausa larga (cada 3 calls): {segundos}s...')
      else:
          segundos = random.randint(12, 22)
          print(f'  ⏳ Pausa GET: {segundos}s...')
      time.sleep(segundos)
  ════════════════════════════════════════
  DATOS DE LA CUENTA
  ════════════════════════════════════════
  - Token de acceso: [PEGA EL TOKEN AQUI]
  - Account ID: act_[ID_AQUI]
  - Page ID: [PAGE_ID_AQUI]
  - Nombre del cliente: [NOMBRE]
  - Producto/servicio: [DESCRIPCION]
  - WhatsApp(s): [NUMEROS SIN + NI ESPACIOS]
  ════════════════════════════════════════
  QUERIES A EJECUTAR (máx 15 GET por sesión)
  ════════════════════════════════════════
  Ejecutar en este orden, usando llamada_api() y pausa_humana() entre cada una.
  Si se alcanzan 15 llamadas, pausar y reportar avance antes de continuar.
  1.  Info de la cuenta (spend_cap, amount_spent, currency, timezone, account_status)
  2.  Campañas con insights completos (spend, impressions, reach, actions,
      cost_per_action_type, ctr, frequency) — date_preset: maximum
  3.  Breakdown edad/género (breakdowns: age, gender — date_preset: maximum)
  4.  Breakdown plataforma/placement (publisher_platform, platform_position)
  5.  Breakdown dispositivo (impression_device)
  6.  Estructura adsets (targeting, promoted_object, budget, status, end_time)
  7.  Imágenes en la cuenta (hash, name, url, width, height, created_time)
  8.  Audiencias personalizadas (id, name, subtype, time_created, description)
  9.  Tendencia semanal — últimos 90 días (time_increment: 7)
  10. Datos diarios último mes (time_increment: 1, date_preset: last_30d)
  11. Breakdown regional/geográfico (breakdowns: region — date_preset: maximum)
  12. Info de la página (fan_count, followers_count, category, about, website)
  13. Lista campañas con IDs, nombres y fechas de creación
  14. [Opcional] Breakdown hora del día si hay suficiente historial
  15. [Opcional] Breakdown día de semana con time_range explícito
  NO usar curl — solo Python urllib. Nunca requests ni librerías externas.
  Usar sys.stdout.buffer.write(json.dumps(..., ensure_ascii=False).encode('utf-8'))
  para salida sin errores de encoding en Windows.
  ════════════════════════════════════════
  ESTRUCTURA DEL HTML MASTER BRIEF
  ════════════════════════════════════════
  Con todos los datos recopilados, construir HTML con:
  1.  Header con datos clave + tags de contexto
  2.  Alerta crítica (spend cap, cuenta suspendida, campañas sin conversiones, etc.)
  3.  Comparación: período anterior / período actual / próximo objetivo (KPIs)
  4.  Ranking completo de campañas ordenadas por CPL
  5.  Análisis detallado de campañas recientes (curva de aprendizaje diaria)
  6.  Tendencia semanal con diagnóstico de causas de subida/bajada
  7.  Segmentación edad/género con insights accionables
  8.  Plataformas y placements (% gasto + CPL por cada uno, recomendaciones)
  9.  Dispositivos (Android vs iPhone vs Desktop)
  10. Geografía (qué regiones convierten, cuáles desperdician presupuesto)
  11. Audiencias personalizadas existentes y oportunidades nuevas
  12. Creativos e imágenes (hashes, formatos disponibles, rendimiento)
  13. Parámetros optimizados completos (campaña + adset + placements en código)
  14. Fórmula y ejemplos de copy ganador
  15. Guía API 6 pasos con código Python completo, con pausa_humana() integrada
  16. Errores conocidos y sus fixes exactos
  17. Plan de acción priorizado (urgente + esta semana + próximo mes)
  18. Reglas de monitoreo (señales positivas / alerta / pausar inmediatamente)
  Nivel de detalle y diseño visual igual a:
  C:\Users\User\gestion-ads-alejo\MTV Cables\master-brief-mtv-jun2026.html
  Guardar en:
  C:\Users\User\gestion-ads-alejo\[NOMBRE_CLIENTE]\master-brief-[CLIENTE]-[MES][AÑO].html