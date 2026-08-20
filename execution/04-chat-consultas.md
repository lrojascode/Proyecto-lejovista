# 04 · El chat de consultas, contra `PROMPT-CONSULTAS-ITS.md`

> **Estado: hecho lo esencial (§1, §2, §3, §5, §9).** Queda pendiente lo de la
> cola de la lista, que depende de datos que todavía no se sincronizan o que son
> mejoras de presentación:
>
> | # | Qué falta | Por qué quedó |
> |---|---|---|
> | 4 | Métricas *lifetime* por campaña | El agregado mensual ya está; falta exponerlo por campaña |
> | 6, 7 | ID de campaña y formato tabla en las respuestas | Presentación; el ID ya está en los datos |
> | 8 | Verificar que el total cuadre con las partes | Chequeo de consistencia sobre los facts |
> | 10 | Píxeles (`/adspixels`) | Una llamada de sync y una tabla nueva |
> | 11-14 | Públicos, bit.ly, gestor, estacionalidad | Baja prioridad según el propio plan |

## Contexto

`PROMPT-CONSULTAS-ITS.md` es cómo opera hoy un analista real sobre la cuenta de
ITS. La consigna: donde el documento y la app difieran, **manda el documento**.

Lo primero, para no confundir: ese documento describe un analista de **una sola
cuenta leyendo CSV de disco**. La app es **multi-tenant sobre Postgres**. Se
portan los **comportamientos**, no las rutas ni las cifras de ITS. Cualquier
constante de ITS que termine hardcodeada en el producto es un bug.

---

## Tabla de brechas

| # | Lo que pide el documento | Estado en la app | Gravedad |
|---|---|---|---|
| 1 | `resultados = leads + wa`, reportados por separado | Colapsa a un número: `conversations or conversions` | **Alta** |
| 2 | Mirar `effective_status`, no `status` | **No se sincroniza** | **Alta** |
| 3 | Avisar hasta qué fecha llegan los datos | No existe (ver [03](03-sync-historico-completo.md)) | **Alta** |
| 4 | Métricas *lifetime* además de ventanas | Solo ventanas relativas | Alta |
| 5 | Negarse a hablar de otra cuenta o marca | Sin límite explícito de alcance | Media |
| 6 | Nombres completos + ID + fechas exactas | Nombres sí; IDs y fechas no | Media |
| 7 | Formato: resumen → tabla → qué significa → sugerencia | Prosa libre | Media |
| 8 | Verificar totales antes de responder | Nada | Media |
| 9 | Decir que no hay matrículas, ingresos ni ROAS | No se menciona | Media |
| 10 | Estado de los píxeles | **No se sincronizan** | Media |
| 11 | Públicos personalizados como activo desaprovechado | Se sincronizan, el chat no los usa | Baja |
| 12 | Quién gestionó la cuenta (prefijos de nombre) | No existe | Baja |
| 13 | `bit.ly` en el copy rompe atribución | El copy se guarda, no se analiza | Baja |
| 14 | Estacionalidad, "anuncios asfixiados" | Requiere histórico largo | Baja |

---

## 1 · Leads y conversaciones no son lo mismo (lo más importante)

El documento es tajante:

> *"**Nunca dividas el gasto de una campaña de WhatsApp entre `leads`.** Da cifras
> absurdas. Ejemplo real: medida mal da **S/101.78 por lead**; medida bien da
> **S/3.38 por conversación**."*

Y define: `resultados = leads + wa`, `costo_resultado = gasto / resultados`.

**Qué hace la app.** El sync separa bien `conversions` (whitelist de action types
de lead) de `conversations` (eventos de mensajería), con un fallback razonable:
si la campaña no tiene mensajería, `conversations = conversions`.

Pero `ReportBuilder` colapsa: `conv = a["conversations"] or a["conversions"]`.
**Elige uno, no suma.** En una cuenta que corre **los dos tipos a la vez** —el
caso de ITS— eso significa que el número final no es "resultados", es "lo que
haya". Y todo el producto lo etiqueta como *"conversaciones"*, así que un cliente
con campañas de formulario lee una palabra que no corresponde a su dato.

**Arreglo:**
- Guardar y exponer **las dos** métricas por separado, más `resultados` como suma
  explícita.
- Que el chat las nombre por lo que son: *"1.240 resultados: 890 formularios y 350
  conversaciones de WhatsApp"*.
- El costo por resultado se calcula sobre la suma.
- Etiquetar cada campaña por su tipo de resultado, derivándolo de
  `destination_type`/`optimization_goal` como indica el documento.

**Ojo con el fallback:** hoy `conversations = conversions` cuando no hay
mensajería. Si además se suma, se cuenta dos veces. La suma tiene que hacerse
sobre los valores crudos, no sobre el proxy.

---

## 2 · `effective_status` — qué está realmente al aire

Ya está descrito en [01](01-marca-y-vista-cliente.md) §4, y sirve a los dos
frentes. Sin él, ni la pantalla de campañas ni el chat pueden responder *"¿cuáles
están corriendo ahora?"*. Cero referencias en el backend hoy.

Prerrequisito de este archivo y del 01.

---

## 3 · Hasta cuándo llegan los datos

El documento lo pone como *regla crítica* y hasta dicta el aviso:

> *"⚠️ Ojo: estos datos están actualizados al 18-ago-2026."*

En la app esto es el bloque **`cobertura`** que ya está diseñado: rango real de
fechas, última sincronización, huecos detectados. Va en [03](03-sync-historico-completo.md).

Lo que suma este documento es **cuándo avisar sin que lo pregunten**: si la
respuesta toca los últimos 30 días, el aviso va **al principio**, no al final.

---

## 4 · Métricas *lifetime*

El documento trabaja con tres prefijos: `life_` (desde el inicio), `d30_`, `d7_`.
La app solo tiene ventanas relativas a hoy; no hay forma de preguntar *"¿cuánto
gastó esta campaña en toda su vida?"*.

Depende del histórico completo del [03](03-sync-historico-completo.md). Una vez
que exista, agregar el agregado *lifetime* por campaña es barato y desbloquea
media docena de preguntas naturales.

---

## 5 · Límite de alcance

El documento define una respuesta exacta para preguntas sobre otra cuenta o marca.
En la app el aislamiento por organización ya es real a nivel de datos —una query
nunca cruza tenants—, pero **el chat no dice que no**: si le preguntan por otra
marca, improvisa.

Agregar el rechazo explícito, nombrando la cuenta activa. Es barato y evita que el
modelo conteste "de memoria" sobre marcas que no son del cliente.

---

## 6, 7 y 8 · Cómo se responde

Tres reglas del documento que la app no aplica:

- **Exactitud**: nombre completo **+ ID** de la campaña, y fechas completas
  (*"18 de agosto de 2026"*, no *"18/08"*, no *"la última semana"*). El ID lo
  tenemos; simplemente no se emite.
- **Formato**: resumen en una o dos frases → los datos (tabla si son más de tres
  cifras) → qué significa en criollo → sugerencia solo si la pidieron.
- **Verificar antes de responder**: que el total cuadre con la suma de las partes.

Las tres se implementan **en el texto canónico y en el prompt**, nunca dejando que
el modelo calcule. Los IDs y las fechas entran a los facts; la guarda de grounding
los admite porque son datos, no cálculos.

> Cuidado con el presupuesto de grounding: cada ID de Meta son 15-17 dígitos y cada
> fecha aporta varias entradas. Los IDs conviene marcarlos como opacos —ya existe
> `OPAQUE_KEYS`— para que no sirvan de respaldo numérico a cifras inventadas.

---

## 9 · Lo que el producto NO puede saber

El documento lo dice sin vueltas: el píxel nunca registró una compra ni una
matrícula y el CRM no está conectado, así que **no hay dato de alumnos ni de
ingresos, y no se puede calcular ROAS ni retorno**. Se mide **costo por contacto,
no costo por alumno**.

Esto es honestidad comercial, no una limitación técnica a esconder. El chat tiene
que decirlo cuando le pregunten por ventas, ingresos, ROI o matrículas, en vez de
improvisar una respuesta con las métricas que sí tiene.

Es la misma disciplina del "no sé" honesto que ya se corrigió: decir qué falta y
ofrecer lo que sí hay.

---

## 10 · Píxeles

No se sincronizan (la única aparición de "pixel" en el backend es un action type
dentro de la whitelist de leads). El documento los usa para responder *"¿está
midiendo bien esta cuenta?"* — cuál está activo, cuál nunca disparó, qué
conversión personalizada existe.

Es una llamada más al sync (`/adspixels`) y una tabla chica. Alto valor
diagnóstico por poco trabajo: detecta cuentas que gastan sin medir.

---

## 11-14 · El resto

- **Públicos personalizados**: ya se sincronizan (`custom_audiences`) y el chat no
  los menciona nunca. El documento los llama *"un activo importante y muy poco
  aprovechado"*. Exponerlos en un bloque es casi gratis.
- **Quién gestionó la cuenta**: se deduce de los prefijos de los nombres de
  campaña. Si se implementa, hay que decir **siempre** que es una deducción del
  nombre y no un dato de Meta — el documento insiste en eso.
- **`bit.ly` en el copy**: el copy ya se guarda (`creative_title`,
  `creative_body`). Detectar acortadores es una regex y una advertencia.
- **Estacionalidad y "anuncios asfixiados"**: dependen del histórico largo. Después
  del [03](03-sync-historico-completo.md), son reglas deterministas sobre datos que
  ya estarán.

---

## Orden sugerido

```
1. resultados = leads + wa            → corrige cifras que hoy son engañosas
2. effective_status                   → compartido con el 01
3. cobertura + aviso de frescura      → viene del 03
4. exactitud, formato, límite de alcance → prompt + canónico, sin datos nuevos
5. métricas lifetime                  → cuando exista el histórico
6. píxeles                            → alto valor, poco trabajo
7. públicos, bitly, gestor, estacionalidad
```

## Verificación

Sobre una cuenta mock con campañas de formulario **y** de WhatsApp:

- "¿cuántos resultados tuve?" → distingue formularios de conversaciones y da la
  suma; el costo por resultado se calcula sobre la suma.
- Una campaña de WhatsApp **nunca** muestra su costo dividido entre leads.
- "¿qué campañas están corriendo?" → usa `effective_status`, no `status`.
- Preguntar por otra marca → rechazo explícito nombrando la cuenta activa.
- Preguntar por ventas o ROAS → dice que no se mide y por qué.
- Toda mención a una campaña trae nombre completo, ID y fechas completas.
