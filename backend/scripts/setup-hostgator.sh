#!/bin/bash
# ══════════════════════════════════════════════════════════
# 🚀 LejoVista — Script de Configuración de VPS HostGator
# ══════════════════════════════════════════════════════════
#
# Instrucciones de uso:
# 1. Copiar este script al VPS: scp scripts/setup-hostgator.sh root@tu_ip:/tmp/
# 2. Entrar por SSH al VPS y ejecutar: chmod +x /tmp/setup-hostgator.sh && /tmp/setup-hostgator.sh

set -e # Terminar ejecución ante cualquier error

echo "=================================================="
echo "⚡ Configurando VPS HostGator para LejoVista"
echo "=================================================="

# 1. Actualizar repositorios e instalar paquetes base
echo "📦 Actualizando paquetes de Ubuntu/Debian..."
apt-get update && apt-get upgrade -y
apt-get install -y curl git ufw fail2ban certbot python3-certbot-nginx -y

# 2. Configurar Firewall de Seguridad (UFW)
echo "🔒 Configurando UFW Firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (para Certbot / redirect)
ufw allow 443/tcp   # HTTPS
ufw --force enable

# 3. Configurar e Habilitar Fail2ban (Anti Fuerza Bruta SSH)
echo "🛡 Habilitando Fail2ban..."
systemctl enable fail2ban
systemctl start fail2ban

# 4. Instalar Docker Engine y Docker Compose
echo "🐳 Instalando Docker + Docker Compose..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Instalar CLI de Docker Compose si no está disponible
if ! docker compose version &> /dev/null; then
    apt-get install -y docker-compose-plugin
fi

# 5. Crear usuario de sistema lejovista (no-root) por seguridad
echo "👤 Creando usuario no-privileged 'lejovista'..."
if ! id "lejovista" &>/dev/null; then
    adduser --disabled-password --gecos "" lejovista
    usermod -aG docker lejovista
fi

# 6. Crear archivo SWAP de 2GB (Crucial en VPS con 1-2GB RAM)
echo "💾 Configurando Memoria de Intercambio (SWAP)..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "SWAP configurada con éxito (2GB)."
else
    echo "SWAP ya configurada."
fi

# 7. Crear directorios de ejecución del proyecto
echo "📂 Creando carpetas del proyecto y backups..."
mkdir -p /opt/lejovista
mkdir -p /backups/lejovista
chown -R lejovista:lejovista /opt/lejovista
chown -R lejovista:lejovista /backups/lejovista

echo "=================================================="
echo "✅ VPS LISTO Y CONFIGURADO CON ÉXITO!"
echo "=================================================="
echo "Siguientes pasos:"
echo "1. Configurar los A Records en tu registrador DNS para tu dominio:"
echo "   - midominio.com -> IP_DEL_VPS"
echo "   - *.midominio.com -> IP_DEL_VPS"
echo "   - api.midominio.com -> IP_DEL_VPS"
echo ""
echo "2. Genera el certificado SSL wildcard con Certbot:"
echo "   certbot certonly --manual --preferred-challenges dns -d midominio.com -d *.midominio.com"
echo ""
echo "3. Copia el código a /opt/lejovista y ejecuta: docker compose -f docker-compose.prod.yml up -d"
echo "=================================================="
