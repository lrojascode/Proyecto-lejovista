# 08 · Meta Graph/Marketing API v26 y gestión de deprecaciones

## Estado

Completada. Los trece criterios se verifican en
`backend/tests/test_spec08_meta_version.py`. La fuente de verdad es
`app/services/meta_ads/version.py`; el inventario vive en
[`docs/11-inventario-meta.md`](../11-inventario-meta.md).

**Lo que realmente cierra el hueco no es alinear la versión, es la comprobación
de CI.** Alinear los ocho sitios una vez no arregla nada: la próxima migración
vuelve a desalinearlos, que es exactamente cómo se llegó a tener v25 en el código
y v26 en producción. `scripts/verificar_version_meta.py` corre en cada
construcción y falla cuando alguno se desvía.

**Pendiente para producción.** El flag de versión candidata está implementado y
probado, pero no se ha ejercitado contra Meta real: la prueba de contrato externo
—sync completo y creación pausada en la cuenta piloto— es trabajo de despliegue,
no de código, y no se puede simular. Hasta hacerla, el procedimiento de migración
está escrito y verificado en su forma, no en su ejecución.

## Prioridad

P1.

## Objetivo

Que la versión de la API de Meta sea explícita, única y coherente entre configuración,
despliegue, pruebas y documentación; y que cambiar de versión sea un procedimiento con
inventario, cuenta piloto, feature flag y rollback, en vez de un salto a ciegas.

## Problema

Producción utiliza v26, pero el valor predeterminado del código, el fichero de ejemplo de
entorno, la plantilla de despliegue y seis documentos dicen v25. Cualquier despliegue que no
inyecte la variable correcta —o cualquier entorno nuevo levantado desde la plantilla— vuelve a
v25 en silencio. La spec maestra lo califica de deuda operativa inmediata.

El sistema tampoco sabe qué versión le está atendiendo Meta realmente. Meta responde con una
cabecera que indica la versión servida, y no se lee ni se compara con la configurada. Una
discrepancia entre lo que se pide y lo que se sirve pasa inadvertida hasta que un campo
desaparece.

No existe inventario de qué endpoints, campos y permisos usa el producto. Sin él, evaluar el
impacto de una deprecación obliga a leer el código entero cada vez, y una migración de versión
se convierte en descubrir errores en producción.

## Alcance

- Una única fuente de verdad para la versión de la API, alineada en código, configuración de
  entorno, plantillas de despliegue, pruebas y documentación.
- Registro y verificación de la versión realmente atendida por Meta, con alerta ante
  discrepancia.
- Vigilancia de la fecha de expiración de la versión en uso.
- Inventario versionado de endpoints, campos, permisos y comportamientos utilizados.
- Procedimiento de migración de versión: prueba contra cuenta piloto con sync completo y
  creación pausada, despliegue progresivo por feature flag y rollback a la versión anterior.

## Historias de usuario

Tomadas de la spec maestra:

- **91** — fijar Meta Graph/Marketing API v26 explícitamente.
- **92** — comprobar el header de versión atendida por Meta.
- **93** — inventario de endpoints, campos y permisos de Meta.
- **94** — probar la siguiente versión con una cuenta piloto.
- **95** — desplegar una versión candidata mediante feature flag.
- **96** — conservar la versión anterior y un rollback operativo.

## Decisiones de implementación

- **Una fuente de verdad, replicada mecánicamente.** El valor predeterminado del código es la
  versión soportada, y todos los demás lugares donde aparece —fichero de ejemplo de entorno,
  plantilla de despliegue, documentación— se derivan de él o se verifican contra él de forma
  automática. La verificación forma parte del CI: una discrepancia rompe la construcción. Sin
  esa comprobación, el problema vuelve a aparecer en la siguiente migración.
- **La versión atendida se lee de la respuesta y se contrasta.** Meta declara en cada respuesta
  qué versión sirvió. Se registra y se compara con la configurada; una divergencia sostenida es
  una alerta, no una línea de log. Es la única forma de detectar que una llamada está cayendo a
  una versión distinta de la pedida.
- **La expiración de la versión es una cuenta atrás vigilada.** Meta retira cada versión a los
  dos años aproximadamente. La fecha de expiración de la versión en uso se mantiene como dato y
  se alerta con antelación suficiente para planificar una migración, no para reaccionar a un
  corte.
- **El inventario es un documento versionado, no una consulta.** Lista los endpoints que el
  producto llama, los campos que pide de cada uno, los permisos que requiere y los
  comportamientos de los que depende —por ejemplo, qué campo obligatorio tiene un tipo de
  creativo, o qué forma tiene el objeto promocionado de un conjunto—. Se actualiza en el mismo
  cambio que introduce un uso nuevo. Su valor está en poder leer un changelog de Meta y
  responder en minutos si nos afecta.
- **La versión candidata convive con la vigente detrás de un flag.** El flag es por
  Organización, para que la cuenta piloto pueda ejercitar la versión nueva mientras el resto
  sigue en la vigente. Conviven dos versiones a la vez y ninguna más: mantener tres
  simultáneamente multiplica los caminos sin aportar seguridad.
- **La prueba de una versión candidata es un recorrido completo, no una llamada de humo.**
  Sincronización completa de una cuenta con histórico real y creación de una campaña pausada de
  bajo presupuesto. Los campos que desaparecen se manifiestan en el sync; los cambios de
  contrato de escritura, solo al escribir.
- **El rollback es cambiar el flag, no desplegar.** La versión anterior sigue siendo un valor
  admitido mientras dure la migración, de modo que volver atrás no requiera construir ni
  publicar una imagen.

### Contradicción con ADR implícitas

Ninguna decisión arquitectónica documentada se contradice. Lo que esta spec corrige es una
divergencia entre configuración y realidad de producción, no una decisión.

## Criterios de aceptación

1. El valor predeterminado de la versión en el código es v26. Sí/No.
2. El fichero de ejemplo de entorno, la plantilla de despliegue y la documentación declaran la
   misma versión que el código. Sí/No.
3. Existe una comprobación automática en CI que falla si alguno de esos lugares declara una
   versión distinta a la del código. Sí/No.
4. Un entorno levantado desde la plantilla de despliegue, sin variables adicionales, usa v26.
   Sí/No.
5. El sistema registra la versión que Meta declara haber servido en sus respuestas. Sí/No.
6. Una discrepancia sostenida entre versión configurada y versión servida genera una alerta.
   Sí/No.
7. La fecha de expiración de la versión en uso está registrada y genera un aviso con antelación
   configurada. Sí/No.
8. Existe un inventario versionado de endpoints, campos, permisos y comportamientos usados,
   consultable en el repositorio. Sí/No.
9. Introducir el uso de un endpoint o de un campo nuevo sin actualizar el inventario es
   detectable en revisión. Sí/No.
10. Una versión candidata puede activarse para una sola Organización sin afectar a las demás.
    Sí/No.
11. Con la versión candidata activa en la cuenta piloto, un sync completo y una creación pausada
    se ejecutan correctamente y su resultado queda registrado. Sí/No.
12. Desactivar el flag devuelve esa Organización a la versión vigente sin desplegar una imagen
    nueva. Sí/No.
13. Existe un procedimiento escrito de migración de versión, con sus pasos, sus criterios de
    éxito y su rollback. Sí/No.

## Decisiones de testing

- La coherencia de versión entre archivos se prueba en **CI**, como comprobación de
  construcción. Es el único lugar donde una divergencia se detecta antes de llegar a
  producción.
- El comportamiento del flag se prueba desde la **API**, con dos Organizaciones configuradas de
  forma distinta en la misma ejecución, verificando que cada llamada usa la versión que le
  corresponde.
- La lectura de la versión servida se prueba con un adaptador de Meta controlado que devuelve
  una cabecera distinta de la pedida, comprobando que la discrepancia se detecta.
- El seam de contratos externos ejecuta, con periodicidad y fuera del CI de cada commit, un
  sync real y una creación pausada real contra Meta v26 con la cuenta piloto, verificando
  campos presentes y versión atendida. Es la única prueba que puede detectar un campo retirado.
- Se reutilizan como prior art las pruebas existentes del constructor de creativos, de las
  tareas de creación y de compatibilidad de parámetros, que ya codifican comportamientos
  concretos de la versión en uso: esas pruebas son, de hecho, parte del inventario y deben
  citarse en él.
- No se prueba el contenido del inventario con código; se verifica en revisión y mediante la
  prueba de contrato externo, que falla cuando el inventario está desactualizado respecto de la
  realidad.

## Dependencias

Ninguna. Es independiente del resto de specs P1 y puede adelantarse: la corrección de la
versión predeterminada es de bajo riesgo y alto valor operativo.

## Fuera de alcance

- Migrar a una versión posterior a v26. Esta spec construye el procedimiento; ejecutarlo para
  una versión concreta es trabajo posterior.
- Ampliar el conjunto de endpoints o permisos de Meta que el producto usa.
- El flujo OAuth completo de emisión y renovación de tokens de Meta.
- El catálogo de modelos y proveedores de IA, que es un mecanismo análogo pero distinto:
  pertenece a `10`.
- La infraestructura de feature flags por Organización como capacidad general: pertenece a
  `13`. Esta spec la consume para un caso concreto y debe declararla como dependencia cuando
  aquella exista.

## Riesgos y rollback

- **Daño máximo si no se hace:** un despliegue rutinario devuelve el sistema a v25, y una
  versión que Meta ya retiró hace que todas las llamadas fallen. El síntoma es una pérdida
  total de sincronización para todos los clientes a la vez.
- **Daño máximo al hacerlo:** cambiar el valor predeterminado a v26 en un entorno que dependía
  del anterior por alguna razón no documentada rompe llamadas que hoy funcionan.
- **Activación:** la alineación de la versión predeterminada se despliega primero, porque
  producción ya usa v26 y el cambio solo elimina la divergencia. La lectura de la versión
  servida se activa en modo de registro antes de convertirse en alerta. El flag de versión
  candidata no se usa hasta que exista una versión posterior que probar.
- **Rollback:** la versión es una variable de entorno; volver atrás no requiere desplegar. La
  comprobación de coherencia en CI es la que impide que ese rollback se quede permanente sin
  que nadie lo note.

## Checklist de finalización

- [ ] Versión predeterminada alineada en código, ejemplo de entorno, plantilla de despliegue y
      documentación.
- [ ] Comprobación de coherencia de versión integrada en CI.
- [ ] Registro de la versión atendida por Meta y alerta ante discrepancia sostenida.
- [ ] Fecha de expiración de la versión en uso registrada y vigilada.
- [ ] Inventario de endpoints, campos, permisos y comportamientos, versionado en el repositorio
      y enlazado desde la documentación de servicios externos.
- [ ] Flag de versión candidata por Organización, con la versión anterior admitida durante la
      migración.
- [ ] Prueba de contrato externo periódica: sync completo y creación pausada contra la cuenta
      piloto.
- [ ] Procedimiento escrito de migración de versión, con criterios de éxito y rollback.
- [ ] Documentación de servicios externos, de arquitectura y de configuración actualizadas.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Meta API y proveedores* (párrafos de v26,
versión atendida, inventario, cuenta piloto, feature flag y rollback); *Problem Statement*
(párrafo de la discrepancia v25/v26); *Further Notes* (deuda operativa inmediata); *Testing
Decisions* (párrafo del seam de contratos externos); *Entrega por fases* → **P1**.
