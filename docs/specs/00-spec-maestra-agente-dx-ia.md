# 00 · Spec maestra — Agente DX IA

> **Copia fiel** del GitHub Issue [#2](https://github.com/lrojascode/Proyecto-lejovista/issues/2)
> — *«Spec: Agente DX IA — producto conversacional seguro y copiloto basado en histórico»*.
>
> Recuperada el 2026-08-31 con `gh issue view 2 --comments`. El issue no tiene comentarios;
> todo el contenido de requisitos vive en el cuerpo.
>
> Este archivo **no se edita**. Es la fuente de requisitos de las specs `01`–`14` de este
> directorio. Cualquier corrección se hace en el issue y se vuelve a copiar aquí.

---

## Problem Statement

Agente DX IA necesita convertirse en un producto coherente para dos actores con necesidades distintas. El cliente final no necesita operar Meta Ads ni comprender infraestructura, modelos de IA o diagnósticos técnicos: necesita conversar con el sistema, entender el rendimiento de sus campañas y solicitar nuevas campañas con confianza. La agencia, en cambio, necesita acceso operativo completo para configurar organizaciones, investigar datos, revisar propuestas, controlar presupuestos, aprobar campañas y responder ante incidentes.

El producto actual ya contiene sincronización histórica, análisis determinista, chat, creación mediante Saga, módulos configurables y compuertas humanas, pero mezcla las alturas de cliente y agencia, expone conceptos técnicos al cliente y presenta contradicciones entre algunas promesas y el comportamiento real. El límite mensual de presupuesto no se aplica como una garantía, algunas clasificaciones pueden utilizar muestras insuficientes, el archivo frío pierde información necesaria para el aprendizaje, la eliminación declarada no coincide con toda la persistencia y existen riesgos en autenticación, concurrencia e idempotencia que podrían permitir acceso indebido o duplicar escrituras en Meta.

La propuesta de valor depende de aprender de campañas pasadas del mismo cliente para reducir el costo por conversación de WhatsApp. Sin una definición reproducible de campaña comparable, baseline, muestra mínima, trazabilidad y nivel de confianza, el producto corre el riesgo de confundir correlación con causalidad o llamar exitosa a una campaña con muy poco volumen. El LLM tampoco puede ser la autoridad de cifras, presupuesto, permisos o acciones irreversibles.

La operación parte con un cliente y un VPS limitado a 2 vCPU, 4 GB de RAM y 100 GB de almacenamiento. No es razonable ejecutar un staging permanente en ese mismo servidor ni construir alta disponibilidad prematuramente, pero sí se necesitan backups externos, restauraciones comprobadas, despliegues reproducibles, rollback y degradación segura. Producción ya utiliza Meta Graph/Marketing API v26, mientras la configuración predeterminada y documentación del repositorio todavía mencionan v25, creando riesgo de regresión durante un despliegue.

Finalmente, el modelo comercial actual es un pago por implementación, no una mensualidad. La implementación incluirá 60 días de garantía para defectos; infraestructura, consumo de IA y mantenimiento posterior pertenecen al cliente o requieren una renovación separada. El producto debe expresar esa responsabilidad sin crear un SLA indefinido implícito.

## Solution

Agente DX IA será un sistema de diagnóstico y un copiloto de creación de campañas de Meta orientadas inicialmente a conversaciones de WhatsApp en Perú. Tendrá una sola plataforma y una sola fuente de datos, pero dos experiencias claramente diferenciadas y gobernadas por roles: una experiencia conversacional y simplificada para el cliente, y una experiencia operativa completa para la agencia. Los módulos seguirán configurándose por organización sin reemplazar ni debilitar los permisos.

El chat será la superficie principal de valor. Permitirá al cliente preguntar por sus campañas, recibir avisos proactivos, aportar un brief y medios, y solicitar una nueva campaña. La agencia revisará la evidencia histórica, modificará la propuesta cuando corresponda y aprobará la creación. Toda campaña se creará pausada. Cualquier modificación material invalidará la aprobación previa y obligará a recalcular presupuesto, riesgo y preflight antes de una nueva confirmación.

El aprendizaje inicial será recuperación y ranking, no fine-tuning. Solo utilizará campañas del mismo cliente y de la misma cuenta publicitaria. Filtrará por objetivo y evento comparables, excluirá datos insuficientes o anómalos, comparará contexto de negocio, audiencia, ubicación, formato, copy, medio, presupuesto y periodo, y presentará las campañas históricas que respaldan la recomendación. La IA distinguirá hechos, asociaciones e hipótesis; nunca afirmará causalidad que los datos no demuestren.

La métrica primaria será el costo por conversación de WhatsApp atribuida por Meta. El baseline utilizará la mediana de campañas comparables, no un promedio sensible a valores extremos. De forma predeterminada, una campaña requerirá siete días y al menos diez conversaciones para considerarse validada. Antes de ese umbral será insuficiente o prometedora. Cada nueva campaña conservará un baseline congelado, las campañas y reglas que la justificaron y la versión de prompt/modelo; los baselines utilizados para campañas futuras podrán evolucionar con temporada y variantes.

Las cifras, elegibilidad, presupuesto, permisos, límites, baseline, riesgo y validaciones seguirán siendo responsabilidad de código determinista. El LLM interpretará lenguaje libre, extraerá el brief, redactará explicaciones y propondrá alternativas sobre datos previamente autorizados. Todas sus salidas estructurales atravesarán catálogos, grounding y validadores. En producción no existirán respuestas mock presentadas como análisis real ni cambios silenciosos de proveedor o modelo.

Antes de ampliar la base de clientes o habilitar más write-back se cerrarán los riesgos P0: separación estricta de tokens, revocación de sesiones, suspensión efectiva, invitaciones atómicas, idempotencia de escrituras, presupuesto mensual real, dinero exacto, backups restaurables y trazabilidad. La operación se diseñará para las primeras 20–50 cuentas publicitarias sin alta disponibilidad prematura. CI y desarrollo local cumplirán el papel de preproducción; los cambios mayores de proveedores podrán validarse en infraestructura temporal. El VPS productivo solo recibirá imágenes inmutables verificadas, migraciones compatibles y despliegues reversibles.

## User Stories

1. Como cliente, quiero ingresar mediante una invitación preparada por la agencia, para empezar con mi organización ya configurada.
2. Como cliente, quiero ver únicamente los módulos habilitados para mi organización, para no enfrentar herramientas que no necesito o no contraté.
3. Como cliente, quiero conversar en lenguaje natural sobre mis campañas, para entender resultados sin aprender Meta Ads.
4. Como cliente, quiero recibir un resumen proactivo al abrir el chat, para encontrar valor sin saber qué pregunta formular.
5. Como cliente, quiero preguntar qué campañas mejoraron o empeoraron, para priorizar mi atención.
6. Como cliente, quiero que cada cifra del chat provenga de datos sincronizados, para confiar en las respuestas.
7. Como cliente, quiero saber el periodo y la cuenta utilizados en una respuesta, para interpretar correctamente el resultado.
8. Como cliente, quiero que el producto llame conversión a una conversación de WhatsApp atribuida por Meta, para no confundirla con una venta.
9. Como cliente, quiero ver el costo por conversación frente a un baseline comparable, para entender si el resultado es realmente bueno.
10. Como cliente, quiero distinguir entre datos insuficientes, campaña prometedora, validada y por debajo del baseline, para no sacar conclusiones prematuras.
11. Como cliente, quiero saber cuándo los datos están desactualizados o incompletos, para no decidir sobre una falsa sensación de actualidad.
12. Como cliente, quiero solicitar una campaña desde el chat, para evitar formularios publicitarios complejos.
13. Como cliente, quiero explicar el objetivo comercial, la oferta y las restricciones de la campaña, para que la propuesta represente el negocio real.
14. Como cliente, quiero adjuntar imágenes o videos al brief, para que el sistema comprenda el producto y la creatividad disponibles.
15. Como cliente, quiero revisar qué contexto extrajo el sistema de mis medios, para corregir interpretaciones equivocadas.
16. Como cliente, quiero que el sistema verifique formato y coherencia de una creatividad, para reducir rechazos y errores técnicos.
17. Como cliente, quiero que el sistema no prometa que una imagen será exitosa solo por su apariencia, para recibir recomendaciones honestas.
18. Como cliente, quiero ver el estado de una solicitud de campaña, para saber si está en revisión, aprobada, creándose, creada o fallida.
19. Como cliente, quiero que una campaña creada quede pausada, para que la agencia pueda verificarla antes de gastar.
20. Como cliente, quiero ver el límite mensual de presupuesto establecido por la agencia, para conocer la protección financiera disponible.
21. Como cliente, quiero recibir un mensaje claro cuando la IA o Meta no estén disponibles, para no confundir un fallo técnico con ausencia de datos.
22. Como cliente, quiero que el chat conserve respuestas deterministas durante una caída del LLM, para seguir consultando hechos básicos cuando sea seguro.
23. Como cliente, quiero recibir avisos sobre anomalías, deterioro y oportunidades, para actuar antes de que el problema crezca.
24. Como cliente, quiero exportar mis datos al finalizar la relación, para conservar mi información comercial.
25. Como cliente, quiero confirmación de que mis credenciales y datos fueron eliminados según la política acordada, para cerrar la relación con confianza.
26. Como operador de agencia, quiero una experiencia operativa separada de la experiencia del cliente, para acceder a herramientas profundas sin exponerlas al cliente.
27. Como operador de agencia, quiero conservar acceso operativo a todos los módulos necesarios, aunque estén ocultos para el cliente, para poder prestar el servicio.
28. Como administrador de plataforma, quiero habilitar módulos por organización, para ajustar el producto a cada implementación.
29. Como administrador de plataforma, quiero que módulos y roles sean controles independientes, para que ocultar una función no sustituya la autorización.
30. Como administrador de plataforma, quiero dar de alta una organización mediante un flujo asistido, para validar Meta, IA, WhatsApp y permisos antes de invitar al cliente.
31. Como administrador de plataforma, quiero configurar las credenciales propiedad del cliente sin mostrarlas en su interfaz, para reducir complejidad y exposición.
32. Como administrador de plataforma, quiero asignar roles y accesos por cuenta publicitaria, para aplicar mínimo privilegio.
33. Como operador de agencia, quiero cambiar explícitamente de AdAccount, para no mezclar datos entre cuentas de una misma organización.
34. Como operador de agencia, quiero consultar métricas, breakdowns, Insights, AIAction, Drafts y Outcome loop, para investigar el rendimiento completo.
35. Como operador de agencia, quiero ver qué campañas históricas respaldan una propuesta, para revisar la evidencia antes de aprobar.
36. Como operador de agencia, quiero conocer la cantidad de campañas comparables y el nivel de confianza, para detectar recomendaciones débiles.
37. Como operador de agencia, quiero distinguir hechos observados, asociaciones e hipótesis, para no presentar correlaciones como causas.
38. Como operador de agencia, quiero ver el baseline congelado de una campaña nueva, para evaluarla contra la promesa con la que nació.
39. Como operador de agencia, quiero que los baselines futuros cambien por temporada y variantes, para no quedar atado a condiciones antiguas.
40. Como operador de agencia, quiero modificar presupuesto, audiencia, copy, medios o restricciones de una propuesta, para aplicar criterio humano.
41. Como operador de agencia, quiero registrar el motivo de cada cambio humano, para convertir la revisión en aprendizaje auditable.
42. Como operador de agencia, quiero que un cambio material invalide la aprobación anterior, para evitar ejecutar una versión distinta de la aprobada.
43. Como operador de agencia, quiero que cada versión modificada repita presupuesto, riesgo, preflight y validaciones, para mantener las compuertas de seguridad.
44. Como operador de agencia, quiero aprobar exactamente una versión identificable, para que la autorización no sea ambigua.
45. Como operador de agencia, quiero que una aprobación pueda consumirse una sola vez, para evitar ejecuciones duplicadas.
46. Como operador de agencia, quiero que una respuesta incierta de Meta quede como verificándose, para no reintentar ciegamente una creación.
47. Como operador de agencia, quiero reconciliar con Meta antes de repetir un POST, para evitar campañas, adsets, creativos o anuncios duplicados.
48. Como operador de agencia, quiero que el sistema registre los identificadores finales de Meta, para reconstruir cada escritura.
49. Como operador de agencia, quiero que el límite mensual bloquee realmente creación y escalado, para cumplir la garantía financiera mostrada.
50. Como operador de agencia, quiero que los montos críticos se comparen con precisión decimal, para evitar errores de autorización por redondeo binario.
51. Como operador de agencia, quiero administrar un límite de consumo de IA por organización, para controlar uso accidental o abusivo de la key del cliente.
52. Como operador de agencia, quiero recibir alertas de consumo de IA al 70 %, 90 % y 100 %, para actuar antes del bloqueo.
53. Como operador de agencia, quiero suspender una organización de forma efectiva, para detener login, API, sync, IA y write-back.
54. Como administrador de plataforma, quiero conservar acceso administrativo a una organización suspendida, para exportar, investigar o reactivarla.
55. Como operador de agencia, quiero revocar credenciales al suspender o eliminar una organización, para cortar accesos externos.
56. Como operador de agencia, quiero ver diagnósticos técnicos durante un incidente, para resolverlo sin exponer detalles al cliente.
57. Como operador de agencia, quiero activar modo solo lectura global o por organización, para contener incidentes sin apagar toda la plataforma.
58. Como operador de agencia, quiero interruptores separados para creación, write-back, sync, IA y medios, para aislar la capacidad afectada.
59. Como operador de agencia, quiero activar cambios mediante feature flags por organización, para realizar rollouts progresivos.
60. Como operador de agencia, quiero probar nuevas capacidades primero con una cuenta piloto, para limitar el daño máximo.
61. Como operador de agencia, quiero registrar la propuesta original, los cambios humanos y el resultado posterior, para mejorar las reglas con evidencia.
62. Como operador de agencia, quiero promover patrones repetidos a reglas explícitas, para no convertir automáticamente cada corrección individual en política.
63. Como responsable de seguridad, quiero que un refresh token no pueda utilizarse como access token, para preservar la separación de credenciales.
64. Como responsable de seguridad, quiero que cambiar o restablecer la contraseña revoque todas las sesiones, para expulsar accesos comprometidos.
65. Como responsable de seguridad, quiero que la aceptación de invitaciones sea atómica y de un solo uso, para evitar carreras y reutilización.
66. Como responsable de seguridad, quiero restricciones únicas para membresías, para impedir duplicados bajo concurrencia.
67. Como responsable de seguridad, quiero que el token de WebSocket no viaje en una URL registrable, para reducir exposición en logs y proxies.
68. Como responsable de seguridad, quiero que una caída del mecanismo de revocación bloquee renovaciones y escrituras sensibles, para fallar de forma segura.
69. Como responsable de seguridad, quiero que tokens, teléfonos completos, credenciales y URLs firmadas se eliminen de los logs, para minimizar datos sensibles.
70. Como responsable de privacidad, quiero eliminar credenciales inmediatamente al dar de baja una organización, para cerrar accesos externos.
71. Como responsable de privacidad, quiero eliminar histórico y medios dentro de 30 días, para cumplir la política comunicada.
72. Como responsable de privacidad, quiero conservar únicamente logs de seguridad sanitizados hasta 90 días, para equilibrar investigación y minimización.
73. Como responsable de privacidad, quiero que cualquier retención superior tenga una base contractual o legal visible, para no conservar datos silenciosamente.
74. Como sistema, quiero conservar sin pérdida los campos históricos utilizados por el aprendizaje, para que el archivo frío no degrade recomendaciones futuras.
75. Como sistema, quiero aprender exclusivamente de la organización y AdAccount activas, para no mezclar datos entre clientes.
76. Como sistema, quiero filtrar campañas históricas por objetivo y evento comparables, para no enfrentar métricas incompatibles.
77. Como sistema, quiero excluir campañas con datos insuficientes o anómalos, para evitar baselines engañosos.
78. Como sistema, quiero usar la mediana del costo por conversación comparable, para reducir la influencia de outliers.
79. Como sistema, quiero exigir por defecto siete días y diez conversaciones para validar una campaña, para disponer de una muestra mínima.
80. Como sistema, quiero considerar también volumen y gasto suficiente, para no premiar una única conversación barata.
81. Como sistema, quiero etiquetar como exploratoria una campaña sin evidencia comparable, para distinguir buenas prácticas de aprendizaje propio.
82. Como sistema, quiero guardar las campañas fuente, baseline, periodo, reglas, prompt, modelo y confianza de cada propuesta, para asegurar trazabilidad.
83. Como sistema, quiero impedir que el LLM invente cifras, presupuestos o periodos, para mantener AI-1.
84. Como sistema, quiero validar toda salida del LLM contra catálogos y grounding, para limitar el espacio de acciones posibles.
85. Como sistema, quiero rechazar una recomendación cuando no exista evidencia suficiente, para priorizar honestidad sobre cobertura.
86. Como sistema, quiero evitar respuestas mock en producción, para no presentar simulación como análisis real.
87. Como sistema, quiero registrar proveedor, modelo, prompt y costo de cada respuesta, para investigar regresiones y consumo.
88. Como sistema, quiero impedir cambios silenciosos de proveedor o modelo, para conservar comportamiento y presupuesto predecibles.
89. Como mantenedor, quiero evaluar prompts, reglas y modelos con casos reales anonimizados antes de publicarlos, para detectar regresiones.
90. Como mantenedor, quiero un catálogo configurable de modelos permitidos, capacidades, precios y estado, para retirar modelos sin cambios dispersos.
91. Como mantenedor, quiero fijar Meta Graph/Marketing API v26 explícitamente, para no depender de versiones implícitas o predeterminadas antiguas.
92. Como mantenedor, quiero comprobar el header de versión atendida por Meta, para detectar discrepancias entre configuración y ejecución.
93. Como mantenedor, quiero un inventario de endpoints, campos y permisos de Meta, para evaluar el impacto de deprecaciones.
94. Como mantenedor, quiero probar la siguiente versión de Meta con una cuenta piloto, para encontrar campos eliminados antes de una migración.
95. Como mantenedor, quiero desplegar una versión candidata de Meta mediante feature flag, para migrar de forma progresiva.
96. Como mantenedor, quiero conservar la versión anterior y un rollback operativo, para responder a incompatibilidades.
97. Como sistema, quiero cargar medios mediante streaming o carga directa, para no consumir memoria proporcional al archivo.
98. Como sistema, quiero validar el contenido real y los límites del medio, para no confiar únicamente en el MIME declarado.
99. Como sistema, quiero almacenar medios en almacenamiento de objetos con URLs temporales, para desacoplar API, workers y VPS.
100. Como sistema, quiero eliminar automáticamente archivos temporales y medios vencidos, para controlar almacenamiento y privacidad.
101. Como operador, quiero límites de medios por organización, para impedir que una sola cuenta agote recursos compartidos.
102. Como responsable de operaciones, quiero backups diarios cifrados fuera del VPS, para sobrevivir a la pérdida total del servidor.
103. Como responsable de operaciones, quiero una pérdida máxima objetivo de 24 horas, para conocer el riesgo aceptado.
104. Como responsable de operaciones, quiero una recuperación objetivo inferior a ocho horas, para restaurar el servicio de forma predecible.
105. Como responsable de operaciones, quiero comprobar una restauración trimestral, para validar que el backup es utilizable.
106. Como responsable de operaciones, quiero monitorear antigüedad de sync, colas, errores de Meta, IA, backups, disco y memoria, para detectar fallos funcionales.
107. Como responsable de operaciones, quiero alertas externas al VPS, para saber que el host está caído aunque no pueda reportarlo.
108. Como responsable de operaciones, quiero que una caída de Redis bloquee write-back y renovaciones sensibles, para evitar bypass de controles.
109. Como cliente, quiero conservar lectura segura del histórico durante un incidente parcial, para seguir obteniendo valor cuando sea posible.
110. Como responsable de operaciones, quiero una imagen inmutable identificada por commit, para desplegar exactamente lo validado.
111. Como responsable de operaciones, quiero una sola versión de Node, un gestor de paquetes y dependencias bloqueadas, para reproducir CI y producción.
112. Como responsable de operaciones, quiero migraciones expand-and-contract, para poder volver a una imagen anterior sin revertir destructivamente datos.
113. Como responsable de operaciones, quiero ejecutar staging fuera del VPS productivo limitado, para no competir con los recursos de producción.
114. Como responsable de operaciones, quiero validar localmente y en CI con PostgreSQL y Redis reales, para acercar las pruebas al entorno desplegado.
115. Como responsable de operaciones, quiero usar infraestructura temporal para upgrades mayores de proveedores, para obtener aislamiento sin pagar staging permanente.
116. Como responsable de operaciones, quiero una ventana de mantenimiento y modo solo lectura para cambios incompatibles, para controlar el impacto.
117. Como responsable de operaciones, quiero conservar imágenes anteriores y realizar smoke tests posteriores al deploy, para habilitar rollback rápido.
118. Como propietario del producto, quiero soportar inicialmente 20–50 cuentas publicitarias, para escalar según evidencia sin sobrediseñar.
119. Como propietario del producto, quiero que el pago de implementación incluya 60 días de garantía, para delimitar claramente la responsabilidad inicial.
120. Como propietario del producto, quiero que infraestructura e IA sean pagadas por el cliente, para evitar costos recurrentes indefinidos sin mensualidad.
121. Como propietario del producto, quiero separar mantenimiento posterior y compatibilidad futura de la garantía, para no crear un SLA implícito permanente.
122. Como propietario del producto, quiero medir tiempo hasta borrador, tiempo de revisión y tasa de aprobación sin cambios, para demostrar ahorro operativo.
123. Como propietario del producto, quiero medir errores bloqueados antes de Meta, para demostrar el valor de las compuertas.
124. Como propietario del producto, quiero medir la diferencia de costo por conversación frente al baseline, para validar el beneficio publicitario.
125. Como propietario del producto, quiero medir uso y comprensión del chat, para validar que la experiencia central funciona para clientes no expertos.

## Implementation Decisions

### Producto, roles y módulos

- La plataforma mantendrá una única fuente de datos multi-tenant, con experiencias diferenciadas para cliente y agencia.
- La experiencia del cliente priorizará chat, resultados, campañas activas y estado de solicitudes. No expondrá grounding, proveedores, API keys, write-back, IDs internos ni diagnósticos de infraestructura.
- La experiencia de agencia incluirá cartera de organizaciones, configuración, diagnóstico, creación, aprobación, auditoría e incidentes.
- Los módulos continuarán habilitándose por organización. Los roles determinarán acciones autorizadas y se evaluarán además de los módulos.
- La agencia conservará acceso operativo a herramientas necesarias aunque un módulo esté oculto para el cliente.
- El onboarding será asistido. Se retirará o deshabilitará el registro público incoherente con este modelo.
- Una organización suspendida bloqueará login, API, sync, IA y write-back para sus miembros; los administradores de plataforma conservarán acceso de soporte.

### Experiencia conversacional

- El chat será el punto principal para preguntas y solicitudes de campaña.
- El chat será reactivo y proactivo: responderá preguntas y generará resúmenes, alertas y oportunidades basadas en datos.
- Toda respuesta indicará cuenta, periodo, frescura y evidencia suficiente cuando sean relevantes.
- Los fallos de dominio continuarán dentro de la conversación con estados comprensibles; los fallos de autenticación y aislamiento conservarán semántica de autorización.
- Cuando la IA no esté disponible, el producto mostrará el impacto con honestidad y conservará solo respuestas deterministas seguras.
- Los estados de rendimiento serán datos insuficientes, prometedora, validada y por debajo del baseline. Se evitarán etiquetas definitivas con muestras pequeñas.

### Brief y medios

- El brief mínimo incluirá objetivo comercial, oferta, condiciones, presupuesto, duración, público o restricciones, destino WhatsApp, medios y capacidad aproximada de atención.
- El sistema podrá extraer contexto de imágenes y videos, pero mostrará lo extraído para revisión humana.
- La primera etapa permitirá análisis contextual, verificación técnica y comparación con medios históricos del mismo cliente. La edición o generación automática de medios requerirá una solicitud explícita y queda fuera de esta spec.
- Los medios se cargarán mediante streaming o directamente a almacenamiento de objetos; se validarán contenido real, tamaño y propiedad.
- Se definirán límites por organización, URLs temporales, ciclo de vida y eliminación de temporales.

### Aprendizaje, baseline y outcome

- La métrica primaria será costo por conversación de WhatsApp atribuida por Meta. No se presentará como venta o ingreso.
- El aprendizaje solo utilizará datos de la misma Organización y AdAccount. No habrá pooling entre clientes.
- La primera implementación será recuperación y ranking de campañas comparables, no entrenamiento o fine-tuning por cliente.
- La comparabilidad considerará objetivo, evento, periodo, oferta, público, ubicación, formato, copy, medios y presupuesto.
- Se excluirán muestras insuficientes, anomalías y combinaciones incompatibles.
- El baseline utilizará mediana y conservará además volumen y gasto. El valor predeterminado de madurez será siete días y diez conversaciones, configurable por organización.
- Cada propuesta persistirá un baseline congelado, campañas fuente, ventana, versión de reglas, prompt, modelo y confianza.
- Los baselines futuros podrán recalibrarse por temporada y variantes sin alterar la evaluación histórica de una campaña ya creada.
- Las explicaciones separarán hechos, asociaciones e hipótesis. Solo una prueba controlada podrá justificar lenguaje causal.
- Las campañas sin evidencia comparable se etiquetarán como exploratorias y se basarán en buenas prácticas explícitas.
- El Outcome loop registrará propuesta, cambios humanos, resultado y veredicto. Las correcciones no se convertirán automáticamente en reglas.

### Límites del LLM

- Se preserva AI-1: todo número, presupuesto, límite, elegibilidad, riesgo y decisión de gasto proviene de datos y código determinista.
- El LLM podrá interpretar texto libre, seleccionar opciones de catálogos cerrados, redactar, resumir y proponer alternativas dentro de un contrato validado.
- Las salidas numéricas sin evidencia y las opciones fuera de catálogo se descartarán.
- En producción no se presentará contenido mock como resultado real.
- Cada respuesta registrará proveedor, modelo, prompt, uso y costo.
- Los cambios de proveedor o modelo no serán silenciosos. Se probarán contra un conjunto de evaluación y se desplegarán por organización.
- La key de IA pertenecerá al cliente, será configurada por la agencia y permanecerá oculta al cliente final.
- Habrá presupuesto mensual de IA, límites de frecuencia y reintentos, alertas 70/90/100 y bloqueo seguro.

### Creación, aprobación e idempotencia

- El cliente podrá solicitar una campaña; la agencia será responsable de revisar y aprobar.
- Toda campaña se creará pausada y mantendrá las compuertas de write-back existentes.
- La aprobación se asociará a una versión inmutable de la propuesta y se consumirá una sola vez.
- Todo cambio material invalidará aprobación, riesgo y preflight anteriores.
- Las escrituras usarán una clave lógica de idempotencia y persistirán intención, estado e identificadores externos.
- Ante timeout o respuesta incierta, el sistema consultará Meta y reconciliará antes de reintentar.
- Los reintentos automáticos no repetirán ciegamente operaciones no idempotentes.
- El límite mensual será una compuerta de backend aplicada antes de crear, aprobar o escalar.
- Presupuestos, límites y comparaciones financieras utilizarán Decimal o unidades enteras mínimas; Float no autorizará gasto.

### Seguridad e identidad

- Access y refresh tokens tendrán tipo validado en cada superficie, incluidos WebSocket y API.
- Cambiar o restablecer contraseña revocará todas las sesiones del usuario.
- Las invitaciones se almacenarán y consumirán de forma segura, atómica y de un solo uso.
- Las membresías tendrán una restricción única por usuario y organización.
- Los tokens de WebSocket no viajarán en query strings registrables.
- Los controles de seguridad fallarán cerrados cuando no pueda verificarse revocación o autorización para una escritura.
- Los interruptores operativos se aplicarán en backend, no solo en navegación.

### Retención, privacidad y auditoría

- Credenciales y accesos se eliminarán o revocarán inmediatamente durante la baja.
- Datos de campañas, medios e histórico se eliminarán dentro de 30 días, salvo obligación contractual o legal visible.
- Logs de seguridad sanitizados podrán mantenerse hasta 90 días.
- El archivo histórico preservará todos los campos utilizados por aprendizaje, explicación y auditoría.
- Los logs excluirán secretos, teléfonos completos, URLs firmadas, medios y payloads sensibles. Se conservarán identificadores internos, códigos, tiempos y metadatos mínimos.
- La auditoría registrará actor, rol, organización, AdAccount, solicitud, medios, evidencia, propuesta, versiones, cambios, aprobaciones, validaciones y resultado externo sanitizado.

### Meta API y proveedores

- Meta Graph/Marketing API v26 será la versión explícita y única fuente de verdad en configuración, despliegue, pruebas y documentación.
- Se registrará la versión realmente atendida por Meta y se vigilarán las fechas de expiración.
- Se mantendrá un inventario de endpoints, campos, permisos y comportamientos utilizados.
- Las nuevas versiones se probarán contra una cuenta piloto con sync completo y creación pausada antes del rollout.
- El cambio de versión será progresivo mediante configuración o feature flag y conservará rollback.
- El catálogo de modelos de IA será configurable y representará capacidades, precio, disponibilidad y retiro.

### Operación en el VPS limitado

- El VPS productivo no ejecutará staging permanente ni temporal junto a la carga de producción.
- CI levantará PostgreSQL y Redis reales, ejecutará pruebas y publicará imágenes inmutables identificadas por commit o digest.
- El staging funcional se ejecutará localmente con el mismo stack. Las actualizaciones mayores podrán utilizar un VPS temporal separado.
- Producción desplegará exactamente la imagen validada; no se utilizarán tags flotantes.
- Las migraciones seguirán expand-and-contract. Cambios incompatibles requerirán ventana de mantenimiento y modo solo lectura.
- Antes de migraciones riesgosas se realizará backup externo y se conservarán imágenes anteriores.
- Cada deploy incluirá healthcheck, smoke test, monitoreo y procedimiento de rollback.
- PostgreSQL, Redis, workers y almacenamiento tendrán límites y alertas de recursos apropiados al VPS.
- Ante caída de Redis se bloquearán write-back, aprobaciones y renovaciones que dependan de seguridad; la lectura directa de PostgreSQL podrá continuar cuando sea segura.

### Recuperación y observabilidad

- Habrá backup diario cifrado fuera del VPS para base de datos, configuración y medios.
- El objetivo inicial será RPO de 24 horas y RTO inferior a ocho horas.
- Se realizará una restauración completa trimestral y después de cambios materiales al sistema de backup.
- La observabilidad incluirá frescura de sync, colas, tiempos, errores Meta, grounding, IA, uso/costo, duplicados inciertos, backup, disco, memoria y disponibilidad de dependencias.
- Las alertas críticas serán externas al VPS.
- Los mensajes de incidente diferenciarán la experiencia simple del cliente de la información técnica de agencia.

### Entrega por fases

- **P0 — Seguridad y daño máximo:** tokens, sesiones, suspensión, invitaciones, límite mensual, dinero exacto, idempotencia, reconciliación Meta, backups y restauración.
- **P1 — Verdad del producto:** experiencias cliente/agencia, onboarding asistido, roles+módulos, métrica y estados correctos, medios seguros, retención y Meta v26 como fuente de verdad.
- **P2 — Copiloto basado en evidencia:** recuperación/ranking, baseline versionado, brief multimodal, chat proactivo, correcciones humanas y evaluación de prompts/modelos.
- **P3 — Operación y escala:** observabilidad completa, despliegue reproducible, catálogo de proveedores, pruebas de contrato, optimización de recursos y preparación para 20–50 cuentas.

### Modelo comercial y responsabilidad

- El producto mantiene pago por implementación en esta etapa.
- La implementación incluye 60 días de garantía para defectos del alcance entregado.
- Infraestructura y consumo de IA son responsabilidad económica del cliente.
- Hosting, mantenimiento, soporte continuo y compatibilidad futura requieren renovación o acuerdo separado; no existe SLA indefinido implícito.

## Testing Decisions

- Los tests deben verificar comportamiento observable, contratos públicos, persistencia y efectos externos; no deben acoplarse a funciones privadas o estructura interna cuando el mismo riesgo pueda comprobarse desde una frontera superior.
- El seam principal será un recorrido de sistema completo mediante navegador y API contra PostgreSQL y Redis reales, con adaptadores controlados de Meta e IA. Cubrirá onboarding, roles, módulos, chat, baseline, solicitud, modificación, aprobación, presupuesto, creación pausada y auditoría.
- Se reutilizarán como prior art las pruebas HTTP del backend, las pruebas del flujo y máquina de estados del chat, las pruebas de la Saga de creación y las pruebas de componentes frontend. Las nuevas pruebas elevarán esas coberturas a la frontera de sistema.
- La seguridad se probará desde API y WebSocket: rechazo de refresh como Bearer, revocación total, suspensión, aislamiento por Organización/AdAccount, invitación de un uso y carreras de membresía.
- La idempotencia se probará mediante solicitudes concurrentes, respuestas inciertas simuladas y reintentos contra PostgreSQL real. El resultado observable será una sola operación lógica y un solo conjunto de identificadores Meta.
- El límite mensual se probará desde las superficies de creación, aprobación y escalado, incluyendo bordes decimales y concurrencia.
- El aprendizaje se probará con datasets de campañas comparables, outliers, muestras pequeñas, estaciones distintas, cuentas múltiples y ausencia de evidencia. Se verificará el baseline y la trazabilidad, no el algoritmo interno de ranking.
- El grounding se probará con respuestas que intenten introducir cifras ausentes, cambiar periodo, mezclar cuentas o exceder presupuesto.
- El seam de contratos externos ejecutará periódicamente sync y lecturas contra Meta v26, verificará campos y versión atendida, y realizará creación pausada en una cuenta piloto de bajo presupuesto.
- Los proveedores de IA se probarán con un corpus anonimizado de preguntas, briefs, negativas, datos insuficientes y límites. Una versión candidata no podrá degradar grounding, seguridad o costo fuera de umbrales aprobados.
- Los medios se probarán con streaming, tamaños límite, contenido incompatible con MIME, archivos truncados, concurrencia y eliminación por ciclo de vida.
- La retención se probará como comportamiento de baja: revocación inmediata, exportación, eliminación a 30 días y sanitización/expiración de logs a 90 días.
- El seam operativo será un drill verificable de backup, restauración, despliegue de imagen inmutable, healthcheck, smoke test y rollback. Se conservará evidencia fechada del resultado.
- CI utilizará las mismas versiones y lockfiles que producción, ejecutará lint, tipos, pruebas, migraciones sobre PostgreSQL y construcción de imágenes.
- La aceptación de P0 bloqueará el onboarding de nuevos clientes y la ampliación de write-back. Cada fase posterior tendrá feature flags, cuenta piloto, criterios de éxito y rollback.

## Out of Scope

- Mezclar datos o aprendizajes entre organizaciones.
- Fine-tuning o entrenamiento de un modelo por cliente en la primera versión.
- Garantizar ventas, ingresos, ROI o ROAS sin integración CRM.
- Integrar CRM o calificación de leads en esta spec; queda como evolución posterior a costo por conversación.
- Autonomía total sin aprobación humana.
- Activar campañas automáticamente después de crearlas.
- Editar o generar creatividades automáticamente sin solicitud explícita.
- Expandirse a Google Ads, TikTok Ads, otros países o monedas antes de validar Perú, Meta y WhatsApp.
- Abrir múltiples verticales antes de validar el vertical con mayor histórico, inicialmente educación/capacitación si ITS es representativo.
- Construir alta disponibilidad o arquitectura distribuida antes de que la carga de 20–50 cuentas lo justifique.
- Ejecutar staging en el VPS productivo de 2 vCPU y 4 GB.
- Implementar checkout, facturación recurrente o suscripción dentro del producto.
- Prometer soporte o mantenimiento indefinidos dentro del pago único.
- Reescribir completamente la arquitectura existente que ya separa reglas, estadística, LLM, planner, Saga y compuertas humanas.

## Further Notes

- El repositorio ya posee fortalezas que deben preservarse: AI-1, grounding, análisis determinista antes del LLM, aislamiento multi-tenant, credenciales cifradas, colas dedicadas, campañas pausadas y aprobación humana.
- La spec corrige y extiende ADR implícitas existentes. En particular, el fail-open general de Redis debe reabrirse para funciones de seguridad y write-back: disponibilidad no puede tener prioridad sobre revocación o autorización financiera.
- El uso de Meta API v26 en producción debe alinearse con valores predeterminados, documentación y despliegue. La discrepancia actual con v25 es una deuda operativa inmediata.
- La suite actual es amplia, pero la confianza adicional debe venir de PostgreSQL real, concurrencia, E2E, contratos externos y restauración, no solo de más tests internos sobre SQLite.
- El primer cliente permitirá validar el flujo completo y obtener métricas de producto antes de escalar. Se medirán tiempo hasta borrador, tiempo de revisión, aprobación sin cambios, errores bloqueados, costo frente al baseline y uso/comprensión del chat.
- Esta es una spec madre. La ejecución debe dividirse por las fases P0–P3 y evitar una única migración masiva. P0 debe completarse antes de expandir funcionalidad o clientes.

