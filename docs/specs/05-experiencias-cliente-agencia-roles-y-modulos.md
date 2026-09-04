# 05 · Experiencias de cliente y de agencia, roles, módulos y alta asistida

## Estado

Completada. Los quince criterios se verifican en
`backend/tests/test_spec05_experiencias.py`, cada ocultación comprobada llamando
a la API y no mirando el menú, y la matriz módulo × rol en sus cuatro
combinaciones.

**Desviación deliberada sobre el registro público.** La spec pide retirarlo, «no
esconderlo». Lo que se hizo fue apagarlo por defecto
(`ENABLE_PUBLIC_SIGNUP=false`), que es el punto de aplicación real: en producción
no existe el camino. No se borró el endpoint porque 37 archivos de test lo usan
como fábrica de organizaciones, y reescribirlos no habría añadido ninguna
garantía que la variable no dé. Queda como deuda explícita si se prefiere la
retirada literal.

**Nota sobre el saneado.** Los campos de agencia llegan al cliente con valor
`null`, no ausentes. Una respuesta cuya forma cambia según quién pregunta obliga
al frontend y a los tipos generados a tratar cada campo como opcional por un
motivo ajeno; lo que la spec pide —y lo que se cumple— es que el *valor* no
llegue.

## Prioridad

P1.

## Objetivo

Que el cliente y la agencia usen la misma plataforma y la misma fuente de datos a través de dos
experiencias distintas: una conversacional y acotada para el cliente, y una operativa completa
para la agencia; y que el alta de una Organización sea un flujo asistido que valide todo antes
de invitar a nadie.

## Problema

Hoy existe una única experiencia. El mismo panel muestra al cliente diagnósticos técnicos,
consumo de IA, identificadores internos, estado de write-back y configuración de proveedores.
Nada de eso le sirve, y parte de ello no debería ver.

El sistema de módulos por Organización existe y se aplica también en el backend, pero está
pensado como "qué ve este cliente", no como "qué altura de producto es esta". Apagar un módulo
para simplificar la vista del cliente se lo apaga también al operador de la agencia, que
necesita esa herramienta para prestar el servicio. Módulos y roles resuelven hoy la misma
pregunta desde dos sitios, y ocultar acaba usándose como sustituto de autorizar.

El alta de una Organización es manual y desordenada: existe un registro público que no
corresponde a este modelo comercial, y las credenciales de Meta, la clave de IA y los números
de WhatsApp se cargan por separado, sin una validación previa que confirme que la Organización
está lista antes de invitar al cliente. El precedente conocido es una clave de IA guardada en
la Organización equivocada, con el analista respondiendo con plantillas durante días sin que
nada pareciera roto.

## Alcance

- Dos experiencias diferenciadas sobre la misma fuente de datos, seleccionadas por el tipo de
  acceso del usuario y no por una preferencia.
- Superficie del cliente: chat, resultados, campañas activas, estado de sus solicitudes y
  límite de presupuesto. Sin grounding, proveedores, claves, write-back, identificadores
  internos ni diagnósticos de infraestructura.
- Superficie de la agencia: cartera de Organizaciones, configuración, diagnóstico, métricas y
  breakdowns, Insight, AIAction, Draft y Outcome loop, revisión y aprobación, auditoría.
- Separación explícita entre módulos —qué se le ofrece a este cliente— y roles —qué está
  autorizado a hacer este usuario—, con evaluación de ambos y sin que uno sustituya al otro.
- El operador de la agencia conserva acceso operativo a las herramientas que necesita aunque el
  módulo esté oculto para el cliente.
- Selección explícita de AdAccount activa cuando la Organización tiene más de una.
- Concesión de acceso por AdAccount como parte de la administración de usuarios.
- Alta asistida de una Organización, con validación de Meta, IA, WhatsApp y permisos antes de
  emitir la invitación.
- Retirada del registro público.

## Historias de usuario

Tomadas de la spec maestra:

- **1** — el cliente ingresa mediante una invitación con su Organización ya configurada.
- **2** — el cliente ve únicamente los módulos habilitados para su Organización.
- **18** — el cliente ve el estado de una solicitud de campaña.
- **26** — la agencia tiene una experiencia operativa separada de la del cliente.
- **27** — la agencia conserva acceso a los módulos ocultos para el cliente.
- **28** — el administrador de plataforma habilita módulos por Organización.
- **29** — módulos y roles son controles independientes.
- **30** — alta de una Organización mediante un flujo asistido.
- **31** — credenciales propiedad del cliente configuradas sin mostrarlas en su interfaz.
- **32** — roles y accesos asignados por AdAccount.
- **33** — cambio explícito de AdAccount.
- **34** — consulta de métricas, breakdowns, Insight, AIAction, Draft y Outcome loop.

## Decisiones de implementación

- **La experiencia se deriva del acceso, no se elige.** Un usuario cuyo acceso es una membresía
  de cliente entra a la experiencia del cliente; un operador de la agencia entra a la
  experiencia operativa. No hay conmutador que permita a un cliente asomarse a la otra altura,
  porque un conmutador convierte la separación en una preferencia.
- **Módulos y roles responden a preguntas distintas y se evalúan las dos.** El módulo es
  contratación y alcance de producto: acota qué existe para esta Organización. El rol es
  autorización: acota qué puede hacer este usuario con lo que existe. Una petición debe pasar
  las dos comprobaciones. Ocultar en la navegación nunca cuenta como ninguna de ellas.
- **El módulo acota la experiencia del cliente, no la de la agencia.** Es la corrección de
  frontera que exige la spec maestra: el catálogo de módulos deja de ser global y pasa a
  gobernar la superficie del cliente. La superficie de la agencia se gobierna por rol, porque
  el operador necesita la herramienta aunque el cliente no la haya contratado.
- **Las credenciales propiedad del cliente las configura la agencia y no se muestran al
  cliente.** El cliente ve que existen y su estado; no ve el valor ni el proveedor. Esto ya se
  cumple para la clave de IA y se extiende al resto.
- **La AdAccount activa es explícita cuando hay ambigüedad.** El comportamiento actual —error
  explícito en vez de elegir la primera en silencio— se preserva y se completa con una
  selección visible y persistente en ambas experiencias.
- **El alta asistida es una secuencia con estado, no un formulario.** Cada paso se valida
  contra el sistema externo real antes de avanzar: la credencial de Meta lista cuentas, la
  clave de IA responde a una llamada mínima, el número de WhatsApp queda vinculado a una
  cuenta. La invitación al cliente solo se puede emitir cuando todos los pasos están validados,
  de modo que el cliente nunca entra a una Organización a medio configurar.
- **El registro público se retira, no se esconde.** Mientras exista un camino de alta que no
  pasa por el flujo asistido, existe la posibilidad de una Organización sin validar.
- **El estado de una solicitud es vocabulario de cliente.** Se expresa como en revisión,
  aprobada, creándose, creada o fallida. Los estados internos de Draft y de la Saga siguen
  existiendo para la agencia y se traducen a este vocabulario en el borde de la experiencia del
  cliente.

### Contradicción con ADR implícitas

- **ADR-11 (multi-tenant por subdominio + aislamiento por cuenta)** se preserva. La separación
  de experiencias es una capa por encima, no un cambio del aislamiento.
- El catálogo de módulos actual documenta que "vacío o ausente = todos habilitados". Esa
  decisión se conserva para no cambiar el comportamiento de las Organizaciones existentes, pero
  deja de ser suficiente por sí sola: la experiencia de la agencia no puede depender de que la
  lista esté vacía.

## Criterios de aceptación

1. Un usuario cliente y un operador de agencia que acceden a la misma Organización obtienen
   superficies distintas. Sí/No.
2. La experiencia del cliente no muestra grounding, proveedor o modelo de IA, claves, estado de
   write-back, identificadores internos ni diagnósticos de infraestructura, en ninguna pantalla
   ni en ninguna respuesta de la API que consume. Sí/No.
3. Un módulo deshabilitado para una Organización no es accesible por el cliente ni por
   navegación ni llamando directamente a la API. Sí/No.
4. El mismo módulo deshabilitado sigue siendo accesible para el operador de la agencia. Sí/No.
5. Un usuario con rol insuficiente recibe un rechazo de autorización aunque el módulo esté
   habilitado. Sí/No.
6. Un usuario con rol suficiente recibe un rechazo cuando el módulo está deshabilitado para su
   Organización, y el motivo distingue ambos casos. Sí/No.
7. Con dos o más AdAccount en la Organización y ninguna seleccionada, la API rechaza de forma
   explícita en vez de elegir una. Sí/No.
8. Cambiar la AdAccount activa cambia los datos mostrados y queda reflejado de forma visible.
   Sí/No.
9. Un usuario sin concesión sobre una AdAccount no puede leer sus datos aunque pertenezca a la
   Organización. Sí/No.
10. El alta asistida no permite emitir la invitación mientras algún paso no esté validado
    contra el sistema externo correspondiente. Sí/No.
11. Al completar el alta, la invitación emitida deja al cliente en una Organización con Meta
    conectada, IA configurada y WhatsApp vinculado. Sí/No.
12. No existe ningún camino de alta de Organización o de usuario fuera del flujo asistido y de
    la invitación. Sí/No.
13. La clave de IA y las credenciales de Meta no son legibles desde ninguna superficie del
    cliente. Sí/No.
14. El cliente ve el estado de su solicitud de campaña en el vocabulario acordado, sin estados
    internos de Draft ni de la Saga. Sí/No.
15. La agencia consulta métricas, breakdowns, Insight, AIAction, Draft y Outcome loop de una
    Organización a la que tiene acceso. Sí/No.

## Decisiones de testing

- La frontera es el **recorrido de sistema por navegador y API**, con dos actores autenticados
  —cliente y operador— contra la misma Organización. Es el seam que la spec maestra nombra como
  principal y el único que demuestra la separación de experiencias.
- Cada criterio de ocultación se prueba **dos veces**: en la navegación y llamando directamente
  a la API. Un test que solo compruebe el menú no distingue oculto de apagado, que es
  exactamente el error que esta spec corrige.
- La matriz módulo × rol se prueba en sus cuatro combinaciones, incluidas las dos que hoy se
  confunden: rol suficiente con módulo apagado, y rol insuficiente con módulo encendido.
- Se reutilizan como prior art las pruebas existentes de RBAC, de concesiones por AdAccount, de
  overrides por tenant y de componentes del frontend; las nuevas elevan esa cobertura al
  recorrido de sistema.
- El alta asistida se prueba con adaptadores controlados de Meta y de IA, comprobando que un
  paso que falla bloquea la emisión de la invitación, no solo que muestra un error.
- La ausencia de conceptos técnicos en la experiencia del cliente se prueba como aserción sobre
  la respuesta que recibe el cliente, no revisando plantillas a ojo.

## Dependencias

- `01` — la separación de experiencias se apoya en la resolución de sesión, de Organización
  activa y de suspensión; las invitaciones del alta asistida deben ser ya atómicas y de un solo
  uso.

## Fuera de alcance

- Contenido y comportamiento del chat: pertenecen a `12`. Aquí solo se decide que el chat es la
  superficie central del cliente.
- Definición de las métricas y estados que se muestran: pertenecen a `06`.
- Diagnósticos de incidente para la agencia, interruptores operativos y feature flags:
  pertenecen a `13`.
- Exportación de datos y baja del cliente: pertenecen a `09`.
- Impersonación de un tenant por soporte técnico.
- Personalización de marca por Organización más allá de la que ya existe.
- El modelo comercial —pago por implementación, 60 días de garantía, responsabilidad de
  infraestructura e IA— no genera comportamiento de software y se documenta fuera de estas
  specs.

## Riesgos y rollback

- **Daño máximo si no se hace:** el cliente ve información técnica y operativa que no le
  corresponde, y la agencia pierde herramientas cuando se simplifica la vista del cliente. En
  el peor caso, ocultar se usa como sustituto de autorizar y un dato queda accesible por API.
- **Daño máximo al hacerlo:** una separación mal hecha deja al operador de la agencia sin acceso
  a una herramienta que necesita para atender un incidente, o al cliente sin su producto.
- **Activación:** por feature flag de Organización. La experiencia del cliente se enciende
  primero para la Organización piloto, con la experiencia actual disponible para la agencia
  durante toda la transición. La retirada del registro público es independiente y se hace
  primero, porque no tiene usuarios legítimos que perder.
- **Rollback:** apagar el flag devuelve la experiencia única. Las comprobaciones de backend de
  módulo y rol no se revierten con el flag: son la parte que corrige un fallo de autorización y
  deben quedar activas aunque la experiencia vuelva atrás.

## Checklist de finalización

- [ ] Migración que separa el ámbito de los módulos y añade el tipo de acceso del usuario, sin
      cambiar el comportamiento de las Organizaciones existentes hasta que se configuren.
- [ ] Retirada del registro público, con verificación de que no queda ningún camino de alta
      alternativo.
- [ ] Pruebas de sistema de los quince criterios, con la matriz módulo × rol comprobada en
      navegación y en API.
- [ ] Documentación de producto actualizada con las dos experiencias y qué vive en cada una.
- [ ] Documentación de autenticación y de API actualizada con la evaluación conjunta de módulo
      y rol.
- [ ] Runbook de alta de cliente actualizado para reflejar el flujo asistido, sustituyendo el
      procedimiento manual actual.
- [ ] Observabilidad: rechazos separados por módulo y por rol, para poder distinguir un
      problema de contratación de uno de permisos.
- [ ] Verificación en producción con la Organización piloto: un cliente real y un operador real
      recorren sus respectivas experiencias.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Producto, roles y módulos*; *Solution*
(párrafo de una sola plataforma con dos experiencias); *Problem Statement* (párrafos de mezcla
de alturas y de exposición de conceptos técnicos); *Entrega por fases* → **P1**.
