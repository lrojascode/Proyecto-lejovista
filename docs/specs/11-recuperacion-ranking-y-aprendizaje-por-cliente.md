# 11 · Recuperación, ranking y aprendizaje por cliente

## Estado

Completada. Los dieciséis criterios están implementados y verificados
(`backend/tests/test_spec11_recuperacion.py`, 44 pruebas).

**Faltaba un dato, no solo una función.** El evento de conversión —la segunda
condición dura de comparabilidad— no se sincronizaba desde Meta: vive en el
adset (`optimization_goal`) y nadie lo pedía. Se añadió al sync, a los dos
snapshots y a sus archivos. Para el histórico anterior a la columna se **deriva**
de qué métrica primaria tuvo la campaña, que es el mismo hecho visto por la otra
cara; pedirle a Meta el goal de dos años de campañas serían miles de llamadas
para reconstruir algo que ya se puede leer.

**Excluir resultó más honesto que ponderar.** La primera versión ponderaba hacia
cero las campañas de otro objetivo. Funciona, y produce un conjunto de treinta
donde veintiocho pesan casi nada: el número que ve el operador dice treinta. Las
incompatibles se excluyen antes de puntuar, y las excluidas se devuelven con su
motivo, porque «¿y por qué no está aquella campaña?» es la primera pregunta de
cualquiera que conozca la cuenta.

**La confianza se degrada y nunca se promueve.** Al principio sumaba puntos por
virtudes, y con eso dieciocho campañas compensaban una dispersión enorme: el
conjunto disperso salía con más confianza que uno pequeño y consistente. Hay un
test que lo fija —dieciocho campañas dispersas siguen dando confianza baja—
porque es la clase de cosa que un refactor bienintencionado revierte.

**El umbral se dejó bajo a propósito.** Tres campañas comparables es poco, y el
riesgo simétrico que la spec declara es real: demasiado estricto deja a toda
cuenta pequeña en modo exploratorio permanente, y ese cliente nunca vería el
producto aprender.

### Un desajuste de producto que esta spec sacó a la luz

Las reglas del tenant crean campañas con `objective = OUTCOME_LEADS`, mientras
que el sync reporta las campañas de WhatsApp de la cuenta como
`OUTCOME_ENGAGEMENT`. Con el objetivo como condición dura, **una cuenta real
podría quedar sin ninguna campaña comparable de forma permanente**, y todas sus
propuestas saldrían marcadas como exploratorias por mucho histórico que tuviera.

No se resolvió aquí porque los dos valores son legítimos en Meta para
Click-to-WhatsApp y la respuesta depende de qué hay en la cuenta de producción.
Queda anotado como trabajo aparte. Los tests leen el objetivo de
`TENANT_CAMPAIGN_RULES` en vez de fijarlo, así que seguirán la decisión que se
tome.

### Deudas que se llevan

- **El periodo en modo consultivo no se ha hecho.** La spec pide encender la
  recuperación para la agencia —mostrar la evidencia sin que altere la
  propuesta— durante varios ciclos reales antes de conectarla. Lo implementado
  ya es consultivo: la evidencia se muestra y se registra, y **no** cambia
  todavía los parámetros que propone el sugeridor. Conectarla es la decisión que
  queda, y necesita esos ciclos de observación.
- **Los pesos de las dimensiones no están calibrados** con datos, y el código lo
  dice en vez de disimularlo. Son un orden de prioridad declarado para poder
  discutirlo.
- **El baseline congelado solo existe desde esta spec.** Las campañas anteriores
  no lo tienen y su veredicto sigue calculándose contra el presupuesto sugerido;
  `expectativa_origen` declara cuál de los dos se usó, para que dos veredictos
  calculados contra referencias distintas no se lean como comparables.
- **La regla candidata se propone, no se aplica.** Aceptarla deja constancia y
  nada más: traducir una regla aceptada en configuración ejecutable es otro
  alcance, y hacerlo aquí habría sido justo la automatización que el criterio 14
  prohíbe.
- **La pantalla todavía no consume nada de esto.** La evidencia viaja en la
  respuesta de la sugerencia y los endpoints de revisión existen, pero el
  formulario de creación no los muestra: es trabajo de interfaz, y la spec 12
  es la que se ocupa de presentar la evidencia conversacionalmente.

## Prioridad

P2.

## Objetivo

Que una recomendación del sistema esté respaldada por campañas históricas concretas del mismo
cliente y de la misma AdAccount, con su nivel de confianza a la vista, distinguiendo lo
observado de lo asociado y de lo hipotético; y que la revisión humana de una propuesta se
convierta en aprendizaje auditable en vez de perderse.

## Problema

El sistema ya aprende de sí mismo: registra qué propuso, con qué razonamiento, y mide el
resultado real de las campañas que creó. Lo que falta es la mitad que sostiene la propuesta de
valor: **recuperar campañas históricas comparables del mismo cliente y mostrarlas como
evidencia**.

Hoy la propuesta se apoya en agregados de la cuenta y en reglas de negocio, no en un conjunto
identificable de campañas anteriores. El operador que revisa no puede ver qué campañas
respaldan la recomendación, cuántas son ni qué tan comparables. Sin ese conjunto, una
recomendación débil —basada en dos campañas de otra temporada y otro objetivo— es
indistinguible de una fuerte.

Falta también una definición de comparabilidad: qué hace que dos campañas sean comparables, y
qué combinaciones son incompatibles. Sin ella, cualquier baseline es un promedio de cosas
distintas.

La revisión humana no deja rastro utilizable. Un operador puede cambiar el presupuesto o la
audiencia de una propuesta, pero no se registra el motivo, y la corrección no alimenta ninguna
regla. El riesgo simétrico es el contrario: convertir cada corrección individual en política y
sobreajustar a la última opinión.

Por último, no hay separación explícita entre hechos, asociaciones e hipótesis en el lenguaje
de las explicaciones, ni una etiqueta para las campañas que se proponen sin evidencia
comparable.

## Alcance

- Criterios de comparabilidad entre campañas: objetivo, evento de conversión, periodo, oferta,
  público, ubicación, formato, copy, medios y presupuesto.
- Recuperación de campañas comparables restringida a la Organización y AdAccount activas.
- Ranking del conjunto recuperado y exposición de la evidencia que respalda una propuesta.
- Nivel de confianza derivado del tamaño y la homogeneidad del conjunto comparable.
- Separación explícita de hechos observados, asociaciones e hipótesis en las explicaciones.
- Etiqueta de campaña exploratoria cuando no existe evidencia comparable.
- Rechazo de una recomendación cuando la evidencia es insuficiente.
- Registro de trazabilidad de cada propuesta: campañas fuente, ventana, versión de reglas,
  prompt, modelo y confianza.
- Modificación humana de una propuesta con registro del motivo.
- Outcome loop completo: propuesta original, cambios humanos, resultado y veredicto.
- Promoción explícita de patrones repetidos a reglas, nunca automática.

## Historias de usuario

Tomadas de la spec maestra:

- **35** — la agencia ve qué campañas históricas respaldan una propuesta.
- **36** — la agencia conoce la cantidad de campañas comparables y el nivel de confianza.
- **37** — se distinguen hechos observados, asociaciones e hipótesis.
- **40** — la agencia modifica presupuesto, audiencia, copy, medios o restricciones.
- **41** — se registra el motivo de cada cambio humano.
- **61** — se registra la propuesta original, los cambios humanos y el resultado posterior.
- **62** — los patrones repetidos se promueven a reglas de forma explícita.
- **75** — el aprendizaje usa exclusivamente la Organización y AdAccount activas.
- **76** — las campañas históricas se filtran por objetivo y evento comparables.
- **81** — una campaña sin evidencia comparable se etiqueta como exploratoria.
- **82** — se guardan campañas fuente, baseline, periodo, reglas, prompt, modelo y confianza.
- **85** — se rechaza una recomendación cuando no hay evidencia suficiente.

## Decisiones de implementación

- **Recuperación y ranking, no entrenamiento.** La primera implementación busca campañas
  anteriores parecidas y las ordena; no ajusta un modelo por cliente. Es lo que la spec maestra
  fija, y tiene la ventaja de que la evidencia es exhibible: se puede señalar con el dedo qué
  campañas sostienen la recomendación.
- **Sin agrupación entre clientes, sin excepciones.** La recuperación se restringe a la
  Organización y AdAccount activas. No hay conjunto global, ni "aprendizajes del rubro"
  derivados de otros clientes. Es una regla de aislamiento, no una preferencia de calidad.
- **La comparabilidad es una función determinista y explicable.** Dos campañas son comparables
  cuando comparten objetivo y evento de conversión —condición dura, sin la cual las métricas no
  se pueden poner en la misma escala— y se parecen lo suficiente en el resto de dimensiones. El
  parecido produce un grado, no un booleano, y ese grado es lo que ordena el conjunto. La
  función es código determinista: el LLM no decide qué es comparable.
- **Las combinaciones incompatibles se excluyen antes de puntuar.** Una campaña de tráfico y
  una de mensajería no son comparables por mucho que coincida el resto. Excluir es más barato y
  más honesto que ponderar hacia cero.
- **La confianza sale del conjunto, no de una impresión.** Se deriva del número de campañas
  comparables, de su grado de parecido, del volumen de conversaciones que aportan y de su
  dispersión. Se expone junto a la recomendación, siempre, también cuando es baja.
- **Sin evidencia suficiente no hay recomendación basada en evidencia.** El sistema lo dice y
  ofrece una campaña exploratoria, etiquetada como tal y fundada en buenas prácticas
  explícitas, no en un histórico que no existe. La spec maestra prioriza honestidad sobre
  cobertura.
- **Tres registros de lenguaje, marcados.** *Hecho observado*: algo que está en los datos.
  *Asociación*: dos cosas que aparecen juntas en el histórico. *Hipótesis*: una explicación
  posible que los datos no demuestran. El lenguaje causal solo se admite tras una prueba
  controlada, que este alcance no construye. La marca es estructural, no un matiz de redacción
  que el LLM pueda perder.
- **La trazabilidad de la propuesta se persiste al proponer, no al crear.** Campañas fuente,
  ventana temporal, versión de las reglas, versión de prompt, modelo y confianza quedan
  fijados en el momento en que se propone. El baseline congelado que define `06` forma parte de
  ese registro.
- **El motivo del cambio humano es un campo requerido y estructurado.** Se ofrece un conjunto
  acotado de motivos más un texto libre. El conjunto acotado es lo que permite detectar
  patrones; el texto libre es lo que evita que el conjunto acotado mienta.
- **La promoción a regla es una decisión humana registrada.** El sistema detecta que un mismo
  motivo de corrección se repite y lo propone como regla candidata, con las ocurrencias que la
  sostienen. Un operador la acepta o la rechaza. Convertir correcciones en política de forma
  automática sobreajusta a la última opinión y hace el comportamiento impredecible.
- **El Outcome loop se cierra sobre la versión aprobada.** El veredicto compara el resultado
  real contra el baseline congelado de esa versión, no contra el baseline vigente.

### Contradicción con ADR implícitas

- **ADR-03 (la IA nunca inventa cifras; el clasificador, el CPL objetivo, el riesgo y el
  veredicto son código determinista)** se preserva y se extiende: la comparabilidad, el ranking
  y la confianza se suman a la lista de piezas deterministas.
- **ADR-11 (aislamiento multi-tenant)** se extiende al aprendizaje: el aislamiento deja de ser
  solo de lectura de datos y pasa a ser también de derivación de conocimiento.

## Criterios de aceptación

1. Una propuesta muestra el conjunto identificable de campañas históricas que la respaldan, con
   su periodo y su costo por conversación. Sí/No.
2. Todas las campañas de ese conjunto pertenecen a la Organización y AdAccount activas. Sí/No.
3. Ninguna campaña de otra Organización o de otra AdAccount aparece en el conjunto, aunque el
   usuario tenga acceso a ambas. Sí/No.
4. Dos campañas con objetivos o eventos de conversión distintos nunca aparecen en el mismo
   conjunto comparable. Sí/No.
5. La propuesta declara el número de campañas comparables y su nivel de confianza. Sí/No.
6. Un conjunto pequeño o disperso produce un nivel de confianza bajo, declarado como tal. Sí/No.
7. Sin campañas comparables, el sistema no emite una recomendación basada en evidencia y
   propone una campaña marcada como exploratoria. Sí/No.
8. Las explicaciones marcan cada afirmación como hecho observado, asociación o hipótesis. Sí/No.
9. Ninguna explicación afirma causalidad sin una prueba controlada que la sostenga. Sí/No.
10. Cada propuesta persiste campañas fuente, ventana, versión de reglas, versión de prompt,
    modelo, confianza y baseline congelado. Sí/No.
11. Modificar una propuesta exige registrar un motivo, y sin motivo la modificación no se
    guarda. Sí/No.
12. El registro de una campaña creada permite comparar la propuesta original con la versión
    finalmente aprobada y ver qué cambió y por qué. Sí/No.
13. Tras la ventana de evaluación, el Outcome loop registra el resultado real y un veredicto
    comparado contra el baseline congelado de esa versión, no contra el vigente. Sí/No.
14. Un motivo de corrección que se repite genera una regla candidata con sus ocurrencias, y esa
    regla no entra en vigor hasta que un operador la acepta. Sí/No.
15. Rechazar una regla candidata deja constancia y no vuelve a proponerse de inmediato. Sí/No.
16. Una campaña histórica cuyo periodo está en el archivo frío puede formar parte del conjunto
    comparable. Sí/No.

## Decisiones de testing

- La frontera es la **API de propuesta y de revisión**, más el registro de trazabilidad
  consultable. Se verifican el conjunto comparable, el baseline y la trazabilidad; **no** el
  algoritmo interno de ranking, tal como fija la spec maestra.
- Los datasets son el entregable central y cubren los casos que nombra la spec maestra:
  campañas comparables, valores extremos, muestras pequeñas, temporadas distintas, cuentas
  múltiples y ausencia total de evidencia.
- El aislamiento se prueba con una Organización que tiene dos AdAccount pobladas y un usuario
  con acceso a ambas: el conjunto comparable de una nunca debe contener campañas de la otra.
- La marca de hecho, asociación e hipótesis se prueba como estructura de la respuesta, no
  buscando palabras en el texto redactado.
- El Outcome loop se prueba con el reloj controlado, comprobando que el veredicto usa el
  baseline congelado incluso después de recalibrar el vigente.
- Se reutilizan como prior art las pruebas existentes del sugeridor de campañas, del aprendizaje
  de campañas, del outcome y del planificador; las nuevas añaden la capa de recuperación y de
  evidencia sobre ellas.
- La promoción a regla se prueba por su comportamiento: repetición detectada, candidata
  propuesta, sin efecto hasta la aceptación.

## Dependencias

- `06` — la métrica primaria, el baseline por mediana, los umbrales de madurez y la
  preservación de los campos históricos en el archivo frío. Sin ellos no hay conjunto
  comparable utilizable a partir de cierta antigüedad.
- `03` — el versionado inmutable de la propuesta y la autorización de un solo uso, sobre los
  que se apoya el registro de cambios humanos y el cierre del Outcome loop.
- `10` — el confinamiento del LLM y el registro de versión de prompt y modelo, que forman parte
  de la trazabilidad de cada propuesta.

## Fuera de alcance

- Fine-tuning o entrenamiento de un modelo por cliente.
- Agrupación de datos o de aprendizajes entre Organizaciones.
- Pruebas controladas de tipo A/B que habiliten lenguaje causal. Esta spec prohíbe el lenguaje
  causal precisamente porque ese mecanismo no existe todavía.
- Garantizar ventas, ingresos, retorno o ROAS, y cualquier integración con CRM.
- La presentación conversacional de la evidencia al cliente: pertenece a `12`.
- Comparación de medios con los medios históricos del cliente más allá de tratarlos como una
  dimensión de comparabilidad.
- Apertura de verticales adicionales antes de validar el de mayor histórico.

## Riesgos y rollback

- **Daño máximo si no se hace:** el producto recomienda sin poder mostrar en qué se basa, y una
  recomendación fundada en dos campañas de otra temporada se presenta igual que una fundada en
  treinta. El cliente gasta contra una correlación.
- **Daño máximo al hacerlo:** una función de comparabilidad demasiado laxa mete campañas
  incompatibles en el conjunto y produce baselines engañosos con apariencia de rigor, que es
  peor que no tener baseline. Demasiado estricta, deja a toda cuenta pequeña en modo
  exploratorio permanente.
- **Activación:** la recuperación se enciende primero en modo consultivo para la agencia
  —mostrar la evidencia sin que altere la propuesta— durante varios ciclos de propuesta real.
  Se conecta a la generación de la propuesta cuando los operadores confirmen que el conjunto
  recuperado es el que ellos habrían mirado. El registro del motivo del cambio humano puede
  activarse antes, de forma independiente, porque solo añade información.
- **Rollback:** apagar la recuperación devuelve la propuesta actual basada en agregados y
  reglas. Los registros de trazabilidad, motivos y outcome son aditivos y se conservan; volver
  atrás no pierde el aprendizaje ya recogido.

## Checklist de finalización

- [x] Migración del registro de trazabilidad de propuesta, del motivo estructurado de cambio
      humano y de las reglas candidatas.
- [x] Función determinista de comparabilidad, con sus dimensiones y exclusiones documentadas
      como contrato.
- [x] Pruebas de sistema de los dieciséis criterios, con los datasets versionados junto a ellas.
- [x] Documentación de producto y del motor de IA: qué es una campaña comparable, cómo se
      calcula la confianza y qué significa exploratoria.
- [x] Documento de decisiones arquitectónicas actualizado: extensión de ADR-03 y de ADR-11 al
      aprendizaje.
- [x] Observabilidad: tamaño y confianza del conjunto comparable por propuesta, proporción de
      propuestas exploratorias, tasa de modificación humana por motivo y veredictos del Outcome
      loop.
- [ ] Evidencia del periodo en modo consultivo con operadores reales antes de conectar la
      recuperación a la propuesta. **Pendiente: la recuperación ya es consultiva; conectarla
      exige esos ciclos de observación.**

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Aprendizaje, baseline y outcome*
(párrafos de recuperación y ranking, comparabilidad, explicaciones, campañas exploratorias y
Outcome loop); *Solution* (párrafo del aprendizaje inicial como recuperación y ranking);
*Testing Decisions* (párrafo del aprendizaje con datasets); *Entrega por fases* → **P2**.
