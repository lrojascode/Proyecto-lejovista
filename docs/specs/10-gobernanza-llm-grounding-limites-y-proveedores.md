# 10 · Gobernanza del LLM: grounding, límites, proveedores y consumo

## Estado

Completada. Los dieciocho criterios están implementados y verificados
(`backend/tests/test_spec10_gobernanza_llm.py`, 82 pruebas).

**Lo que más costó no fue el grounding sino la selección de proveedor.** El
mecanismo derogado no era una función: eran dos cascadas repartidas en un método
de 60 líneas, y toda la suite las daba por buenas. Tres pruebas afirmaban
explícitamente el comportamiento prohibido —"la clave presente elige el
proveedor", "una key sin proveedor igual se usa"— y hubo que reescribirlas contra
el contrato nuevo en vez de borrarlas: un test que afirma lo contrario de lo que
antes se garantizaba es la mejor defensa contra que el mecanismo vuelva.

**El invariante acabó en el tipo, no en los sitios de creación.** Fijar el
proveedor en `admin.py` y `auth.py` funcionaba y dejaba fuera cualquier camino
futuro; además, unas cuarenta pruebas construyen `Organization` en memoria y
resolvían distinto de una fila guardada. Se movió a `Organization.__init__` —no a
`default=` de la columna, que solo se aplica al INSERT—, así que "una Organización
nace con proveedor declarado" vale para el objeto y no solo para la fila.

**Tres sustituciones silenciosas, no una.** La spec nombra el cambio de
proveedor. Al implementarla aparecieron dos más del mismo tipo, ninguna anotada
en el issue maestro:

- `pick_model` reemplazaba por `candidates[0]` un modelo ausente del catálogo del
  proveedor activo. En OpenRouter el primer candidato podía costar un múltiplo
  del configurado, y el único rastro quedaba en la factura.
- La pantalla de Configuración hacía lo mismo al cargar: mostraba el primero de
  la lista cuando el modelo guardado no estaba, de modo que **guardar sin tocar
  nada cambiaba el modelo**. Colarse por la interfaz no lo hace menos silencioso.

Las dos se quitaron. Ocultar del desplegable un modelo retirado tampoco sirve: el
`select` se queda sin opción que coincida con su valor y el navegador muestra la
primera, que es la misma sustitución cortesía del HTML. Se marca, no se oculta.

**Lo que la guarda numérica no podía ver.** Los criterios 2 y 3 existen porque un
mes se escribe con letras y un nombre de cuenta también: *"en julio el CPL fue
S/ 12.40"* pasa la validación entera si 12.40 está en los datos de agosto, y una
respuesta que nombra una cuenta hermana tiene **todas sus cifras respaldadas y la
conclusión falsa**. Las cuentas hermanas hay que cargarlas explícitamente; sin
ellas solo se puede comprobar que la cuenta activa aparece, que no es lo mismo.

**El motivo del descarte hacía falta, no solo el descarte.** `numbers_are_grounded`
devolvía un booleano y dejaba el porqué en el log. Que el modelo invente un
identificador y que calcule un promedio son fallos distintos, se arreglan en
sitios distintos, y con un motivo único la diferencia no llegaba a ninguna parte
consultable.

**Un fallo ajeno encontrado de paso**, fuera del alcance de esta spec y anotado
sin corregir: `token_inspection._notify_admins_by_email` llama a `send_email` sin
`await` y sin `html_content`, así que **el aviso de token por vencer nunca se
envió**. La excepción queda tragada por el `try/except` de alrededor. Es la razón
por la que la alerta de presupuesto de esta spec se probó comprobando que el
envío se aguarda de verdad, y no solo que se calcula el umbral.

### Deudas que se llevan

- El **modo de registro** (`GROUNDING_MODO_REGISTRO`) está implementado y
  probado, pero la evidencia sobre tráfico real es trabajo de despliegue: hay que
  correrlo unos días y leer `GET /ai-usage/discards` antes de encender el
  descarte. Hasta entonces, el último punto de la lista de finalización sigue
  abierto.
- El **presupuesto de IA queda en `NULL` (sin techo) para toda organización
  existente**. Es deliberado —un bloqueo retroactivo sobre quien nunca lo acordó
  le cortaría el producto de un despliegue al otro— pero significa que el
  criterio 14 no protege a nadie hasta que se fije un techo por cliente.
- El **consumo histórico no se convierte** a la columna exacta: la precisión
  perdida en coma flotante no se recupera. El primer mes cuenta desde el
  despliegue.
- La **detección de promesas de éxito visual reconoce las formas explícitas**, no
  es un clasificador semántico. Lo que sí queda garantizado por completo es el
  lado determinista. Decir que es hermética sería mentir sobre una guarda.
- El **conjunto de evaluación no mide costo**: no llama al modelo. La dimensión
  queda declarada como `None` en vez de inventar un cero, y se compara cuando haya
  una corrida contra el proveedor real.

## Prioridad

P2.

## Objetivo

Que el LLM quede confinado a lo que sabe hacer —interpretar lenguaje, elegir dentro de
catálogos cerrados y redactar— sin poder introducir cifras, ampliar su espacio de acciones,
cambiar de proveedor o de modelo en silencio, ni consumir la clave del cliente sin techo.

## Problema

El sistema ya sostiene AI-1: los números salen de datos agregados y el LLM redacta. La guarda
de respaldo numérico existe y funciona en el chat de análisis. Quedan cuatro huecos.

**El proveedor se elige por qué clave está presente.** Una clave añadida o retirada en la
configuración cambia el proveedor, el modelo y el costo sin que nadie lo decida
explícitamente. La spec maestra lo prohíbe: los cambios de proveedor o modelo no pueden ser
silenciosos.

**No hay catálogo de modelos.** El modelo predeterminado de una Organización nueva asume un
proveedor concreto, y una Organización que use otro debe cambiarlo a mano. La tabla de precios
para estimar costos vive en el código; un modelo ausente de ella se registra sin costo estimado.
Retirar un modelo obligado por el proveedor exige tocar varios sitios.

**El modo simulado es indistinguible desde fuera en algunos caminos.** El sistema tiene modos
de simulación y de caída a texto de plantilla, con marcas parciales. La spec maestra exige que
en producción no se presente contenido simulado como análisis real, sin excepción.

**El consumo de IA no tiene techo.** Se registra por llamada, con tokens, modelo y costo
estimado, pero no hay presupuesto mensual, ni alertas, ni bloqueo. La clave es del cliente: un
bucle o un uso abusivo se le factura a él.

## Alcance

- Confinamiento del LLM: interpretación de texto libre, selección dentro de catálogos cerrados,
  redacción y propuesta de alternativas, siempre dentro de un contrato validado.
- Rechazo de salidas numéricas sin respaldo y de opciones fuera de catálogo.
- Prohibición efectiva de contenido simulado presentado como análisis real en producción.
- Registro por respuesta de proveedor, modelo, versión de prompt, uso y costo.
- Selección explícita y versionada de proveedor y modelo por Organización, sin cambios
  implícitos.
- Catálogo configurable de modelos permitidos, con capacidades, precio, disponibilidad y
  estado de retiro.
- Presupuesto mensual de consumo de IA por Organización, con alertas al 70 %, 90 % y 100 % y
  bloqueo seguro.
- Límites de frecuencia y de reintentos de las llamadas al modelo.
- Conjunto de evaluación con casos reales anonimizados, ejecutado antes de publicar un cambio
  de prompt, de reglas o de modelo.

## Historias de usuario

Tomadas de la spec maestra:

- **6** — cada cifra del chat proviene de datos sincronizados.
- **17** — el sistema no promete que una imagen será exitosa por su apariencia.
- **51** — límite de consumo de IA por Organización.
- **52** — alertas de consumo al 70 %, 90 % y 100 %.
- **83** — el LLM no inventa cifras, presupuestos ni periodos.
- **84** — toda salida del LLM se valida contra catálogos y grounding.
- **86** — no hay respuestas simuladas en producción.
- **87** — se registra proveedor, modelo, prompt y costo de cada respuesta.
- **88** — no hay cambios silenciosos de proveedor o modelo.
- **89** — prompts, reglas y modelos se evalúan con casos reales anonimizados antes de
  publicarse.
- **90** — catálogo configurable de modelos permitidos, capacidades, precios y estado.

## Decisiones de implementación

- **El contrato de salida precede a la llamada.** Toda invocación al modelo declara qué forma
  puede tener su respuesta: qué campos, qué tipos y, para los campos de elección, qué valores
  concretos son admisibles. Lo que no encaja se descarta; no se corrige ni se reinterpreta. Un
  descarte es un fallo registrado, no un silencio.
- **Grounding como condición, no como filtro posterior.** Toda cifra en una salida debe
  corresponder a un valor del conjunto de hechos autorizados para esa respuesta. Una cifra sin
  respaldo invalida la salida completa, porque una respuesta parcialmente inventada no es
  parcialmente confiable. El mismo criterio se extiende al periodo y a la cuenta: cambiar la
  ventana o mezclar AdAccount es una invención tanto como cambiar un número.
- **El proveedor y el modelo son configuración explícita por Organización, versionada.** Dejan
  de derivarse de qué clave esté presente. Una Organización tiene un proveedor y un modelo
  declarados; si la credencial de ese proveedor falta, el sistema falla de forma visible en vez
  de usar otro. Cambiar cualquiera de los dos es una operación registrada con actor y momento.
- **El catálogo es dato, no código.** Los modelos admisibles, sus capacidades, su precio por
  unidad y su estado —disponible, en retirada, retirado— viven en configuración administrable.
  El selector solo ofrece modelos del proveedor activo y no retirados. Retirar un modelo es un
  cambio de dato, y las Organizaciones que lo usen quedan señaladas en vez de romperse en
  silencio.
- **La simulación es un estado del entorno, no un modo de respuesta.** En producción, una
  respuesta que no proviene de una llamada real al modelo no se presenta como análisis. La caída
  a un texto determinista sigue existiendo cuando es segura, pero se identifica como tal en la
  respuesta y en el registro, y nunca lleva cifras que no vengan de datos.
- **El presupuesto de IA es una compuerta con avisos escalonados.** Se expresa en la moneda de
  costo del proveedor y usa la representación exacta que define `02`. Al 70 % y al 90 % se
  avisa a la agencia; al 100 % se bloquean las llamadas no esenciales, se conserva la lectura
  determinista del producto y se informa con honestidad. El bloqueo es de backend y no lo evita
  ninguna superficie.
- **Límites de frecuencia y de reintentos por Organización.** El reintento de una llamada al
  modelo tiene tope y retroceso; un bucle de reintentos es la forma más barata de consumir el
  presupuesto de un cliente en minutos.
- **El conjunto de evaluación es un requisito de publicación.** Preguntas, briefs, negativas,
  casos de datos insuficientes y casos de límite, anonimizados. Un cambio de prompt, de reglas
  o de modelo se compara contra la línea base y no puede degradar grounding, seguridad ni costo
  fuera de umbrales aprobados. Sin este conjunto, cambiar un prompt es una apuesta.
- **Cada respuesta deja su ficha.** Proveedor, modelo, versión de prompt, tokens de entrada y
  salida, tokens de razonamiento y costo. Es lo que permite investigar una regresión de calidad
  o un pico de gasto sin conjeturar.

### Contradicción con ADR implícitas

- **ADR-04 (cliente LLM unificado con auto-selección de proveedor por clave presente)** queda
  derogada en su mecanismo de selección. La portabilidad entre proveedores compatibles se
  conserva —sigue siendo el motivo por el que existe un cliente unificado— pero la elección pasa
  a ser explícita por Organización. Es la contradicción más directa entre la spec maestra y las
  ADR existentes.
- **ADR-03 (la IA nunca inventa cifras)** se preserva y se refuerza: se extiende de las cifras
  al periodo, a la cuenta y al espacio de opciones.

## Criterios de aceptación

1. Una respuesta del modelo que incluye una cifra ausente del conjunto de hechos autorizados se
   descarta por completo y el descarte queda registrado. Sí/No.
2. Una respuesta que cambia el periodo consultado se descarta. Sí/No.
3. Una respuesta que menciona datos de una AdAccount distinta de la activa se descarta. Sí/No.
4. Una respuesta que propone un valor fuera del catálogo cerrado correspondiente se descarta.
   Sí/No.
5. Ninguna respuesta del sistema afirma que una creatividad será exitosa por su apariencia.
   Sí/No.
6. En producción, ninguna respuesta simulada se presenta como análisis real; una caída a texto
   determinista se identifica como tal. Sí/No.
7. Cada llamada real al modelo deja registrados proveedor, modelo, versión de prompt, uso y
   costo. Sí/No.
8. Retirar la clave del proveedor configurado produce un fallo visible, no un cambio de
   proveedor. Sí/No.
9. Añadir la clave de otro proveedor no cambia el proveedor de ninguna Organización. Sí/No.
10. Cambiar el proveedor o el modelo de una Organización queda registrado con actor y momento.
    Sí/No.
11. El selector de modelos solo ofrece modelos del proveedor activo y no retirados. Sí/No.
12. Marcar un modelo como retirado en el catálogo impide seleccionarlo y señala a las
    Organizaciones que lo usaban, sin romperlas en silencio. Sí/No.
13. Al alcanzar el 70 % y el 90 % del presupuesto mensual de IA se emite una alerta a la
    agencia. Sí/No.
14. Al alcanzar el 100 % se bloquean las llamadas al modelo para esa Organización y el producto
    lo comunica con honestidad. Sí/No.
15. Con el presupuesto de IA agotado, las respuestas deterministas seguras del producto siguen
    funcionando. Sí/No.
16. Un bucle de reintentos no puede superar el tope de reintentos ni el límite de frecuencia
    configurados. Sí/No.
17. El presupuesto de IA se reinicia al comenzar el mes. Sí/No.
18. Existe un conjunto de evaluación anonimizado y una ejecución registrada que compara un
    cambio candidato contra la línea base en grounding, seguridad y costo. Sí/No.

## Decisiones de testing

- La frontera es la **respuesta que el producto entrega**, no la salida cruda del modelo. Se
  prueba con un adaptador de proveedor controlado que devuelve salidas hostiles construidas a
  propósito: cifras ausentes, periodo cambiado, cuenta mezclada, presupuesto excedido y
  opciones fuera de catálogo. La spec maestra nombra exactamente esos cinco.
- Se reutilizan como prior art las pruebas existentes de grounding del chat, de integración con
  el LLM, de seguimiento de tokens y de coherencia de respuestas; las nuevas amplían el corpus
  hostil en vez de reescribir la guarda.
- El presupuesto de IA se prueba desde la superficie que consume IA, con bordes decimales y con
  el reloj controlado para el reinicio mensual, igual que la compuerta de `02`.
- La ausencia de cambios silenciosos de proveedor se prueba manipulando la configuración de
  claves y comprobando que el proveedor efectivo no cambia: es una prueba sobre el
  comportamiento del sistema ante su propia configuración.
- El conjunto de evaluación no es una prueba de CI de cada commit: es un procedimiento de
  publicación con su propio registro de resultados, ejecutado cuando cambia un prompt, una
  regla o un modelo.
- No se prueba la calidad del texto redactado. Se prueba que no puede introducir hechos.

## Dependencias

- `02` — la representación exacta de dinero que usa el presupuesto de IA.
- `05` — el proveedor, el modelo y el consumo son configuración de la agencia y no se muestran
  al cliente.
- `06` — el conjunto de hechos autorizados sobre el que se valida el grounding se apoya en la
  métrica primaria y en los estados definidos allí.

## Fuera de alcance

- Fine-tuning o entrenamiento de un modelo por cliente.
- La superficie conversacional, sus estados y su comportamiento proactivo: pertenecen a `12`.
- La recuperación de campañas comparables y el ranking de evidencia: pertenecen a `11`.
- Elección del proveedor concreto para el primer cliente, que es una decisión comercial.
- Un mecanismo genérico de feature flags: pertenece a `13`. Esta spec despliega cambios de
  modelo por Organización y lo consumirá cuando exista.
- Autonomía del LLM sobre acciones irreversibles, expresamente excluida por la spec maestra.

## Riesgos y rollback

- **Daño máximo si no se hace:** una respuesta con cifras inventadas lleva al cliente a decidir
  gasto sobre datos falsos, un cambio de configuración cambia el modelo y multiplica el costo
  sin aviso, y un bucle de reintentos agota la clave del cliente.
- **Daño máximo al hacerlo:** un grounding demasiado estricto descarta respuestas legítimas y
  el analista deja de analizar. Ya ocurrió una vez por un tope mal calibrado en el conjunto de
  hechos, con el síntoma de respuestas de plantilla y la causa en la guarda, no en el modelo.
- **Activación:** el endurecimiento del grounding se despliega en modo de registro, midiendo
  qué descartaría sobre tráfico real, antes de descartar. La selección explícita de proveedor se
  activa fijando en cada Organización el proveedor que hoy usa de facto, de modo que el cambio
  no altere el comportamiento el primer día. El presupuesto de IA se despliega con alertas
  antes que con bloqueo.
- **Rollback:** cada pieza tiene su interruptor de configuración: modo de registro del
  grounding, bloqueo del presupuesto y límites de frecuencia. El catálogo de modelos es dato y
  se revierte editándolo. Volver a la auto-selección de proveedor no se ofrece como rollback:
  es precisamente el comportamiento que la spec maestra prohíbe.

## Checklist de finalización

- [x] Migración que fija proveedor y modelo explícitos por Organización, poblada con el valor
      efectivo actual de cada una.
- [x] Catálogo de modelos administrable, con capacidades, precio, disponibilidad y estado.
- [x] Migración del presupuesto mensual de IA por Organización con representación exacta.
- [x] Pruebas de sistema de los dieciocho criterios, con el corpus de salidas hostiles
      versionado.
- [x] Conjunto de evaluación anonimizado versionado, con procedimiento de publicación y registro
      de resultados.
- [x] Documentación del motor de IA y de servicios externos actualizada.
- [x] Documento de decisiones arquitectónicas actualizado: derogación del mecanismo de
      auto-selección de ADR-04.
- [x] Observabilidad: descartes por grounding y por catálogo, consumo y margen de IA por
      Organización, costo por funcionalidad y tasa de caída a respuesta determinista.
- [ ] Evidencia del modo de registro del grounding sobre tráfico real antes de activar el
      descarte. **Pendiente: es trabajo de despliegue.**

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Límites del LLM*; *Meta API y
proveedores* (último párrafo, catálogo de modelos); *Solution* (párrafo de responsabilidad del
código determinista); *Testing Decisions* (párrafos de grounding y de proveedores de IA);
*Entrega por fases* → **P2**.
