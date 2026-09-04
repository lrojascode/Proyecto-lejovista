# 14 · CI, despliegue reproducible y operación en un VPS limitado

## Estado

Completada con una excepción declarada. Diecisiete de los dieciocho criterios
están implementados y verificados (`backend/tests/test_spec14_ci_y_despliegue.py`,
25 pruebas). El criterio 14 —el ejercicio de rollback— queda a medias a
propósito: el procedimiento está escrito y la mitad ensayable se ensayó, pero el
repin de imagen en producción no se puede hacer desde una máquina de desarrollo.

**Lo primero que apareció no estaba en la lista de criterios: CI ya no describía
lo que se desplegaba.** La imagen del frontend se construía con `npm ci` sobre un
`package-lock.json` de julio, con Node 20, mientras CI validaba
`pnpm install --frozen-lockfile` sobre un `pnpm-lock.yaml` de agosto, con Node 22.
El commit que migró CI a pnpm dejó anotado que «package-lock.json queda en el
repo pero ya no lo usa nadie», y el Dockerfile sí lo usaba. Un mes de CI verde
describiendo un artefacto que nadie construía así. Ahora hay un solo lockfile, un
solo gestor y una sola versión de Node, y
`backend/scripts/verificar_paridad.py` falla la construcción cuando divergen —
que es lo único que impide que vuelva a pasar.

**La suite entera corre contra PostgreSQL, no solo las pruebas de concurrencia.**
Era el trabajo grande de la spec y resultó más barato de lo temido: 1589 de 1601
pruebas pasaron al primer intento. Lo que falló fue informativo. Tres archivos de
tests de workers le pasaban la sesión del test a una tarea que corre en otro
hilo, con su propio bucle de eventos: tolerable con el motor embebido, imposible
con asyncpg. Se les da ahora una fábrica de sesiones, que es lo que la tarea usa
en producción. Y tres tests insertaban filas con claves foráneas inventadas
(`draft_id="d1"`, `resuelta_por_user_id="u1"`) que SQLite acepta porque no
comprueba las claves foráneas por defecto.

**Correr contra el motor de producción destapó además que la base de pruebas
puede no ser la de producción.** Levantar un clúster de PostgreSQL en macOS
exige `initdb` con `LC_ALL=C`, y eso crea las bases en `SQL_ASCII`. Aceptan casi
todo y revientan en el primer texto con un carácter fuera de ASCII —una raya en
un mensaje de error de Meta alcanzaba— con un `UntranslatableCharacterError` que
no se parece en nada a su causa. `conftest.py` lo comprueba al arrancar y lo dice
con su motivo, en vez de dejarlo aparecer como un fallo suelto a mitad de la
suite.

**El hallazgo con consecuencias fue una prueba que pasaba o fallaba según la
hora.** `conftest.py` no sustituía `app.database.async_session`, así que todo
servicio que abre su propia sesión —en vez de recibir la del request— apuntaba a
la base del `.env`, que en una máquina de desarrollo no existe. No fallaba a la
vista: la comprobación de interruptores de la spec 13 falla **cerrado**, así que
apagaba la capacidad de IA y el chat devolvía una plantilla. El test
correspondiente pasaba solo si otro test había dejado el estado en la caché de
cinco segundos. La spec 13 pasó con ese test en verde por casualidad.

**El smoke test comprueba una cosa que ningún healthcheck mira:** si el
contenedor `migrate` corrió. `GET /version` publica el commit horneado en la
imagen y compara la revisión que trae el código con la que tiene la base. Sin
eso, un despliegue en el que `migrate` falló arranca igual, responde igual, pasa
el healthcheck y se va rompiendo por donde toca el esquema nuevo. Se ensayó
provocándolo: todas las demás comprobaciones siguieron en verde.

**El verificador de migraciones tuvo que aprender a distinguir un reemplazo de
una pérdida.** Borrar una restricción y volver a crearla en el mismo `upgrade()`
es cómo se cambia el `ondelete` de una clave foránea, no una contracción.
Marcarlo habría enseñado a ignorar al verificador, que es la única forma segura
de inutilizarlo. Con esa distinción, las 52 migraciones existentes pasan; las dos
contracciones históricas quedaron declaradas en su propio archivo en vez de
exentas en una lista aparte que nadie vuelve a leer.

**Una puerta que se equivoca dejando pasar es peor que no tenerla.** El preflight
de migración riesgosa comprobaba el modo de solo lectura con
`"solo_lectura"[^}]*true`, que encontraba el `"encendida":true` de la primera
capacidad y daba por activo el bloqueo estando apagado. Se detectó ejecutándolo
contra la respuesta real de la API antes de que llegara a usarse.

### Lo que queda fuera

- **El ejercicio de rollback en producción (criterio 14).** Requiere dos
  imágenes publicadas en GHCR con su sha, repin de `IMAGE_TAG` en Coolify y un
  Redeploy. Lo ensayable se ensayó y está fechado en
  `docs/infra/operacion/despliegues/2026-09-04-ensayo-verificacion.md`: smoke
  test en verde, detección de commit equivocado, detección de migración sin
  aplicar, detección de Redis caído, reversibilidad de la última migración sobre
  PostgreSQL real y la puerta de migración riesgosa en los dos sentidos.
- **La construcción de las imágenes no se ejecutó localmente**: el entorno de
  trabajo no tenía Docker. La construye CI en cada corrida desde esta spec, que
  es donde tiene que fallar si los Dockerfiles no cierran.
- **La carga sintética proporcional a 20–50 cuentas.** Los límites de recursos
  son una estimación razonada y holgada, no una medición. La aritmetica y lo que
  se sabe del crecimiento están en `docs/infra/operacion/dimensionado.md`.
- **El despliegue sigue siendo manual** en Coolify, por decisión de la spec
  maestra. Lo que cambió es que ahora se despliega un identificador y no una
  etiqueta flotante.

### Verificación

| Comprobación | Resultado |
|---|---|
| Suite contra PostgreSQL 16 y Redis reales | 1629 pruebas, ninguna saltada |
| Suite contra el motor embebido | 1621 pruebas, 8 saltadas (las que exigen PostgreSQL o Redis) |
| Spec 14 | 25 pruebas, cada verificador probado en los dos sentidos |
| Migraciones | 52 aplicadas desde cero sobre PostgreSQL real |
| Lint | limpio |
| Frontend | `pnpm install --frozen-lockfile`, lint, `tsc --noEmit`, 52 pruebas y `build` limpios |
| Bundle standalone construido con pnpm | arranca y sirve HTTP 200 |

La construcción de las imágenes Docker no se ejecutó: el entorno de trabajo no
tenía Docker. Es la única pieza de la spec que se verifica por primera vez en CI.

## Prioridad

P3.

## Objetivo

Que producción reciba exactamente el artefacto que CI validó, que ese artefacto sea
identificable y reversible, y que el VPS de 2 vCPU, 4 GB y 100 GB sostenga de 20 a 50
AdAccount sin construir alta disponibilidad prematura.

## Problema

CI ejecuta las pruebas contra un motor de base de datos embebido y aplica las migraciones
contra PostgreSQL por separado. Es decir, **ninguna prueba se ejecuta contra el motor de
producción**: el dialecto, la concurrencia, los bloqueos y las restricciones reales solo se
ejercitan en la cadena de migraciones. Varias specs anteriores dependen de comportamientos que
solo existen en PostgreSQL —restricciones únicas, actualizaciones condicionales, carreras—, y
esas pruebas no tienen dónde correr. Redis no se levanta en CI en absoluto.

El despliegue publica cada imagen con dos etiquetas: el identificador del commit y una etiqueta
flotante. El redespliegue habitual usa la flotante, de modo que lo que llega a producción no es
un artefacto identificable sino "lo último que se construyó". La spec maestra prohíbe
explícitamente las etiquetas flotantes.

No hay una política declarada de migraciones compatibles hacia atrás. Una migración destructiva
convierte el rollback a la imagen anterior en una restauración desde respaldo, que es un
procedimiento de horas.

No existe un entorno de preproducción, y la spec maestra decide que no debe existir en el VPS
productivo. Falta declarar qué cumple ese papel y cómo se valida un cambio mayor.

Por último, no hay límites de recursos declarados para PostgreSQL, Redis, los workers y el
almacenamiento en un servidor donde todos comparten 4 GB.

## Alcance

- CI que ejecuta las pruebas contra PostgreSQL y Redis reales, con las mismas versiones que
  producción.
- Una sola versión de Node, un único gestor de paquetes y dependencias bloqueadas, compartidas
  entre CI y producción.
- Publicación de imágenes inmutables identificadas por commit, y despliegue por identificador
  explícito en vez de etiqueta flotante.
- Política de migraciones expand-and-contract, con ventana de mantenimiento y modo de solo
  lectura para los cambios que no la admitan.
- Respaldo externo verificado antes de una migración riesgosa, y conservación de imágenes
  anteriores.
- Healthcheck, smoke test posterior al despliegue y procedimiento de rollback.
- Preproducción funcional fuera del VPS productivo: entorno local con el mismo stack, e
  infraestructura temporal para actualizaciones mayores de proveedores.
- Límites de recursos y alertas para PostgreSQL, Redis, workers y almacenamiento, dimensionados
  para 20–50 AdAccount.

## Historias de usuario

Tomadas de la spec maestra:

- **110** — imagen inmutable identificada por commit.
- **111** — una sola versión de Node, un gestor de paquetes y dependencias bloqueadas.
- **112** — migraciones expand-and-contract.
- **113** — staging fuera del VPS productivo limitado.
- **114** — validación local y en CI con PostgreSQL y Redis reales.
- **115** — infraestructura temporal para upgrades mayores de proveedores.
- **116** — ventana de mantenimiento y modo de solo lectura para cambios incompatibles.
- **117** — conservación de imágenes anteriores y smoke tests posteriores al despliegue.
- **118** — soporte inicial de 20–50 cuentas publicitarias.

## Decisiones de implementación

- **Las pruebas corren contra el motor de producción.** El motor embebido puede seguir
  existiendo para la parte de la suite que no toca el dialecto, pero toda prueba de
  concurrencia, de restricción única, de actualización condicional o de comportamiento
  transaccional se ejecuta contra PostgreSQL. Redis se levanta también, porque el
  comportamiento de fallo cerrado que definen `01` y `13` no se puede probar sin poder
  detenerlo.
- **El artefacto de despliegue es el identificador del commit.** La etiqueta flotante puede
  seguir publicándose como conveniencia de desarrollo, pero el despliegue a producción se hace
  por identificador explícito. Es la diferencia entre saber qué está corriendo y suponerlo.
- **Una imagen, un origen, un lockfile.** Una sola versión de Node, un solo gestor de paquetes y
  las dependencias bloqueadas, iguales en CI y en la imagen. Cualquier divergencia entre
  ambos convierte un CI verde en una promesa que no se cumple.
- **Toda migración es compatible hacia atrás por defecto.** Se añade, se escribe en paralelo, se
  conmuta la lectura y solo en un despliegue posterior se retira lo antiguo. Una migración que
  no pueda expresarse así requiere ventana de mantenimiento con modo de solo lectura, respaldo
  externo verificado inmediatamente antes y una decisión explícita, no un despliegue rutinario.
- **El rollback es volver a la imagen anterior, y hay que poder hacerlo.** Las imágenes
  anteriores se conservan. La política de migraciones existe precisamente para que ese rollback
  no requiera revertir datos.
- **Cada despliegue termina con una comprobación, no con un "up".** Healthcheck y smoke test
  que ejerciten el recorrido mínimo del producto: autenticación, lectura de histórico y una
  llamada que toque base de datos y Redis. Un despliegue sin smoke test no está verificado, solo
  está hecho.
- **La preproducción es local y efímera, no permanente.** El stack completo se levanta en la
  máquina de desarrollo con las mismas versiones. Para una actualización mayor de un proveedor
  —motor de base de datos, versión mayor del framework— se levanta infraestructura temporal
  separada, se valida y se destruye. Esto sustituye a un staging permanente, que la spec maestra
  descarta por el costo y por el consumo de recursos del VPS.
- **Los límites de recursos se declaran por contenedor.** En 4 GB compartidos, un worker sin
  techo de memoria puede llevarse por delante la base de datos. Los límites se dimensionan para
  el objetivo de 20–50 AdAccount y se acompañan de alertas, definidas en `13`.
- **La capacidad objetivo es una decisión declarada, no un límite técnico.** 20–50 AdAccount es
  el rango para el que se dimensiona; superarlo es una decisión con evidencia, no una sorpresa.

### Contradicción con ADR implícitas

- **ADR-12 (una imagen de backend para API, workers y beat)** se preserva y se refuerza: es
  precisamente lo que hace que la paridad entre CI y producción sea verificable.
- **ADR-05 (colas Celery dedicadas)** se preserva; los límites de recursos se aplican por cola,
  que es lo que permite que un pico de sincronización no desplace a la creación.
- El uso de una etiqueta flotante en el despliegue contradice de forma directa el requisito de
  la spec maestra. Es una práctica actual, no una decisión documentada.

## Criterios de aceptación

1. CI levanta PostgreSQL y Redis con las mismas versiones mayores que producción. Sí/No.
2. Las pruebas de concurrencia, restricción única y actualización condicional se ejecutan contra
   PostgreSQL en CI. Sí/No.
3. Las pruebas que requieren detener Redis pueden ejecutarse en CI. Sí/No.
4. CI ejecuta lint, comprobación de tipos, pruebas, migraciones sobre PostgreSQL y construcción
   de imágenes en la misma ejecución. Sí/No.
5. Existe una sola versión de Node declarada y un solo gestor de paquetes, iguales en CI y en la
   imagen. Sí/No.
6. Las dependencias están bloqueadas y CI falla si el lockfile no corresponde al manifiesto.
   Sí/No.
7. El despliegue a producción se realiza por identificador de commit, no por etiqueta flotante.
   Sí/No.
8. Es posible determinar, desde el sistema en ejecución, qué commit está desplegado. Sí/No.
9. Las imágenes de los despliegues anteriores siguen disponibles para rollback. Sí/No.
10. Una migración de ejemplo aplicada con la imagen nueva no impide que la imagen anterior
    siga funcionando. Sí/No.
11. Existe un procedimiento documentado de ventana de mantenimiento con modo de solo lectura
    para migraciones incompatibles. Sí/No.
12. Existe un requisito verificable de respaldo externo comprobado antes de una migración
    riesgosa. Sí/No.
13. Cada despliegue ejecuta un smoke test que ejercita autenticación, lectura de histórico y una
    llamada que toca base de datos y Redis, y su resultado queda registrado. Sí/No.
14. Existe un procedimiento de rollback documentado y ejecutado al menos una vez como ejercicio.
    Sí/No.
15. El stack completo se levanta localmente con las mismas versiones que producción. Sí/No.
16. Existe un procedimiento documentado para levantar infraestructura temporal para una
    actualización mayor de proveedor y destruirla al terminar. Sí/No.
17. PostgreSQL, Redis, los workers y el almacenamiento tienen límites de recursos declarados,
    dimensionados para 20–50 AdAccount. Sí/No.
18. No se ejecuta ningún entorno de staging en el VPS productivo. Sí/No.

## Decisiones de testing

- La frontera es el **pipeline y el drill de despliegue**: construcción, publicación,
  despliegue por identificador, smoke test y rollback, con evidencia fechada. Es el mismo tipo
  de seam operativo que `04`.
- La paridad entre CI y producción se verifica comparando versiones declaradas, no confiando en
  que coincidan.
- La política de migraciones se prueba con un ejercicio concreto: aplicar una migración con la
  imagen nueva y comprobar que la anterior sigue funcionando. Es la única forma de saber si el
  rollback existe de verdad.
- El rollback se ejecuta como ejercicio al menos una vez, con evidencia. Un procedimiento
  escrito y nunca ejecutado tiene el mismo valor que un respaldo nunca restaurado.
- La migración de las pruebas al motor de producción se hace por lotes, empezando por las que
  hoy no pueden probar lo que deberían; se prioriza sobre añadir pruebas nuevas, tal como
  señala la spec maestra.
- Los límites de recursos se validan con una carga sintética proporcional al objetivo declarado,
  no con la carga actual de un solo cliente.

## Dependencias

- `04` — el respaldo externo verificado que exige el procedimiento de migración riesgosa.
- `13` — el modo de solo lectura que usa la ventana de mantenimiento, y las alertas de recursos.

## Fuera de alcance

- Alta disponibilidad, réplica, balanceo y arquitectura distribuida. La spec maestra los excluye
  hasta que la carga los justifique.
- Staging permanente, en el VPS productivo o fuera de él.
- Migración a otro proveedor de alojamiento o a orquestación distinta de la actual.
- Despliegue continuo automático a producción. El disparo del despliegue sigue siendo una
  decisión humana.
- Optimización del rendimiento de la aplicación más allá de los límites de recursos y del
  dimensionado declarado.
- Escalado por encima de 50 AdAccount.
- La elección de proveedores concretos de almacenamiento de objetos y de monitorización
  externa, que se documentan aquí como decisión de infraestructura cuando `07` y `13` las
  requieran.

## Riesgos y rollback

- **Daño máximo si no se hace:** producción corre algo distinto de lo validado, y un fallo que
  solo se manifiesta en PostgreSQL bajo concurrencia —exactamente la clase de fallo que `01`,
  `02` y `03` corrigen— pasa CI y llega a producción. Un rollback que requiere restaurar desde
  respaldo convierte un despliegue fallido en horas de indisponibilidad.
- **Daño máximo al hacerlo:** migrar la suite de pruebas al motor de producción destapa fallos
  latentes que hoy pasan, y el trabajo se dispara. Un CI con servicios reales es también más
  lento y más frágil.
- **Activación:** los servicios reales se añaden a CI en paralelo a la suite actual, migrando
  las pruebas por lotes en vez de todas a la vez. El despliegue por identificador se adopta de
  inmediato: es un cambio de procedimiento, no de código. Los límites de recursos se aplican
  primero con valores holgados y se ajustan con medición.
- **Rollback:** cada pieza es independiente y reversible. La única que no admite vuelta atrás
  cómoda es la migración de la suite de pruebas, y no debería tenerla: revertirla equivale a
  volver a no probar lo que importa.

## Checklist de finalización

- [x] PostgreSQL y Redis reales en CI, con las versiones mayores de producción.
- [x] Suite de pruebas migrada por lotes al motor de producción, empezando por concurrencia y
      restricciones.
- [x] Versión de Node única, gestor de paquetes único y lockfiles verificados en CI.
- [x] Despliegue por identificador de commit; retirada de la etiqueta flotante del procedimiento
      de producción.
- [x] Identificador del commit desplegado consultable desde el sistema en ejecución.
- [x] Política de migraciones expand-and-contract documentada, con el procedimiento de ventana
      de mantenimiento para las excepciones.
- [x] Smoke test posterior al despliegue, con resultado registrado.
- [ ] Ejercicio de rollback ejecutado, con evidencia fechada versionada. — **pendiente**:
      necesita dos imágenes publicadas y un Redeploy. Lo ensayable está fechado en
      `docs/infra/operacion/despliegues/2026-09-04-ensayo-verificacion.md`.
- [x] Procedimiento de infraestructura temporal para actualizaciones mayores.
- [ ] Límites de recursos declarados por contenedor y validados con carga sintética. —
      declarados y comprobados en CI; la validación con carga sintética queda pendiente.
- [x] Documentación de despliegue, de testing y de configuración actualizadas.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Operación en el VPS limitado*; *Testing
Decisions* (párrafos de CI y del seam operativo); *Problem Statement* (párrafo del VPS de 2
vCPU y 4 GB); *Further Notes* (párrafo sobre la confianza que debe venir de PostgreSQL real,
concurrencia y contratos externos); *Entrega por fases* → **P3**.
