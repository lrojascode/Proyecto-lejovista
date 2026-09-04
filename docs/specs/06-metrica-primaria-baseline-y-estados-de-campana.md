# 06 · Métrica primaria, baseline y estados de campaña

## Estado

Completada. Los catorce criterios se verifican en
`backend/tests/test_spec06_baseline.py`, con los datasets versionados junto a las
pruebas y una comprobación de ida y vuelta del archivo que **falla si se añade
una columna a una tabla caliente sin añadirla al archivo**.

**Precisión sobre el criterio 1.** Dice «nunca validada ni por debajo del
baseline», no «siempre datos insuficientes». Una campaña con 30 conversaciones en
3 días tiene evidencia de sobra en volumen y le falta recorrido: se reporta como
*prometedora*, que sigue sin ser un veredicto. Lo que la spec prohíbe es emitir un
juicio definitivo, no reconocer una tendencia. Se añadió además un piso de
evidencia para que *prometedora* no se alcance con una única conversación barata.

**Hueco conocido y no rellenado.** Las filas ya archivadas antes de esta spec no
tienen `objective`, `results`, `effective_status` ni las fechas estructurales, y
no son recuperables sin volver a pedírselas a Meta: el archivado borró la fila
caliente. Se dejan en `NULL` en vez de inventarles un valor.

## Prioridad

P1.

## Objetivo

Que el producto tenga una definición reproducible de "esto funcionó": una métrica primaria sin
ambigüedad, un baseline resistente a valores extremos, una muestra mínima antes de emitir un
juicio, y un histórico que conserve sin pérdida los campos de los que ese juicio depende.

## Problema

El sistema ya unifica la conversión como conversaciones de WhatsApp con caída a leads, y ya
tiene un clasificador único relativo a un CPL objetivo calibrado con el histórico de la cuenta.
Quedan tres huecos.

**Muestras insuficientes producen etiquetas definitivas.** El clasificador califica una campaña
sin exigir un mínimo de días ni de conversaciones. Una campaña con una conversación barata
puede salir como ganadora, y esa etiqueta alimenta las recomendaciones siguientes.

**El baseline es sensible a valores extremos.** El CPL objetivo se calibra con estadísticos que
un puñado de campañas anómalas desplaza. La spec maestra fija la mediana como base.

**El archivo frío pierde exactamente lo que el aprendizaje necesita.** Al mover Snapshot de
campaña a las tablas de archivo se copian gasto, impresiones, clics, conversiones,
conversaciones y desgloses, pero **no** el objetivo de la campaña, el conteo crudo de
resultados, el estado efectivo de entrega ni las fechas de creación, inicio y fin. Son
precisamente los campos que determinan si dos campañas son comparables y cuántas conversaciones
reales hubo. Pasados los meses de retención en caliente, una campaña histórica deja de poder
usarse como evidencia, y el producto pierde su propuesta de valor a medida que envejece.

Falta además una noción declarada de frescura: el cliente no sabe cuándo está mirando datos
desactualizados o incompletos.

## Alcance

- Definición formal de la métrica primaria: costo por conversación de WhatsApp atribuida por
  Meta, su caída a leads y su presentación honesta.
- Cálculo del baseline por mediana sobre campañas comparables, conservando además volumen y
  gasto.
- Umbrales de madurez configurables por Organización, con siete días y diez conversaciones como
  valor predeterminado.
- Exclusión de muestras insuficientes y anomalías del cálculo del baseline.
- Cuatro estados de rendimiento: datos insuficientes, prometedora, validada y por debajo del
  baseline.
- Baseline congelado en el momento de crear una campaña, y recalibración de baselines futuros
  sin alterar la evaluación de campañas ya creadas.
- Preservación sin pérdida en el archivo frío de todos los campos que usan aprendizaje,
  explicación y auditoría.
- Señal de frescura y de cobertura del histórico, expuesta junto a cada cifra relevante.

## Historias de usuario

Tomadas de la spec maestra:

- **8** — el producto llama conversión a una conversación de WhatsApp atribuida por Meta.
- **9** — el cliente ve el costo por conversación frente a un baseline comparable.
- **10** — se distingue datos insuficientes, prometedora, validada y por debajo del baseline.
- **11** — el cliente sabe cuándo los datos están desactualizados o incompletos.
- **38** — la agencia ve el baseline congelado de una campaña nueva.
- **39** — los baselines futuros cambian por temporada y variantes.
- **74** — el archivo frío conserva sin pérdida los campos usados por el aprendizaje.
- **77** — se excluyen campañas con datos insuficientes o anómalos.
- **78** — se usa la mediana del costo por conversación comparable.
- **79** — se exigen por defecto siete días y diez conversaciones para validar.
- **80** — se consideran también volumen y gasto suficientes.

## Decisiones de implementación

- **Una métrica primaria, nombrada por lo que es.** El costo por conversación es gasto dividido
  entre conversaciones de WhatsApp atribuidas por Meta. No se presenta como venta, ingreso ni
  retorno. La caída a leads cuando la campaña no es de mensajería se mantiene, y la respuesta
  declara cuál de las dos se está usando: mezclarlas sin decirlo produce comparaciones falsas
  entre campañas de distinta naturaleza.
- **Mediana, no promedio.** El baseline es la mediana del costo por conversación de las
  campañas comparables. Junto a ella se conservan el volumen de conversaciones y el gasto del
  conjunto: una mediana sobre tres campañas diminutas y una sobre treinta campañas grandes son
  cifras distintas aunque coincidan, y el consumidor del baseline necesita poder distinguirlas.
- **La madurez es una condición doble y configurable.** Una campaña se considera validada
  cuando ha cumplido a la vez la ventana temporal y el mínimo de conversaciones. Los valores
  predeterminados son siete días y diez conversaciones, ajustables por Organización porque el
  volumen razonable depende del rubro. Añadir un umbral de gasto mínimo evita el caso de una
  única conversación barata que dispara la etiqueta.
- **Cuatro estados y ninguna etiqueta definitiva sobre muestra pequeña.** *Datos insuficientes*
  mientras no se cumple la madurez. *Prometedora* cuando la tendencia es favorable pero la
  muestra aún no basta. *Validada* cuando cumple madurez y mejora o iguala el baseline. *Por
  debajo del baseline* cuando cumple madurez y no lo alcanza. Las etiquetas actuales del
  clasificador siguen existiendo para la agencia; estos cuatro estados son el vocabulario
  común y el único que ve el cliente.
- **El baseline de una campaña se congela al crearla.** Es el número contra el que se prometió
  esa campaña y contra el que se la evalúa toda su vida. El baseline vigente para propuestas
  futuras evoluciona con temporada y variantes; ambos coexisten sin sobrescribirse. Reevaluar
  el pasado con el baseline de hoy destruye la posibilidad de saber si el sistema acertó.
- **El archivo frío conserva todo lo que el aprendizaje lee.** La lista de campos preservados
  se deriva de lo que consumen comparabilidad, baseline, explicación y auditoría, e incluye
  como mínimo el objetivo de la campaña, el conteo crudo de resultados, el estado efectivo de
  entrega y las fechas estructurales. La regla operativa es que un campo solo puede omitirse
  del archivo si ningún consumidor de aprendizaje lo lee, y esa comprobación es parte del
  cambio, no una revisión posterior.
- **Frescura y cobertura son datos, no notas al pie.** Cada cifra relevante puede declarar de
  cuándo son los datos que la sustentan y qué parte del periodo consultado está efectivamente
  cubierta por Snapshot. La reconciliación de histórico contra el consolidado mensual por
  AdAccount ya mide esa diferencia; esta spec la convierte en una señal de producto.

### Contradicción con ADR implícitas

- **ADR-01 (snapshots diarios idempotentes y archivado)** se corrige en su consecuencia: el
  archivado, tal como está, degrada el aprendizaje. La decisión de archivar se mantiene; la
  selección de columnas deja de ser un subconjunto arbitrario y pasa a derivarse de los
  consumidores.
- **ADR-10 (clasificador único relativo al CPL objetivo dinámico)** se extiende: el
  clasificador sigue siendo único y determinista, y ahora exige muestra mínima antes de emitir
  etiqueta y usa mediana en la calibración.
- **ADR-09 (métrica primaria unificada con caída a leads)** se preserva y se hace explícita en
  la presentación.

## Criterios de aceptación

1. Una campaña con menos de la ventana de días configurada se reporta como datos insuficientes,
   nunca como validada ni como por debajo del baseline. Sí/No.
2. Una campaña con menos del mínimo de conversaciones configurado se reporta como datos
   insuficientes, aunque haya superado la ventana temporal. Sí/No.
3. Una campaña con una única conversación muy barata no se reporta como validada. Sí/No.
4. El baseline de un conjunto que contiene un valor extremo coincide con la mediana y no se
   desplaza hacia ese valor. Sí/No.
5. El baseline se acompaña siempre del número de campañas comparables, del volumen de
   conversaciones y del gasto del conjunto. Sí/No.
6. Cambiar los umbrales de madurez de una Organización cambia los estados reportados para esa
   Organización y no para otra. Sí/No.
7. Una campaña creada por el sistema conserva su baseline congelado y ese valor no cambia
   cuando el baseline vigente de la cuenta se recalibra. Sí/No.
8. El baseline vigente para propuestas nuevas sí cambia al recalibrarse. Sí/No.
9. Tras archivar un Snapshot de campaña, todos los campos que consume el aprendizaje siguen
   disponibles con el mismo valor. Sí/No.
10. Una campaña cuyo periodo cae íntegramente en el archivo frío puede seguir usándose como
    campaña comparable. Sí/No.
11. La respuesta que declara un costo por conversación indica si la cifra proviene de
    conversaciones o de leads. Sí/No.
12. Ninguna superficie presenta el costo por conversación como venta, ingreso o retorno. Sí/No.
13. Una consulta sobre un periodo con Snapshot faltantes declara la cobertura incompleta en vez
    de devolver la cifra parcial como si fuera completa. Sí/No.
14. Una consulta cuya última sincronización es anterior a un umbral declara los datos como
    desactualizados. Sí/No.

## Decisiones de testing

- La frontera es la **API de reporte, de campañas y de diagnóstico**, más lo que el chat
  recibe como hechos. El baseline y los estados se prueban por lo que devuelven esas
  superficies, no llamando a la función de cálculo.
- Los datasets de prueba son el entregable central: conjuntos con valor extremo, con muestra
  pequeña, con volumen alto y costo alto, con una sola conversación barata, con periodos de
  temporadas distintas y con ausencia total de comparables. Se verifica el baseline y el
  estado resultante, no el algoritmo que los produce.
- La fidelidad del archivo se prueba como un recorrido de ida y vuelta: se archiva un Snapshot
  conocido y se comprueba que el aprendizaje obtiene los mismos valores desde el archivo que
  obtenía desde la tabla caliente. La prueba se escribe de forma que añadir un campo nuevo al
  Snapshot sin añadirlo al archivo la haga fallar.
- Se reutilizan como prior art las pruebas existentes del clasificador, del CPL objetivo, del
  constructor de reportes, del agregado de siete días y de la reconciliación de histórico.
- Los umbrales configurables se prueban con dos Organizaciones con valores distintos en la
  misma ejecución, para verificar que no hay estado compartido.
- La frescura se prueba controlando el reloj y la marca de última sincronización, no esperando.

## Dependencias

- `01` — el aislamiento por Organización y AdAccount debe ser firme antes de que el baseline se
  calcule sobre datos de una cuenta.

## Fuera de alcance

- Los criterios de comparabilidad entre campañas y el ranking de campañas históricas:
  pertenecen a `11`. Esta spec define el baseline sobre un conjunto ya considerado comparable.
- La presentación conversacional de estos estados y la explicación en lenguaje natural:
  pertenecen a `12`.
- El veredicto del Outcome loop sobre campañas creadas por el sistema y su uso como
  aprendizaje: pertenecen a `11`.
- Integración con CRM, calificación de leads y cualquier métrica de venta, ingreso, retorno o
  ROAS.
- Cambiar la política de retención en caliente o la ventana de archivado. Esta spec cambia qué
  se conserva, no cuándo se archiva.
- Eliminación de histórico por baja de un cliente: pertenece a `09`.

## Riesgos y rollback

- **Daño máximo si no se hace:** el producto llama exitosa a una campaña con dos conversaciones,
  recomienda repetirla, y el cliente gasta contra una conclusión que los datos no sostienen. A
  medio plazo, el archivo frío vacía la base de evidencia y el copiloto queda sin histórico
  utilizable.
- **Daño máximo al hacerlo:** unos umbrales demasiado exigentes dejan a una cuenta pequeña sin
  ninguna campaña validada, y el producto no dice nada útil nunca. La corrección del archivo
  toca una tarea de mantenimiento que borra filas de la tabla caliente: un error ahí pierde
  datos de forma irreversible.
- **Activación:** los estados nuevos se calculan y registran en paralelo a las etiquetas
  actuales durante un ciclo, para comparar. Se exponen al cliente cuando la comparación no
  muestra sorpresas. El cambio del archivo se despliega en dos tiempos: primero se añaden las
  columnas al archivo y se detiene la tarea de archivado, después se rellenan las filas ya
  archivadas donde el dato sea recuperable y solo entonces se reanuda.
- **Rollback:** los estados nuevos se apagan por configuración. El cambio del archivo es
  aditivo —columnas nuevas, nunca eliminación— y por tanto reversible volviendo a la imagen
  anterior; las filas ya archivadas sin los campos nuevos quedan como hueco conocido y
  documentado, no como corrupción.

## Checklist de finalización

- [ ] Migración aditiva que añade al archivo frío los campos que consume el aprendizaje.
- [ ] Procedimiento de relleno de las filas ya archivadas, con constancia de qué no es
      recuperable y por qué.
- [ ] Migración de los umbrales de madurez configurables por Organización, con los valores
      predeterminados aplicados sin cambiar el comportamiento existente.
- [ ] Pruebas de sistema de los catorce criterios, con los datasets de baseline versionados
      junto a las pruebas.
- [ ] Prueba de ida y vuelta del archivo que falla si se añade un campo al Snapshot sin añadirlo
      al archivo.
- [ ] Documentación de producto y glosario actualizados: métrica primaria, baseline, madurez y
      los cuatro estados.
- [ ] Documento de decisiones arquitectónicas actualizado: consecuencia de ADR-01 y extensión de
      ADR-10.
- [ ] Observabilidad: distribución de campañas por estado, tamaño del conjunto comparable y
      cobertura del histórico por AdAccount.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Aprendizaje, baseline y outcome*
(párrafos de métrica primaria, baseline por mediana, madurez y baselines congelados);
*Experiencia conversacional* (párrafo de estados de rendimiento); *Retención, privacidad y
auditoría* (párrafo del archivo histórico); *Problem Statement* (párrafos de clasificaciones con
muestras insuficientes y de pérdida de información en el archivo frío); *Entrega por fases* →
**P1**.
