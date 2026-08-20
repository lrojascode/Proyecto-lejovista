# 05 · La creación de campañas, contra `ORDEN-CAMPANA-ITS.md`

> **Estado: hecho del 1 al 4 y parte del 5. El bloque de plantillas de
> formulario nativo queda pendiente, a propósito.**
>
> | # | Qué | Estado |
> |---|---|---|
> | 1 | Verificar tras crear (`issues_info`) | ✅ paso 6 de la Saga |
> | 6 | Pausar antes de editar | ✅ |
> | 9 | Prohibir acortadores | ✅ con explicación del porqué |
> | 3 | Edad/género configurable, apagado por defecto | ✅ |
> | 4 | Piso de presupuesto por aprendizaje | ✅ avisa, no bloquea |
> | 2 | Geo por países | ✅ conviven países y región |
> | 7 | Token de tipo en el nombre | ✅ sale de las reglas |
> | 1 (plantillas) | **Formulario nativo y LATAM** | ❌ **pendiente** |
> | 8 | Fórmula de copy por organización | ❌ pendiente (material de prompt) |
> | 10 | Revisar exclusión de Audience Network | ❌ pendiente |
> | 11 | Token de System User | ❌ pendiente (documentación) |
>
> **Por qué las plantillas quedaron afuera, y no es pereza.** Un formulario de
> leads nativo no es un parámetro distinto: es otra forma de creativo. Hay que
> sincronizar los formularios de la cuenta (`/leadgen_forms`), poder elegir uno,
> y hacer que `CreativeBuilder` ramifique — hoy arma el `object_story_spec`
> asumiendo WhatsApp, con `call_to_action` fijo, `page_welcome_message`
> obligatorio y el deep link de WhatsApp en `link_data.link`.
>
> Eso es un pase propio, y **el write-back está encendido en producción** con la
> cuenta piloto en el allowlist. Dejar a medias un camino de creación que
> escribe con dinero real es peor que dejarlo sin empezar: media plantilla no
> falla al crearla, falla al entregar, y eso se descubre gastando.
>
> Decisión de producto ya tomada: la plantilla es **fija por organización**, no
> elegible en el chat. El propio archivo lo recomienda —"un cliente de cursos
> SAP siempre hace lo mismo"— y el terreno ya está listo: `geo_countries`,
> `campaign_name_type_token` y `use_age_targeting` son reglas por organización.

## Contexto

`ORDEN-CAMPANA-ITS.md` es la orden de trabajo con la que hoy se arman campañas a
mano. La consigna es la misma: donde difiera de la app, **manda el documento**.

**Advertencia previa, y es la más importante de todo el plan:** la app tiene la
creación clavada a **un solo tipo de campaña** —WhatsApp, geo Lima, segmentada por
edad— y el documento pide justo lo contrario. Esto no es afinar parámetros: es
abrir el motor a otros tipos de campaña. Es el archivo más grande de los seis y el
que más decisiones de producto necesita antes de tocar código.

---

## Tabla de brechas

| # | Documento | App hoy | Gravedad |
|---|---|---|---|
| 1 | Tres plantillas: leads con formulario nativo (PE), leads LATAM, WhatsApp | **Solo WhatsApp** | **Crítica** |
| 2 | Geo por países (`PE`, o lista LATAM) | **Solo región Lima** (`allowed_geo_keys: ["2771"]`) | **Crítica** |
| 3 | **Nunca** segmentar por edad ni género | Segmenta con `best_age_range` del histórico | **Alta** |
| 4 | Presupuesto mínimo por conjunto: S/16/día | Sugiere sobre otro criterio | **Alta** |
| 5 | Verificar los 4 niveles tras crear, revisar `issues_info` | No se verifica | **Alta** |
| 6 | Pausar antes de editar una campaña ACTIVE | No existe | Alta |
| 7 | Nomenclatura `TIPO_CURSO_MERCADO_MESAA` | `{MARCA}_MSJ_{PRODUCTO}_{MESAÑO}` | Media |
| 8 | Fórmula de copy verificada (6 puntos) | Prompt genérico | Media |
| 9 | Prohibido `bit.ly` en el copy | Sin validación | Media |
| 10 | Posiciones: feed, story, reels; excluir Audience Network | Parcial | Media |
| 11 | Token de System User que no expira | Token manual de 1-2 h | Media |

---

## 1 y 2 · El motor solo sabe hacer un tipo de campaña

`TENANT_CAMPAIGN_RULES` hoy:

```python
"objective":         "OUTCOME_LEADS",
"optimization_goal": "CONVERSATIONS",
"destination_type":  "WHATSAPP",
"default_geo_region_key": "2771",   # Lima
"allowed_geo_keys":  ["2771"],
```

El documento necesita **tres** plantillas, y dos de ellas son imposibles hoy:

- **A · Leads con formulario nativo (Perú)** — `optimization_goal: LEAD_GENERATION`,
  `destination_type: ON_AD`, `call_to_action: SIGN_UP` **+ `lead_gen_form_id`**.
  La app no tiene ningún concepto de formulario de leads: ni lo sincroniza, ni lo
  ofrece, ni sabe adjuntarlo al creativo.
- **B · Leads LATAM** — igual que A pero con **lista de países**. La app solo
  entiende una clave de región.
- **C · WhatsApp** — es la única que hoy funciona.

**Esto no se resuelve con overrides por organización.** `campaign_rules_overrides`
puede cambiar *valores*, pero el `CreativeBuilder` construye el `object_story_spec`
asumiendo WhatsApp: `call_to_action` fijo, `page_welcome_message` obligatorio, deep
link de WhatsApp en `link_data.link`. Un formulario nativo es otra forma de
creativo.

**Trabajo real:** un concepto de **plantilla de campaña** (objetivo + optimización
+ destino + CTA + qué exige el creativo), con las tres del documento como primeras
implementaciones, y `CreativeBuilder` ramificando por plantilla en vez de asumir
una. Más sincronizar los formularios de leads de la cuenta (`/leadgen_forms`) para
poder elegir uno.

**Y geo por países**, no por región: `geo_locations.countries`. La app tendría que
aceptar ambas formas — hay clientes locales que sí quieren Lima.

> **Decisión de producto pendiente:** ¿el chat de creación va a ofrecer las tres
> plantillas al usuario, o cada organización tiene la suya fija en su
> configuración? Lo segundo es mucho menos trabajo y probablemente más correcto:
> un cliente de cursos SAP siempre hace lo mismo.

---

## 3 · No segmentar por edad ni género

El documento:

> *"**Nunca segmentar por edad o género.** La data es plana (S/1.83 a S/2.68 entre
> extremos). Público amplio + Advantage+ y listo."*

La app hace exactamente lo contrario: `build_targeting` toma `best_age_range` del
histórico ganador y lo aplica. Peor: esa elección arrastra una consecuencia técnica
—si el rango apunta a público mayor, se ve obligada a apagar `advantage_audience`,
porque Meta no admite Advantage+ con `age_min > 25`.

O sea que segmentar por edad **desactiva** justo lo que el documento recomienda
usar.

**Arreglo:** que la segmentación por edad sea una regla por organización, apagada
por defecto. Con público amplio, `advantage_audience: 1` deja de ser un caso
especial y pasa a ser lo normal.

**Ojo:** esto cambia el comportamiento del sugeridor para clientes existentes. Va
detrás de un override, no como cambio global silencioso.

---

## 4 · El presupuesto mínimo tiene un porqué

> *"Presupuesto mínimo por conjunto: **S/16/día**. Al costo actual eso da los ~50
> eventos/semana que Meta necesita. Por debajo, el conjunto nunca sale de
> aprendizaje. **Antes que crear dos conjuntos a S/8, creo uno a S/16.**"*

Es una regla operativa con fundamento (la fase de aprendizaje de Meta), no una
preferencia. El sugeridor actual calcula el presupuesto con otro criterio —un
colchón de ~20 conversaciones al menor costo observado— y no conoce el piso.

**Arreglo:** piso configurable por organización, y que el validador **avise** —no
que bloquee— cuando el presupuesto propuesto deje al conjunto por debajo del
umbral de aprendizaje. Explicando el porqué, que es lo que convierte un número en
una decisión informada.

---

## 5 y 6 · Verificar después de crear, pausar antes de editar

Dos disciplinas del documento que la app no tiene:

**Verificación posterior.** El documento reporta *"IDs, estado, `issues_info`, y —
en campañas de WhatsApp— que el flow quedó montado"*. La Saga hoy guarda los IDs
que devolvió Meta, pero **no vuelve a leer** para confirmar que el objeto quedó
sano. `issues_info` es justamente donde Meta avisa que un anuncio tiene problemas
de política o de configuración.

Un paso 6 de verificación en la Saga: releer los cuatro niveles, guardar
`issues_info` y mostrarlo en la pantalla de resultado. Es barato y cierra el bucle:
hoy "creada con éxito" solo significa "Meta aceptó el POST".

**Pausar antes de editar.** El documento lo pone como regla dura para cualquier
campaña `ACTIVE`. El `MetaCampaignModifier` hace backup y rollback, pero no pausa
antes. Es un cambio chico con impacto real en la seguridad de la cuenta.

---

## 7, 8, 9, 10 · Nomenclatura, copy y placements

- **Nomenclatura**: la de la app (`{MARCA}_MSJ_{PRODUCTO}_{MESAÑO}`) tiene el tipo
  clavado en `MSJ`, coherente con ser WhatsApp-only. El documento usa
  `TIPO_CURSO_MERCADO_MESAA`, donde TIPO varía (`LEADS`/`WA`/`TRAF`) y agrega
  mercado. Cae solo cuando existan las plantillas del §1: el tipo **es** la
  plantilla. Hacerla configurable por organización.
- **Fórmula de copy**: seis elementos verificados sobre S/300.861 de gasto real
  (titular en negrita Unicode, pregunta directa, autoridad, 6-8 beneficios con ✅,
  dos cursos de regalo, video vertical). Es material de prompt, no de código: va al
  system prompt de `suggest_campaign_copy`, **por organización**, no global — es la
  fórmula de ITS, no de todos.
- **`bit.ly` prohibido**: *"Todos los ganadores los llevan y rompen la atribución
  de Meta."* Validación en `CampaignValidator`: rechazar acortadores en el copy con
  un mensaje que explique el porqué. Barato y evita un error caro.
- **Placements**: el documento pide feed, story y reels, excluyendo Audience
  Network y Marketplace. La app tiene `required_placements: ["facebook_reels"]` y
  posiciones de Instagram configurables — cerca, pero no es lo mismo. Revisar que
  Audience Network quede excluido explícitamente.

---

## 11 · El token, que es el cuello de botella real

El documento lo dice con todas las letras:

> *"Los tokens del Graph API Explorer duran **1-2 horas**. Sin un token de System
> User o de 60 días, el flujo se corta cada dos horas."*

La app ya canjea a token largo (~60 días) **si** `META_APP_ID`/`META_APP_SECRET`
están configurados; si no, guarda el token corto tal cual y la cuenta muere en
horas. Ya existe el aviso anticipado de expiración y la reconexión.

**Lo que falta es documentar y soportar el token de System User**, que no expira
nunca y es la respuesta correcta para un producto que sincroniza solo. Es
mayormente documentación y un chequeo al conectar, no código nuevo.

---

## Orden sugerido

```
1. Verificar tras crear (issues_info) + pausar antes de editar → seguridad, barato
2. Prohibir bit.ly + revisar placements                        → validaciones chicas
3. Edad/género configurable, apagado por defecto                → corrige el targeting
4. Piso de presupuesto por aprendizaje                          → con aviso, no bloqueo
5. Plantillas de campaña + geo por países + formularios de leads → el bloque grande
6. Nomenclatura y fórmula de copy por organización              → cae solo tras el 5
7. Token de System User                                          → documentación
```

Del 1 al 4 son mejoras acotadas sobre lo que ya existe. El 5 es un proyecto en sí
mismo y conviene decidir antes su alcance.

## Verificación

- Crear una campaña de prueba en simulación: la Saga relee los 4 niveles y reporta
  `issues_info`.
- Un copy con un `bit.ly` se rechaza explicando por qué.
- Con la regla de edad apagada: targeting amplio y `advantage_audience: 1`.
- Un presupuesto por debajo del piso avisa, explica y **deja continuar**.
- Con plantilla de formulario nativo: el creativo sale con `SIGN_UP` y su
  `lead_gen_form_id`, sin `page_welcome_message` de WhatsApp.
- Todo sigue naciendo `PAUSED` en los cuatro niveles.
