# 16 · Decisiones Arquitectónicas (ADR implícitas)

Decisiones de diseño **inferidas del código** y su justificación. No existen ADRs formales en el repo; este documento las reconstruye.

## ADR-01 · Snapshots diarios idempotentes como modelo analítico
- **Contexto**: hay que analizar tendencias de campañas a lo largo del tiempo.
- **Decisión**: guardar una fila por entidad (campaña/adset/anuncio) y día, con *upsert* por clave natural `(cuenta, entidad, fecha)`.
- **Por qué**: idempotencia frente a syncs repetidos, consultas por rango de fechas triviales, y un modelo simple sin data warehouse.
- **Consecuencia**: crecimiento sin límite → se necesitó el **archivado** (`D-3`). Los IDs de Meta no son FKs (entidades que pueden no existir localmente).

## ADR-02 · Human-in-the-loop y triple gating de escrituras
- **Decisión**: las acciones se **proponen** y requieren aprobación; las escrituras reales están gateadas por `MOCK_SERVICES` + `ENABLE_META_WRITEBACK` + `WRITEBACK_ACCOUNT_ALLOWLIST`; las campañas se crean `PAUSED`.
- **Por qué**: la IA puede gastar dinero real; un error de parámetros es costoso e irreversible.
- **Consecuencia**: encendido gradual por cuenta piloto (`F-2`); overhead de aprobación por diseño.

## ADR-03 · La IA nunca inventa cifras (`AI-1`)
- **Decisión**: todo número sale de datos reales agregados en DB; el LLM solo redacta texto. El clasificador, el CPL objetivo, el riesgo, el sugeridor y el veredicto de outcome son **código determinista**.
- **Por qué**: un informe que decide gasto no puede basarse en alucinaciones; auditabilidad.
- **Consecuencia**: el LLM es opcional con fallback local; la lógica de negocio es testeable como funciones puras.

## ADR-04 · Cliente LLM unificado con auto-selección de proveedor
> **Su mecanismo de selección quedó DEROGADO por [ADR-26](#adr-26-el-proveedor-y-el-modelo-de-ia-son-configuración-explícita-no-una-deducción-spec-10).** El cliente unificado y la portabilidad se conservan; la elección del proveedor pasó a ser explícita por Organización. Lo de abajo describe el estado anterior.

- **Decisión**: un `OpenRouterClient` contra endpoints OpenAI-compatibles; el proveedor se elige por qué API key esté presente (OpenAI > Gemini > OpenRouter).
- **Por qué**: portabilidad entre proveedores sin reescribir la integración; migrar es cambiar una env var.
- **Consecuencia**: normalización de nombres de modelo (`google/…`), y un selector que solo ofrece modelos del proveedor activo (`AI-4`).
- **Por qué se derogó**: "migrar es cambiar una env var" resultó ser el problema, no la ventaja. Cambiarla migraba a **todas** las organizaciones sin clave propia a la vez, en el siguiente reinicio, sin que nadie lo decidiera ni quedara registro.

## ADR-05 · Colas Celery dedicadas (`I-2`)
- **Decisión**: separar `sync`, `ai`, `creation` (+ `celery` de mantenimiento) en workers independientes.
- **Por qué**: un pico de syncs (que hacen 12-15 llamadas a Meta) no debe retrasar la ejecución de acciones aprobadas ni la creación de campañas.
- **Consecuencia**: se escalan por separado; más contenedores que operar.

## ADR-06 · Saga por pasos con `celery.chain` para crear campañas
- **Decisión**: la creación es un `chain` de 5 tareas (imagen → campaña → adset → creativo → anuncio) con `countdown=40s` entre pasos y `on_error`.
- **Por qué**: la creación tiene múltiples llamadas dependientes a Meta; la pausa reduce el riesgo de bloqueo de cuenta; cada paso persiste su avance para un polling real.
- **Consecuencia**: latencia de creación de varios minutos (aceptable, es asíncrono); el estado vive en `CampaignDraft.payload["result"]`.

## ADR-07 · Debounce de análisis con lock Redis (`D-1`)
- **Decisión**: tras completar un sync, agendar el análisis con `SET NX EX`; solo el primero de la org lo encola.
- **Por qué**: una org con N cuentas disparaba N análisis, cada uno iterando todas las cuentas y llamando al LLM → hasta N² llamadas.
- **Consecuencia**: un análisis diferido 5 min tras el primer sync; fail-open (mejor un análisis de más que ninguno).

## ADR-08 · Cache-aside del ReportBuilder (`B-2`)
- **Decisión**: cachear el reporte en Redis por `(cuenta, ventana)` con TTL 600 s; invalidar tras cada sync.
- **Por qué**: `ReportBuilder.build` se llama varias veces en el flujo de creación (`/suggestion`, `/risk-preview` al tipear, `/create`) y en el brief.
- **Consecuencia**: fail-open (si Redis cae, se construye sin cache).

## ADR-09 · Métrica primaria unificada: conversaciones con fallback a leads (`E-2`)
- **Decisión**: en todo el sistema, la conversión relevante es `conversations or conversions` (mensajes de WhatsApp; leads si no hay mensajería).
- **Por qué**: el producto vende campañas de mensajería a WhatsApp; unificar evita que `/diagnostics` y `/reporte` muestren números distintos.

## ADR-10 · Clasificador único relativo al `target_cpl` dinámico (`C-2`)
- **Decisión**: `classify_campaign` y `compute_target_cpl` centralizados en `thresholds.py`; el CPL objetivo se calibra con el histórico real de la cuenta (con piso/techo).
- **Por qué**: antes cada consumidor tenía su umbral absoluto en soles y la misma campaña salía "Buena" en un lado y "FALLIDA" en otro.

## ADR-11 · Multi-tenant por subdominio + aislamiento por cuenta
- **Decisión**: tenant = subdominio (`X-Tenant-Slug`) validado contra la pertenencia del usuario (`S-8`); cuenta activa vía `X-Ad-Account-Id` (`get_active_ad_account_id`).
- **Por qué**: separar clientes y, dentro de un cliente, no mezclar datos entre varias cuentas publicitarias.
- **Consecuencia**: 400 explícito si hay 2+ cuentas y no se elige una (en vez de "la primera" en silencio).

## ADR-12 · Una imagen de backend para API + workers + beat
- **Decisión**: misma imagen, distinto `command`.
- **Por qué**: simplifica build/publish/deploy y garantiza paridad de dependencias.
- **Consecuencia**: la imagen incluye todo (API y workers) aunque cada contenedor use un subconjunto.

## ADR-13 · Fail-open en la capa Redis, **excepto** en seguridad y write-back
- **Decisión original**: throttle, blocklist, cache y pubsub degradan sin romper la request.
- **Por qué**: Redis es soporte, no fuente de verdad; la disponibilidad del producto no debe depender de él (excepto como broker de Celery).
- **Corrección (spec 01)**: la disponibilidad **no** puede tener prioridad sobre revocación ni sobre autorización financiera. Cuando no se puede comprobar la validez de una sesión, fallan **cerrado**:
  - renovación de sesión (`POST /auth/refresh`),
  - aprobación de una AIAction y creación de campaña (`require_verified_session`),
  - toda escritura real hacia Meta (compuerta única en `MetaAPIRateLimiter`).
- **Lo que sigue fail-open**: throttle, cooldown, cache del ReportBuilder, debounce del análisis, pubsub y el consumo del ticket de WebSocket. Y la **lectura** del histórico: una caída del cache no puede dejar al cliente sin producto.
- **Consecuencia**: la revocación tiene dos mecanismos con disponibilidad distinta — el corte por usuario (`users.sessions_valid_from`, en PostgreSQL, siempre comprobable) y la blocklist por `jti` (en Redis). El primero se comprueba antes que el segundo, para que un token revocado en bloque reciba 401 y no un 503 genérico.

## ADR-14 · La suspensión de una Organización es una compuerta de backend (spec 01)
- **Contexto**: `Organization.status` se podía fijar a `SUSPENDED` y solo la ejecución de una AIAction lo miraba. Login, API, sync, IA y write-back seguían operando.
- **Decisión**: la suspensión se evalúa en los cuellos por los que pasa cada capacidad — `get_current_user` (login y API), `organization_gate` (sync e IA en workers) y `MetaAPIRateLimiter` (write-back) — nunca por endpoint.
- **Por qué**: una compuerta por endpoint se olvida en el siguiente endpoint que alguien añada.
- **Excepción**: el administrador de plataforma la atraviesa; necesita entrar justamente a las organizaciones suspendidas para exportar, investigar o reactivarlas.
- **Consecuencia**: suspender revoca además las credenciales de Meta (`REVOKED`) y corta las sesiones vivas de los miembros.

## ADR-15 · El límite mensual es una compuerta de backend, y el dinero que autoriza es exacto (spec 02)
- **Contexto**: `Organization.max_budget` existía, se mostraba y **ningún camino de ejecución lo consultaba**. La promesa financiera que veía el cliente no correspondía a ninguna garantía.
- **Decisión**: compuerta evaluada antes de comprometer gasto en las tres superficies que lo hacen — crear campaña (dentro de `start_campaign_creation`, el único camino de escritura real, para que el chat no la evite), aprobar una AIAction y escalar un presupuesto.
- **Ámbito y ventana**: la Organización (aplicarlo por cuenta permitiría multiplicar el techo abriendo cuentas) y el mes calendario **en la zona horaria del negocio** (`BUSINESS_TIMEZONE`), no en UTC: en Perú (UTC-5) las últimas cinco horas de cada día ya son el día siguiente en UTC, y con eso el gasto de fin de mes caía fuera de la ventana.
- **Consumo**: gasto real sincronizado **más** el presupuesto comprometido por campañas creadas en el mes que aún no acumulan gasto. Sumar solo el gasto real dejaría crear veinte campañas el mismo día.
- **Aritmética**: `Decimal` en todo lo que decide (`app/services/money.py`). Float sigue valiendo para métricas descriptivas. Lo que el Decimal elimina no es un error visible en una comparación suelta —a las magnitudes que admite `NUMERIC(14,2)` las dos aritméticas coinciden— sino la **acumulación** sobre cientos de snapshots y la dependencia de la magnitud.
- **Concurrencia**: la comprobación toma un bloqueo de fila sobre la Organización (`SELECT … FOR UPDATE`) que se mantiene hasta confirmar el compromiso. Sin él, dos autorizaciones simultáneas leen el mismo consumo y pasan las dos — comprobado contra PostgreSQL, donde la carrera ocurre de verdad.
- **Consecuencia**: extiende ADR-02 con una cuarta compuerta. Rebajar el límite bloquea creación y escalado pero **no pausa** campañas activas: apagar gasto en curso es una decisión humana.

## ADR-16 · La autorización es un objeto de un solo uso sobre una versión inmutable (spec 03)
- **Contexto**: la aprobación no estaba atada a nada. Se podía aprobar una propuesta, modificarla y ejecutar una versión distinta de la autorizada, sin rastro de la discrepancia; y no existía la noción de "consumida", así que la misma aprobación podía dar lugar a más de una ejecución.
- **Decisión**: el `CampaignDraft` sigue siendo el contenedor mutable; lo que se aprueba es una `CampaignProposalVersion` inmutable. La `CampaignApproval` se liga a UNA versión y se consume con un `UPDATE` condicional en la misma operación que lanza la Saga.
- **Cambio material**: definido por una lista cerrada de campos (`CAMPOS_MATERIALES`), no por criterio de quien lea. Ampliarla es un cambio de comportamiento visible. Un cambio material crea versión nueva, invalida la aprobación anterior y vuelve a ejecutar presupuesto, riesgo y preflight.
- **Consecuencia**: reejecutar una autorización consumida devuelve el resultado de la primera ejecución en vez de lanzar otra Saga.

## ADR-17 · Ninguna escritura hacia Meta se emite sin registro previo, y una respuesta incierta no se reintenta (spec 03)
- **Contexto**: ninguna llamada de creación es idempotente del lado de Meta. Repetir un POST tras un timeout crea una segunda entidad; no repetirlo deja una campaña huérfana que nadie sabe que existe. Sin un tercer estado, toda política de reintentos elige entre duplicar y abandonar.
- **Decisión**: `MetaWriteIntent` se registra ANTES del POST, con una clave lógica derivada de la autorización más el paso. Tres estados: `PENDING`, `CONFIRMED` y `VERIFYING`. Una intención confirmada devuelve su identificador sin volver a escribir; una incierta queda en `VERIFYING` y **no** autoriza reintento.
- **Reconciliación**: salir de `VERIFYING` exige preguntarle a Meta por la huella de la intención (nombre determinista + ventana temporal de una hora). **Ante ambigüedad no se adopta nada**: dos candidatos dejan la intención en verificación y exigen una persona. Adoptar una entidad ajena haría que la Saga colgara conjuntos de la campaña de otro.
- **Corrige ADR-06**: los pasos de la Saga que ya emitieron una escritura dejan de ser reintentables sin reconciliar. La estructura del `chain` y su pausa entre pasos se preservan.
- **Consecuencia**: los identificadores de Meta pasan a ser un campo consultable, no una clave dentro del documento de resultado del Draft. Es lo que permite reconstruir qué escribió el sistema y bajo qué autorización.

## ADR-18 · El módulo acota la experiencia del cliente; el rol acota a cualquiera (spec 05)
- **Contexto**: el catálogo de módulos gobernaba la superficie de todos. Apagar un módulo para simplificarle la vista al cliente se lo apagaba también al operador de la agencia, que lo necesita para prestar el servicio. Módulos y roles respondían la misma pregunta desde dos sitios, y «ocultar» acababa usándose como sustituto de «autorizar».
- **Decisión**: `OrganizationUser.access_kind` (`CLIENT` | `AGENCY`) decide a qué **altura** del producto entra una membresía; los tres roles siguen decidiendo **qué puede hacer**. Una petición pasa las dos comprobaciones, y sus rechazos se distinguen (`MODULE_DISABLED` frente a `ROLE_REQUIRED`).
- **La experiencia se deriva, no se elige**: no hay conmutador de sesión, porque un conmutador la convertiría en una preferencia.
- **Default `AGENCY`** en el modelo y en la migración: al desplegar nadie pierde acceso. Convertir una membresía en `CLIENT` es un acto deliberado, cliente por cliente.
- **Consecuencia**: la superficie del cliente recibe los campos de agencia —proveedor, modelo, clave, estado de write-back— con valor `null`, no ausentes: una respuesta que cambia de forma según quién pregunta obliga al frontend a tratar cada campo como opcional por un motivo ajeno.

## ADR-19 · El alta de una Organización es una secuencia validada, no un formulario (spec 05)
- **Contexto**: crear la organización e invitar al cliente ocurrían en el mismo paso, sin validar nada. El precedente es una clave de IA guardada en la organización equivocada, con el analista contestando con plantillas durante días sin que nada pareciera roto.
- **Decisión**: `Organization.onboarding_state` registra qué pasos se validaron **contra el sistema externo real** —no que el campo esté lleno, que funcione—. La invitación no se emite hasta que Meta, IA y WhatsApp están resueltos. Un paso puede marcarse OMITIDO con motivo: queda registrado y es distinto de validado.
- **Consecuencia**: `POST /auth/signup` queda apagado por defecto (`ENABLE_PUBLIC_SIGNUP=false`). Mientras exista un camino de alta que no pase por el flujo asistido, existe la posibilidad de una Organización sin validar.

## ADR-20 · Baseline por mediana, madurez doble y cuatro estados (spec 06)
- **Contexto**: el clasificador calificaba una campaña sin exigir muestra mínima —una campaña con una conversación barata salía ganadora, y esa etiqueta alimentaba las recomendaciones siguientes— y el CPL objetivo se calibraba con un promedio, que un puñado de anomalías desplaza.
- **Decisión**: el baseline es la **mediana** del costo por conversación de las campañas comparables, y viaja siempre con el tamaño del conjunto, el volumen y el gasto que lo sostienen. La madurez es una condición **triple** —días, conversaciones y gasto mínimo— configurable por Organización (7 / 10 / 50 por defecto). El gasto mínimo cierra el caso de la única conversación barata.
- **Cuatro estados**: *datos insuficientes*, *prometedora*, *validada* y *por debajo del baseline*. Deliberadamente no hay una quinta categoría de fracaso: con muestra pequeña no se puede afirmar el fracaso más de lo que se puede afirmar el éxito. Las etiquetas del clasificador (ADR-10) siguen existiendo para la agencia.
- **Baseline congelado**: cada campaña creada guarda el baseline con el que se prometió, en su versión de propuesta (ADR-16). El vigente de la cuenta sigue recalibrándose; los dos coexisten. Reevaluar el pasado con el baseline de hoy destruye la posibilidad de saber si el sistema acertó.

## ADR-21 · El archivo frío conserva todo lo que lee el aprendizaje (spec 06)
- **Contexto y corrección de ADR-01**: el archivado copiaba un subconjunto de columnas mantenido a mano, y perdía `objective`, `results`, `effective_status` y las fechas estructurales — justo lo que decide si dos campañas son comparables y cuántas conversaciones reales hubo. Pasados los meses de retención en caliente, una campaña histórica dejaba de poder usarse como evidencia: **el producto perdía su propuesta de valor a medida que envejecía**.
- **Decisión**: la lista de columnas se **deriva** de la intersección de los dos modelos, no se mantiene a mano, y una prueba falla si una tabla caliente tiene una columna de negocio que su archivo no conserva.
- **Consecuencia**: las filas ya archivadas quedan con los campos nuevos en `NULL`. El dato no es recuperable sin volver a pedírselo a Meta, y ese hueco se documenta en vez de rellenarse con un valor inventado.

## ADR-22 · Los medios son una entidad con dueño, cuota y caducidad (spec 07)
- **Contexto**: la carga leía el archivo **entero en memoria** antes de escribirlo; con 100 MB de límite por video y un VPS de 4 GB compartido entre API, workers, base y Redis, unas pocas cargas simultáneas agotaban el proceso que atiende a todos los clientes. La validación miraba el tipo *declarado*. Los archivos vivían en el disco del VPS, accesibles por ruta.
- **Decisión**: streaming por trozos con el límite aplicado **durante** la transferencia; detección del formato por **contenido**; almacenamiento como driver (`local` / `s3`) con URL firmadas HMAC de cinco minutos; cuota por Organización; y caducidad para todo medio desde que nace.
- **Consecuencia**: la conversión WebP→PNG que Meta exige pasa a un hilo aparte — recodificar 8 MB en el bucle de eventos bloquea a todas las demás peticiones.

## ADR-23 · El saneado de registros se aplica en la creación del registro (spec 07, base de la 09)
- **Contexto**: la URL firmada de un medio aparecía íntegra en el log de peticiones de `httpx`.
- **Decisión**: una **fábrica de `LogRecord`**, no un `logging.Filter`. Un filtro en un logger solo alcanza a lo que se emite *por ese* logger, no a lo que sube propagado desde sus hijos: un secreto registrado por `httpx` o `uvicorn` nunca pasaría por él. La fábrica corre al crear cada registro, sin importar quién lo emita.
- **Detalle que importa**: se sanean también los argumentos que **no** son cadenas. `httpx` registra la URL como objeto y se convierte a texto al formatear — después de cualquier saneado que mire solo cadenas.
- **Consecuencia**: se conservan identificadores internos, códigos y tiempos. Un log que no sirve para investigar es tan malo como uno que filtra.

## ADR-24 · La versión de la API de Meta tiene una fuente de verdad, verificada en CI (spec 08)
- **Contexto**: producción usaba v26 mientras el valor predeterminado del código, el ejemplo de entorno, la plantilla de despliegue y seis documentos decían v25. Cualquier despliegue que no inyectara la variable volvía a v25 en silencio, y Meta retira cada versión a los ~2 años — cuando eso pasa fallan todas las llamadas de todos los clientes a la vez.
- **Decisión**: `meta_ads/version.py::VERSION_SOPORTADA` es el valor; los demás sitios se comprueban contra él en cada construcción. **Alinearlos una vez no arregla nada** — la próxima migración vuelve a desalinearlos; lo que lo arregla es que la construcción falle.
- **Además**: se lee la versión que Meta declara haber **atendido** y se avisa de la divergencia (una llamada que cae a otra versión solo se nota cuando un campo desaparece), y una versión candidata se activa **por Organización** para migrar con la cuenta piloto y hacer rollback vaciando un campo.

## ADR-25 · La baja de un cliente son dos fases con plazos (spec 09)
- **Contexto**: la eliminación declarada no coincidía con la persistencia real. Lo que existía era una herramienta de limpieza de organizaciones *residuales* que se niega a actuar si hay datos — sin revocación de credenciales, sin exportación, sin medios y sin plazo.
- **Decisión**: **inmediata** (corta accesos: credenciales de Meta revocadas, clave de IA borrada, sesiones invalidadas, estado `INACTIVE`) y **diferida a 30 días** (destruye información: histórico, medios, campañas). Confundirlas obligaría a elegir entre cortar tarde o borrar sin dar tiempo a exportar.
- **Corrige una consecuencia de ADR-01**: las tablas `*_archive` no tienen claves foráneas —para sobrevivir a un borrado accidental— así que el `CASCADE` no las alcanza. La eliminación las recorre explícitamente por `organization_id`; omitirlas convertía la promesa en falsa.
- **La fila de `organizations` sobrevive vacía**: un `DELETE` ahí cascadea a media base y deja la baja sin rastro auditable.
- **Consecuencia**: el certificado dice qué se conserva y por qué, incluidos los respaldos previos, que no se reescriben y desaparecen por rotación. Prometer lo contrario sería falso.

## ADR-26 · El proveedor y el modelo de IA son configuración explícita, no una deducción (spec 10)
- **Deroga el mecanismo de selección de [ADR-04](#adr-04)**, no su motivo. El cliente LLM unificado se conserva —la portabilidad entre proveedores compatibles sigue siendo la razón por la que existe— pero la elección deja de derivarse de qué credencial esté presente.
- **Contexto**: había dos cascadas y las dos cambiaban de proveedor sin que nadie lo decidiera. La primera deducía el proveedor por la **forma** de la clave del cliente (`sk-or-` → OpenRouter, `sk-` → OpenAI, `AIza` → Gemini), así que rotarle la credencial le cambiaba proveedor, modelo y precio. La segunda, más cara: sin clave propia se tomaba la primera credencial global disponible en el orden OpenAI → Gemini → OpenRouter, de modo que **añadir `OPENAI_API_KEY` al entorno movía a todas las organizaciones sin clave propia al siguiente reinicio**.
- **Decisión**: el proveedor es el que la Organización declara. La credencial se busca solo para ese proveedor —primero la suya, si no la global— y si falta, la llamada falla de forma visible. Nunca se usa la de otro proveedor, ni siquiera cuando es la única disponible.
- **El invariante vive en el tipo**: una `Organization` nace con proveedor declarado (`__init__`, no `default=` de columna, que solo se aplica al INSERT y dejaría los objetos en memoria comportándose distinto de las filas). Así el criterio se cumple por construcción y no porque cada sitio de creación se acuerde.
- **Consecuencia**: cambiar solo el proveedor se rechaza si el modelo guardado no existe en el catálogo del nuevo. Elegir uno automáticamente sería el mismo cambio silencioso por otra puerta.

## ADR-27 · El catálogo de modelos es dato administrable, no código (spec 10)
- **Contexto**: los modelos elegibles vivían en un diccionario y sus precios en otro, y nada obligaba a que coincidieran. Un modelo elegible sin tarifa se registraba con costo `NULL`, así que **el gasto de esa organización aparecía como cero**. Retirar un modelo que el proveedor deja de servir exigía tocar código y desplegar, cuando es un cambio de dato.
- **Decisión**: una fila por modelo con capacidades, precio y estado (`disponible` / `en_retirada` / `retirado`). El desalineamiento deja de ser representable.
- **Retirar no rompe a quien lo usa**: se deja de ofrecer y la Organización queda **señalada**. No se reasigna: el modelo nuevo puede costar un múltiplo del anterior, y hacerlo solo sería el cambio silencioso que la ADR-26 elimina. Por el mismo motivo se quitó la sustitución que hacía `pick_model` al no encontrar el modelo en el catálogo del proveedor activo.

## ADR-28 · El consumo de IA tiene techo mensual, con avisos antes que bloqueo (spec 10)
- **Contexto**: la clave del LLM es del **cliente**. El sistema medía su gasto con detalle —tokens de entrada, salida, razonamiento y cacheados, costo por funcionalidad— y no lo detenía nunca. Un bucle de reintentos se le factura a él, y la primera noticia llega en el resumen del proveedor.
- **Decisión**: techo mensual por Organización en USD, con avisos a la agencia al 70 % y 90 % y bloqueo al 100 %. Aritmética en `Decimal` (spec 02) y ventana en la zona horaria del negocio, igual que la compuerta publicitaria: es la misma clase de decisión.
- **El bloqueo no apaga el producto**: se bloquean las llamadas al modelo; métricas, estados, tablas, clasificador e informes deterministas siguen funcionando. El cliente pierde el redactor, no el tablero, y se le dice eso.
- **Vive en el borde de la llamada**, no en cada superficie: una superficie nueva queda cubierta por construcción en vez de por acordarse.
- **NULL = sin techo**, que es el estado de toda organización anterior. Un bloqueo retroactivo sobre quien nunca lo acordó le cortaría el producto de un despliegue al otro.

## ADR-29 · La guarda de grounding se extiende al periodo, la cuenta y el catálogo (spec 10)
- **Refuerza [ADR-03](#adr-03)** (la IA nunca inventa cifras). Cambiar la ventana o mezclar AdAccount es una invención tanto como cambiar un número.
- **Contexto**: la guarda numérica no puede ver lo que no es un número. *"En julio el CPL fue S/ 12.40"* pasa entera si 12.40 está en los datos de **agosto**: un mes se escribe con letras. Lo mismo con las cuentas — una respuesta que mezcla dos cuentas de la misma Organización tiene **todas sus cifras respaldadas y aun así es falsa**.
- **Decisión**: una función valida toda salida —cuenta, periodo, promesa de éxito visual y cifras— y registra el descarte con su motivo. Antes cada camino elegía qué guardas aplicar, así que el camino que se agregara mañana no tendría ninguna.
- **El descarte es un fallo registrado, no un silencio**: en una tabla consultable, no en un log que se rota. Una tasa de descarte que sube es la señal más temprana de que un prompt se rompió; sin registro, el síntoma tarda semanas en atribuirse a su causa.
- **Se activa en modo de registro**: se anota qué se descartaría sobre tráfico real antes de descartar. El riesgo no es teórico — un tope mal calibrado ya dejó al analista contestando plantillas durante días, con todos los tests en verde.

## ADR-30 · La comparabilidad entre campañas es código determinista y explicable (spec 11)
- **Extiende [ADR-03](#adr-03)** (la IA nunca inventa cifras). La comparabilidad, el ranking y la confianza se suman a la lista de piezas deterministas: el LLM no decide qué campañas respaldan una recomendación.
- **Contexto**: la propuesta se apoyaba en agregados de la cuenta y en reglas, no en un conjunto identificable de campañas anteriores. Una recomendación fundada en dos campañas de otra temporada se presentaba exactamente igual que una fundada en treinta, y el operador que revisaba no tenía forma de distinguirlas.
- **Decisión**: dos condiciones **duras** —mismo objetivo y mismo evento de conversión— y un **grado** de parecido sobre el resto (temporada, presupuesto, público, formato, ubicación) que solo ordena el conjunto. Sin las condiciones duras las métricas no se pueden poner en la misma escala.
- **Las incompatibles se excluyen antes de puntuar**, no se ponderan a cero: ponderar las deja dentro con peso minúsculo, y un conjunto de treinta con veintiocho pesos minúsculos parece grande.
- **Las dimensiones que no se pueden evaluar se sacan del promedio**, no cuentan como cero. Contarlas como cero castigaría a las campañas peor registradas, que es lo contrario de lo que se busca.
- **Consecuencia**: los pesos de las dimensiones NO son una calibración empírica y el código lo dice. Presentarlos como calibrados sería la misma falsa precisión que esta spec ataja.

## ADR-31 · El aislamiento multi-tenant alcanza a la derivación de conocimiento (spec 11)
- **Extiende [ADR-11](#adr-11)**. El aislamiento deja de ser solo de lectura de datos y pasa a ser también de aprendizaje.
- **Decisión**: la recuperación se restringe a la Organización **y a la AdAccount activas**. No hay conjunto global ni "aprendizajes del rubro" derivados de otros clientes, y tampoco se mezclan dos cuentas de la misma Organización: su histórico describe negocios distintos aunque la factura sea la misma.
- **Sin excepción para el usuario con acceso a las dos cuentas.** Que alguien pueda VER las dos no significa que una pueda derivar conocimiento de la otra. Se prueba con dos cuentas pobladas a la vez: con una sola, un filtro roto pasaría igual porque no habría nada ajeno que pudiera aparecer.

## ADR-32 · Sin evidencia comparable no hay recomendación basada en evidencia (spec 11)
- **Contexto**: el producto no tenía forma de decir "no sé". Una cuenta sin histórico recibía la misma clase de recomendación que una con dos años de datos.
- **Decisión**: la confianza sale del conjunto —cuántas campañas, cuánto se parecen, cuánto volumen aportan y qué dispersos son sus costos— y **se degrada, nunca se promueve**. Sumar puntos por virtudes permitiría que muchas campañas compensaran una dispersión enorme, que es justo lo que no debe pasar.
- **Por debajo del mínimo, la propuesta es exploratoria** y se dice, fundada en buenas prácticas explícitas marcadas como hipótesis. Presentar una buena práctica general como si saliera de los datos del cliente es la mentira concreta que esto impide.
- **El umbral es bajo a propósito** (tres campañas). El riesgo simétrico es real: demasiado estricto, toda cuenta pequeña queda en modo exploratorio permanente.

## ADR-33 · Tres registros de lenguaje, marcados en la estructura (spec 11)
- **Decisión**: *hecho observado*, *asociación* e *hipótesis* son un campo de la respuesta, no un matiz de redacción. Un matiz el LLM lo pierde; un campo no.
- **El lenguaje causal no se admite en ninguno de los tres.** Haría falta una prueba controlada, y ese mecanismo no existe todavía. La etiqueta de asociación viaja con su propia explicación —"no quiere decir que una cause la otra"— porque sin ella el cliente la lee como causa, que es la lectura por defecto.

## ADR-34 · La corrección humana enseña, y la promoción a regla la decide una persona (spec 11)
- **Contexto**: un operador podía cambiar el presupuesto o la audiencia de una propuesta y eso no dejaba rastro utilizable. El riesgo simétrico es el contrario: convertir cada corrección en política sobreajusta a la última opinión.
- **Decisión**: el motivo es **obligatorio y estructurado** —conjunto acotado más texto libre— y la restricción vive en el esquema (`NOT NULL`), no solo en la validación. El conjunto acotado es lo que permite detectar patrones; el texto libre es lo que evita que el conjunto acotado mienta, y `otro` exige escribir.
- **Un motivo repetido se PROPONE como regla candidata, con sus ocurrencias.** No entra en vigor hasta que alguien la acepta. Un rechazo también se registra y abre un plazo: reproponer lo mismo al día siguiente convierte la propuesta en ruido y garantiza que se dejen de leer también las buenas.
- **El Outcome loop cierra contra el baseline CONGELADO** de esa versión, no contra el vigente. Juzgar una campaña de hace dos meses con la referencia de hoy la premia o la castiga por lo que hicieron otras campañas después: eso no mide el resultado de una decisión, mide el ruido posterior.

## ADR-35 · El chat abre con contenido, y ese contenido es determinista (spec 12)
- **Contexto**: el chat era reactivo. Un cliente que no sabe de Meta Ads se encontraba un cursor parpadeante, que le exige justo lo que no tiene: saber qué preguntar. La spec maestra hace del chat el punto principal de valor para ese cliente.
- **Decisión**: al abrir una sesión se entrega un resumen del periodo reciente construido con datos ya sincronizados. El LLM, si está, solo lo redacta.
- **Determinista de punta a punta a propósito**: así el resumen existe con el modelo caído, que es la diferencia entre un producto que degrada y uno que se queda en blanco.
- **No dispara análisis.** Se consume lo que el análisis agendado ya dejó; abrir una conversación no puede provocar trabajo pesado ni saltarse el debounce de [ADR-07](#adr-07). Veinte aperturas en una mañana serían veinte análisis de la misma cuenta.
- **Usa los cuatro estados de la spec 06**, no etiquetas propias. "Va bien" no significa nada y sobre tres conversaciones sería mentira. [ADR-10](#adr-10) se preserva: el chat presenta lo que el clasificador produce.

## ADR-36 · El contexto viaja con la respuesta, no en una nota al pie (spec 12)
- **Contexto**: el cliente leía una cifra sin saber de qué cuenta era, de qué periodo, de cuándo eran los datos ni si la evidencia bastaba para la frase que acababa de leer.
- **Decisión**: cuenta, periodo, frescura y suficiencia son parte **estructurada** de la respuesta. Un texto con el contexto dentro se rompe al truncarlo o al copiarlo, y sobre todo lo puede perder el modelo al redactar — que es el caso en que más falta hace.
- **La frescura se declara siempre, no solo cuando hay problema.** Una cifra sin fecha invita a decidir sobre una falsa sensación de actualidad: el cliente no puede distinguir "no pasó nada" de "no nos enteramos", y las dos se ven igual en la pantalla.
- **La suficiencia tiene tres estados y no dos.** "No aplica" existe porque clasificar como insuficiente lo que simplemente no necesita muestra haría que el cliente leyera una advertencia donde no hay nada que advertir.

## ADR-37 · Cuatro situaciones degradadas, nombradas y distinguibles (spec 12)
- **Contexto**: un fallo de la IA, un fallo de Meta y un fallo de autorización producían mensajes que el cliente no podía distinguir entre sí ni de "no hay datos". Ante la duda sacaba la conclusión más pesimista y más frecuente: que no hay datos.
- **Decisión**: `ia_no_disponible`, `meta_no_disponible`, `no_autorizado` y `sin_datos` son valores explícitos que viajan en la respuesta, y cada mensaje **nombra primero lo que sigue funcionando**, porque eso es lo que decide si el cliente puede seguir trabajando o tiene que volver luego.
- **El fallo de autorización conserva su semántica.** Un 403 que llega como "no encontré datos de esa cuenta" le enseña al cliente que la cuenta está vacía cuando lo que pasa es que no le corresponde: eso es peor que un error.
- **La ausencia de datos es la cuarta y no es una degradación.** Nada está roto, la cuenta es nueva. Meterla en el mismo saco haría que un cliente nuevo creyera que el producto falla el primer día.

## ADR-38 · El brief es un contrato de negocio, y los parámetros se derivan de él (spec 12)
- **Contexto**: la solicitud recogía parámetros publicitarios —objetivo, presupuesto, placements— que son la CONSECUENCIA de una decisión comercial que nadie preguntaba. Un cliente que no sabe de Meta Ads no puede contestar qué `optimization_goal` quiere, y sí puede contestar qué vende y a quién.
- **Decisión**: objetivo comercial, oferta, presupuesto, duración, destino de WhatsApp, medios y capacidad de atención son obligatorios; condiciones y público, opcionales. La solicitud **no avanza a propuesta** con un obligatorio ausente: rellenarlo el sistema es inventar la decisión de negocio del cliente.
- **La capacidad de atención es obligatoria por una razón que no es obvia.** Una campaña que trae más consultas de las que el negocio puede atender **empeora** su negocio aunque mejore la métrica: los mensajes sin responder queman la reputación del número y el dinero se gasta igual. Sin ese dato el sistema puede optimizar hacia un daño.
- **Se pide de a un elemento por turno**, en el orden en que el sistema los echa en falta. Siete preguntas en un mensaje de chat no se contestan: se abandonan.
- **El brief se DERIVA de los slots, no se duplica.** Dos fuentes sobre si falta el presupuesto acaban discrepando, y la que se quedaría vieja es la que decide si se puede proponer.

## ADR-39 · Lo extraído de un medio se muestra antes de usarse (spec 12)
- **Decisión**: el sistema propone qué entendió de la foto o el video y el cliente lo confirma o lo corrige antes de que llegue a la propuesta. Una interpretación equivocada que nadie ve se convierte en una campaña equivocada, y el momento barato de corregirla es antes de gastar.
- **Lo medido se separa de lo interpretado.** Las dimensiones y la duración son hechos del archivo: no hay nada que corregir, y aceptar una corrección ahí dejaría al sistema mandándole a Meta unas dimensiones que la imagen no tiene. La descripción sí es interpretación, y el cliente es la autoridad.
- **La corrección se guarda junto a lo extraído, no encima.** Conservar las dos permite responder en qué se equivoca la interpretación, que es lo único que permite mejorarla.
- **Nada de esto es un juicio de calidad ni una promesa de rendimiento**, prohibida por [ADR-03](#adr-03) y la spec 10: el rendimiento de una creatividad no se lee en sus píxeles.

## ADR-40 · Los avisos proactivos se limitan en frecuencia, y el vocabulario interno se traduce (spec 12)
- **No hay un segundo motor de detección.** El análisis determinista ya produce `AIInsight`; el chat es su superficie de entrega. Inventar otra detección daría dos verdades sobre la misma cuenta, y la que el cliente vería sería la peor mantenida.
- **Decisión**: se limita la frecuencia por huella —categoría más sujeto, no el texto, que lleva las cifras del día y cambia solo— con ventana según el nivel. Un hallazgo vigente lo sigue siendo todos los días hasta que alguien lo toque; sin límite, el mismo aviso saldría en cada apertura y en tres aperturas el cliente dejaría de leerlos, perdiendo también el que importaba.
- **La ventana depende del nivel.** La misma para todos convertiría lo urgente en ruido o lo accesorio en insistencia.
- **El vocabulario interno se traduce en el borde.** `Draft`, `Saga`, `AIAction`, `grounding` y `write-back` no llegan al cliente. Se traducen y no se borran, para no dejar frases mutiladas, y se comprueba sobre las respuestas de un recorrido completo: revisar plantillas encuentra lo escrito a mano y pierde lo que llega desde una excepción.

## ADR-41 · Los interruptores se aplican donde se ejecuta, no donde se pide (spec 13)
- **Contexto**: no existía ningún interruptor operativo. Detener la creación, el write-back, la sincronización, la IA o los medios exigía cambiar variables de entorno y redesplegar, que en mitad de un incidente es lento y arriesgado. Las únicas compuertas eran las tres de write-back, todas de configuración.
- **Decisión**: cinco interruptores independientes, aplicados en el **punto de ejecución** — `start_campaign_creation`, el limitador de Meta, el cuerpo de la tarea de sync, el borde de la llamada al modelo y el servicio de medios. Un interruptor que solo oculta un botón no contiene nada.
- **Cortan también las tareas ya encoladas.** Es la mitad que se olvida: cortar solo la puerta de entrada deja la cola vaciándose sobre el problema que se acaba de intentar contener, y en un incidente esa cola suele ser justo lo que hay que parar.
- **Son estado, no configuración.** Efecto inmediato, con registro de quién y cuándo. Un interruptor que exige un despliegue no sirve en el único momento en que se usa.
- **Cinco y no uno**, porque un incidente rara vez los afecta a todos. Apagar la creación porque Meta devuelve errores de escritura dejaría al cliente sin consultar su histórico, que no tiene nada que ver.
- **Complementa [ADR-02](#adr-02)**: las tres compuertas de configuración siguen, y esto añade una capa administrable por encima.

## ADR-42 · Fallo cerrado en el plano operativo, y la lectura del histórico no se apaga (spec 13)
- **Acota otra vez [ADR-13](#adr-13)**, ahora en el plano de contención: el estado de los interruptores no puede degradar abierto. La excepción principal la define la spec 01; esta la extiende.
- **Decisión**: si el almacén de estado no responde, las cinco capacidades se consideran apagadas. No poder comprobar qué está apagado es, a efectos de contención, indistinguible de estar apagado, y equivocarse hacia "se puede escribir" es el error caro.
- **La consulta del histórico NUNCA pasa por un interruptor.** No es que el suyo esté encendido: es que no tiene. Un cliente que mira lo que ya ocurrió no puede romper nada, y quitarle esa consulta durante un incidente lo deja sin saber qué pasa con su dinero.
- **El modo de solo lectura es una capacidad aparte, no la suma de las cinco.** Se activa de una vez, que es lo que hace falta cuando aún no se sabe la causa. Su `encendido` significa lo CONTRARIO que el de las capacidades —encendido = está bloqueando— y por eso se resuelve con su propia función: mezclarlos produciría, tarde o temprano, un `not` en el sitio que decide si se puede escribir.

## ADR-43 · Los feature flags son infraestructura compartida con default en el código (spec 13)
- **Contexto**: cuatro specs necesitan encender algo gradualmente y cada una tendría que inventar su mecanismo. Cuatro mecanismos son cuatro sitios donde mirar cuando algo está encendido para quien no debería.
- **Decisión**: ámbito cuenta → organización → global → valor predeterminado, resuelto en ese orden. La **cuenta piloto es un caso de uso** de este mecanismo, no un concepto aparte: es simplemente el ámbito más estrecho, y si no ganara a la organización no serviría para pilotar.
- **El valor predeterminado vive en el código, junto al flag**, no en la base. Un flag sin fila tiene un valor conocido en vez de un `NULL` que cada consumidor interprete, y declararlo ahí obliga a pensar qué pasa con quien todavía no lo tiene.
- **Un flag desconocido falla en vez de responder "apagado".** Uno mal escrito que responde apagado produce una capacidad que nunca se enciende y nadie entiende por qué.
- **Su política de fallo es la contraria a la de los interruptores**: ante un almacén caído se usa el default, que es lo que el sistema hacía ayer. Un interruptor apagado contiene un incidente; un flag apagado por accidente apaga una capacidad que llevaba meses funcionando bien.

## ADR-44 · Se monitoriza lo funcional, y la alerta se emite desde fuera (spec 13)
- **Contexto**: la observabilidad cubría errores de proceso. Un proceso que responde al healthcheck y lleva tres días sin traer datos está roto para el cliente y perfecto para el supervisor.
- **Decisión**: once señales funcionales, cada una con su **umbral declarado en el código junto a lo que mide**. Ponerlo en el sistema de alertas lo separaría de su medición: quien lee el código no sabría cuándo es demasiado, y quien configura la alerta no sabría qué mide.
- **`desconocido` no es `ok`.** Una señal que falla al medirse y se reporta como sana es la peor de las tres opciones.
- **El vigilante vive fuera del VPS** y comprueba tres cosas: que el endpoint responda, que su marca de tiempo AVANCE y que no haya alertas. La segunda es la que se olvida y la que atrapa el caso peor —el servicio arriba y el trabajo parado—. Con el VPS apagado no hay respuesta, y esa ausencia es la alerta: un sistema que solo alerta desde dentro no puede informar de su propia muerte.
- **El endpoint de señales va sin autenticación**, porque un monitor de disponibilidad simple no puede sondear uno que exija credenciales. No expone datos de ningún cliente: estados, umbrales y una marca de tiempo.

## ADR-45 · Dos audiencias, dos mensajes de incidente (spec 13)
- **Aplica [ADR-05 de la spec 05](#adr-19) al caso de incidente.** La agencia necesita saber qué dependencia falla, qué interruptor está activo y quién lo movió; el cliente necesita saber qué puede y qué no puede hacer ahora.
- **Darle el diagnóstico técnico al cliente es peor que inútil**: le enseña nombres de infraestructura que no puede usar y lo alarma sobre algo que no está en su mano arreglar. Darle a la agencia el mensaje del cliente la deja sin lo único que necesita para arreglarlo.
- **El aviso al cliente calla cuando no hay incidente.** Una interfaz que siempre muestra algo sobre el estado del sistema entrena a ignorar ese sitio, y el día que importe no se va a mirar.
- **Las métricas de producto se derivan de eventos que el flujo ya produce**, no se reconstruyen después ni se agregan a diario. Sin recorrido devuelven `null` y no cero: "sin datos" no es "cero minutos", que se leería como que el sistema propone al instante.

---

## Mantenimiento automático sugerido

Estos documentos deberían regenerarse cuando cambie el código correspondiente:

| Documento | Regenerar cuando cambie |
|-----------|-------------------------|
| [06-api](06-api.md) | `app/api/v1/*.py`, `app/schemas/*.py` |
| [07-modelos](07-modelos.md) + ERD | `app/models/*.py` |
| [05-base-de-datos](05-base-de-datos.md) (cadena de migraciones) | `alembic/versions/*` |
| [10-configuracion](10-configuracion.md) | `app/config.py`, `.env.example` |
| [11-servicios-externos](11-servicios-externos.md) | `services/meta_ads/*`, `services/ai/llm.py`, `services/ai/proveedor.py`, `services/ai/catalogo.py` |
| [21-producto-datos-visualizados](21-producto-datos-visualizados.md) | `app/services/report_builder.py`, `frontend/app/(dashboard)/page.tsx`, `reporte/page.tsx`, `campaigns/page.tsx`, `frontend/lib/types.ts` |

Herramientas candidatas: exportar el OpenAPI (`/openapi.json`) para la referencia de API y los tipos del frontend; introspección de `Base.metadata` para el ERD.
