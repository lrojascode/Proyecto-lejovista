# 13 · Observabilidad, interruptores operativos, feature flags e incidentes

## Estado

Completada. Los diecisiete criterios están implementados y verificados
(`backend/tests/test_spec13_operacion.py`, 46 pruebas).

**El criterio que costó no fue apagar sino apagar en el sitio correcto.** Un
interruptor en el endpoint se escribe en cinco minutos y no contiene nada: la
tarea que ya salió de la API sigue viva en la cola y se vacía sobre el problema
que se acaba de intentar contener. Los cinco se aplican en el punto de
ejecución, y hay un test que ejerce el **cuerpo real** de la tarea de sync —no
la resolución del interruptor— porque el fallo que importa es que alguien lo
consulte y siga adelante igual.

**El modo de solo lectura estuvo a punto de ser un `not` mal puesto.** Su
`encendido` significa lo contrario que el de las cinco capacidades: en una
capacidad, encendido es que funciona; en el modo de solo lectura, encendido es
que está bloqueando. La primera versión los resolvía en la misma función y
`panorama` ya tenía la expresión invertida. Se separó en su propia función
antes de que llegara a decidir si se puede escribir durante un incidente.

**Dos señales estaban consultando columnas inexistentes.** `APICallLog` usa
`timestamp` y `SyncJob` usa `started_at`, no `created_at`. Las dos consultas
lanzaban, el `except` las capturaba y la señal se reportaba como `desconocido`
para siempre. Es exactamente el modo de fallo que la spec ataja —una señal que
no puede medirse y no lo grita— y lo produjo el propio código que lo evita.

**Los flags fallan al revés que los interruptores, a propósito.** Ante un
almacén caído, un interruptor se considera apagado y un flag toma su valor
predeterminado. No es una inconsistencia: un interruptor apagado contiene un
incidente, mientras que un flag apagado por accidente apaga una capacidad que
llevaba meses funcionando bien. El default de un flag es lo que el sistema hacía
ayer.

### Deudas que se llevan

- **El drill de caída total no se ha hecho.** El vigilante externo está escrito
  y su lógica de las tres comprobaciones se puede leer, pero el criterio 12 pide
  apagar el entorno de prueba por completo y verificar que la notificación
  llega. No hay forma de comprobarlo desde dentro, y por eso sigue abierto.
- **El periodo de calibración de umbrales tampoco.** La spec pide dos semanas en
  modo de observación antes de que las alertas notifiquen. Los umbrales de hoy
  son una primera estimación razonada, no medida.
- **El proveedor de monitorización externa no está elegido.** El script acepta
  un webhook y no asume ninguno; la decisión es de infraestructura y pertenece a
  `14`.
- **Las specs anteriores todavía no consumen los flags.** El catálogo declara
  los seis que necesitan `08`, `10`, `11` y `12`, y ninguna los lee aún: cada una
  se desplegó con su propio mecanismo, como la spec previó. Migrarlas es trabajo
  aparte y sin urgencia.
- **La señal de memoria solo funciona en Linux.** Se lee de `/proc/meminfo` y
  fuera de ahí se declara `desconocido` en vez de inventar un número. En el VPS
  existe; en el portátil de quien la desarrolla, no.
- **La pantalla de operación no existe.** Todo se maneja por API. Es suficiente
  para contener un incidente desde una terminal y no para hacerlo con prisa
  desde el móvil.

## Prioridad

P3.

## Objetivo

Que un incidente pueda contenerse sin apagar la plataforma, que una capacidad nueva pueda
encenderse por Organización y por cuenta piloto, y que los fallos funcionales —no solo las
caídas de proceso— se detecten antes de que un cliente los reporte.

## Problema

No existe ningún interruptor operativo. Detener la creación de campañas, el write-back, la
sincronización, la IA o los medios exige cambiar variables de entorno y redesplegar, lo que en
mitad de un incidente es lento y arriesgado. No hay modo de solo lectura, ni global ni por
Organización. Las únicas compuertas son las tres de write-back, que son de configuración y
requieren despliegue.

No existe una infraestructura de feature flags por Organización. Varias specs anteriores la
necesitan —el rollout de las experiencias de cliente y agencia, la versión candidata de la API
de Meta, el chat proactivo, el endurecimiento del grounding— y cada una tendría que inventarla.

La observabilidad cubre errores de proceso a través de la captura de excepciones y un
healthcheck, pero no fallos funcionales: un sync que dejó de traer datos hace tres días, una
cola que crece, una tasa de errores de Meta que sube, un respaldo que no se hizo, un disco al
90 %. Las alertas, además, viven en el mismo VPS que vigilan: si el host cae, nadie avisa.

Las métricas de producto que la spec maestra declara —tiempo hasta borrador, tiempo de
revisión, aprobación sin cambios, errores bloqueados antes de Meta, diferencia frente al
baseline y uso del chat— no se miden, de modo que no hay forma de demostrar el ahorro
operativo ni el beneficio publicitario con el primer cliente.

## Alcance

- Interruptores operativos por capacidad —creación, write-back, sync, IA, medios— aplicados en
  backend, globales y por Organización.
- Modo de solo lectura, global y por Organización.
- Feature flags por Organización como capacidad reutilizable, con concepto de cuenta piloto.
- Señales funcionales monitorizadas: frescura de sync, profundidad y latencia de colas, errores
  de Meta, descartes de grounding, uso y costo de IA, escrituras inciertas, salud del respaldo,
  disco, memoria y disponibilidad de dependencias.
- Alertas críticas emitidas fuera del VPS.
- Diagnóstico técnico de incidente para la agencia, separado del mensaje que ve el cliente.
- Lectura segura del histórico durante un incidente parcial.
- Instrumentación de las métricas de producto declaradas.

## Historias de usuario

Tomadas de la spec maestra:

- **56** — la agencia ve diagnósticos técnicos durante un incidente sin exponerlos al cliente.
- **57** — modo de solo lectura global o por Organización.
- **58** — interruptores separados para creación, write-back, sync, IA y medios.
- **59** — activación de cambios mediante feature flags por Organización.
- **60** — prueba de capacidades nuevas primero con una cuenta piloto.
- **106** — monitoreo de antigüedad de sync, colas, errores de Meta, IA, backups, disco y
  memoria.
- **107** — alertas externas al VPS.
- **109** — el cliente conserva lectura segura del histórico durante un incidente parcial.
- **122** — medición de tiempo hasta borrador, tiempo de revisión y tasa de aprobación sin
  cambios.
- **123** — medición de errores bloqueados antes de Meta.
- **124** — medición de la diferencia de costo por conversación frente al baseline.
- **125** — medición de uso y comprensión del chat.

## Decisiones de implementación

- **Los interruptores viven en el backend y se aplican en el punto de ejecución.** Un
  interruptor que solo oculta un botón no contiene nada: la capacidad debe rechazarse donde se
  ejecuta, incluidas las tareas asíncronas ya encoladas. Cada rechazo nombra el interruptor
  responsable.
- **Cambiarlos no requiere desplegar.** Son estado administrable con efecto inmediato, con
  registro de quién los movió y cuándo. Un interruptor que exige un despliegue no sirve durante
  un incidente, que es el único momento en que se usa.
- **El estado de los interruptores se resuelve de forma segura ante su propia caída.** Si el
  almacén de estado no responde, las capacidades de escritura se consideran apagadas y las de
  lectura encendidas. Es la misma política de fallo cerrado que define `01`, aplicada al plano
  operativo.
- **El modo de solo lectura es una capacidad, no la suma de los interruptores.** Se activa de
  una vez y garantiza que ninguna escritura —ni a Meta, ni de configuración, ni de propuestas—
  se ejecute, mientras la consulta del histórico sigue funcionando. Es la respuesta de contención
  por defecto ante un incidente cuya causa aún no se conoce.
- **Los feature flags son infraestructura compartida, no una solución por spec.** Ámbito global
  y por Organización, con un valor predeterminado explícito y resolución determinista. La cuenta
  piloto es un caso de uso de este mecanismo, no un concepto aparte.
- **Se monitoriza lo funcional, no solo lo vivo.** Un proceso que responde al healthcheck y
  lleva tres días sin traer datos está roto para el cliente. Las señales de esta spec son
  indicadores de que el producto hace su trabajo, y cada una tiene un umbral declarado por
  encima del cual alerta.
- **Las alertas críticas se emiten desde fuera.** Un vigilante externo comprueba la
  disponibilidad y la antigüedad de las señales que el sistema publica. Si el VPS cae, la
  ausencia de señal es en sí misma la alerta. Un sistema que solo alerta desde dentro no puede
  informar de su propia muerte.
- **Dos audiencias, dos mensajes.** Durante un incidente, la agencia ve el diagnóstico técnico
  —qué dependencia falla, qué interruptor está activo, qué error devolvió Meta— y el cliente ve
  una explicación honesta y comprensible de qué puede y qué no puede hacer ahora. Es la
  aplicación de la separación de experiencias de `05` al caso de incidente.
- **Las métricas de producto se instrumentan en el flujo, no se reconstruyen después.** El
  tiempo hasta borrador, el tiempo de revisión, la aprobación sin cambios y los errores
  bloqueados antes de llegar a Meta se derivan de eventos que el flujo ya produce; lo que falta
  es marcarlos y poder consultarlos.

### Contradicción con ADR implícitas

- **ADR-13 (fallbacks fail-open en toda la capa Redis)** vuelve a acotarse, ahora en el plano
  operativo: el estado de los interruptores no puede degradar abierto para las escrituras. La
  excepción principal la define `01`; esta spec la extiende al plano de contención.
- **ADR-02 (triple gating de escrituras)** se preserva y se complementa: las tres compuertas de
  configuración siguen existiendo, y los interruptores añaden una capa administrable sin
  despliegue por encima de ellas.

## Criterios de aceptación

1. Existe un interruptor independiente para creación, write-back, sync, IA y medios. Sí/No.
2. Apagar un interruptor rechaza la capacidad en la API y también en las tareas asíncronas ya
   encoladas. Sí/No.
3. El rechazo nombra el interruptor responsable. Sí/No.
4. Cambiar un interruptor tiene efecto inmediato sin desplegar y queda registrado con actor y
   momento. Sí/No.
5. Un interruptor puede aplicarse a una sola Organización sin afectar a las demás. Sí/No.
6. El modo de solo lectura impide toda escritura y conserva la consulta del histórico. Sí/No.
7. Con el almacén de estado de los interruptores caído, las capacidades de escritura se
   consideran apagadas y las de lectura siguen disponibles. Sí/No.
8. Un feature flag puede resolverse a valores distintos para dos Organizaciones en la misma
   ejecución. Sí/No.
9. Un feature flag sin configurar toma su valor predeterminado explícito. Sí/No.
10. Existe una señal monitorizable, con umbral declarado, para cada elemento de la lista:
    frescura de sync, colas, errores de Meta, descartes de grounding, uso y costo de IA,
    escrituras inciertas, salud del respaldo, disco, memoria y disponibilidad de dependencias.
    Sí/No.
11. Superar el umbral de cualquiera de esas señales genera una alerta. Sí/No.
12. Con el VPS apagado por completo, llega una alerta emitida desde fuera de él. Sí/No.
13. Durante un incidente, la agencia obtiene el diagnóstico técnico y el cliente un mensaje que
    no expone detalles de infraestructura. Sí/No.
14. Durante un incidente que afecta a la escritura, el cliente conserva la lectura de su
    histórico. Sí/No.
15. Se puede consultar, para una Organización y un periodo, el tiempo hasta borrador, el tiempo
    de revisión, la tasa de aprobación sin cambios y los errores bloqueados antes de Meta.
    Sí/No.
16. Se puede consultar la diferencia de costo por conversación frente al baseline de las
    campañas creadas por el sistema. Sí/No.
17. Se puede consultar el uso del chat por Organización. Sí/No.

## Decisiones de testing

- La frontera es la **API y el comportamiento de las tareas asíncronas**. Un interruptor se
  prueba apagándolo y comprobando que la capacidad se rechaza en las dos superficies; probar
  solo la API deja fuera precisamente el camino que un incidente necesita cortar.
- El fallo del almacén de estado se prueba deteniéndolo y comprobando el par completo: qué
  queda bloqueado y qué sigue funcionando.
- La alerta externa se prueba con un drill: se detiene el entorno de prueba por completo y se
  verifica que la notificación llega. No hay forma de comprobarlo desde dentro.
- Los umbrales de las señales funcionales se prueban inyectando el estado que debe dispararlos
  —una sincronización envejecida, una cola profunda, un respaldo ausente— en vez de esperar a
  que ocurran.
- La separación de mensajes de incidente se prueba con dos actores autenticados, igual que la
  separación de experiencias de `05`.
- Las métricas de producto se prueban por su consulta: dado un recorrido completo simulado, la
  consulta devuelve las cifras esperadas.

## Dependencias

- `01` — la política de fallo cerrado y el registro de actor sobre el que se apoyan los cambios
  de interruptor.
- `05` — la separación de experiencias, que el mensaje de incidente aplica.

Las specs `05`, `08`, `10` y `12` consumen los feature flags de esta spec para su rollout. Ese
consumo no las bloquea: cada una puede desplegarse con un interruptor propio y migrarlo al
mecanismo común cuando exista. La alternativa —bloquearlas todas hasta P3— retrasaría el P0 y
el P1 sin reducir riesgo.

## Fuera de alcance

- Alta disponibilidad, redundancia y conmutación automática.
- Trazado distribuido y agregación centralizada de registros de varios hosts.
- Elección del proveedor concreto de monitorización externa, que es una decisión de
  infraestructura y se documenta en `14`.
- La salud del respaldo como proceso: pertenece a `04`. Esta spec la consume como señal.
- El saneamiento del contenido de los registros: pertenece a `09`.
- La compuerta de presupuesto de campañas y la de consumo de IA: pertenecen a `02` y `10`. Esta
  spec las observa, no las implementa.
- Paneles de análisis de negocio más allá de la consulta de las métricas declaradas.

## Riesgos y rollback

- **Daño máximo si no se hace:** un incidente obliga a apagar la plataforma entera o a
  desplegar bajo presión, y un fallo funcional —el sync detenido— pasa días inadvertido
  mientras el cliente decide sobre datos viejos.
- **Daño máximo al hacerlo:** un interruptor mal resuelto apaga una capacidad para todas las
  Organizaciones cuando se pretendía apagarla para una; o un umbral demasiado sensible genera
  tantas alertas que se dejan de mirar.
- **Activación:** los interruptores se despliegan encendidos, con su efecto verificado uno a uno
  en un entorno de prueba antes de tocar producción. Las alertas se despliegan en modo de
  observación durante al menos dos semanas para calibrar umbrales antes de que notifiquen. Los
  feature flags se estrenan con el rollout de una capacidad ya prevista, no como cambio
  aislado.
- **Rollback:** los interruptores son estado, no código: volver atrás es volver a encenderlos.
  El riesgo de rollback está en la resolución del estado, no en su valor, y se mitiga con el
  valor predeterminado explícito de cada flag.

## Checklist de finalización

- [x] Interruptores por capacidad, globales y por Organización, aplicados en API y en tareas
      asíncronas.
- [x] Modo de solo lectura, global y por Organización.
- [x] Mecanismo de feature flags por Organización con valores predeterminados explícitos y
      concepto de cuenta piloto.
- [x] Señales funcionales publicadas con umbral declarado para cada elemento de la lista.
- [ ] Vigilante externo al VPS con alertas críticas, verificado mediante drill de caída total.
      **El script existe (`scripts/vigilante/vigilar.sh`); el drill sigue pendiente.**
- [x] Vista de diagnóstico de incidente para la agencia y mensaje diferenciado para el cliente.
- [x] Instrumentación y consulta de las métricas de producto declaradas.
- [x] Pruebas de sistema de los diecisiete criterios.
- [x] Documentación de operación actualizada: qué interruptor apaga qué y en qué orden usarlos.
- [x] Runbook de incidente con el árbol de decisión de contención.
- [ ] Evidencia del periodo de calibración de umbrales antes de activar las notificaciones.
      **Pendiente: los umbrales de hoy son una estimación razonada, no medida.**

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Recuperación y observabilidad* (párrafos
de observabilidad, alertas externas y mensajes de incidente); *Producto, roles y módulos*
(último párrafo, interruptores en backend); *Solution* y *User Stories* 122–125 (métricas de
producto); *Entrega por fases* → **P3**.
