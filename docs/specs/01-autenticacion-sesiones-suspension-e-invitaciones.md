# 01 · Autenticación, sesiones, suspensión, invitaciones y fallo cerrado

## Estado

Completada. Los quince criterios de aceptación se verifican en
`backend/tests/test_spec01_seguridad_sesiones.py`; las dos carreras (criterios 7
y 10) se verifican además contra PostgreSQL real en
`backend/tests/test_spec01_concurrencia_postgres.py`. Desde la spec `14` esas dos
corren en cada construcción, junto con el resto de la suite, contra PostgreSQL y
Redis reales.

## Prioridad

P0.

## Objetivo

Que la identidad, la pertenencia a una Organización y la vigencia de una sesión sean
verificables en toda superficie de entrada —API HTTP y WebSocket— y que ningún control de
seguridad pueda ser evitado por concurrencia, por reutilización de una credencial de otro
tipo o por la caída de una dependencia de soporte.

## Problema

Existen cuatro huecos observables desde fuera del sistema:

1. **Un refresh token funciona como access token.** La validación de un Bearer decodifica la
   firma y lee el sujeto, pero no comprueba el `type` del claim ni consulta la lista de
   revocación. Un refresh robado —que dura siete días frente a las 24 horas del access—
   abre la API completa. La misma laxitud aplica al handshake del WebSocket.
2. **El token del WebSocket viaja en el query string.** Queda en logs de acceso del proxy,
   en historiales y en cualquier trazado intermedio.
3. **La membresía Organización–usuario no tiene restricción única.** Dos aceptaciones
   concurrentes de la misma invitación producen dos filas; la resolución de rol lee una sola
   y el resultado depende del orden de inserción. La invitación tampoco se consume de forma
   atómica: se comprueba `accepted_at` y luego se escribe, con una ventana entre ambas.
4. **Suspender una Organización no suspende nada.** El estado se puede fijar y solo la
   ejecución de AIAction lo consulta; login, API de lectura, sync, IA y write-back siguen
   operando.

A esto se suma una decisión arquitectónica que la spec maestra corrige de forma explícita: la
capa Redis degrada fail-open de manera general. Para revocación y para autorización de gasto
eso significa que una caída del soporte desactiva el control en silencio.

## Alcance

- Tipado estricto de credenciales: un token de refresco no autentica ninguna superficie que
  no sea la renovación de sesión.
- Consulta de revocación en la autenticación de acceso, no solo en la renovación.
- Revocación total de sesiones de un usuario ante cambio o restablecimiento de contraseña.
- Transporte del token de WebSocket fuera del query string.
- Consumo atómico y de un solo uso de una invitación, con almacenamiento no reversible del
  secreto.
- Restricción única de membresía por (usuario, Organización), aplicada en la base.
- Suspensión efectiva de una Organización como compuerta de backend sobre login, API, sync,
  IA y write-back, conservando el acceso administrativo de plataforma.
- Revocación de credenciales externas de la Organización al suspenderla.
- Política de **fallo cerrado**: cuando no puede verificarse revocación o autorización, se
  rechazan renovación de sesión, aprobaciones y escrituras hacia Meta. El resto de usos de la
  capa de soporte (throttle, cache, debounce) conserva su degradación fail-open actual.

## Historias de usuario

Tomadas de la spec maestra:

- **53** — suspender una Organización de forma efectiva (login, API, sync, IA, write-back).
- **54** — el administrador de plataforma conserva acceso a una Organización suspendida.
- **55** — revocar credenciales al suspender una Organización.
- **63** — un refresh token no puede utilizarse como access token.
- **64** — cambiar o restablecer la contraseña revoca todas las sesiones.
- **65** — la aceptación de invitaciones es atómica y de un solo uso.
- **66** — restricción única para membresías.
- **67** — el token de WebSocket no viaja en una URL registrable.
- **68** — una caída del mecanismo de revocación bloquea renovaciones y escrituras sensibles.
- **108** — una caída de Redis bloquea write-back y renovaciones sensibles.

## Decisiones de implementación

- **Tipo de credencial como parte del contrato.** Todo token lleva un tipo declarado y cada
  superficie declara qué tipo acepta. La verificación de tipo ocurre antes de resolver el
  sujeto, y un tipo inesperado es indistinguible de una firma inválida para el llamador.
- **La revocación se consulta en autenticación, no solo en renovación.** El identificador
  único del token es la unidad de revocación. Revocar a nivel de usuario se expresa como una
  marca de corte temporal por usuario: toda credencial emitida antes de ese instante queda
  inválida sin necesidad de enumerar identificadores.
- **Fallo cerrado acotado y nombrado.** Se distingue "no revocado" de "no se pudo comprobar".
  El segundo caso rechaza: renovación de sesión, aprobación de una propuesta y cualquier
  write-back. Las funciones de soporte sin consecuencia de seguridad siguen degradando
  abiertas. Esta distinción es un contrato explícito, no un efecto colateral del manejo de
  errores.
- **Token de WebSocket fuera de la URL.** El handshake obtiene la credencial por un canal no
  registrable en logs de acceso. Se admite un ticket de un solo uso y vida corta, emitido por
  la API autenticada y canjeado en la conexión.
- **Invitación con secreto no reversible y consumo atómico.** Se persiste una derivación
  irreversible del secreto, no el secreto. La aceptación es una única operación condicional
  sobre el estado de la invitación: si no cambió exactamente una fila, la invitación ya se
  consumió. El resultado ante reintento es idéntico y no crea membresías adicionales.
- **La unicidad de membresía vive en la base.** Una restricción única por (usuario,
  Organización) convierte la carrera en un error de integridad manejable, en vez de en dos
  filas válidas. La lógica de aplicación deja de ser la única defensa.
- **La suspensión es una compuerta de backend, evaluada en la resolución de la Organización
  activa.** Se aplica una vez, donde ya se resuelve el tenant, para que ninguna superficie
  nueva pueda olvidarla. El administrador de plataforma conserva las operaciones de soporte
  que ya tiene —crear, configurar, exportar, reactivar— sin obtener acceso a datos de
  campañas del tenant.
- **Semántica de error preservada.** Un fallo de autenticación o de aislamiento responde con
  semántica de autorización, no como un estado de dominio dentro de una conversación.

### Contradicción con ADR implícitas

- **ADR-13 (fallbacks fail-open en toda la capa Redis)** queda parcialmente derogada. La spec
  maestra lo declara de forma expresa: la disponibilidad no puede tener prioridad sobre
  revocación ni sobre autorización financiera. Esta spec acota la excepción a revocación,
  aprobación y write-back; el resto de ADR-13 sigue vigente.
- **ADR-11 (multi-tenant por subdominio + aislamiento por cuenta)** se preserva y se refuerza:
  la suspensión se evalúa en el mismo punto donde se resuelve el tenant activo.

## Criterios de aceptación

1. Un refresh token presentado como Bearer contra cualquier endpoint de la API es rechazado
   con semántica de autorización. Sí/No.
2. Un refresh token presentado en el handshake del WebSocket es rechazado. Sí/No.
3. Un access token cuyo identificador fue revocado es rechazado en la siguiente petición a la
   API, no solo en la renovación. Sí/No.
4. Tras cambiar la contraseña, todo access y refresh emitido antes del cambio es rechazado en
   API y WebSocket. Sí/No.
5. Tras restablecer la contraseña por el flujo de recuperación, se cumple el criterio 4. Sí/No.
6. Ninguna conexión de WebSocket se establece con la credencial en el query string; el
   handshake sigue funcionando con el mecanismo nuevo. Sí/No.
7. Dos aceptaciones concurrentes de la misma invitación producen exactamente una membresía;
   la segunda recibe un error de dominio y no crea filas. Sí/No.
8. Una invitación ya aceptada no puede reutilizarse aunque no haya expirado. Sí/No.
9. El secreto de la invitación no es recuperable desde la base. Sí/No.
10. Dos altas concurrentes del mismo usuario en la misma Organización dejan una sola fila de
    membresía; la base rechaza la segunda. Sí/No.
11. En una Organización suspendida: el login de sus miembros falla, toda petición autenticada
    a su API falla, no se encolan ni ejecutan sync, análisis de IA ni write-back. Sí/No.
12. En una Organización suspendida, el administrador de plataforma conserva las operaciones
    de soporte —consultar configuración, exportar, reactivar— sin acceso a datos de campañas.
    Sí/No.
13. Al suspender una Organización, sus credenciales de Meta quedan revocadas y su estado lo
    refleja. Sí/No.
14. Con la dependencia de revocación caída: la renovación de sesión falla, la aprobación de
    una propuesta falla y todo write-back falla, cada uno con un motivo que nombra la causa.
    Sí/No.
15. Con la dependencia de revocación caída: la lectura del histórico ya sincronizado sigue
    respondiendo. Sí/No.

## Decisiones de testing

- La frontera es la **API HTTP y el WebSocket**, no las funciones de firma ni la capa de
  repositorio. Cada criterio se verifica emitiendo una credencial real y observando el código
  y el cuerpo de la respuesta.
- Se reutilizan como prior art las pruebas HTTP del backend y las pruebas de RBAC y de
  aislamiento por tenant ya existentes; las nuevas elevan esa cobertura a la frontera de
  sistema en vez de duplicarla.
- Las carreras de invitación y de membresía se prueban con peticiones concurrentes contra
  **PostgreSQL real**, no contra el motor de pruebas embebido: la restricción única y el
  `UPDATE` condicional solo existen en el dialecto de producción.
- La suspensión se prueba como comportamiento observable en las cinco superficies nombradas
  (login, API, sync, IA, write-back), incluido el caso del administrador de plataforma.
- El fallo cerrado se prueba deteniendo la dependencia de revocación y comprobando el par
  completo: lo que debe bloquearse y lo que debe seguir funcionando. Un test que solo
  compruebe el bloqueo no distingue fail-closed de caída total.
- No se prueba la ausencia de la credencial en logs leyendo archivos de log: se prueba que la
  superficie no acepta la credencial por el canal registrable.

## Dependencias

Ninguna. Es la primera spec de la fase P0 y la base de las compuertas de `02`, `03` y `13`.

## Fuera de alcance

- Impersonación de un tenant por soporte técnico. La spec maestra la nombra como mecanismo
  aparte, explícito y con aviso al cliente; no se construye aquí.
- Autenticación federada, SSO, segundo factor y políticas de contraseña.
- Rotación automática de credenciales de Meta y el flujo OAuth completo de emisión de tokens.
- Retirada del registro público y flujo de onboarding asistido: pertenecen a `05`.
- Eliminación de credenciales por baja definitiva de la Organización: pertenece a `09`. Aquí
  solo se cubre la revocación al **suspender**.
- Sanitización de logs: pertenece a `09`.
- Interruptores operativos, modo solo lectura y feature flags: pertenecen a `13`.

## Riesgos y rollback

- **Daño máximo si no se hace:** una credencial de larga duración robada da acceso completo a
  los datos de un cliente, y una Organización dada de baja comercialmente sigue sincronizando
  y pudiendo escribir en Meta.
- **Daño máximo al hacerlo:** un tipado o una comprobación de revocación demasiado estricta
  expulsa a todos los usuarios con sesión abierta; una compuerta de suspensión mal evaluada
  deja fuera a una Organización activa.
- **Activación:** el endurecimiento de tipos y la revocación se activan de una vez —no admiten
  rollout parcial sin dejar un camino débil abierto—, pero se despliegan en una ventana
  acordada asumiendo que toda sesión viva se invalida. El nuevo transporte del token de
  WebSocket se despliega aceptando ambos mecanismos durante una versión y retirando el query
  string en la siguiente, para no romper clientes ya cargados en el navegador.
- **Rollback:** volver a la imagen anterior. La restricción única de membresía es la única
  migración que no se revierte sola: se aplica tras deduplicar, y su reversión es soltar el
  índice. El fallo cerrado se puede desactivar por configuración durante un incidente, con
  registro explícito de que el control quedó abierto.

## Checklist de finalización

- [ ] Migración que deduplica membresías existentes y añade la restricción única, aplicable en
      caliente sobre datos de producción.
- [ ] Migración que sustituye el secreto en claro de las invitaciones por su derivación y
      añade el campo de corte de sesiones por usuario, en modo expand-and-contract.
- [ ] Pruebas de sistema de los quince criterios de aceptación, con las de concurrencia
      ejecutándose contra PostgreSQL real en CI.
- [ ] Documentación de autenticación actualizada: tipos de token por superficie, transporte
      del WebSocket, semántica de revocación y de fallo cerrado.
- [ ] Documento de decisiones arquitectónicas actualizado: ADR-13 acotada con la excepción de
      seguridad y write-back.
- [ ] Runbook de suspensión y reactivación de una Organización, incluido qué conserva el
      administrador de plataforma.
- [ ] Observabilidad: contador de rechazos por tipo de credencial, por revocación y por fallo
      cerrado, separados entre sí para poder distinguir un ataque de una caída.
- [ ] Verificación manual en producción con una Organización de prueba: suspender, comprobar
      las cinco superficies, reactivar.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Seguridad e identidad*; *Producto, roles
y módulos* (párrafo de suspensión); *Operación en el VPS limitado* (párrafo de caída de Redis);
*Further Notes* (corrección explícita del fail-open de ADR-13); *Entrega por fases* → **P0**.
