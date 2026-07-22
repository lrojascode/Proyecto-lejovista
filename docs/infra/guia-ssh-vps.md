# Guía de conexión SSH al VPS (agenciakibo.com)

Cómo conectarse por SSH al VPS que aloja Coolify, n8n, Evolution API, lejovista y el
CRM (wacrm).

## Datos de conexión

| Dato | Valor |
|---|---|
| Host / IP | `69.6.220.199` |
| Usuario | `root` |
| Puerto | `22022` (no el 22 por defecto) |
| Hostname | `vps-1803332.agenciakibo.com` |

> 🔒 **La contraseña NO se guarda en este repositorio.** Está en el gestor de
> contraseñas del equipo. Este documento describe cómo conectarse, no las claves.

## Conexión

### Con contraseña (método actual)
```bash
ssh -p 22022 root@69.6.220.199
```
Pedirá la contraseña (la del gestor de contraseñas).

### Atajo con alias (opcional)
Añadir a `~/.ssh/config` en tu máquina:
```
Host agenciakibo
    HostName 69.6.220.199
    User root
    Port 22022
```
Luego basta con:
```bash
ssh agenciakibo
```

## Migrar a llave SSH (recomendado)

Más seguro que la contraseña (el server registró cientos de intentos de fuerza
bruta). Una sola vez, desde tu máquina:

1. Generar un par de llaves (si no tienes uno):
   ```bash
   ssh-keygen -t ed25519 -C "agenciakibo-vps"
   ```
2. Copiar la llave pública al servidor:
   ```bash
   ssh-copy-id -p 22022 root@69.6.220.199
   ```
3. Probar que entra sin contraseña:
   ```bash
   ssh -p 22022 root@69.6.220.199
   ```
4. (Endurecimiento, opcional) una vez confirmado el acceso por llave, deshabilitar
   el login por contraseña en `/etc/ssh/sshd_config` (`PasswordAuthentication no`) y
   `systemctl reload sshd`. Esto encaja con el hardening del plan de migración
   (`fail2ban` + `firewalld`).

## Si la host key cambió (aviso de "possible MITM")

Ocurre si el servidor se reinstala o cambia su llave de host. No es un ataque en ese
caso: limpiar la entrada vieja y reconectar.
```bash
ssh-keygen -R "[69.6.220.199]:22022"
ssh -p 22022 root@69.6.220.199
```

## Acceso al panel de Coolify

Una vez instalado Coolify (ver plan de migración), su panel web queda en:

- **https://coolify.agenciakibo.com**

Desde ahí se gestionan las 4 apps (n8n, Evolution API, lejovista, wacrm) sin
necesidad de entrar por SSH para la operación diaria — el SSH queda para
mantenimiento del servidor.

## Referencias

- Plan de migración: [plan-migracion-coolify.md](plan-migracion-coolify.md)
