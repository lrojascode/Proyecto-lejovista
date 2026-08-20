# 02 · Panel de administración: alta de clientes y módulos

## Contexto

Pediste sugerencias, así que este archivo es más de diseño que de tareas. La
pregunta de fondo: *¿cómo doy de alta un cliente y le prendo o apago módulos sin
tocar la base a mano?*

**Lo que ya existe y hay que reusar:**

| Pieza | Estado |
|---|---|
| RBAC por organización (`ADMIN` / `OPERATOR` / `VIEWER`) | ✅ `OrganizationUser.role` + `require_role` con jerarquía |
| Invitaciones por email con token de un solo uso | ✅ tabla `invitations`, endpoints y pantalla de aceptación |
| Marca por organización (`display_name`, `logo_url`, `accent_color`) | ✅ columnas ya creadas |
| Configuración por empresa (reglas de campaña, umbrales de IA) | ✅ UI en Settings |
| Runbook de alta de cliente | ✅ `docs/runbooks/alta-nuevo-cliente.md` |

**Lo que NO existe** (verificado, cero referencias en el backend):

- Cualquier noción de **administrador de plataforma**. Todos los roles son
  *dentro* de una organización; nadie puede crear organizaciones desde la app.
- Cualquier noción de **módulo habilitable**.

O sea: la mitad del trabajo ya está hecha, y lo que falta son dos conceptos
nuevos bien acotados.

---

## 1 · El rol que falta: administrador de plataforma

Hoy `require_role("ADMIN")` significa *"admin de esta organización"*. Un ADMIN de
Cliente A no debe poder crear Cliente B ni ver sus datos.

### Tres formas de resolverlo

| Opción | Cómo | Veredicto |
|---|---|---|
| **(a) Flag en `User`** | `is_platform_admin: bool`, y un `require_platform_admin` nuevo | **Recomendada.** Una columna, una dependencia, nada que puedas confundir con el RBAC de tenant |
| (b) Organización "plataforma" | Una org especial cuyos ADMIN son superadmins | Reusa el modelo, pero mezcla dos conceptos: un bug en `require_role` se vuelve escalada de privilegios |
| (c) Solo CLI/runbook | Sin UI; se sigue haciendo por script | Es lo de hoy. Funciona, pero no escala ni delega |

**Recomiendo (a)**, y con una regla dura: **el flag no da acceso a los datos de
los tenants.** Un platform admin puede crear organizaciones, invitar al primer
ADMIN y prender/apagar módulos. **No** puede leer campañas, gastos ni el chat de
un cliente. Si algún día hace falta soporte técnico con acceso a datos, eso es
un mecanismo aparte —impersonación explícita, con registro y con aviso al
cliente—, no un permiso permanente.

Esto importa porque el aislamiento por organización es una de las cuatro reglas
innegociables del proyecto. Un rol que la saltea en silencio la deroga de hecho.

### Alta de un cliente, de punta a punta

```
Panel /admin → "Nuevo cliente"
  ├── nombre visible, slug, rubro
  ├── marca: logo y color de acento
  ├── módulos habilitados (checkboxes)
  └── email del primer ADMIN
        └── reusa la INVITACIÓN que ya existe → el cliente pone su propia clave
```

Nadie escribe la contraseña del cliente. La invitación ya está construida y
resuelve exactamente esto.

El signup público **no cambia**: sigue fundando organizaciones nuevas y sigue
rechazando slugs existentes.

---

## 2 · Módulos habilitables

### Modelo

Una columna `enabled_modules` (JSON) en `Organization`, mismo patrón que
`campaign_rules_overrides`: **vacío o ausente = todos habilitados**, así ninguna
organización existente cambia de comportamiento hasta que alguien la toque.

Catálogo cerrado, definido en el backend como fuente de verdad (no en el
frontend, o se desincronizan):

```python
AVAILABLE_MODULES = {
    "resumen":      "Resumen / dashboard",
    "campanas":     "Campañas",
    "chat":         "Chat analista",
    "chat_creacion":"Creación de campañas por chat",
    "reporte":      "Reporte ejecutivo",
    "diagnosticos": "Diagnósticos de IA",
    "acciones":     "Acciones sugeridas",
    "ai_usage":     "Consumo de IA",
    "settings":     "Configuración",
}
```

Separar `chat` de `chat_creacion` a propósito: hay clientes que van a querer
preguntar sin poder crear. Hoy eso ya está medio resuelto por el modo ASK/BUILD y
el rol OPERATOR — el módulo es la tercera llave, la comercial.

### Aplicación en tres capas

Repito lo del [01](01-marca-y-vista-cliente.md) porque es donde se suele fallar:

1. **Menú** — se filtra la lista.
2. **Ruta** — el layout redirige si la pantalla no está habilitada.
3. **API** — una dependencia `require_module("reporte")` en los routers.

**Solo la 3 es seguridad.** Las otras dos son experiencia de usuario. Si se
implementan solo las dos primeras, el módulo está "oculto", no apagado.

### Un endpoint que el frontend necesita

`GET /organizations/my/modules` → lista de módulos habilitados de la organización
activa. El layout lo pide una vez y arma el menú. Sin esto, el frontend tendría
que adivinar o hardcodear.

---

## 3 · Qué NO haría (y por qué lo menciono)

- **Permisos por usuario dentro del cliente.** Ya existe el RBAC de tres roles y
  alcanza. Sumar permisos finos ahora multiplica los estados a testear sin que
  nadie lo haya pedido.
- **Un sistema de planes/suscripciones.** El modelo de negocio es cobro por
  implementación, no mensualidad. Los módulos son configuración por cliente, no
  un tier comercial. Si algún día hay planes, un plan se puede expresar como un
  preset de `enabled_modules`.
- **Panel de admin como app separada.** Una ruta `/admin` en la misma app, gateada
  por el flag, cuesta muchísimo menos y comparte sesión, tipos y componentes.

---

## 4 · Sugerencia de secuencia

```
1. Columna enabled_modules + AVAILABLE_MODULES + GET /my/modules
   → habilita YA la vista recortada del 01, sin panel todavía
2. Flag is_platform_admin + require_platform_admin + require_module
3. Pantalla /admin: listar organizaciones, crear una, editar sus módulos y marca
4. Alta completa con invitación al primer ADMIN
```

Después del paso 1 ya podés recortar la vista del cliente configurando la
organización a mano una vez. El panel es comodidad; el modelo es lo que
desbloquea.

---

## Verificación

- Un ADMIN de Cliente A **no** puede llamar a ningún endpoint de `/admin` (403).
- Un platform admin puede crear una organización y **no** puede leer sus campañas.
- Apagar `reporte` en Cliente A: desaparece del menú, la URL directa redirige, y
  `GET /reports/...` responde 403.
- Una organización sin `enabled_modules` configurado se comporta **exactamente**
  como hoy.
- La invitación al primer ADMIN crea `User` + `OrganizationUser` con rol ADMIN.
