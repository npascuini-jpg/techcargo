#!/bin/bash
# TechCargo — Setup automático en VPS Ubuntu
# Ejecutar como root: bash setup_vps.sh

set -e

echo "=== TechCargo VPS Setup ==="

# 1. Instalar Docker
if ! command -v docker &> /dev/null; then
    echo "[1] Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker $USER
else
    echo "[1] Docker ya instalado"
fi

# 2. Instalar Docker Compose
if ! command -v docker compose &> /dev/null; then
    echo "[2] Instalando Docker Compose..."
    apt-get install -y docker-compose-plugin
else
    echo "[2] Docker Compose ya instalado"
fi

# 3. Crear directorio de la app
echo "[3] Creando /opt/techcargo..."
mkdir -p /opt/techcargo
cd /opt/techcargo

# 4. Copiar archivos (deben estar en el mismo directorio)
echo "[4] Copiando archivos..."
cp -r $(dirname "$0")/* /opt/techcargo/ 2>/dev/null || true

# 5. Verificar .env
if [ ! -f .env ]; then
    echo ""
    echo "⚠️  IMPORTANTE: Creá el archivo /opt/techcargo/.env"
    echo "   Copiá .env.example y completá GOOGLE_CREDENTIALS_JSON"
    echo ""
    echo "   cat token.json | tr -d '\\n' → pegá ese output en .env"
    echo ""
    exit 1
fi

# 6. Editar Caddyfile (pedir dominio)
read -p "Ingresá tu dominio (ej: techcargo.tudominio.com): " DOMINIO
sed -i "s/tu-dominio.com/$DOMINIO/g" Caddyfile

# 7. Build y arrancar
echo "[5] Construyendo imágenes..."
docker compose build

echo "[6] Iniciando servicios..."
docker compose up -d

echo ""
echo "✅ TechCargo corriendo en https://$DOMINIO"
echo ""
echo "Ver logs:        docker compose logs -f"
echo "Ver QR del bot:  docker compose logs -f wpp"
echo "Reiniciar:       docker compose restart"
echo "Detener:         docker compose down"
