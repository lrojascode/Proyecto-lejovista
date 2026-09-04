# Especificaciones locales · Agente DX IA

Descomposición de la [spec maestra](00-spec-maestra-agente-dx-ia.md) —copia fiel del GitHub
Issue [#2](https://github.com/lrojascode/Proyecto-lejovista/issues/2)— en catorce
especificaciones implementables y verificables una por una.

El issue es la **fuente de requisitos** y no se modifica. Estas specs son su ejecución.

## Cómo leer este índice

- **Estado**: `Pendiente` · `En progreso` · `Bloqueada` · `Completada`. Vive también en la
  cabecera de cada archivo; este índice es el resumen.
- **Prioridad**: fases P0–P3 de la spec maestra. **La aceptación de P0 bloquea el onboarding de
  nuevos clientes y la ampliación de write-back.**
- **Dependencias**: specs que deben estar `Completada` antes de empezar.
- El orden de los números **es** el orden recomendado de ejecución.

## Orden recomendado

> **P0 completado.** Las cuatro specs de la fase están implementadas y
> verificadas, incluido el primer ejercicio de restauración
> ([evidencia](../infra/backups/drills/)). Queda pendiente repetir el drill
> contra el destino remoto real de producción antes de considerar levantada la
> compuerta que bloquea ampliar write-back y dar de alta clientes.

### P0 · Seguridad y daño máximo

| # | Spec | Prioridad | Estado | Dependencias |
|---|------|-----------|--------|--------------|
| 01 | [Autenticación, sesiones, suspensión, invitaciones y fallo cerrado](01-autenticacion-sesiones-suspension-e-invitaciones.md) | P0 | **Completada** | — |
| 02 | [Límite presupuestario mensual y dinero exacto](02-limite-presupuestario-mensual-y-dinero-exacto.md) | P0 | **Completada** | 01 |
| 03 | [Idempotencia de escrituras y reconciliación con Meta](03-idempotencia-y-reconciliacion-de-escrituras-meta.md) | P0 | **Completada** | 01, 02 |
| 04 | [Backups cifrados fuera del VPS y restauración verificada](04-backups-cifrados-y-restauracion-verificada.md) | P0 | **Completada** | — |

`04` es independiente y puede ejecutarse en paralelo con `01`–`03`: es la red de seguridad bajo
la que esas tres se despliegan.

> **P1 completado.** Las cinco specs de la fase están implementadas y
> verificadas. Quedan las fases P2 (copiloto basado en evidencia) y P3
> (operación y escala).

### P1 · Verdad del producto

| # | Spec | Prioridad | Estado | Dependencias |
|---|------|-----------|--------|--------------|
| 05 | [Experiencias de cliente y agencia, roles, módulos y alta asistida](05-experiencias-cliente-agencia-roles-y-modulos.md) | P1 | **Completada** | 01 |
| 06 | [Métrica primaria, baseline y estados de campaña](06-metrica-primaria-baseline-y-estados-de-campana.md) | P1 | **Completada** | 01 |
| 07 | [Medios: carga, validación, almacenamiento y ciclo de vida](07-medios-almacenamiento-y-ciclo-de-vida.md) | P1 | **Completada** | 01, 05 |
| 08 | [Meta Graph/Marketing API v26 y gestión de deprecaciones](08-meta-api-v26-y-gestion-de-deprecaciones.md) | P1 | **Completada** | — |
| 09 | [Baja de clientes, retención, privacidad y auditoría](09-baja-de-clientes-retencion-y-auditoria.md) | P1 | **Completada** | 01, 06, 07 |

`08` no depende de nada y **puede adelantarse a P0**: corrige una divergencia entre la
configuración (v25) y lo que producción ya usa (v26), es de bajo riesgo y evita que un
despliegue rutinario revierta la versión.

### P2 · Copiloto basado en evidencia

| # | Spec | Prioridad | Estado | Dependencias |
|---|------|-----------|--------|--------------|
| 10 | [Gobernanza del LLM: grounding, límites, proveedores y consumo](10-gobernanza-llm-grounding-limites-y-proveedores.md) | P2 | **Completada** | 02, 05, 06 |
| 11 | [Recuperación, ranking y aprendizaje por cliente](11-recuperacion-ranking-y-aprendizaje-por-cliente.md) | P2 | **Completada** | 03, 06, 10 |
| 12 | [Chat proactivo, brief y contexto multimodal](12-chat-proactivo-brief-y-contexto-multimodal.md) | P2 | **Completada** | 05, 06, 07, 10, 11 |

**P2 completado.** Las tres specs de la fase están implementadas y verificadas.
Las dos contradicciones con las ADR implícitas quedaron resueltas: la
auto-selección de proveedor de **ADR-04** está derogada en su mecanismo, y
**ADR-03** y **ADR-11** se extendieron al aprendizaje — la comparabilidad y el
ranking son deterministas, y el aislamiento alcanza ahora a la derivación de
conocimiento y no solo a la lectura de datos.

**P3** cerró las deudas operativas que las fases anteriores fueron dejando: el
mecanismo de feature flags que `12` necesita para activarse por Organización, los
interruptores administrables, y PostgreSQL y Redis en la integración continua.
Las seis pruebas de concurrencia que se saltaban en cada construcción ya corren,
y con ellas la suite entera contra el motor de producción.

Queda pendiente una sola cosa, declarada en la spec `14`: el ejercicio de
rollback en producción, que necesita dos imágenes publicadas y un redespliegue y
no se puede ensayar desde una máquina de desarrollo.

### P3 · Operación y escala

| # | Spec | Prioridad | Estado | Dependencias |
|---|------|-----------|--------|--------------|
| 13 | [Observabilidad, interruptores operativos, feature flags e incidentes](13-observabilidad-kill-switches-e-incidentes.md) | P3 | **Completada** | 01, 05 |
| 14 | [CI, despliegue reproducible y operación en un VPS limitado](14-ci-despliegue-y-operacion-en-vps-limitado.md) | P3 | **Completada** (17/18; el ejercicio de rollback queda pendiente) | 04, 13 |

`13` provee los feature flags que `05`, `08`, `10` y `12` usan para su rollout. Ese consumo **no
las bloquea**: cada una puede desplegarse con un interruptor propio y migrarlo al mecanismo
común cuando exista. Bloquearlas hasta P3 retrasaría P0 y P1 sin reducir riesgo.

## Desviaciones respecto de la descomposición sugerida

La descomposición propuesta al encargar el trabajo tenía trece archivos. Se ajustó en seis
puntos, cada uno con su motivo:

1. **`04-backups-restauracion-y-baja-de-clientes` se dividió** en `04` (backups, P0) y `09`
   (baja y retención, P1). La spec maestra asigna backups a P0 y retención a P1; una spec de
   prioridad mixta no puede marcarse como completada para cerrar la compuerta de P0. Además
   sus superficies son distintas: scripts de operación frente a comportamiento de producto.
2. **El límite presupuestario se adelantó** de `03` a `02`, y la idempotencia pasó de `02` a
   `03`. La historia 43 exige que cada versión modificada de una propuesta vuelva a ejecutar la
   compuerta de presupuesto: el versionado depende de la compuerta, no al revés.
3. **Meta v26 subió** de `11` a `08` (P1). La spec maestra lo sitúa en P1 y sus *Further Notes*
   lo llaman deuda operativa inmediata. El código confirma la divergencia: el valor
   predeterminado, el fichero de ejemplo de entorno, la plantilla de despliegue y seis
   documentos declaran v25 mientras producción usa v26.
4. **El bloque P2 se reordenó** a gobernanza del LLM → recuperación y ranking → chat. El chat y
   el aprendizaje consumen el confinamiento del LLM y el registro de versión de prompt y
   modelo; construirlos antes obligaría a rehacerlos.
5. **El fallo cerrado de Redis se movió a `01`.** Las historias 68 y 108 son el mismo requisito
   —no autorizar cuando no se puede verificar— visto desde seguridad y desde operación.
   Repartirlo entre `01` y `13` habría duplicado un requisito. `13` conserva los interruptores,
   los feature flags, la monitorización y los incidentes.
6. **La fidelidad del archivo frío (historia 74) se asignó a `06`,** no a la spec de retención.
   El archivador descarta hoy exactamente las columnas que `06` necesita para comparar campañas
   —objetivo, resultados crudos, estado efectivo de entrega y fechas estructurales—, así que la
   lista de campos preservados se deriva de los consumidores que `06` define.

## Contradicciones con las ADR implícitas

Señaladas en cada spec, tal como pide la documentación de dominio del repositorio. Ninguna se
sustituye en silencio.

| ADR | Estado | Spec | Naturaleza |
|-----|--------|------|-----------|
| **ADR-13** · fail-open en toda la capa Redis | **Derogada en parte** | `01`, `13` | La spec maestra lo declara expresamente: la disponibilidad no puede tener prioridad sobre revocación ni sobre autorización financiera. La excepción se acota a revocación, aprobación y write-back; throttle, cache y debounce siguen fail-open. |
| **ADR-04** · auto-selección de proveedor LLM por clave presente | **Derogada en su mecanismo** | `10` | La historia 88 prohíbe los cambios silenciosos de proveedor o modelo. Se conserva el cliente unificado y la portabilidad; la elección pasa a ser explícita por Organización. |
| **ADR-06** · Saga con reintentos entre pasos | **Corregida** | `03` | Los pasos que ya emitieron una escritura dejan de ser reintentables sin reconciliación previa. La estructura de la Saga se preserva. |
| **ADR-01** · snapshots idempotentes y archivado | **Corregida en su consecuencia** | `06`, `09` | El archivo frío degrada el aprendizaje al descartar columnas, y sobrevive al borrado del tenant. Se corrige qué se conserva y se añade eliminación dirigida en la baja. |
| **ADR-10** · clasificador único relativo al CPL objetivo | **Extendida** | `06` | Sigue siendo único y determinista; ahora exige muestra mínima y usa mediana. |
| **ADR-02** · human-in-the-loop y triple gating | **Extendida** | `02`, `03`, `13` | Se añaden la compuerta de presupuesto mensual, la autorización de un solo uso y los interruptores administrables. |
| **ADR-03** · la IA nunca inventa cifras (AI-1) | **Preservada y reforzada** | `10`, `11` | Se extiende de las cifras al periodo, la cuenta y el espacio de opciones; comparabilidad, ranking y confianza se suman a las piezas deterministas. |
| **ADR-11** · aislamiento multi-tenant | **Preservada y extendida** | `01`, `05`, `11` | El aislamiento deja de ser solo de lectura de datos y pasa a ser también de derivación de conocimiento. |
| **ADR-05**, **ADR-07**, **ADR-08**, **ADR-09**, **ADR-12** | **Preservadas** | — | Sin cambios. |

Fuera del documento de ADR, el despliegue publicaba y desplegaba con una **etiqueta flotante**,
práctica que la spec maestra prohíbe expresamente. Corregido en `14`: a producción se despliega
por identificador de commit, y `GET /version` publica cuál está corriendo. La etiqueta flotante
se sigue publicando como conveniencia de desarrollo, pero ya no es lo que se despliega. Nunca
estuvo documentada como decisión, así que no derogaba ninguna ADR.

`14` destapó además una divergencia que ninguna ADR cubría: la imagen del frontend se construía
con otro gestor de paquetes, otro lockfile y otra versión de Node que los que CI validaba. Ahora
hay uno solo de cada, y la construcción falla cuando se desalinean.

## Trazabilidad: las 125 historias de usuario

Toda historia del issue #2 está asignada a una spec o marcada explícitamente sin spec técnica.
Ninguna aparece en dos specs.

| Spec | Historias | Total |
|------|-----------|-------|
| `01` | 53, 54, 55, 63, 64, 65, 66, 67, 68, 108 | 10 |
| `02` | 20, 49, 50 | 3 |
| `03` | 19, 42, 43, 44, 45, 46, 47, 48 | 8 |
| `04` | 102, 103, 104, 105 | 4 |
| `05` | 1, 2, 18, 26, 27, 28, 29, 30, 31, 32, 33, 34 | 12 |
| `06` | 8, 9, 10, 11, 38, 39, 74, 77, 78, 79, 80 | 11 |
| `07` | 14, 16, 97, 98, 99, 100, 101 | 7 |
| `08` | 91, 92, 93, 94, 95, 96 | 6 |
| `09` | 24, 25, 69, 70, 71, 72, 73 | 7 |
| `10` | 6, 17, 51, 52, 83, 84, 86, 87, 88, 89, 90 | 11 |
| `11` | 35, 36, 37, 40, 41, 61, 62, 75, 76, 81, 82, 85 | 12 |
| `12` | 3, 4, 5, 7, 12, 13, 15, 21, 22, 23 | 10 |
| `13` | 56, 57, 58, 59, 60, 106, 107, 109, 122, 123, 124, 125 | 12 |
| `14` | 110, 111, 112, 113, 114, 115, 116, 117, 118 | 9 |
| **Sin spec técnica** | 119, 120, 121 | 3 |
| | | **125** |

### Historias sin spec técnica

- **119** — la implementación incluye 60 días de garantía para defectos.
- **120** — infraestructura y consumo de IA son responsabilidad económica del cliente.
- **121** — mantenimiento y compatibilidad futura se separan de la garantía; no hay SLA
  indefinido implícito.

Son términos del acuerdo comercial. No generan comportamiento de software: no hay compuerta,
migración ni criterio verificable en el producto que las implemente, y la spec maestra excluye
expresamente construir facturación o suscripción dentro del producto. Viven en el contrato y en
la documentación comercial. Su único reflejo técnico —hacer visible la base de una retención
superior a la declarada— está cubierto por la historia 73 en `09`.

## Fuera de alcance heredado

La sección *Out of Scope* de la spec maestra rige sobre las catorce specs y no se repite en
cada una. En resumen: nada de mezclar datos entre Organizaciones, fine-tuning por cliente,
promesas de venta o retorno, integración con CRM, autonomía sin aprobación humana, activación
automática de campañas, generación automática de creatividades, otras plataformas o países,
alta disponibilidad prematura, staging en el VPS productivo, facturación dentro del producto,
soporte indefinido, ni reescritura de la arquitectura existente.

Cada spec añade sus propios límites en su sección *Fuera de alcance*.

## Convenciones

- Vocabulario del [glosario](../17-glosario.md): Organización, AdAccount, Snapshot, Insight,
  AIAction, Saga, Draft, Outcome loop, CPL, costo por conversación, write-back.
- Invariantes que ninguna spec puede debilitar: **AI-1**, human-in-the-loop, aislamiento
  multi-tenant y campañas creadas en **PAUSED**.
- Las *Decisiones de implementación* no incluyen rutas de archivo ni fragmentos de código: son
  contratos, no implementaciones.
- Los *Criterios de aceptación* se responden con sí o no. Un criterio que necesite matiz está
  mal escrito.
