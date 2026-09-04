# 02 · Límite presupuestario mensual y dinero exacto

## Estado

Completada. Los catorce criterios se verifican en
`backend/tests/test_spec02_limite_presupuestario.py`; la acumulación exacta y la
carrera del criterio 9 se verifican contra PostgreSQL real en
`backend/tests/test_spec02_concurrencia_postgres.py`.

**Matiz sobre el criterio 7.** A las magnitudes que admite el esquema
(`NUMERIC(14,2)`, hasta ~1e12), una comparación suelta en coma flotante y una
exacta dan el mismo resultado: el residuo binario está muchos órdenes por debajo
de un céntimo. Lo que la aritmética exacta elimina son las dos formas en que ese
residuo llega a decidir — la igualdad en el borde exacto (criterio 8) y la
**acumulación** sobre cientos de snapshots — y eso es lo que se prueba. Afirmar
que una comparación suelta cambiaba de resultado habría sido falso.

## Prioridad

P0.

## Objetivo

Que el límite mensual de presupuesto de una Organización sea una compuerta de backend que
realmente impide crear y escalar campañas, y que toda comparación que autorice gasto se
resuelva con aritmética exacta en vez de coma flotante binaria.

## Problema

La Organización tiene un campo de límite financiero mensual que se muestra y se puede
configurar, pero **ningún camino de ejecución lo consulta**. No hay comprobación al crear una
campaña, al aprobar una propuesta ni al subir un presupuesto existente. La promesa financiera
que ve el cliente no corresponde a ninguna garantía del sistema.

El mismo campo, el gasto mensual acumulado por AdAccount y los presupuestos de las propuestas
se almacenan y comparan como coma flotante. Una comparación de tipo "el acumulado más el
presupuesto propuesto no supera el límite" puede autorizar un gasto que lo excede, o
rechazar uno que no lo hace, por un residuo de representación binaria. En una compuerta que
decide si se gasta dinero real, eso es un fallo de autorización, no un problema de formato.

Además, el límite carece hoy de una definición operativa: no está dicho contra qué se compara
—gasto ya ejecutado, presupuesto comprometido, o ambos—, ni qué ocurre con una campaña
plurimensual, ni qué hace el sistema con las campañas ya activas cuando se rebaja el límite.

## Alcance

- Definición del límite mensual como contrato: unidad, moneda, ámbito, ventana y qué se
  computa contra él.
- Compuerta de backend aplicada en las tres superficies que comprometen gasto: creación de
  campaña, aprobación de una propuesta y escalado de presupuesto de una campaña existente.
- Cálculo del consumo del mes en curso a partir del gasto real ya sincronizado más el
  presupuesto comprometido por campañas creadas y aún no medidas.
- Representación y comparación exactas de todo monto que participe en una decisión de
  autorización.
- Exposición del límite y del consumo al cliente y a la agencia, con la misma cifra en ambas
  experiencias.
- Comportamiento definido y no destructivo al rebajar el límite por debajo del consumo actual.

## Historias de usuario

Tomadas de la spec maestra:

- **20** — el cliente ve el límite mensual establecido por la agencia.
- **49** — el límite mensual bloquea realmente creación y escalado.
- **50** — los montos críticos se comparan con precisión decimal.

## Decisiones de implementación

- **El límite es una compuerta, no un aviso.** Se evalúa en el backend antes de comprometer
  gasto y devuelve un rechazo de dominio con el motivo, el límite, el consumo y el margen
  disponible. No hay camino que lo evite desde el frontend, desde el chat ni desde un worker.
- **Ámbito: la Organización; ventana: el mes calendario de la zona horaria de la cuenta.** El
  límite protege al cliente, y un cliente puede tener varias AdAccount; aplicarlo por cuenta
  permitiría multiplicar el techo abriendo cuentas. La ventana se ancla al mes calendario
  porque es la unidad en que ya se consolida el gasto por cuenta y mes, y es la que el cliente
  entiende.
- **El consumo del mes es gasto real más compromiso pendiente.** El gasto real proviene de los
  Snapshot ya sincronizados y del consolidado mensual por AdAccount. El compromiso pendiente es
  el presupuesto de las campañas creadas por el sistema en el mes que aún no han acumulado
  gasto medido. Sumar solo el gasto real permitiría crear en un mismo día veinte campañas que
  ninguna ha gastado todavía.
- **Dinero exacto de extremo a extremo en el camino de autorización.** Los montos que
  participan en la compuerta se almacenan y comparan como decimales exactos o como unidades
  enteras mínimas de la moneda. La coma flotante puede seguir usándose para métricas
  descriptivas y gráficos, pero no puede autorizar gasto. La conversión hacia el formato que
  espera Meta es un paso explícito en el borde de la integración, no una representación
  interna.
- **Moneda única por AdAccount y sin conversión implícita.** El límite se expresa en la moneda
  de la cuenta. Mientras el alcance sea Perú, no se implementa conversión; si una Organización
  tuviera cuentas en monedas distintas, la compuerta rechaza en vez de sumar cifras
  incomparables.
- **Rebajar el límite no apaga campañas.** Un límite por debajo del consumo actual bloquea
  toda creación y todo escalado hasta el mes siguiente, y queda visible como estado, pero no
  pausa campañas ya activas: apagar gasto en curso es una decisión humana, no un efecto
  colateral de editar un número.
- **El margen disponible es una lectura pública.** Cliente y agencia consultan el mismo valor
  calculado por el mismo código, para que la cifra mostrada y la que decide sean la misma.

### Contradicción con ADR implícitas

- **ADR-02 (human-in-the-loop y triple gating de escrituras)** se extiende con una cuarta
  compuerta, financiera y por Organización. No la contradice: las tres existentes son de
  configuración global y por cuenta piloto; esta es por cliente y por mes.
- No hay ADR que documente el uso de coma flotante para dinero; es una consecuencia no
  decidida del modelo de datos inicial. Esta spec la fija como decisión explícita y acotada.

## Criterios de aceptación

1. Con el consumo del mes por debajo del límite, crear una campaña cuyo presupuesto cabe en el
   margen se permite. Sí/No.
2. Con el consumo del mes por debajo del límite, crear una campaña cuyo presupuesto excede el
   margen se rechaza, y el rechazo nombra límite, consumo y margen. Sí/No.
3. Aprobar una propuesta que excede el margen se rechaza en la superficie de aprobación,
   aunque la propuesta se hubiera generado cuando había margen. Sí/No.
4. Subir el presupuesto de una campaña existente por encima del margen se rechaza. Sí/No.
5. Bajar el presupuesto de una campaña existente nunca se rechaza por límite. Sí/No.
6. El consumo del mes incluye el presupuesto de campañas creadas en el mes que aún no
   registran gasto medido. Sí/No.
7. Un caso construido para que la comparación en coma flotante autorice un gasto que excede el
   límite es rechazado por el sistema. Sí/No.
8. Un caso construido para que la comparación en coma flotante rechace un gasto que cabe
   exactamente en el margen es aceptado por el sistema. Sí/No.
9. Dos peticiones concurrentes que individualmente caben en el margen pero juntas lo exceden
   dejan como resultado un solo compromiso aceptado. Sí/No.
10. El cliente ve el límite mensual y el margen disponible en su experiencia, sin exponer
    identificadores internos ni configuración de la agencia. Sí/No.
11. La cifra de consumo que ve el cliente y la que usa la compuerta son idénticas para la misma
    Organización y mes. Sí/No.
12. Rebajar el límite por debajo del consumo bloquea creación y escalado, y no pausa ni
    modifica campañas activas. Sí/No.
13. El primer día del mes siguiente el consumo se reinicia y el margen vuelve a ser el límite
    completo. Sí/No.
14. Una Organización con AdAccount en monedas distintas obtiene un rechazo explícito en vez de
    una suma. Sí/No.

## Decisiones de testing

- La frontera es la **API de creación, de aprobación y de modificación de presupuesto**. La
  compuerta se prueba por lo que devuelven esas superficies, no llamando a la función de
  cálculo del margen.
- Los bordes decimales se prueban con casos elegidos para que la aritmética binaria y la
  exacta discrepen; el valor de la prueba está en el par de casos 7 y 8, no en uno solo.
- La concurrencia se prueba con peticiones simultáneas contra **PostgreSQL real**: el resultado
  observable es un único compromiso aceptado, cualquiera de los dos.
- Se reutilizan las pruebas existentes del validador de campaña y del preflight como prior art
  del formato de rechazo, para que el nuevo motivo sea consistente con los que ya existen.
- El reinicio mensual se prueba controlando el reloj del sistema, no esperando al cambio de mes.
- No se prueba el tipo interno del campo: se prueba que la decisión es la correcta en los
  bordes donde el tipo importa.

## Dependencias

- `01` — la compuerta debe rechazar cuando no se puede verificar autorización, y la
  suspensión de una Organización debe bloquear estas mismas superficies antes de que el
  límite se evalúe.

## Fuera de alcance

- Límite de consumo de IA por Organización y sus alertas de 70 %, 90 % y 100 %: pertenecen a
  `10`, aunque reutilizan la representación exacta de dinero definida aquí.
- Facturación, cobro, checkout y suscripción dentro del producto.
- Conversión entre monedas y soporte de países fuera de Perú.
- Pausado automático de campañas al alcanzar el límite.
- Límites por AdAccount o por campaña adicionales al de la Organización.
- La versión inmutable de la propuesta y el consumo único de la aprobación: pertenecen a `03`;
  esta spec expone la compuerta que `03` vuelve a ejecutar en cada versión.

## Riesgos y rollback

- **Daño máximo si no se hace:** el sistema crea campañas sin techo financiero mientras la
  interfaz afirma que existe una protección. El daño se mide directamente en el gasto
  publicitario del cliente.
- **Daño máximo al hacerlo:** un cálculo de consumo demasiado conservador —por ejemplo,
  contando dos veces el compromiso pendiente y el gasto ya medido de la misma campaña— bloquea
  la creación legítima y deja al producto inservible durante el mes.
- **Activación:** la compuerta se despliega primero en modo de solo registro, anotando qué
  habría rechazado sin rechazarlo, durante al menos un ciclo mensual completo con datos
  reales. Se activa como bloqueo cuando el registro no muestra falsos positivos, primero en la
  cuenta piloto y después en el resto.
- **Rollback:** volver al modo de solo registro por configuración, sin desplegar. La migración
  de tipos de dinero es expand-and-contract: se añaden las columnas exactas, se escriben en
  paralelo, se conmuta la lectura y solo después se retiran las antiguas; volver atrás en
  cualquiera de esos pasos no pierde datos.

## Checklist de finalización

- [ ] Migración expand-and-contract de los montos que participan en autorización hacia
      representación exacta, con las columnas antiguas conservadas hasta la fase de contracción.
- [ ] Pruebas de sistema de los catorce criterios, con los bordes decimales y la concurrencia
      contra PostgreSQL real en CI.
- [ ] Documentación de producto que describe el límite: ámbito, ventana, qué computa y qué
      ocurre al rebajarlo.
- [ ] Documentación de configuración actualizada con el nuevo contrato del límite.
- [ ] Observabilidad: consumo y margen por Organización y mes, contador de rechazos por límite
      y contador del modo de solo registro durante el rollout.
- [ ] Evidencia fechada de un ciclo mensual completo en modo de solo registro sin falsos
      positivos, antes de activar el bloqueo.
- [ ] Runbook: cómo ajustar el límite de una Organización y qué efecto inmediato tiene.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Creación, aprobación e idempotencia*
(últimos dos párrafos: compuerta de backend antes de crear/aprobar/escalar, y Decimal o
unidades enteras mínimas); *Problem Statement* (párrafo del límite mensual que no se aplica
como garantía); *Entrega por fases* → **P0**.
