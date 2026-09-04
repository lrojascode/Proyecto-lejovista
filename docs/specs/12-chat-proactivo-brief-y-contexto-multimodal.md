# 12 · Chat proactivo, brief y contexto multimodal

## Estado

Completada. Los diecisiete criterios están implementados y verificados
(`backend/tests/test_spec12_chat.py`, 54 pruebas).

**Lo que más cambió no fue el chat sino su máquina de estados.** El brief mínimo
y la revisión del medio son dos compuertas nuevas entre la foto y la propuesta, y
eso movió el recorrido que siete archivos de test daban por sentado. Se arregló
en el helper que ya compartían —`_pasar_las_compuertas_del_brief`— y no
archivo a archivo: duplicar el recorrido los habría hecho divergir al primer
cambio del brief.

**El presupuesto pasó a preguntarse.** El producto lo decidía por el cliente: el
sugeridor calculaba un monto y la conversación nunca lo consultaba. La spec lo
lista como elemento del brief, y tiene razón — cuánto gastar es una decisión de
negocio, no un parámetro publicitario derivable. Es el cambio con más fricción
añadida y el primer candidato a revertir si el piloto lo rechaza.

**La capacidad de atención era el elemento que faltaba y no era obvio.** Una
campaña que trae más consultas de las que el negocio puede atender empeora su
negocio aunque mejore la métrica: los mensajes sin responder queman la
reputación del número y el dinero se gasta igual. Sin ese dato el sistema puede
optimizar hacia un daño y llamarlo éxito.

**Las cuatro situaciones degradadas se probaron comparándolas entre sí.** El
riesgo del criterio es que se confundan, así que un test por situación no
alcanza: hay uno que exige que los cuatro mensajes sean distintos. Probar cada
una por separado dejaría pasar exactamente el fallo que importa.

**Dos aserciones se volvieron vacías al declarar los campos en el esquema.**
Con `contexto`, `resumen`, `avisos` y `degradacion` declarados en
`ChatTextMessage`, la clave existe siempre y `"avisos" in mensaje` es cierto para
todos. El test de la ventana de repetición pasaba a comprobar nada. Se cambió a
comprobar el valor; el descubrimiento fue que el mecanismo funcionaba y el test
no lo estaba viendo.

### Deudas que se llevan

- **No hay feature flag por Organización.** La spec pide activarlo así,
  empezando por la piloto, y el mecanismo genérico de flags pertenece a `13`. Lo
  que hay hoy se enciende para todos a la vez; el rollback es revertir el
  despliegue, no apagar un flag.
- **El medio no se interpreta todavía.** El contexto que se muestra es lo
  medido —dimensiones, duración, avisos técnicos— y la descripción visual se
  declara ausente en vez de inventarse. Cuando exista visión en este camino, el
  contrato de revisión y corrección ya está puesto.
- **Los avisos silenciados por frecuencia no se persisten.** Serían una fila por
  apertura y por hallazgo vigente, que crece sin aportar nada nuevo. La
  observabilidad los declara en cero y el dato vive en el log; mentir con un
  número inventado haría inútil la métrica que calibra la ventana.
- **La verificación con el cliente piloto no se ha hecho.** Uso del chat y
  comprensión de las respuestas son dos métricas de producto declaradas, y
  ninguna se puede medir desde aquí.
- **La pantalla no consume los campos nuevos.** El contexto, el resumen, los
  avisos y la revisión del medio viajan en la respuesta y el frontend todavía no
  los muestra.

## Prioridad

P2.

## Objetivo

Que el chat sea la superficie donde el cliente entiende sus campañas y solicita una nueva: que
responda con contexto —cuenta, periodo, frescura y suficiencia—, que se adelante con avisos
útiles, que recoja un brief completo con medios, y que degrade con honestidad cuando la IA o
Meta no estén disponibles.

## Problema

El chat existe en dos formas —un analista de datos y un flujo de creación de campañas— y ambos
son reactivos: si el cliente no sabe qué preguntar, no obtiene nada. La spec maestra hace del
chat el punto principal de valor para un cliente que no es experto en Meta Ads, y eso exige
que el chat empiece la conversación.

Las respuestas no siempre declaran su contexto. El cliente no sabe con certeza de qué cuenta y
de qué periodo le están hablando, ni si los datos están actualizados, ni si la evidencia basta
para la afirmación que acaba de leer.

El brief no tiene una forma mínima acordada. La solicitud de campaña recoge parámetros
publicitarios en vez de la información de negocio que los determina: objetivo comercial,
oferta, condiciones, restricciones y capacidad de atención. Los medios se adjuntan pero su
contenido no se interpreta, y cuando se interprete, el cliente necesita ver qué entendió el
sistema para poder corregirlo.

La degradación no está diferenciada. Un fallo de la IA, un fallo de Meta y un fallo de
autorización producen mensajes que el cliente no puede distinguir de "no hay datos".

## Alcance

- Resumen proactivo al abrir el chat, y avisos de anomalías, deterioro y oportunidades.
- Consultas en lenguaje natural sobre rendimiento, con la cuenta, el periodo, la frescura y la
  suficiencia de evidencia declaradas en la respuesta.
- Consulta de qué campañas mejoraron o empeoraron, expresada con los estados de `06`.
- Solicitud de campaña desde el chat, con recogida del brief mínimo.
- Adjunto de medios al brief y presentación de lo que el sistema extrajo de ellos para revisión
  humana.
- Degradación diferenciada y honesta: caída de la IA, caída de Meta y fallo de autorización se
  distinguen entre sí y de la ausencia de datos.
- Conservación de las respuestas deterministas seguras cuando el LLM no está disponible.
- Continuidad de los fallos de dominio dentro de la conversación, con estados comprensibles.

## Historias de usuario

Tomadas de la spec maestra:

- **3** — conversar en lenguaje natural sobre las campañas.
- **4** — recibir un resumen proactivo al abrir el chat.
- **5** — preguntar qué campañas mejoraron o empeoraron.
- **7** — saber el periodo y la cuenta utilizados en una respuesta.
- **12** — solicitar una campaña desde el chat.
- **13** — explicar objetivo comercial, oferta y restricciones.
- **15** — revisar qué contexto extrajo el sistema de los medios.
- **21** — recibir un mensaje claro cuando la IA o Meta no están disponibles.
- **22** — conservar respuestas deterministas durante una caída del LLM.
- **23** — recibir avisos sobre anomalías, deterioro y oportunidades.

## Decisiones de implementación

- **El chat abre con contenido, no con un cursor.** Al iniciar una sesión se presenta un
  resumen construido con datos ya sincronizados: qué pasó en el periodo reciente, qué cambió y
  qué merece atención. Se genera de forma determinista y el LLM únicamente lo redacta, de modo
  que exista aunque el modelo no esté disponible.
- **Los avisos proactivos nacen de los Insight que el análisis ya produce.** No se inventa un
  segundo motor de detección: la Capa de análisis determinista ya genera hallazgos, y el chat
  es su superficie de entrega para el cliente. Lo que esta spec añade es la selección de cuáles
  merecen interrumpir y con qué frecuencia, para que el aviso no se convierta en ruido que el
  cliente aprende a ignorar.
- **El contexto viaja con la respuesta, no en una nota al pie.** Cuenta, periodo, momento de la
  última sincronización y suficiencia de evidencia son parte estructurada de la respuesta y se
  presentan siempre que sean relevantes. Es lo que permite que el cliente interprete la cifra
  en vez de creerla.
- **La AdAccount de una sesión de chat se congela al abrirla.** El comportamiento actual se
  preserva: cambiar de cuenta a mitad de conversación produciría una conversación cuyos
  primeros mensajes hablan de otra cuenta.
- **El brief mínimo es un contrato de negocio, no un formulario publicitario.** Objetivo
  comercial, oferta, condiciones, presupuesto, duración, público o restricciones, destino de
  WhatsApp, medios y capacidad aproximada de atención. La conversación reúne esos elementos en
  el orden que el cliente traiga; el sistema sabe cuáles faltan y los pide. Los parámetros
  publicitarios se derivan del brief y no se le preguntan al cliente.
- **La capacidad de atención es parte del brief por una razón.** Una campaña que genera más
  conversaciones de las que el cliente puede atender empeora su negocio aunque mejore la
  métrica.
- **Lo extraído de los medios se muestra antes de usarse.** El sistema propone qué entendió
  —producto, contexto, elementos visibles— y el cliente lo corrige. Una interpretación
  equivocada que nadie ve se convierte en una campaña equivocada. Lo extraído no se presenta
  como juicio de calidad ni como promesa de rendimiento.
- **Tres degradaciones distintas y nombradas.** La caída del LLM conserva las respuestas
  deterministas seguras —cifras, estados, listados— y dice que la interpretación de lenguaje
  libre no está disponible. La caída de Meta afecta a la frescura y se dice como tal: los datos
  existen, pero son de antes. El fallo de autorización o de aislamiento conserva su semántica de
  autorización y no se disfraza de estado conversacional. La ausencia de datos es la cuarta y no
  se confunde con ninguna de las tres.
- **Los fallos de dominio se quedan en la conversación.** Un preflight que no pasa, un
  presupuesto que excede el límite o una evidencia insuficiente son estados comprensibles
  dentro del chat, no errores técnicos.
- **El vocabulario del cliente no incluye conceptos internos.** Draft, Saga, AIAction, grounding
  y write-back no aparecen en la superficie del cliente; se traducen a estados comprensibles en
  el borde.

### Contradicción con ADR implícitas

- **ADR-09 (métrica primaria unificada)** y **ADR-10 (clasificador único)** se preservan: el
  chat presenta lo que esas decisiones producen y no calcula por su cuenta.
- **ADR-07 (debounce del análisis con lock)** se preserva; el resumen proactivo consume el
  resultado del análisis ya agendado y no dispara análisis adicionales por abrir una
  conversación.

## Criterios de aceptación

1. Al abrir una sesión de chat, el cliente recibe un resumen del periodo reciente sin haber
   preguntado nada. Sí/No.
2. Ese resumen se genera también cuando el LLM no está disponible, con redacción determinista.
   Sí/No.
3. Una respuesta con cifras declara la cuenta y el periodo utilizados. Sí/No.
4. Una respuesta sobre datos con última sincronización antigua declara la frescura. Sí/No.
5. Una respuesta cuya evidencia no alcanza el umbral de suficiencia lo declara en vez de
   afirmar. Sí/No.
6. La pregunta por qué campañas mejoraron o empeoraron se responde con los estados definidos en
   `06`, sin etiquetas definitivas sobre muestras pequeñas. Sí/No.
7. Cambiar la AdAccount activa no altera la cuenta de una sesión de chat ya abierta. Sí/No.
8. El cliente puede solicitar una campaña desde el chat y el sistema identifica qué elementos
   del brief mínimo faltan y los pide. Sí/No.
9. La solicitud no avanza a propuesta mientras falte un elemento obligatorio del brief mínimo.
   Sí/No.
10. El cliente adjunta un medio y el sistema muestra qué contexto extrajo de él antes de usarlo.
    Sí/No.
11. El cliente puede corregir lo extraído y la corrección se refleja en la propuesta. Sí/No.
12. Ninguna respuesta afirma que un medio garantizará resultados por su apariencia. Sí/No.
13. Con el LLM caído, las consultas de hechos deterministas siguen respondiendo y el mensaje
    distingue esa situación de la ausencia de datos. Sí/No.
14. Con la sincronización de Meta caída, el chat responde con los datos existentes y declara la
    frescura, distinguiéndolo de la ausencia de datos. Sí/No.
15. Un fallo de autorización o de aislamiento produce una respuesta con semántica de
    autorización y no un estado conversacional. Sí/No.
16. Ninguna superficie del chat del cliente muestra los términos internos Draft, Saga, AIAction,
    grounding ni write-back. Sí/No.
17. Los avisos proactivos tienen una frecuencia acotada y no repiten el mismo aviso en cada
    apertura de sesión. Sí/No.

## Decisiones de testing

- La frontera es el **recorrido de sistema por navegador y API sobre la conversación completa**:
  apertura, resumen, consulta, brief, medios y solicitud. Es el seam principal que fija la spec
  maestra.
- Se reutilizan como prior art las pruebas existentes de la máquina de estados del chat, del
  flujo de creación por chat, de coherencia, de formato y de aislamiento por tenant; las nuevas
  elevan esa cobertura a la conversación completa en vez de repetir transiciones sueltas.
- Las tres degradaciones se prueban **por separado y contra el caso de ausencia de datos**: cada
  una debe producir un mensaje distinguible de las otras tres. Es la única forma de comprobar
  el criterio, porque el riesgo es precisamente que se confundan.
- La completitud del brief se prueba omitiendo cada elemento obligatorio de uno en uno.
- La ausencia de vocabulario interno en la superficie del cliente se prueba como aserción sobre
  las respuestas de un recorrido completo, no revisando plantillas.
- Los avisos proactivos se prueban con el reloj controlado, verificando la ventana de
  repetición.
- No se prueba la calidad conversacional del texto. Se prueba la presencia del contexto, la
  distinción de estados y el respeto del contrato del brief.

## Dependencias

- `05` — la experiencia del cliente y su vocabulario; el chat es su superficie central.
- `06` — la métrica primaria, los estados de campaña y las señales de frescura y cobertura que
  el chat presenta.
- `07` — la carga y validación de medios que el brief adjunta.
- `10` — el confinamiento del LLM y la prohibición de contenido simulado presentado como
  análisis real.
- `11` — la evidencia y la confianza que acompañan a una propuesta cuando el chat la presenta.

## Fuera de alcance

- Generación o edición automática de creatividades.
- Aprobación de una campaña desde el chat del cliente. La aprobación es responsabilidad de la
  agencia y vive en su experiencia.
- Activación de campañas desde la conversación.
- Canales de conversación fuera del producto, incluida la entrega de avisos por WhatsApp o
  correo.
- Voz, audio y cualquier modalidad distinta de texto, imagen y video.
- Traducción o idiomas adicionales.
- El algoritmo de detección de anomalías, que ya existe en el análisis determinista y no se
  modifica aquí.

## Riesgos y rollback

- **Daño máximo si no se hace:** el cliente no experto abre el producto, no sabe qué preguntar y
  no obtiene valor; o lee una cifra sin saber de qué cuenta y de qué periodo es y decide sobre
  ella.
- **Daño máximo al hacerlo:** los avisos proactivos se convierten en ruido y el cliente deja de
  leerlos, con lo que también se pierde el aviso importante. Un resumen automático que llega
  con datos desactualizados y no lo declara es peor que no enviarlo.
- **Activación:** por feature flag de Organización, empezando por la piloto. El resumen
  proactivo se enciende antes que los avisos; los avisos se encienden con la ventana de
  repetición más conservadora y se relajan con evidencia de uso. La declaración de contexto en
  las respuestas puede activarse de inmediato porque solo añade información.
- **Rollback:** apagar el flag devuelve el chat reactivo actual. La recogida del brief mínimo es
  la pieza con mayor riesgo de fricción: se puede revertir al flujo de solicitud actual sin
  perder las conversaciones ya iniciadas, porque el brief se persiste como parte del Draft.

## Checklist de finalización

- [x] Migración del brief mínimo estructurado y del contexto extraído de los medios, asociados
      al Draft.
- [x] Resumen proactivo determinista, con redacción por LLM opcional.
- [x] Selección y limitación de frecuencia de los avisos proactivos sobre los Insight
      existentes.
- [x] Pruebas de sistema de los diecisiete criterios, con las cuatro situaciones de degradación
      probadas por separado.
- [x] Documentación de producto: qué hace el chat, qué pide el brief mínimo y qué significa cada
      estado que el cliente ve.
- [x] Documentación del chat de creación actualizada con el brief de negocio y la revisión del
      contexto multimodal.
- [x] Observabilidad: aperturas de chat con resumen entregado, avisos emitidos y descartados por
      frecuencia, briefs completados frente a abandonados, y tasa de caída a respuesta
      determinista.
- [ ] Verificación con el cliente piloto: uso del chat y comprensión de las respuestas, que son
      dos de las métricas de producto declaradas. **Pendiente: no se puede medir desde aquí.**

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Experiencia conversacional*; *Brief y
medios* (párrafos de brief mínimo y de extracción de contexto para revisión humana); *Solution*
(párrafo del chat como superficie principal de valor); *Entrega por fases* → **P2**.
