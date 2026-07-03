# Comité de Inversión: Auditoría Crítica de LejoVista (2026)

Este documento representa la evaluación conjunta de un comité de expertos (Product Manager Senior, CTO, UX Researcher, Diseñador UX/UI Senior, Ingeniero de Software Senior, QA Lead, Especialista en Ciberseguridad, Growth Product Manager y Analista de Mercado) sobre la plataforma **LejoVista**. El análisis es deliberadamente severo, diseñado para proteger una potencial inversión de capital y evaluar la viabilidad real del producto en el mercado actual de 2026.

---

## 1. Resumen Ejecutivo

*   **Problema que resuelve:** La ineficiencia, propensión a errores manuales y el riesgo de baneo por comportamiento automatizado al gestionar y optimizar campañas publicitarias en Meta Ads.
*   **Diferenciación:** Su enfoque principal es una técnica de "anti-detección" (inyectar pausas aleatorias de 12-75s) para simular comportamiento humano y un flujo de aprobación "Semi-autónomo" (la IA propone, el humano aprueba).
*   **Competitividad actual:** Baja a nivel global. En 2026, el mercado global está dominado por agentes de IA autónomos (Hyper, AdStellar) que gestionan presupuestos de forma omnicanal (Meta, Google, TikTok, Amazon) con generación creativa fluida. A nivel local (Chile/LATAM), tiene una ventana de oportunidad si se enfoca en agencias boutique locales que sufren baneos de Meta constantemente.
*   **Preparación para el mercado:** Nivel beta cerrado alcanzado. El producto sufre de *overengineering* (Arquitectura de microservicios con Celery y Redis) alojado en una infraestructura precaria (HostGator VPS). Sin embargo, se ha estabilizado la seguridad al eliminar el bypass de autenticación por URL query params, erradicar el iframe, y migrar a un flujo de datos JSON nativo. Además, se ha dinamizado la Capa 1 de optimización IA.

---

## 2. Evaluación General (0-100)

| Categoría | Puntaje | Justificación del Comité |
| :--- | :---: | :--- |
| **Producto** | 65 | Resuelve un dolor real (ahorro de tiempo), y ha mejorado su lógica al incorporar CPL dinámico. |
| **UX** | 70 | **MEJORADO.** Se eliminó el iframe y ahora los reportes se renderizan de forma nativa en la UI de Next.js. Queda pendiente el editor de copys. |
| **UI** | 75 | Estética moderna (Glassmorphism, fuentes premium), lo cual da una buena primera impresión al tomador de decisión. |
| **Velocidad** | 70 | Next.js 16 es rápido, pero las pausas forzadas de 50s en el backend durante la ejecución generarán ansiedad en el usuario si el UI no lo refleja bien. |
| **Arquitectura** | 60 | **MEJORADO.** Se simplificó eliminando la autenticación temporal de iframes y la inyección por URL. Se conectan backend y frontend nativamente por API. |
| **Escalabilidad** | 35 | Un VPS HostGator no escala horizontalmente. Cuando se añadan 20 clientes y Celery deba encolar cientos de tareas concurrentes, el servidor colapsará (OOM Kill). |
| **Seguridad** | 75 | **MEJORADO.** Se eliminó por completo el bypass de autenticación por URL query params. Ahora el backend exige exclusivamente `Authorization: Bearer <JWT>` y los datos viajan nativos. |
| **Accesibilidad** | 80 | Buen cumplimiento de etiquetas ARIA y jerarquía semántica (declarado en el status.md). |
| **Onboarding** | 30 | Requiere setup manual mediante bash scripts (`setup-hostgator.sh`). No es un SaaS real "Self-Serve" todavía. |
| **Retención** | 60 | Si funciona y baja el CPL, retendrá. Si un bug en la automatización gasta el presupuesto del cliente por error (budget scaling infinito), habrá demandas. |
| **Engagement** | 65 | El modo "Semi-autónomo" obliga al usuario a entrar al dashboard a aprobar. Esto infla métricas de engagement, pero a largo plazo genera fatiga de clics. |
| **Modelo de Negocio** | 50 | Contrato individual y setup manual no escala en absoluto. Falta integración nativa de pagos (Stripe). |
| **Diferenciación** | 55 | Las reglas de optimización ya no son 100% fijas; se introdujo una base de comparación dinámica del CPL en la cuenta. |
| **Innovación** | 45 | Reglas dinámicas adaptativas a la cuenta de anuncios en lugar de umbrales fijos generales. |
| **Viabilidad Comercial** | 60 | Para agencias boutique en Chile/Latam, es vendible. Para enterprise, es inviable por los riesgos técnicos de hosting. |
| **Potencial Internacional** | 25 | Totalmente superado por gigantes como Hyper, Madgicx y Optmyzr en EE.UU. y Europa. |
| **Potencial en Chile** | 75 | Mercado local (Pymes y agencias) muy ávido de soluciones de automatización en español puro. Competencia local (como Vambe) está enfocada más en IA conversacional/WhatsApp. |
| **Madurez Técnica** | 65 | **MEJORADO.** Mitigación de brechas de seguridad críticas y refactorización de lógica rígida de CPL. |
| **Calidad del MVP** | 65 | **MEJORADO.** Un producto más sólido gracias a la renderización nativa. |
| **Probabilidad de Éxito** | 55 | Reducido el riesgo de robo de tokens y la fricción de uso. |

**Puntaje Final: 57/100**
*Interpretación: 41-60 = MVP funcional pero débil (muy cercano a Beta).*

---

## 3. Comparación contra la competencia (2026)

| Característica | LejoVista | Hyper (Global) | Madgicx (Global) | AdStellar (Global) | Optmyzr (Global) | Vambe (Startup Chile) | Bigbuda (Agencia Chile) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Funciones** | Solo Meta Ads | Meta, Google, TikTok, Amazon | Meta, Google, Shopify | Meta (Agentes IA) | Google, Meta, Microsoft | WhatsApp/IG bots ventas | Servicios Gestionados |
| **IA** | Reglas dinámicas + LLM | Agentes Autónomos Nivel 5 | Predicción Audiencias | Agentes Creadores E2E | Scripts + IA predictiva | Agente de ventas LLM | Consultoría IA + GEO |
| **UX** | Nativa mejorada (Cola) | Excelente, visual | Compleja/Avanzada | Intuitiva y fluida | Muy técnica / Árida | Conversacional | Servicio humano "Done for you" |
| **Precio** | A medida (Alto B2B) | SaaS ($299+) | SaaS ($199+) | SaaS ($499+) | SaaS Enterprise | Accesible Pyme | Alto (Retainer agencia) |
| **Velocidad** | Lenta intencional (Pausas) | Tiempo Real | Tiempo Real | Tiempo Real | Tiempo Real | Tiempo Real | Lenta (Procesos manuales) |
| **Automatizaciones**| Aprobación requerida | Full Automático (Opcional) | Optimización automática | Full-stack ads creation | Reglas de valor cruzadas| Cierre de ventas auto | Setup inicial y monitoreo |
| **Creación Ads**| Solo textos/estructura | Genera Imágenes/Video | Integración IA Diseño | Creación creativa total | Limitada a estructura | N/A | Hecha por diseñadores |
| **Escalabilidad**| Pobre (VPS único) | Global Cloud (AWS) | Global Cloud (AWS) | Global Cloud (AWS) | Global | Regional AWS | N/A (Consultoría) |
| **Ventajas** | Evade heurísticas de bots | Cobertura omnicanal | Data e-commerce | Flujo de trabajo IA nativo | Control PPC granular | Ventas directas WA | Estrategia integral SEO/GEO|
| **Desventajas**| Frágil, Unicanal | Costoso, Opaco | Curva aprendizaje alta | Específico a Meta | UX abrumadora | No gestiona Ads de origen | Caro, no es software puro |

---

## 4. Feature Gap Analysis

**Qué funcionalidades tienen ellos y yo no:**
*   **Multicanalidad:** Integración profunda con Google Ads, TikTok Ads y Amazon Ads. Hoy un e-commerce rara vez depende 100% de Meta.
*   **Inteligencia Creativa Generativa:** Creación y A/B testing automatizado de creatividades (Imágenes/Videos) integrando modelos como DALL-E 3, Midjourney o Runway.
*   **Infraestructura Cloud Nativa y Segura:** Multitenancy real a nivel de base de datos (Row Level Security), auto-scaling de workers.
*   **Self-Serve Onboarding:** Suscripción vía Stripe, onboarding OAuth en 3 clics.

**Qué tengo yo y ellos no:**
*   **Algoritmo explícito de "Anti-Detección" (Human-mimicking):** Pausas forzadas de 12-75s entre llamadas al API para evitar heurísticas de "bot". Es una característica gris (*gray-hat*), pero un dolor inmenso de las agencias latinas que pierden sus BM (Business Managers) a diario.

**Qué funcionalidades debería copiar INMEDIATAMENTE:**
*   Editor interactivo de anuncios ANTES de aprobarlos (ya figura en tu roadmap, la prioridad debe ser absoluta P0).
*   Conexión a Google Ads (para no depender de un solo ecosistema cerrado).

**Qué funcionalidades NO debería copiar:**
*   Dashboards abrumadores de 500 métricas como los de Optmyzr. El valor de LejoVista radica en la simplicidad: "La IA propone, tú haces clic en Aprobar o Rechazar".

**Qué funcionalidades podrían convertirse en una ventaja competitiva:**
*   El **"Modo Copiloto"**. El mercado latinoamericano de pymes teme profundamente perder dinero si la IA actúa sola. Vender LejoVista como "El Trafficker Senior que te consulta cada peso que va a gastar" puede vencer a los agentes autónomos gringos.

---

## 5. Auditoría UX

*   **Flujo de navegación:** **MEJORADO.** Unificado y limpio. El Master Brief se renderiza nativamente en el panel de control Next.js eliminando el iframe.
*   **Carga cognitiva:** Muy bien resuelta en la "Cola de acciones" (semáforos 🟢🟡🔴). El problema es que el usuario *no puede editar* la acción. Si al texto le falta una tilde, el usuario debe rechazar toda la acción y el esfuerzo de la IA fue en vano.
*   **Feedback:** Problemático y causará fricción. Debido a las pausas forzadas de ~50s entre ejecuciones al API, el frontend parecerá "congelado". Es vital implementar los WebSockets/SSE del roadmap ya mismo.
*   **Jerarquía visual / UI:** Next.js + Glassmorphism da una excelente percepción de calidad y modernidad.

---

## 6. Auditoría Técnica

*   **Arquitectura (Overengineering vs Hosting):** Tienes una arquitectura digna de una Serie A (FastAPI, Redis, Celery, PostgreSQL, Next.js) intentando correr en el hardware de un blog personal (VPS HostGator). Un VPS económico se quedará sin RAM (OOM kill) al gestionar conexiones concurrentes, Redis y Celery Workers.
*   **Seguridad:** Guardar el Token OAuth de la cuenta encriptado con AES-256 es bueno. Se solucionó el riesgo crítico de token leakage por query parameter en URLs y el uso de iframes inseguros.
*   **Mantenibilidad:** **MEJORADO.** Se dinamizó la regla de CPL para basarse en el promedio histórico del snapshot de la cuenta, lo cual reduce el acoplamiento a valores hardcodeados de 3.50.
*   **Cuellos de botella (Deuda Técnica):** El `MetaAdsSyncService` serializado y pausado intencionalmente. Si logras 50 clientes con 10 campañas cada uno, y cada query tiene 30s de pausa, un "Sync diario" jamás terminará a tiempo en la mañana para el reporte de las 8:00 AM. La lentitud artificial aniquila la escalabilidad computacional.

---

## 7. Riesgos Identificados

*   **Tecnológicos:** Un cambio en los endpoints de la API Graph de Meta o el deprecation de las métricas que descargas destruirá el flujo.
*   **Legales/Compliance (Crítico):** Las pausas anti-detección (12-75s) violan tácitamente las Políticas de Uso de la API de Meta si el objetivo explícito es burlar los controles de automatización (rate limiting/bot detection). Si Meta audita el App ID, lo baneará a nivel global destruyendo la empresa en un segundo.
*   **Seguridad:** En caso de brecha en el VPS HostGator por mala configuración de puertos o vulnerabilidad de OS (tienes root access, el riesgo recae en ti), los Access Tokens de los clientes quedarán expuestos.
*   **Escalabilidad/Costos:** Mantener Celery Workers durmiendo (`time.sleep()`) cuesta ciclos de CPU y RAM caros. El modelo actual no es costo-eficiente para un SaaS.

---

## 8. Errores Críticos (Showstoppers)

Estos errores impedirían lanzar la plataforma comercialmente el día de hoy:

| Problema | Nivel | Impacto | Probabilidad | Cómo solucionarlo | Prioridad |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auth Token en URL (Iframe)** | **RESUELTO** | - | - | Eliminado por completo. Conexión nativa JWT. | - |
| **Infraestructura HostGator VPS** | Alto | Alto | Muy Alta | Migrar base de datos a un servicio gestionado (Supabase/Neon), backend a Render/AWS y frontend a Vercel. | P0 |
| **Edición Ciega de Copys** | Alto | Medio | Alta | Crear componente UI tipo 'Draft' para modificar el JSON/texto del anuncio antes de lanzar el POST a Meta. | P0 |
| **Workers bloqueados por Pausas** | Medio | Alto | Alta | Si usas `time.sleep()` en un worker síncrono de Celery bloqueas el thread. Asegurar uso de async/await y retries controlados. | P1 |

---

## 9. Roadmap de Decisiones

*   **Qué corregir antes del lanzamiento (Must Have):**
    *   Migrar toda la infraestructura fuera de HostGator.
    *   Añadir editor previo a la aprobación de la cola de acciones de IA.
*   **Qué puede esperar (Should Have):**
    *   Integración de pasarela de pagos (Stripe) (se puede vender manual al inicio).
    *   WebSockets para notificaciones (puedes usar polling temporalmente de 5s, aunque no es ideal).
*   **Qué moverías a futuras versiones (Could Have):**
    *   Integración Google Ads (Concéntrate en que el flujo de Meta funcione perfecto primero).
    *   Análisis de historial comparativo multimes.
*   **Qué jamás construiría (Won't Have):**
    *   Algoritmos "black-hat" para evadir revisiones manuales de Meta, generadores de spam publicitario. Enfocarse en automatización White-hat.

---

## 10. Oportunidades

*   **Oportunidad Latam (B2B):** Vender el software a agencias locales de marketing que tienen rotación altísima de analistas ("Traffickers"). "LejoVista es el Analista Senior que trabaja 24/7 y nunca se equivoca con el presupuesto".
*   **Monetización B2B Híbrida:** En lugar de cobrar un SaaS de \$50 USD (competencia de precio), cobra un *setup fee* de \$500 USD (que cubra tu onboarding manual) + un *fee* mensual del 1% al 3% de la inversión publicitaria optimizada.
*   **IA de Creación de Activos:** Si le añades integración con DALL-E para crear la imagen junto con el texto, la percepción de valor de la herramienta se multiplicará por 10.

---

## 11. Crítica Brutal del Inversor

Imaginando un Pitch por 5 Millones de dólares:

> "Si me pides 5 millones por esto, mi respuesta es **NO**. Lo que me presentas no es un negocio SaaS escalable, es un script en Python sobredimensionado empaquetado con Next.js.
>
> Me sigue preocupando la dependencia de un *hack* (las pausas de 50 segundos) para simular ser un humano. Estás construyendo tu imperio en tierras de Meta intentando burlar a sus guardias. Eso no es un feature diferencial, es un riesgo existencial masivo.
> Además, tu arquitectura técnica demuestra inexperiencia en operaciones. Montaste tecnología empresarial (Redis, Celery) en el hosting más básico del mundo. Es poner un motor de Fórmula 1 en un carro de supermercado; se va a romper apenas aceleres.
>
> **¿Qué cambiaría inmediatamente?** Te obligaría a salir del mercado gris. Juega bajo las reglas de la API oficial. Solicita cuotas legítimas y saca las pausas manuales.
> **¿Qué agregaría?** Arquitectura serverless/PaaS robusta y un enfoque omnicanal. Demuéstrame que esto funciona en Google Ads y volvemos a conversar."

---

## 12. Decisión Final

⚠ **Publicar como Beta cerrada**

**Explicación:** Tras solucionar la brecha de seguridad del token en la URL, remover el iframe por un renderizado de UI nativo y dinamizar el cálculo de CPL, el MVP ha subido de nivel. Ahora es apto para una Beta Cerrada con los primeros clientes seleccionados para monitorear el comportamiento real de los workers y la estabilidad en producción, antes de migrar la infraestructura definitiva de hosting.

---

## 13. Plan para llegar a 100/100 (Top Mejoras de ROI)

Para convertir esta herramienta en un producto 100/100, se debe ejecutar el siguiente roadmap ordenado por Retorno de Inversión (Impacto vs Esfuerzo):

| # | Mejora | Impacto (1-10) | Esfuerzo (1-10) | Relación ROI | Tiempo Estimado | Dependencias | Prioridad |
| :-: | :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| 1 | **[RESUELTO]** Eliminar Token de URL y erradicar Iframe. Pasar datos vía API. | 10 | 3 | **3.33** | - | - | - |
| 2 | Reestructurar pausas Meta Ads; usar celery queues async reales para no bloquear workers. | 9 | 3 | **3.00** | 3 Días | Ninguna | **P0** |
| 3 | Migrar base de datos y backend fuera de HostGator VPS a PaaS (AWS/Render/Neon). | 10 | 5 | **2.00** | 1 Semana | Ninguna | **P0** |
| 4 | Desarrollar UI para Edición de Copys antes de aprobar acción de la IA. | 9 | 5 | **1.80** | 1 Semana | Ninguna | **P0** |
| 5 | **[RESUELTO]** Refinar Capa 1 de IA: Reemplazar variables hardcodeadas de CPL por promedios históricos dinámicos. | 8 | 7 | **1.14** | - | - | - |
| 6 | Integrar WebSockets/SSE para barra de carga en ejecución de campañas. | 7 | 6 | **1.16** | 2 Semanas | Infraestructura | **P1** |
| 7 | Implementar Self-Serve Onboarding con pagos en Stripe. | 8 | 8 | **1.00** | 3 Semanas | Ninguna | **P2** |
| 8 | Integración de Generación de Imágenes (DALL-E) para campañas creativas. | 9 | 9 | **1.00** | 4 Semanas | Editor UI (#4) | **P2** |
| 9 | Integración básica con Reportes y Acciones de Google Ads API. | 10 | 10 | **1.00** | 5 Semanas | Core Multi-ad | **P3** |
