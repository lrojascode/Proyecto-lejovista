# 09 · Baja de clientes, retención, privacidad y auditoría

## Estado

Completada. Los quince criterios se verifican en
`backend/tests/test_spec09_baja_retencion.py`, siempre con una Organización
**testigo**: con una sola, la prueba no distingue una eliminación dirigida de un
borrado excesivo, y el borrado excesivo es el fallo caro.

**Lo que más importaba resultó ser el archivo frío.** Las tablas `*_archive` se
diseñaron sin claves foráneas para sobrevivir al borrado de un tenant, así que el
`CASCADE` de `organizations` no las alcanza: sin eliminarlas explícitamente, el
histórico completo de un cliente dado de baja permanecía. La eliminación las
recorre por `organization_id`.

**El saneado de registros ya estaba**, implementado en la spec `07` como fábrica
de `LogRecord`. Aquí se completó el patrón de teléfonos: cubría siete dígitos y
un móvil peruano tiene nueve, así que dejaba pasar el número entero.

**Dos cosas que el certificado dice y no debería callar:**

- La fila de `organizations` **sobrevive vacía**. Un `DELETE` ahí cascadea a
  media base y deja la baja sin rastro auditable, que es lo contrario de lo que
  un certificado de eliminación necesita.
- Los **respaldos previos a la baja no se reescriben**. Desaparecen por rotación,
  y el certificado lo dice. Prometer lo contrario sería falso.

## Prioridad

P1.

## Objetivo

Que terminar la relación con un cliente sea un procedimiento con garantías comprobables —sus
credenciales revocadas de inmediato, sus datos exportables, su histórico eliminado en el plazo
comunicado— y que quede una traza de auditoría suficiente para reconstruir qué hizo el sistema
sin conservar datos sensibles.

## Problema

La eliminación declarada no coincide con la persistencia real. Existe un script de baja que
marca la Organización como inactiva y retira las membresías, y que se niega deliberadamente a
actuar si la Organización tiene datos: es una herramienta de limpieza de Organizaciones
residuales, no un procedimiento de baja de un cliente con histórico. No hay revocación de
credenciales externas, no hay exportación, no hay eliminación de medios y no hay plazo. Las
tablas de archivo, además, se diseñaron sin claves foráneas precisamente para sobrevivir al
borrado de un tenant: el histórico de un cliente dado de baja permanece.

Los registros del sistema no están saneados. No hay una política declarada de qué no puede
aparecer en un log, y la traza de llamadas externas guarda parámetros y una vista previa de la
respuesta sin filtrar.

La auditoría es parcial. Se registran algunas piezas —consumo de IA, llamadas a Meta,
propuestas de creación— pero no de forma que permita reconstruir, para una campaña concreta,
quién la solicitó, con qué medios, sobre qué evidencia, qué versión se aprobó, quién la aprobó
y qué respondió Meta.

## Alcance

- Procedimiento de baja de una Organización con histórico, distinto del de limpieza de una
  Organización residual.
- Revocación y eliminación inmediata de credenciales y accesos externos al iniciar la baja.
- Exportación de los datos comerciales del cliente en formato utilizable fuera del producto.
- Eliminación de datos de campañas, medios e histórico dentro de 30 días, incluidas las tablas
  de archivo frío.
- Retención de registros de seguridad saneados hasta 90 días.
- Base contractual o legal visible para cualquier retención superior.
- Política de saneamiento de registros: qué nunca aparece y qué sí se conserva.
- Traza de auditoría del ciclo completo de una solicitud de campaña.

## Historias de usuario

Tomadas de la spec maestra:

- **24** — el cliente exporta sus datos al finalizar la relación.
- **25** — el cliente recibe confirmación de que sus credenciales y datos fueron eliminados.
- **69** — tokens, teléfonos completos, credenciales y URL firmadas se eliminan de los logs.
- **70** — eliminación inmediata de credenciales al dar de baja una Organización.
- **71** — eliminación de histórico y medios dentro de 30 días.
- **72** — conservación de logs de seguridad saneados hasta 90 días.
- **73** — cualquier retención superior tiene base contractual o legal visible.

## Decisiones de implementación

- **La baja es una máquina de estados con plazos, no una operación.** Se inicia, revoca de
  inmediato, permite exportar durante una ventana y elimina al vencer el plazo. Cada transición
  queda registrada. Una baja no es reversible después de la eliminación, y eso se dice antes de
  iniciarla.
- **Lo inmediato y lo diferido se separan.** Es inmediato lo que corta accesos: revocación de
  credenciales de Meta, invalidación de sesiones, eliminación de la clave de IA, bloqueo de
  login y de API. Es diferido lo que destruye información: histórico, medios y datos de
  campañas, dentro de los 30 días comunicados. Confundirlas obligaría a elegir entre cortar
  tarde o borrar sin dar tiempo a exportar.
- **La exportación precede a la eliminación y es del cliente.** Contiene sus datos comerciales
  —campañas, métricas, histórico, propuestas y resultados— en un formato legible fuera del
  producto. No contiene secretos, ni identificadores internos que solo tienen sentido dentro
  del sistema, ni datos de otras Organizaciones.
- **La eliminación alcanza el archivo frío.** Las tablas de archivo no tienen claves foráneas y
  por diseño sobreviven al borrado del tenant; la baja debe eliminarlas explícitamente por
  identificador de Organización. Omitirlo convierte la promesa de eliminación en falsa.
- **Los respaldos se tratan aparte y se dicen.** Un respaldo tomado antes de la baja contiene
  los datos del cliente y no se reescribe. La política declara que los datos desaparecen de los
  respaldos por rotación, dentro de la ventana de retención del sistema de respaldo, y esa
  ventana se comunica. Prometer una eliminación que los respaldos contradicen es peor que
  explicarlo.
- **La confirmación de eliminación es un documento verificable.** Enumera qué se eliminó, en qué
  fecha, qué se conserva, por cuánto tiempo y con qué base. Es lo que el cliente recibe.
- **El saneamiento de registros se aplica en la escritura, no en la lectura.** Un filtro
  centralizado en la capa de registro elimina secretos, tokens, números de teléfono completos,
  URL firmadas, medios y cuerpos de petición sensibles. Se conservan identificadores internos,
  códigos de error, tiempos y metadatos mínimos: sin ellos no se puede investigar un incidente.
  Confiar en que cada punto de llamada recuerde no registrar algo garantiza que alguna vez se
  olvidará.
- **La auditoría se organiza por solicitud, no por tabla.** El registro permite reconstruir el
  recorrido completo de una solicitud de campaña: actor, rol, Organización, AdAccount,
  solicitud, medios, evidencia, propuesta, versiones, cambios humanos, aprobaciones,
  validaciones y resultado externo saneado. Las piezas ya existen dispersas; lo que falta es la
  correlación.
- **Los registros de auditoría no son registros de aplicación.** Tienen su propia retención de
  90 días y sobreviven a la baja del cliente en su forma saneada, porque son la única forma de
  investigar un incidente posterior.

### Contradicción con ADR implícitas

- **ADR-01 (snapshots y archivado sin claves foráneas)** tiene una consecuencia no prevista: el
  archivo sobrevive al borrado del tenant. Esta spec no revierte esa decisión —el archivo debe
  seguir sobreviviendo a borrados accidentales de una Organización— pero añade una eliminación
  explícita y dirigida como parte del procedimiento de baja.

## Criterios de aceptación

1. Iniciar la baja de una Organización revoca sus credenciales de Meta y elimina su clave de IA
   de inmediato. Sí/No.
2. Iniciar la baja invalida todas las sesiones de sus miembros y bloquea login y API. Sí/No.
3. Iniciar la baja detiene sync, análisis de IA y write-back para esa Organización. Sí/No.
4. Durante la ventana de exportación, el cliente o la agencia en su nombre puede descargar sus
   datos comerciales en un formato legible fuera del producto. Sí/No.
5. La exportación no contiene secretos, claves ni datos de otras Organizaciones. Sí/No.
6. Al vencer el plazo de 30 días, no queda ningún dato de campañas, métricas ni medios de esa
   Organización en las tablas calientes. Sí/No.
7. Al vencer el plazo de 30 días, tampoco queda ninguno en las tablas de archivo frío. Sí/No.
8. Al vencer el plazo, los medios de esa Organización han sido eliminados del almacenamiento.
   Sí/No.
9. El cliente recibe una confirmación que enumera qué se eliminó, qué se conserva, por cuánto
   tiempo y con qué base. Sí/No.
10. Los registros de seguridad saneados de esa Organización siguen existiendo tras la
    eliminación, y desaparecen a los 90 días. Sí/No.
11. Ningún registro del sistema contiene tokens, claves, secretos, números de teléfono
    completos ni URL firmadas, en un recorrido completo de uso del producto. Sí/No.
12. Los registros conservan identificadores internos, códigos de error y tiempos suficientes
    para investigar un incidente. Sí/No.
13. Cualquier dato retenido más allá de los plazos declarados tiene una base registrada y
    consultable. Sí/No.
14. Para una campaña creada por el sistema, la auditoría permite reconstruir quién la solicitó,
    con qué medios, sobre qué evidencia, qué versión se aprobó, quién la aprobó y qué respondió
    Meta. Sí/No.
15. La baja de la Organización A no elimina ni altera ningún dato de la Organización B. Sí/No.

## Decisiones de testing

- La frontera es el **comportamiento observable de la baja**: se da de baja una Organización
  con datos reales de prueba y se comprueba, desde la API y desde la base, cada garantía en su
  momento del calendario.
- El paso del tiempo se simula controlando el reloj; el plazo de 30 días y el de 90 no se
  esperan.
- La eliminación se prueba con **dos Organizaciones pobladas**: la que se da de baja y una
  testigo que debe quedar intacta. Una prueba con una sola Organización no distingue una
  eliminación dirigida de un borrado excesivo.
- La cobertura del archivo frío se prueba archivando datos antes de la baja, de modo que la
  eliminación tenga que alcanzarlos.
- El saneamiento de registros se prueba capturando la salida de registro durante un recorrido
  completo del producto y afirmando la ausencia de los patrones prohibidos; no se revisan
  puntos de llamada uno a uno.
- La auditoría se prueba por reconstrucción: dado el identificador de una campaña creada, se
  obtiene el recorrido completo. Es una consulta, no una inspección de tablas sueltas.
- Se reutiliza como prior art el script de baja existente y sus pruebas, ampliando su alcance
  del caso residual al caso con histórico.

## Dependencias

- `01` — la revocación de credenciales y la invalidación de sesiones son mecanismos que aquella
  spec define; esta los invoca desde el procedimiento de baja.
- `06` — la eliminación debe alcanzar los campos que aquella spec añade al archivo frío.
- `07` — la eliminación de medios requiere que los medios sean una entidad con propietario y
  ciclo de vida.

## Fuera de alcance

- Eliminación de datos dentro de los respaldos ya tomados. La política declara que desaparecen
  por rotación y comunica la ventana; no se reescriben respaldos.
- Portabilidad hacia otro producto o formato de intercambio con terceros.
- Cumplimiento formal de un régimen normativo concreto y su certificación.
- Anonimización o seudonimización de datos para conservarlos con fines analíticos tras la baja.
- Impersonación de un tenant por soporte técnico.
- Revocación de credenciales al **suspender** una Organización: pertenece a `01`. Esta spec
  cubre la baja definitiva.
- El modelo comercial —pago por implementación, 60 días de garantía, responsabilidad económica
  de infraestructura e IA, ausencia de SLA indefinido— no genera comportamiento de software.
  Se documenta en el contrato y en la documentación comercial, no en una spec técnica.

## Riesgos y rollback

- **Daño máximo si no se hace:** el producto afirma eliminar datos que conserva, y las
  credenciales de un cliente que terminó la relación siguen activas contra su cuenta de Meta.
  Es un incumplimiento de la promesa hecha al cliente, no solo una deuda técnica.
- **Daño máximo al hacerlo:** una eliminación mal dirigida borra los datos de la Organización
  equivocada de forma irreversible. El precedente de una clave guardada en la Organización
  equivocada demuestra que la resolución de tenant ha fallado antes.
- **Activación:** la baja se ejecuta en dos manos: se inicia con confirmación explícita del
  identificador y del nombre de la Organización, y la eliminación diferida se ejecuta como
  tarea que primero informa qué borraría y solo actúa tras una confirmación registrada. Se
  toma un respaldo externo verificado inmediatamente antes de la eliminación.
- **Rollback:** la fase inmediata es reversible salvo por la credencial de Meta, que hay que
  volver a emitir. La fase de eliminación no lo es: el único rollback real es la restauración
  desde el respaldo previo, que por eso es un requisito del procedimiento y no una
  recomendación.

## Checklist de finalización

- [ ] Migración que registra el estado y las fechas de la baja de una Organización.
- [ ] Procedimiento de baja implementado con sus dos fases, con confirmación explícita en cada
      una y respaldo previo obligatorio.
- [ ] Exportación de datos comerciales del cliente en formato legible fuera del producto.
- [ ] Eliminación dirigida que alcanza tablas calientes, tablas de archivo y medios.
- [ ] Filtro central de saneamiento en la capa de registro.
- [ ] Correlación de auditoría por solicitud de campaña, consultable desde la experiencia de
      agencia.
- [ ] Pruebas de sistema de los quince criterios, con Organización testigo y reloj controlado.
- [ ] Documentación de seguridad y de privacidad actualizada: qué se elimina, cuándo, qué se
      conserva, con qué base y qué ocurre con los respaldos.
- [ ] Runbook de baja de cliente, sustituyendo el uso del script de limpieza de Organizaciones
      residuales para este caso.
- [ ] Observabilidad: bajas en curso, plazos vencidos pendientes de ejecución y resultado de
      cada eliminación.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Retención, privacidad y auditoría*;
*Problem Statement* (párrafo de eliminación declarada que no coincide con la persistencia);
*Testing Decisions* (párrafo de retención como comportamiento de baja); *Entrega por fases* →
**P1**. Las historias 119–121 (modelo comercial) se declaran expresamente sin spec técnica.
