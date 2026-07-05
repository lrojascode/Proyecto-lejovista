# Exponer LejoVista a un socio vía túnel (ngrok)

Guía operativa para dar acceso temporal a la app desde internet — ej. para
que un socio cree su cuenta, conecte una cuenta real de Meta Ads y pruebe el
producto. No requiere dominio propio ni certificados: ngrok termina HTTPS por
vos.

Contexto técnico / por qué está armado así: ver la sección correspondiente en
`HANDOFF.md` y `posibles-mejoras.md` (Cloudflare Tunnel con `lrojas.dev` queda
documentado ahí como alternativa futura, una vez que el dominio esté
migrado a Cloudflare — hoy vive en name.com).

## 1. Reservar un dominio estático gratis en ngrok

1. Crear cuenta en https://ngrok.com (gratis, no pide tarjeta).
2. En el dashboard: **Domains → Create Domain** — reservá un dominio estático
   gratuito, ej. `tu-nombre.ngrok-free.app`. A diferencia del dominio
   aleatorio de `ngrok http 80` (que cambia cada vez que reiniciás el túnel),
   este queda fijo mientras tengas la cuenta.
3. Instalar el agente de ngrok y autenticarlo (`ngrok config add-authtoken <tu-token>`,
   el token está en el dashboard).

## 2. Configurar `.env`

Sumar/editar estas variables en el `.env` de la raíz del repo (no se tocan
las demás — el resto del stack sigue igual):

```bash
NEXT_PUBLIC_WS_URL=wss://tu-nombre.ngrok-free.app
NEXT_PUBLIC_SINGLE_HOST_MODE=true
TUNNEL_ORIGIN_REGEX=^https://tu-nombre\.ngrok-free\.app$
ENABLE_API_DOCS=false
```

Y confirmar que estos ya estén en modo "producción real" (no mock):

```bash
MOCK_SERVICES=false
```

## 3. Levantar el stack con el override de túnel

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d --build
```

Esto reconstruye el frontend con `NEXT_PUBLIC_SINGLE_HOST_MODE=true` (mismo
origen para API/WS, sin depender de subdominios) y monta
`nginx/conf.d-tunnel/tunnel.conf` en vez de `nginx/conf.d/default.conf` —
Nginx rutea `/api/` y `/ws/` al backend y todo lo demás al frontend, bajo el
único hostname que da ngrok.

## 4. Levantar el túnel

```bash
ngrok http --domain=tu-nombre.ngrok-free.app 80
```

Dejalo corriendo en una terminal aparte (o como servicio). La URL pública es
`https://tu-nombre.ngrok-free.app`.

## 5. Darle el link al socio

- Le pasás `https://tu-nombre.ngrok-free.app` directamente (no hace falta
  ningún subdominio tipo `cliente1.` — el modo single-host no lo usa).
- Se registra con **Regístrate aquí** (crea su propia organización).
- En **Configuración → Conectar Cuenta de Meta Ads** necesita:
  - El ID de su cuenta publicitaria (`act_XXXXXXXXX`).
  - Un **access token real de Meta** (Graph API Explorer o su Business
    Manager) — esto es responsabilidad suya, no algo que configure la app.
- Con eso conectado, el sync corre solo (dispara un `FULL` automático al
  conectar) y puede navegar el dashboard, `/reporte`, diagnósticos, etc.

## 6. Volver a modo local

Cuando termines la prueba, simplemente volvé a levantar sin el override:

```bash
docker compose up -d --build
```

Esto reconstruye el frontend sin `SINGLE_HOST_MODE` y Nginx vuelve a
`default.conf` (subdominios `*.localhost`). No hace falta revertir nada en
el código.

## Qué NO cubre esta guía (fuera de alcance)

- Migrar `lrojas.dev` a Cloudflare para tener subdominios por tenant reales
  (`cliente1.lrojas.dev`) — ver `posibles-mejoras.md`.
- Dejar esto corriendo de forma permanente / múltiples socios simultáneos —
  pensado para una prueba puntual con un solo tenant.
