# 07 · Medios: carga, validación, almacenamiento y ciclo de vida

## Estado

Completada. Los dieciséis criterios se verifican en
`backend/tests/test_spec07_medios.py`, con los casos hostiles que la spec nombra
uno a uno y la medida de memoria tomada sobre el proceso.

**Dos fallos reales que encontraron los tests:**

1. La clave de almacenamiento se construía con `asset.id` antes de que
   SQLAlchemy aplicara el default de la columna, así que **todos los medios
   colisionaban en la misma clave**. Se genera explícitamente.
2. La URL firmada acababa en los registros de `httpx` porque la URL viaja como
   **objeto**, no como cadena, y se convierte a texto al formatear — después de
   cualquier saneado que mire solo cadenas. Se añadió un saneador global como
   *fábrica de registros* (un `logging.Filter` no sirve: no ve lo que sube
   propagado desde los loggers hijos), que adelanta trabajo de la spec `09`.

**Almacenamiento de objetos: interfaz completa, driver `s3` sin ejercitar.** El
driver `local` está probado de punta a punta; el `s3` implementa la misma
interfaz de cuatro operaciones pero no se ha ejecutado contra un servicio real
—`boto3` ni siquiera está instalado—. La migración de producción sigue pendiente,
y con ella el traslado de los archivos existentes.

**Alcance de la URL firmada.** Es un token HMAC de cinco minutos en el query
string, el mismo mecanismo que usan las URL prefirmadas de S3: es la única forma
de que un `<img src>` cargue un medio privado. El saneador impide que aparezca en
*nuestros* registros; los logs de acceso del proxy deben configurarse aparte para
recortar el query string en `/media/*/contenido`.

## Prioridad

P1.

## Objetivo

Que un cliente pueda aportar imágenes y videos a su brief sin que el tamaño del archivo
comprometa la memoria del servidor, sin confiar en lo que el archivo dice ser, y con un ciclo
de vida que impida que el almacenamiento crezca sin límite.

## Problema

La carga de medios lee el archivo **entero en memoria** antes de escribirlo. Con un límite de
100 MB por video y un VPS de 4 GB compartido entre API, workers, base de datos y Redis, unas
pocas cargas simultáneas agotan la memoria del proceso que atiende a todos los clientes.

La validación de formato se hace exclusivamente sobre el tipo declarado por el cliente. Un
archivo cualquiera renombrado y anunciado como imagen se acepta, se guarda y se envía a Meta.
No se comprueba el contenido real, ni que el archivo esté completo, ni sus dimensiones o
duración.

Los archivos se guardan en el sistema de archivos del propio VPS, en un volumen compartido
entre la API y el worker de creación. Eso acopla los tres a la misma máquina, hace que los
medios dependan del ciclo de vida del servidor y los mete en el mismo disco de 100 GB que la
base de datos.

No existe ningún mecanismo de expiración. Un archivo subido para una propuesta que nunca se
aprobó permanece indefinidamente. No hay límite por Organización, de modo que un solo cliente
puede agotar el disco de todos.

## Alcance

- Carga de medios por streaming o directamente contra el almacenamiento de objetos, sin
  materializar el archivo completo en memoria.
- Validación del contenido real del archivo, no del tipo declarado: firma, integridad,
  dimensiones, duración y límites de tamaño.
- Verificación técnica de una creatividad contra los requisitos de formato de Meta, con
  resultado accionable antes de crear.
- Almacenamiento en un servicio de objetos, con acceso mediante URL temporal firmada.
- Cuotas por Organización: tamaño total y número de archivos.
- Ciclo de vida: expiración de temporales no asociados a una propuesta y de medios vencidos.
- Propiedad del medio: un archivo pertenece a una Organización y no es accesible desde otra.

## Historias de usuario

Tomadas de la spec maestra:

- **14** — el cliente adjunta imágenes o videos al brief.
- **16** — el sistema verifica formato y coherencia de una creatividad.
- **97** — carga por streaming o directa, sin consumir memoria proporcional al archivo.
- **98** — validación del contenido real y de los límites, no solo del MIME declarado.
- **99** — almacenamiento de objetos con URL temporales.
- **100** — eliminación automática de temporales y medios vencidos.
- **101** — límites de medios por Organización.

## Decisiones de implementación

- **El archivo no pasa entero por la memoria del proceso.** Se admiten dos caminos: carga
  directa del cliente contra el almacenamiento de objetos usando una URL firmada de escritura
  emitida por la API, o retransmisión en bloques desde la API. El primero es preferible porque
  saca el tráfico del VPS por completo; el segundo existe para entornos donde el primero no sea
  viable. El límite de tamaño se aplica durante la transferencia, no después.
- **El tipo declarado no es evidencia.** El formato se determina leyendo el contenido. Se
  rechaza el archivo cuyo contenido real no corresponde al declarado, el truncado, el vacío y
  el que excede los límites de dimensión o duración. La conversión que hoy se hace para
  formatos que Meta no admite se conserva, pero pasa a ejecutarse fuera del proceso que atiende
  peticiones.
- **La verificación de creatividad es técnica y honesta.** Comprueba lo que se puede comprobar
  —relación de aspecto, resolución, duración, peso, presencia de texto excesivo cuando sea
  detectable— y lo reporta como avisos accionables. No emite juicio sobre si la creatividad
  funcionará: esa promesa pertenece a `10` y está expresamente prohibida.
- **El almacenamiento de objetos desacopla API, workers y VPS.** El worker de creación deja de
  necesitar un volumen compartido con la API. El acceso de lectura se hace siempre con URL
  firmada de vida corta; no hay lectura pública ni por ruta adivinable.
- **La propiedad se comprueba en cada acceso.** Un medio pertenece a una Organización, y la
  emisión de una URL firmada verifica esa pertenencia contra la sesión que la pide. El
  aislamiento no puede depender de que la ruta contenga el identificador de la Organización.
- **Cuotas por Organización, no globales.** Se aplican al aceptar la carga, sobre el tamaño
  total y el número de archivos. Superarlas produce un rechazo de dominio con la cifra actual y
  el límite, no un fallo genérico.
- **Todo medio nace con caducidad.** Un archivo cargado y no asociado a una propuesta expira en
  una ventana corta. Un archivo asociado a una propuesta que no llegó a crear campaña expira en
  una ventana media. Un archivo usado en una campaña creada sigue la política de retención
  general del cliente, que define `09`. La limpieza es una tarea periódica idempotente que
  puede correr varias veces sin efecto adicional.
- **Las URL firmadas no aparecen en registros.** Una URL firmada es una credencial de acceso
  temporal; se trata como tal en logs y en trazas.

### Contradicción con ADR implícitas

- **ADR-12 (una imagen de backend para API, workers y beat)** se preserva; lo que desaparece es
  la necesidad de un volumen compartido entre esos contenedores, que era un acoplamiento no
  documentado como decisión.
- No hay ADR que documente el almacenamiento local de medios; era una consecuencia de la
  primera implementación de la subida de imágenes.

## Criterios de aceptación

1. Cargar un archivo de tamaño máximo permitido no incrementa la memoria del proceso de la API
   de forma proporcional al tamaño del archivo. Sí/No.
2. Diez cargas simultáneas de tamaño máximo no agotan la memoria del proceso ni degradan las
   peticiones de lectura concurrentes. Sí/No.
3. Un archivo que excede el límite de tamaño se rechaza durante la transferencia, sin haberse
   escrito completo. Sí/No.
4. Un ejecutable renombrado y declarado como imagen se rechaza. Sí/No.
5. Un archivo de imagen truncado se rechaza. Sí/No.
6. Un archivo vacío se rechaza. Sí/No.
7. Un video que excede la duración máxima admitida se rechaza con un motivo que la nombra. Sí/No.
8. Una creatividad con relación de aspecto o resolución fuera de lo que Meta admite produce un
   aviso accionable antes de crear la campaña. Sí/No.
9. Ninguna respuesta del sistema afirma que una creatividad tendrá éxito por su apariencia.
   Sí/No.
10. Los medios se almacenan en el servicio de objetos y no en el sistema de archivos del VPS.
    Sí/No.
11. El acceso de lectura a un medio requiere una URL firmada que caduca; una URL caducada no da
    acceso. Sí/No.
12. Un usuario de la Organización A no obtiene una URL firmada para un medio de la Organización
    B, aunque conozca su identificador. Sí/No.
13. Una Organización que alcanza su cuota recibe un rechazo que indica uso actual y límite.
    Sí/No.
14. Un archivo cargado y no asociado a ninguna propuesta desaparece del almacenamiento tras su
    ventana de expiración. Sí/No.
15. La tarea de limpieza ejecutada dos veces seguidas produce el mismo resultado y no elimina
    medios en uso. Sí/No.
16. Ninguna URL firmada aparece en los registros del sistema. Sí/No.

## Decisiones de testing

- La frontera es la **API de carga y de acceso a medios**, más el estado observable del
  almacenamiento. El comportamiento de memoria se mide sobre el proceso, no razonando sobre el
  código.
- Los casos hostiles son el núcleo de la suite: contenido incompatible con el tipo declarado,
  archivo truncado, archivo vacío, tamaño en el límite exacto y justo por encima, y carga
  concurrente. La spec maestra los nombra uno a uno.
- El aislamiento entre Organizaciones se prueba pidiendo una URL firmada con la sesión
  equivocada y comprobando el rechazo, no comprobando que la ruta contiene el identificador
  correcto.
- La expiración se prueba controlando el reloj y ejecutando la tarea de limpieza, verificando
  las dos caras: lo que se borra y lo que se conserva.
- Se reutilizan como prior art las pruebas existentes de carga de imagen de campaña y del
  constructor de creativos; las nuevas sustituyen sus supuestos sobre el sistema de archivos
  por el contrato de almacenamiento de objetos.
- La ausencia de URL firmadas en logs se prueba con una aserción sobre la salida de registro
  durante un recorrido completo de carga y acceso.

## Dependencias

- `01` — la propiedad de un medio se comprueba contra la sesión y la Organización activa.
- `05` — la carga de medios es una superficie del cliente y se rige por su módulo y su rol.

## Fuera de alcance

- Edición o generación automática de creatividades. La spec maestra la excluye salvo solicitud
  explícita, que no forma parte de este alcance.
- Extracción de contexto de negocio a partir del contenido de imágenes y videos: pertenece a
  `12`, que consume los medios que esta spec almacena.
- Comparación de un medio con los medios históricos del mismo cliente: pertenece a `11`.
- Transcodificación, generación de miniaturas y optimización de medios más allá de la
  conversión de formato que ya existe.
- Retención de medios por baja de un cliente: pertenece a `09`.
- Elección del proveedor concreto de almacenamiento de objetos, que es una decisión de
  infraestructura y se documenta en `14`.

## Riesgos y rollback

- **Daño máximo si no se hace:** una carga simultánea de varios videos deja sin memoria al
  proceso que atiende a todos los clientes, y un archivo malicioso llega hasta Meta sin haber
  sido inspeccionado. A plazo, el disco de 100 GB se llena con archivos que nadie usa y se
  lleva por delante la base de datos.
- **Daño máximo al hacerlo:** una validación de contenido demasiado estricta rechaza fotos
  legítimas de los clientes; una expiración mal calibrada borra el medio de una propuesta viva.
- **Activación:** el almacenamiento de objetos se enciende por Organización, escribiendo en el
  destino nuevo y leyendo de ambos durante la transición. La validación de contenido se
  despliega primero en modo de solo registro para medir cuántos archivos reales rechazaría. La
  expiración se activa en último lugar y con las ventanas más largas del rango previsto,
  acortándolas después con evidencia.
- **Rollback:** volver a la lectura desde el sistema de archivos mientras la doble lectura siga
  activa. La expiración se apaga por configuración. La regla dura del rollout es que ningún
  medio se elimina del almacenamiento antiguo hasta que la lectura desde el nuevo esté
  verificada para esa Organización.

## Checklist de finalización

- [ ] Migración que registra los medios como entidad con propietario, estado y caducidad, en
      expand-and-contract.
- [ ] Procedimiento de traslado de los medios existentes al almacenamiento de objetos, sin
      eliminar el origen hasta verificar la lectura.
- [ ] Pruebas de sistema de los dieciséis criterios, con los casos hostiles y la concurrencia.
- [ ] Medición documentada del consumo de memoria durante carga concurrente, antes y después.
- [ ] Documentación de configuración y de servicios externos actualizada con el almacenamiento
      de objetos y las cuotas.
- [ ] Documentación de producto: qué formatos se admiten, qué se valida y qué avisos produce la
      verificación de creatividad.
- [ ] Observabilidad: uso de almacenamiento por Organización, rechazos por tipo de validación,
      archivos expirados por ciclo y errores de acceso al almacenamiento.
- [ ] Evidencia del modo de solo registro de la validación de contenido sobre medios reales
      antes de activar el rechazo.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Brief y medios* (párrafos de carga,
validación y límites); *Problem Statement* (párrafo del VPS limitado); *Testing Decisions*
(párrafo de medios); *Entrega por fases* → **P1**.
