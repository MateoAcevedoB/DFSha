#!/usr/bin/env bash
# ==============================================================================
# Script de Despliegue Automatizado para AWS Academy (EC2)
# Proyecto 1: DFSha - Sistemas Distribuidos 2026-2
# ==============================================================================
set -euo pipefail

echo "=========================================================="
echo "    Iniciando despliegue de DFSha en AWS EC2             "
echo "=========================================================="

# 1. Instalar dependencias base si no existen
echo "[1/6] Verificando e instalando Docker y Python..."
if ! command -v docker &> /dev/null; then
    echo "Instalando Docker Engine..."
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl gnupg lsb-release python3 python3-pip
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker "$USER" || true
fi

# Instalar librería de criptografía para la PKI
pip3 install --quiet cryptography || pip install --break-system-packages --quiet cryptography || true

# 2. Consultar metadatos de AWS (IMDSv2) para obtener IP Pública y DNS
echo "[2/6] Detectando direcciones IP públicas y privadas de AWS..."
TOKEN=$(curl -s -S -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || echo "")

PUBLIC_IP=""
PRIVATE_IP=""
PUBLIC_DNS=""

if [ -n "$TOKEN" ]; then
    PUBLIC_IP=$(curl -s -S -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "")
    PRIVATE_IP=$(curl -s -S -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4 2>/dev/null || echo "")
    PUBLIC_DNS=$(curl -s -S -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-hostname 2>/dev/null || echo "")
fi

# Fallback si no está en IMDSv2
if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP=$(curl -s https://ifconfig.me || echo "127.0.0.1")
fi
if [ -z "$PRIVATE_IP" ]; then
    PRIVATE_IP=$(hostname -I | awk '{print $1}')
fi

echo "  -> IP Pública: $PUBLIC_IP"
echo "  -> IP Privada: $PRIVATE_IP"
echo "  -> DNS Público: ${PUBLIC_DNS:-'(no asignado)'}"

# 3. Generar certificados PKI con SAN para AWS
echo "[3/6] Generando PKI y certificados con SAN para AWS..."
python3 scripts/setup_aws_pki.py "$PUBLIC_IP,$PRIVATE_IP" "${PUBLIC_DNS:-}"

# 4. Asegurar permisos en carpetas runtime
echo "[4/6] Configurando permisos locales..."
chmod 700 runtime || true
chmod 644 runtime/trust/ca.crt || true

# 5. Construir y levantar contenedores con Docker Compose
echo "[5/6] Construyendo y levantando contenedores con Docker Compose..."
docker compose build
docker compose up -d

# 6. Esperar a que el cluster esté listo
echo "[6/6] Esperando a que el clúster alcance estado saludable (healthy)..."
MAX_ATTEMPTS=30
ATTEMPT=0
HEALTH_URL="https://127.0.0.1:18443/health/ready"

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    ATTEMPT=$((ATTEMPT + 1))
    if curl --cacert runtime/trust/ca.crt -s -f "$HEALTH_URL" > /dev/null 2>&1; then
        echo ""
        echo "=========================================================="
        echo "   ¡DFSha está desplegado y saludable en AWS!             "
        echo "=========================================================="
        echo "URL Pública de Acceso: https://$PUBLIC_IP:18443/docs"
        echo "Certificado CA para Clientes: $(pwd)/runtime/trust/ca.crt"
        echo ""
        echo "Para probar desde tu máquina local (Windows / macOS / Linux):"
        echo "  1. Copia runtime/trust/ca.crt a tu máquina."
        echo "  2. En PowerShell: "
        echo "     `$env:TLS_CA = 'ruta/a/ca.crt'"
        echo "     curl.exe --cacert `$env:TLS_CA https://$PUBLIC_IP:18443/health/ready"
        echo "=========================================================="
        exit 0
    fi
    echo -n "."
    sleep 3
done

echo ""
echo "ADVERTENCIA: El servicio tardó más de lo esperado en responder. Verifica con: docker compose ps"
