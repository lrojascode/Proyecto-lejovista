# 03 · Idempotencia de escrituras y reconciliación con Meta

## Estado

Completada. Los catorce criterios se verifican en
`backend/tests/test_spec03_idempotencia.py`; el consumo concurrente de una
autorización (criterio 5) y la unicidad de la clave de idempotencia se verifican
contra PostgreSQL real en `backend/tests/test_spec03_concurrencia_postgres.py`.

**Nota sobre el criterio 13.** Resultó estar ya garantizado aguas arriba: la capa
de reglas trata `default_status` como clave inmutable, así que ningún override de
organización puede pedir una campaña activa. Se añadieron dos comprobaciones más
—en el validador y sobre el payload que sale hacia Meta— porque una invariante
comprobada en un solo sitio deja de serlo en cuanto alguien añade un camino
nuevo.

**Nota sobre la reconciliación de creativos.** Un creativo no se puede buscar por
nombre como una campaña o un conjunto, así que ante una respuesta incierta su
intención queda en verificación y hace falta una persona. Decirlo es mejor que
fingir una reconciliación incapaz de distinguir un creativo de otro.

## Prioridad

P0.

## Objetivo

Que una solicitud de creación aprobada produzca como máximo una campaña real en Meta, aunque
haya reintentos, clics duplicados, timeouts o respuestas inciertas; y que la autorización
humana se refiera siempre a una versión exacta e identificable de la propuesta.

## Problema

La creación de una campaña es una Saga de varias llamadas dependientes a Meta —campaña,
conjunto, creativo, anuncio— con reintentos automáticos entre pasos. Ninguna de esas llamadas
es idempotente del lado de Meta: repetir un POST tras un timeout crea una segunda entidad. Hoy
la única defensa dura es una restricción de unicidad entre la sesión de chat y su Draft; el
resto del camino —el reintento de un paso, la reejecución de la Saga, una segunda aprobación—
no tiene clave lógica que impida duplicar.

En paralelo, la aprobación no está atada a nada inmutable. El Draft guarda su configuración en
un documento mutable: se puede aprobar una propuesta, modificarla y ejecutar una versión
distinta de la que se autorizó, sin que quede rastro de la discrepancia. Tampoco existe la
noción de "consumida": la misma aprobación puede dar lugar a más de una ejecución.

Cuando Meta responde con un timeout o un error ambiguo, el sistema no sabe si la escritura
ocurrió. Sin un paso de reconciliación —preguntar a Meta antes de repetir—, cualquier
política de reintentos elige entre duplicar o abandonar, y las dos son malas.

## Alcance

- Versión inmutable de una propuesta: cada cambio material produce una versión nueva,
  identificable y no editable.
- Invalidación de aprobación, riesgo y preflight ante cualquier cambio material, con
  reejecución de las compuertas —incluida la de presupuesto de `02`— sobre la versión nueva.
- Aprobación asociada a exactamente una versión y consumible una sola vez.
- Clave lógica de idempotencia por operación de escritura, derivada de la versión aprobada.
- Persistencia de intención, estado y resultado de cada escritura, con los identificadores
  externos definitivos que devuelve Meta.
- Estado explícito *verificándose* para respuestas inciertas, que no reintenta a ciegas.
- Reconciliación contra Meta antes de repetir cualquier operación no idempotente.
- Preservación de la garantía existente: toda campaña se crea en estado PAUSED.

## Historias de usuario

Tomadas de la spec maestra:

- **19** — una campaña creada queda pausada.
- **42** — un cambio material invalida la aprobación anterior.
- **43** — cada versión modificada repite presupuesto, riesgo, preflight y validaciones.
- **44** — se aprueba exactamente una versión identificable.
- **45** — una aprobación se consume una sola vez.
- **46** — una respuesta incierta de Meta queda como verificándose.
- **47** — se reconcilia con Meta antes de repetir un POST.
- **48** — se registran los identificadores finales de Meta.

## Decisiones de implementación

- **La versión de la propuesta es el objeto inmutable del flujo.** El Draft sigue siendo el
  contenedor mutable de una solicitud en curso; la versión es una fotografía cerrada de su
  configuración, con identificador propio. Se aprueba una versión, no un Draft.
- **Cambio material está definido, no es un juicio.** Es material todo cambio que altera
  presupuesto, objetivo, evento de conversión, audiencia, ubicaciones, destino, medios,
  calendario o copy publicado. No lo es un cambio de nombre interno ni de notas. La lista es
  parte del contrato y se versiona con él: ampliarla es un cambio de comportamiento visible.
- **La aprobación es un consumo, no una marca.** Aprobar produce una autorización ligada a una
  versión, con estado propio, que pasa a consumida en la misma operación que lanza la
  ejecución. Una segunda ejecución sobre la misma autorización devuelve el resultado de la
  primera, no una ejecución nueva.
- **La clave de idempotencia se deriva de la autorización y del paso.** Cada operación de
  escritura hacia Meta —campaña, conjunto, creativo, anuncio, ajuste— tiene una clave lógica
  estable y única. Antes de emitir el POST se registra la intención con esa clave; si ya
  existe un registro con resultado, se devuelve ese resultado en vez de escribir.
- **Tres estados de una escritura, no dos.** *Pendiente*, *confirmada* y *verificándose*. La
  tercera existe para timeouts y errores ambiguos y es la única que no autoriza reintento
  automático: exige reconciliación previa.
- **Reconciliar es preguntar a Meta por la huella de la intención.** La reconciliación busca
  en la cuenta la entidad que la operación habría creado, usando la marca de correlación que
  el sistema escribe al crearla —nombre derivado de forma determinista más la referencia de la
  versión— dentro de una ventana temporal acotada. Si aparece, la escritura se marca como
  confirmada y se adoptan sus identificadores. Si no aparece, se puede reintentar.
- **Los identificadores externos son un campo de primera clase.** Dejan de vivir solo dentro
  del documento de resultado del Draft: se persisten de forma consultable para poder
  reconstruir qué escribió el sistema, en qué cuenta y bajo qué autorización.
- **Los reintentos automáticos distinguen el tipo de error.** Un error transitorio de red antes
  de enviar es reintentable; una respuesta incierta después de enviar no lo es sin
  reconciliación. Esta distinción ya existe en la clasificación de errores de Meta y se
  extiende para gobernar la política de reintento de la Saga.
- **La creación en PAUSED se mantiene como invariante.** No es negociable por configuración ni
  por la propuesta; una versión que pidiera crear activa se rechaza en validación.

### Contradicción con ADR implícitas

- **ADR-06 (Saga por pasos con `chain` y reintentos)** se corrige en su política de reintento:
  los pasos que ya emitieron una escritura dejan de ser reintentables sin reconciliación
  previa. La estructura de la Saga y su pausa entre pasos se preservan.
- **ADR-02 (human-in-the-loop y triple gating)** se refuerza: la aprobación humana pasa de ser
  un permiso difuso a una autorización de un solo uso sobre una versión concreta.

## Criterios de aceptación

1. Aprobar una propuesta y luego modificar cualquier campo material deja la propuesta sin
   aprobación válida, y la ejecución se rechaza. Sí/No.
2. Tras un cambio material, el sistema vuelve a calcular presupuesto, riesgo y preflight sobre
   la versión nueva antes de permitir una aprobación. Sí/No.
3. Un cambio no material —nombre interno, notas— no invalida la aprobación. Sí/No.
4. La aprobación identifica una versión concreta, y esa versión no puede editarse después. Sí/No.
5. Dos peticiones de ejecución concurrentes sobre la misma aprobación producen una sola Saga y
   un solo conjunto de identificadores de Meta. Sí/No.
6. Reejecutar una aprobación ya consumida devuelve el resultado de la primera ejecución, sin
   escribir en Meta. Sí/No.
7. Un timeout en la creación de la campaña deja la escritura en estado verificándose y no
   dispara un reintento automático. Sí/No.
8. Desde el estado verificándose, la reconciliación encuentra la entidad realmente creada en
   Meta, la adopta y la Saga continúa sin duplicar. Sí/No.
9. Desde el estado verificándose, cuando la entidad no existe en Meta, el reintento se permite
   y produce exactamente una entidad. Sí/No.
10. Tras una creación completa, el sistema tiene registrados los identificadores de campaña,
    conjunto, creativo y anuncio de forma consultable, asociados a la autorización que los
    originó. Sí/No.
11. Una respuesta incierta en un paso intermedio no deja entidades huérfanas sin registro: toda
    escritura emitida tiene un registro de intención previo. Sí/No.
12. Toda campaña creada por el sistema queda en estado PAUSED en Meta. Sí/No.
13. Una versión que solicite crear una campaña activa se rechaza en validación. Sí/No.
14. La agencia puede ver, para una campaña creada, qué versión se aprobó, quién la aprobó y qué
    identificadores externos resultaron. Sí/No.

## Decisiones de testing

- La frontera es la **API de propuesta, aprobación y ejecución**, más el estado observable de
  la Saga. No se prueban las funciones internas de construcción de payloads.
- El adaptador de Meta se controla para poder inyectar timeouts y respuestas ambiguas de forma
  determinista. La prueba de reconciliación necesita las dos caras: la entidad existe y la
  entidad no existe.
- La concurrencia sobre una misma aprobación se prueba con peticiones simultáneas contra
  **PostgreSQL real**; el resultado observable es una sola operación lógica y un solo conjunto
  de identificadores.
- Se reutilizan como prior art las pruebas existentes de la Saga de creación, de las tareas de
  creación y de idempotencia del chat de campañas; las nuevas elevan esas comprobaciones a la
  frontera de sistema en lugar de duplicar el mismo escenario en el nivel de tarea.
- La invariante PAUSED se comprueba sobre lo que recibe el adaptador de Meta, no sobre lo que
  guarda el Draft.
- El seam de contratos externos ejecuta, con periodicidad y no en cada commit, una creación
  pausada real en una cuenta piloto de bajo presupuesto para validar que la marca de
  correlación es efectivamente recuperable desde Meta.

## Dependencias

- `01` — el fallo cerrado debe impedir aprobar y escribir cuando no se puede verificar
  autorización; la suspensión debe bloquear estas superficies.
- `02` — la reejecución de compuertas en cada versión incluye la compuerta de presupuesto
  mensual, que se define allí.

## Fuera de alcance

- Modificación de la propuesta por parte del operador y registro del motivo del cambio como
  material de aprendizaje: pertenecen a `11`. Esta spec aporta el mecanismo de versionado que
  aquella consume.
- Activación automática de campañas después de crearlas.
- Idempotencia de la sincronización de lectura, que ya está resuelta por la clave natural de
  los Snapshot.
- Ampliación del conjunto de operaciones de write-back más allá de las que existen hoy.
- Reconciliación retroactiva de campañas creadas antes de esta spec.

## Riesgos y rollback

- **Daño máximo si no se hace:** campañas, conjuntos, creativos o anuncios duplicados en la
  cuenta de un cliente, gastando el doble de presupuesto sin que nadie lo haya autorizado, y
  una aprobación humana que no corresponde a lo que se ejecutó.
- **Daño máximo al hacerlo:** una reconciliación con falsos positivos —adoptar como propia una
  entidad que creó otra persona en la misma cuenta— hace que el sistema declare creada una
  campaña que nunca creó, y la Saga continúe colgando conjuntos de una campaña ajena.
- **Activación:** la reconciliación se despliega primero en modo de solo informe sobre la
  cuenta piloto, registrando qué habría adoptado, y se activa cuando ese registro no muestra
  falsos positivos. La marca de correlación debe estar presente en las escrituras antes de que
  la reconciliación se apoye en ella, lo que obliga a un orden: primero escribir la marca,
  después leerla.
- **Rollback:** volver a la imagen anterior. Las autorizaciones ya consumidas y los registros
  de intención son datos añadidos, no destructivos, y sobreviven a un rollback. El
  comportamiento anterior —reintento ciego— se puede restaurar por configuración durante un
  incidente, con registro explícito del riesgo asumido.

## Checklist de finalización

- [ ] Migración que añade versión inmutable de propuesta, autorización de un solo uso, registro
      de intención de escritura e identificadores externos consultables, en expand-and-contract.
- [ ] Pruebas de sistema de los catorce criterios, con timeouts y respuestas ambiguas inyectadas
      y la concurrencia contra PostgreSQL real en CI.
- [ ] Prueba de contrato externo, no incluida en el CI de cada commit, que crea una campaña
      pausada real en la cuenta piloto y recupera su marca de correlación desde Meta.
- [ ] Documentación de flujos actualizada con el ciclo versión → aprobación → autorización →
      escritura → reconciliación.
- [ ] Documento de decisiones arquitectónicas actualizado: política de reintento de ADR-06.
- [ ] Observabilidad: contador de escrituras en estado verificándose, de reconciliaciones con
      y sin hallazgo, y de duplicados evitados. Alerta cuando una escritura permanece
      verificándose más allá de un umbral.
- [ ] Runbook de incidente: qué hacer con una escritura que queda verificándose y la
      reconciliación no resuelve.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Creación, aprobación e idempotencia*;
*Solution* (párrafo sobre invalidación de aprobación por cambio material); *Problem Statement*
(párrafo de riesgos de concurrencia e idempotencia); *Testing Decisions* (párrafo de
idempotencia); *Entrega por fases* → **P0**.
