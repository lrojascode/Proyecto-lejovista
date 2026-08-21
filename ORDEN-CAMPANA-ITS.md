# ORDEN DE TRABAJO — Armar campañas de ITS

> **Para qué sirve este archivo.** Pegalo (o su ruta) al inicio de un chat nuevo y decí
> *"arma la campaña"* + el creativo. Todo lo demás ya está resuelto acá.
> Complementa el `PLAYBOOK-META-ADS-IA.md` general; en lo que se contradigan, manda este.

---

## LO QUE VOS ME DAS (mínimo)

1. **Token** de la app n8n-agenciaKibo (ver §Token abajo).
2. **El creativo**: ruta del archivo (imagen o video) o el ID de un post ya publicado.
3. **El curso**: SAP MM, SAP FI, Business One, ABAP, BTP, etc.
4. **Mercado**: Perú / LATAM / país específico.
5. **Presupuesto**: soles por día.

Con eso alcanza. Si falta algo de 3–5, uso el default de la tabla de plantillas.

## LO QUE YO YA SÉ (no me lo repitas)

| Dato | Valor |
|---|---|
| Cuenta | `act_992953052009562` (INSTITUTE) · PEN · America/Lima |
| Business | ITS IN `996228438057122` |
| **page_id** | `106686772334869` |
| **instagram_user_id** | `17841444608450152` |
| Presupuesto diario mínimo de la cuenta | S/3.40 |
| Spend cap | S/5,000 · gastado S/2,360.53 · **margen ~S/2,640** |
| Formularios de leads más usados | `1354049788990195` (30 creativos) · `4861508273988424` (24) · `1711230306402791` (13) |
| CTA histórico dominante | `SIGN_UP` (98 creativos), luego `LEARN_MORE` (26) y `WHATSAPP_MESSAGE` (14) |

---

## PLANTILLAS

### A · Leads con formulario nativo — Perú *(default)*

```
objective            OUTCOME_LEADS
optimization_goal    LEAD_GENERATION      (o QUALITY_LEAD si se busca filtrar)
destination_type     ON_AD
billing_event        IMPRESSIONS
bid_strategy         LOWEST_COST_WITHOUT_CAP
promoted_object      { page_id: 106686772334869 }
geo_locations        countries: ["PE"]
age_min / age_max    18 / 65
targeting_automation advantage_audience: 1
publisher_platforms  facebook, instagram
positions            feed, story, reels        ← excluye Audience Network y Marketplace
call_to_action       SIGN_UP + lead_gen_form_id
status               PAUSED (los 4 niveles)
```

### B · Leads con formulario nativo — LATAM

Igual que A, cambiando:
```
geo_locations   countries: ["HN","GT","CR","PA","DO","EC","BO","PY"]   ← emergentes
                countries: ["MX","CO","CL","AR"]                        ← grandes
```
⚠️ **El copy NO puede decir "realidad peruana"** — reemplazar por el país o por
"realidad latinoamericana".

### C · Conversaciones por WhatsApp

Igual que A, cambiando:
```
optimization_goal    CONVERSATIONS
destination_type     WHATSAPP
promoted_object      { page_id: 106686772334869, whatsapp_phone_number: "51XXXXXXXXX" }
call_to_action       WHATSAPP_MESSAGE
```
⚠️ Falta confirmar el número de WhatsApp de ITS. **Pedímelo la primera vez** y lo fijo acá.
⚠️ Estas campañas se miden con `onsite_conversion.messaging_conversation_started_7d`,
**jamás con `lead`**.

---

## FÓRMULA DE COPY (verificada sobre S/300,861 de gasto)

Los mejores anuncios de la cuenta repiten esta estructura. Copys completos en
`datos-ago2026\analisis\its_copys.txt`.

1. **Titular en negrita Unicode** — `𝗦𝗔𝗣 𝗲𝗺𝗽𝗿𝗲𝘀𝗮𝘀 𝗽𝗮𝗿𝗮 𝗟𝗼𝗴𝗶́𝘀𝘁𝗶𝗰𝗮`
2. **Pregunta directa** — `¿𝗤𝘂𝗲́ 𝗲𝘀𝗽𝗲𝗿𝗮𝘀 𝗽𝗮𝗿𝗮 𝗰𝗲𝗿𝘁𝗶𝗳𝗶𝗰𝗮𝗿𝘁𝗲 𝗲𝗻 𝗦𝗔𝗣❓`
3. **Autoridad** — "Somos SAP Partner" · "Licencia Original SAP" · "únicos con clases todos los meses"
4. **6–8 beneficios con ✅** — 100% online · licencia original · casuísticas reales ·
   instructores senior · certificación KEY USER · recursos digitales · horarios flexibles · aula virtual
5. **Dos cursos de regalo** — Power BI + Excel (aparece en los de mejor rendimiento sostenido)
6. **Formato video vertical** — domina el top; la imagen estática rinde por debajo

**Prohibido:**
- ❌ **Links `bit.ly` dentro del copy.** Todos los ganadores los llevan y rompen la atribución
  de Meta. El destino va por el botón nativo.
- ❌ Mezclar avisos de empleo con captación de alumnos. Van en campañas separadas y no se
  suman en el mismo reporte.

---

## NOMENCLATURA

`ITS_TIPO_CURSO_MERCADO_MESAA`

- **TIPO:** `LEADS` (formulario) · `WA` (WhatsApp) · `TRAF` (tráfico)
- **CURSO:** MM, FI, SBO, ABAP, BTP, HANA, TECNICO, LOGISTICO
- **MERCADO:** PE, LATAM, MEX, HON…

Ejemplo: `ITS_LEADS_SBO_LATAM_AGO26`
Conjunto: `ITS_AS01_LATAM_EMERGENTE` · Anuncio: `ITS_AD01_<descriptor del creativo>`

> Corta con los prefijos viejos (JSR/ECQ/JMM/AOEC), que son de cuatro gestores distintos
> y hacen la cuenta ilegible.

---

## REGLAS QUE NO SE NEGOCIAN

1. **Presupuesto mínimo por conjunto: S/16/día.** Al costo actual (S/2.21/resultado) eso da
   los ~50 eventos/semana que Meta necesita. Por debajo, el conjunto nunca sale de
   aprendizaje. **Antes que crear dos conjuntos a S/8, creo uno a S/16.**
2. **Todo se crea en `PAUSED`**, los cuatro niveles. Activo solo con tu OK explícito.
3. **Verifico siempre después de crear** y te reporto: IDs, estado, `issues_info`, y —
   en campañas de WhatsApp — que el flow quedó montado.
4. **Pausar antes de editar** cualquier campaña que esté ACTIVE.
5. **Nunca segmentar por edad o género.** La data es plana (S/1.83 a S/2.68 entre extremos).
   Público amplio + Advantage+ y listo.
6. **Espaciar las llamadas** 0.5–3 s. Al primer `Application request limit reached`, parar.
7. Si algo no está cubierto acá, **pregunto antes de inventar**.

---

## LO QUE HAGO CUANDO DECÍS "ARMA LA CAMPAÑA"

1. Valido el token y el estado de la cuenta (margen de spend cap).
2. Subo el creativo → `image_hash` o `video_id`.
3. Creo **campaña** → **conjunto** → **creativo** → **anuncio**, todo en PAUSED.
4. Verifico los 4 niveles por API y reviso `issues_info`.
5. Te reporto la tabla de IDs, el copy final tal como quedó guardado, y qué falta.
6. Actualizo la memoria del proyecto.

Tiempo real de referencia: la campaña de Althea del 15-ago tomó **~20 minutos**
incluyendo dos errores de API.

---

## TOKEN — el único cuello de botella real

Los tokens del Graph API Explorer duran **1–2 horas**. Hoy se venció uno a mitad de trabajo.

**Solución recomendada, en orden:**

1. **Token de usuario del sistema** (System User) desde Business Manager → **no expira nunca**.
   Es lo correcto para esto. Se crea una vez y se olvida.
2. **Token de 60 días**: dame el **app secret** de n8n-agenciaKibo y lo canjeo con
   `oauth/access_token?grant_type=fb_exchange_token`.
3. Token nuevo del Explorer cada sesión — lo que venimos haciendo, y no escala.

Sin 1 o 2, el flujo "decime arma la campaña" se corta cada dos horas.

---

## PENDIENTES ANTES DE LANZAR EN SERIO

- [ ] Número de **WhatsApp de ITS** (para la plantilla C)
- [ ] Confirmar qué **formulario de leads** corresponde a cada curso
- [ ] Respuesta comercial: **¿qué pasó con los 679 leads internacionales?** — bloquea LATAM
- [ ] Conectar el **CRM** a Meta. Hoy no hay dato de matrículas: se optimiza costo por
      contacto, no costo por alumno
