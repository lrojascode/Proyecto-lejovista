# 01 · Marca "Agente DX IA" y vista recortada del cliente

## Contexto

El producto se renombra y el cliente tiene que ver una app más chica que la que ve
el operador. Hoy no existe ninguna noción de "vista del cliente": el sidebar
muestra los 8 módulos a todo el mundo.

Este archivo cubre lo **visual y de navegación**. La maquinaria para decidir *qué
ve cada cliente* está en [02](02-panel-admin-y-modulos.md); acá se deja el terreno
listo y, mientras el 02 no exista, los módulos se ocultan por configuración de
organización.

---

## 1 · Rebrand a "Agente DX IA"

`LejoVista` aparece en **11 archivos** del frontend:

```
app/layout.tsx · app/globals.css · app/landing/page.tsx
app/privacy/page.tsx · app/terms/page.tsx · app/data-deletion/page.tsx
app/(auth)/login/page.tsx · app/(dashboard)/layout.tsx
app/(dashboard)/page.tsx · app/(dashboard)/settings/page.tsx · lib/config.ts
```

Más el backend: `X-Title: "LejoVista Platform"` en la cabecera de OpenRouter
(`services/ai/llm.py`) y textos sueltos.

**No hacer un find-and-replace ciego.** Hay tres clases de aparición y solo una se
renombra:

| Clase | Ejemplo | Qué hacer |
|---|---|---|
| Marca visible | título del sidebar, `<title>`, login | → **Agente DX IA** |
| Marca legal | privacy, terms, data-deletion | Decidir con criterio legal: si la razón social no cambia, **no tocar** |
| Identificador técnico | `lejovista.compose.yml`, nombres de servicios, loggers `lejovista.*`, slug del tenant | **No tocar** — renombrarlos rompe despliegue y logs |

**Fuente única:** llevar el nombre visible a una constante en `lib/config.ts`
(`export const APP_NAME = "Agente DX IA"`) y que los componentes la importen. Hoy
está repetido literal en cada archivo, que es por lo que este cambio toca 11
lugares en vez de 1.

Ojo con el precedente ya resuelto: el login **no** debe mostrar la marca del
tenant cuando no hay slug (se arregló usando `getTenantSlugFromHostname`). El
nombre por defecto pasa a ser "Agente DX IA", no un slug inventado.

---

## 2 · Acento celeste

Buena noticia: el acento está muy centralizado. `--color-acid-lime: #e4f222` (el
amarillo actual) aparece **3 veces en `globals.css`** —definición, `.btn-primary`
y un comentario— y **1 vez** en `app/(dashboard)/page.tsx`.

Y el celeste **ya existe** como token: `--color-signal-teal: #02b8cc`, expuesto
además como `--accent-blue`.

**Cambio:**

1. Introducir un token semántico `--color-accent` y que `.btn-primary` y el resto
   lo usen, en vez de referirse al color por su nombre (`acid-lime`).
2. `--color-accent: var(--color-signal-teal)` como default del producto.
3. Que ese token sea **sobreescribible por organización**: `Organization.accent_color`
   **ya existe** en el modelo (columna creada por el trabajo de marca por org). Basta
   inyectarlo como variable CSS inline en el layout cuando el tenant lo tenga
   definido.

Así el "pasar a celeste" es un default, no un hardcode, y el próximo cliente que
quiera su color no necesita un deploy.

**Revisar de paso:** el comentario de `globals.css:233` dice que el botón primario
es *"LA única pieza cromática de acción (máx. 1 por vista)"*. Es una regla de
diseño deliberada — conviene respetarla al cambiar el color, no aprovechar para
pintar más cosas.

---

## 3 · Ocultar módulos en la vista del cliente

Menú actual (`app/(dashboard)/layout.tsx:195-204`):

| Ítem | Ruta | Cliente |
|---|---|---|
| Resumen | `/` | **oculto** |
| Campañas | `/campaigns` | visible (recortado, ver §4) |
| Chat Analista | `/chat` | visible |
| Reporte | `/reporte` | **oculto** |
| Diagnósticos IA | `/diagnostics` | **oculto** |
| Acciones Sugeridas | `/actions` | a definir — ver nota |
| Uso de IA | `/ai-usage` | **oculto** |
| Configuración | `/settings` | visible (recortado) |

> **Nota sobre Acciones Sugeridas:** no la mencionaste. La dejo **visible por
> defecto y apagable**, porque es la superficie donde el cliente aprueba cambios
> reales sobre su cuenta — si se oculta, el producto pierde su compuerta humana.
> Si preferís ocultarla, es un toggle más.

### Cómo ocultarlas bien

**Ocultar el ítem del menú no alcanza.** `/reporte` seguiría respondiendo si el
cliente escribe la URL, y los endpoints seguirían sirviendo datos. Hay que cortar
en tres capas:

1. **Menú** — filtrar `menuItems` por los módulos habilitados.
2. **Ruta** — el layout del dashboard redirige a la primera ruta permitida si la
   actual no lo está.
3. **API** — el backend rechaza el endpoint del módulo apagado. Sin esto es
   cosmético, no seguridad.

**Redirección de inicio:** hoy `/` **es** el Resumen. Si se oculta, la ruta raíz
tiene que llevar a la primera pantalla habilitada (Campañas o Chat), no a un 404
ni a una página vacía.

### Mientras el 02 no exista

Una columna `enabled_modules` (JSON, lista de claves) en `Organization`, con
default = todos. Es el mismo patrón de `campaign_rules_overrides`: vacío o ausente
significa "comportamiento de siempre". El panel del 02 después le pone UI encima
sin cambiar el modelo.

---

## 4 · Campañas: solo lo que está realmente al aire

Pediste que en Campañas aparezca **solo la campaña activa**. Hay una trampa acá y
vale la pena no caer en ella.

**Filtrar por `status == "ACTIVE"` da un resultado equivocado.** Una campaña puede
estar `ACTIVE` y no estar entregando nada porque su conjunto está pausado, el
presupuesto se agotó o la cuenta está topada. Meta expone eso en
**`effective_status`**, y el documento de ITS lo marca explícitamente como el
error a evitar:

> *"Un anuncio puede estar en `ACTIVE` pero con `estado_efectivo = ADSET_PAUSED`, o
> sea que **no está corriendo**. Para saber qué está al aire, mirá `effective_status`."*

**Y la app no sincroniza `effective_status` en absoluto** — verificado: cero
referencias en todo el backend.

**Entonces esto se hace en dos pasos:**

1. **Sincronizar `effective_status`** en campañas, conjuntos y anuncios: agregarlo
   al `fields` de las tres llamadas y una columna por snapshot. Migración Alembic.
   Es prerrequisito, y además desbloquea la pregunta *"¿cuáles están realmente
   corriendo?"* en el chat (ver [04](04-chat-consultas.md)).
2. **Filtrar la vista** por `effective_status == "ACTIVE"`, con un interruptor
   "Ver todas" para el operador.

Sin el paso 1, la pantalla va a mostrar como activas campañas que no gastan un
sol, que es peor que mostrarlas todas.

---

## 5 · Un solo formato de moneda en todo el producto

Viene del [06](06-bugs-pendientes.md), que cerró la mitad de adentro: el chat ya
habla un solo formato, con una única definición en
`backend/app/services/campaign_chat/formatting.py` (`money` / `fmt_int`). Lo que
queda es el resto del producto, y entra acá porque el 01 ya toca el frontend
entero por el rebrand y el acento — abrir un pase aparte para esto sería pasar dos
veces por los mismos archivos.

**Hoy conviven dos convenciones para el mismo número:**

```
chat            : S/. 7.417,19     ← es-PE, ya unificado
PDF / dashboard : S/.7417.19       ← sin separador, punto decimal, sin espacio
```

El cliente lee una cifra en el chat, descarga el PDF del mismo dato y ve otra
escritura. Son tres diferencias a la vez: el espacio tras `S/.`, el separador de
miles y el separador decimal.

### Alcance real

| Superficie | Sitios | Forma actual |
|---|---|---|
| Frontend | **31** en 8 archivos | `S/.{n.toFixed(2)}` inline |
| `pdf_report.py` | 4 | `S/.{{ "%.2f"\|format(...) }}` en la plantilla Jinja |
| `report_builder.py` | 8 | f-strings dentro de las narrativas |
| Otros servicios backend | 11 archivos | textos de insights, riesgo, sugerencias |

Los 8 archivos del frontend son exactamente los que ya se tocan por el rebrand y
el acento, salvo `campaigns/[id]` y `campaigns/new`.

### Cómo hacerlo

1. **Frontend:** una sola función en `lib/format.ts` (`money(n)`), y reemplazar los
   31 sitios. Hoy `reporte/page.tsx:28` ya tiene un `money()` local — es el molde,
   solo hay que subirlo a `lib/` y que los demás lo importen en vez de repetir
   `toFixed(2)` inline. `Intl.NumberFormat("es-PE")` da el formato correcto sin
   armarlo a mano.
2. **Backend fuera del chat:** el mismo criterio. `formatting.py` del chat ya tiene
   la implementación; conviene subirla a un módulo compartido —
   `app/utils/formatting.py`— e importarla desde el chat, el PDF y
   `report_builder`. **No** duplicarla: la duplicación es literalmente lo que
   causó este bug.
3. **Orden:** frontend y PDF primero (lo que el cliente ve); las narrativas de
   `report_builder` y los insights después, que son texto generado y su formato
   viaja dentro de strings más largos.

### La trampa

Las narrativas de `report_builder.py` y los insights **entran al grounding del
chat** cuando el chat los cita. `formatting.money` ya está probado contra la
guarda (`test_campaign_chat_formatting.py`), pero cualquier formateo nuevo que se
invente por fuera hay que verificarlo igual, o la respuesta del analista se
descarta entera y vuelve a leerse como plantilla.

Y no aplicar `.replace(",", ".")` sobre una cadena que ya contenga un monto
formateado: convierte `S/. 7.417,19` en `S/. 7.417.19`. Por eso `fmt_int` recibe un
entero y no un string.

### Decisión pendiente

Doy por hecho que se alinea **el producto al chat** (es-PE con separador), que es
lo coherente para un cliente peruano. Si preferís lo inverso —volver el chat a
`S/.7417.19`— es cambiar `formatting.py` y los tests que fijan la convención, y no
tocar nada más. Conviene decidirlo antes de empezar, no a mitad de los 31 sitios.

---

## Verificación

- El mismo monto se escribe igual en el chat, la tarjeta de aprobación, el
  dashboard, el reporte y el PDF.
- `grep -rn 'toFixed(2)' frontend/app` no devuelve montos de dinero.
- Una respuesta del chat que cita una narrativa del reporte sigue pasando el
  grounding.
- Un usuario de una organización con módulos recortados: no ve los ítems, y
  **escribiendo la URL a mano tampoco entra**; la API le responde 403.
- La raíz `/` lleva a una pantalla útil, no a la que se ocultó.
- El acento sale de `--color-accent`; una organización con `accent_color` propio lo
  pisa sin deploy.
- Campañas lista solo lo que Meta reporta como entregando de verdad.
- `grep -ri "lejovista" frontend/app frontend/components` no devuelve marca
  visible (sí puede devolver identificadores técnicos).
