# GUÍA PASO A PASO: Despliegue de DFSha en AWS Academy

Esta guía detalla el procedimiento exacto para desplegar el clúster **DFSha** en **AWS Academy Learner Lab**, cumpliendo con los requisitos de la **Sección 5.1** de la guía oficial de la materia ("Desplegar en máquinas virtuales de AWS Academy; demostrar comunicación real entre nodos por red").

---

## 1. Configuración de la Instancia en AWS Academy

### 1.1 Iniciar el Laboratorio
1. Entra a **AWS Academy Learner Lab** y haz clic en **Start Lab**.
2. Espera a que el círculo junto a AWS se ponga en verde (`Lab status: ready`).
3. Abre la consola de AWS haciendo clic en **AWS**.

### 1.2 Configurar Security Group
En la consola de EC2, ve a **Security Groups** -> **Create security group**:
- **Nombre:** `dfsha-sg`
- **Reglas de Entrada (Inbound Rules):**
  | Tipo | Protocolo | Puerto | Origen (Source) | Descripción |
  | :--- | :--- | :--- | :--- | :--- |
  | **SSH** | TCP | `22` | `0.0.0.0/0` (o tu IP) | Acceso remoto terminal |
  | **Custom TCP** | TCP | `18443` | `0.0.0.0/0` | Gateway HTTPS de DFSha |

### 1.3 Lanzar Instancia EC2
Ve a **EC2** -> **Launch Instance**:
- **Nombre:** `DFSha-Cluster`
- **AMI:** `Ubuntu Server 24.04 LTS` o `Ubuntu Server 22.04 LTS` (64-bit x86).
- **Tipo de Instancia:** `t3.medium` (2 vCPU, 4 GiB RAM) o `t3.large` (2 vCPU, 8 GiB RAM).
- **Key Pair:** Selecciona o crea tu par de claves (`.pem`).
- **Network Settings:** Asigna el Security Group `dfsha-sg`.
- **Almacenamiento:** Ampliar a al menos **25 GiB** (para soportar imágenes Docker, contenedores y la prueba de 1 GiB).
- Clic en **Launch Instance**.

---

## 2. Despliegue Automatizado en el Servidor EC2

Una vez la instancia esté en estado `Running`, conéctate por SSH:

```bash
# Reemplaza con tu archivo .pem y la IP pública de tu instancia
ssh -i "tu-llave.pem" ubuntu@<IP_PUBLICA_EC2>
```

### 2.1 Clonar o Transferir el Código
Puedes clonar tu repositorio GitHub o transferir la carpeta del proyecto vía SCP:

```bash
# Opción A: Vía Git
git clone https://github.com/tu-usuario/DFSha.git
cd DFSha

# Opción B: Si transfieres la carpeta desde Windows con scp
# scp -i tu-llave.pem -r DFSha/ ubuntu@<IP_PUBLICA_EC2>:~/DFSha
```

### 2.2 Ejecutar el Script de Despliegue Automatizado
El script `scripts/deploy_aws.sh` se encarga de todo:
1. Instala Docker, Docker Compose y Python.
2. Consulta el servicio de metadatos de AWS (IMDSv2) para detectar la **IP Pública**, **IP Privada** y **DNS Público**.
3. Genera la infraestructura de certificados X.509 (PKI) inyectando la IP pública de AWS en el Subject Alternative Name (SAN) del Gateway.
4. Levanta el clúster completo con Docker Compose (`dfsha5`).
5. Verifica la salud del clúster.

Ejecútalo con:

```bash
chmod +x scripts/deploy_aws.sh
./scripts/deploy_aws.sh
```

Al finalizar verás la confirmación:
```
==========================================================
   ¡DFSha está desplegado y saludable en AWS!             
==========================================================
URL Pública de Acceso: https://<IP_PUBLICA_EC2>:18443/docs
Certificado CA para Clientes: /home/ubuntu/DFSha/runtime/trust/ca.crt
```

---

## 3. Demostración y Pruebas Remotas desde tu Máquina (Windows / macOS)

Para demostrar comunicación real por red (WAN/Internet) hacia AWS como exige la rúbrica:

### 3.1 Descargar el Certificado CA a tu Máquina Local
En tu PowerShell local:
```powershell
# Descarga ca.crt desde AWS EC2
scp -i "tu-llave.pem" ubuntu@<IP_PUBLICA_EC2>:~/DFSha/runtime/trust/ca.crt ./aws_ca.crt
```

### 3.2 Probar Conectividad TLS
```powershell
# Comprobar que responde con verificación TLS estricta (sin -k ni verify=False)
curl.exe --cacert ./aws_ca.crt --ssl-revoke-best-effort https://<IP_PUBLICA_EC2>:18443/health/ready
```
Respuesta esperada:
```json
{"status":"ready"}
```

### 3.3 Abrir la Interfaz de la API en el Navegador
1. Puedes acceder desde tu navegador a:
   `https://<IP_PUBLICA_EC2>:18443/docs`
2. Si tu navegador advierte sobre la autoridad certificadora, es debido a que la CA es privada del laboratorio. Si importas `aws_ca.crt` en tu almacén de certificados de confianza de Windows ("Entidades de certificación raíz de confianza"), el candado aparecerá en verde.

### 3.4 Ejecutar Benchmark sobre la Red Real de AWS
Desde tu máquina local (o desde una segunda instancia EC2 para medir dentro de la VPC de AWS):

```powershell
$env:TLS_CA = (Resolve-Path ./aws_ca.crt).Path
python -m dfsha.client --help

# Ejecutar el benchmark remoto:
python scripts/benchmark.py --url https://<IP_PUBLICA_EC2>:18443 --sizes-mib 4 16 --clients 1 2 --output evidencias/benchmark-aws-normal.csv
```

---

## 4. Prueba de Fallo en Vivo en AWS (Demostración de Tolerancia a Fallos)

Para grabar en el video o demostrar al profesor:

1. **Detener DataNode 1 en AWS:**
   ```bash
   docker stop dfsha5-dn1-1
   ```
2. **Ejecutar benchmark degradado desde el cliente:**
   ```powershell
   python scripts/benchmark.py --url https://<IP_PUBLICA_EC2>:18443 --sizes-mib 4 16 --clients 1 2 --condition dn1_detenido --output evidencias/benchmark-aws-degradado.csv
   ```
3. **Observar la recuperación automática:**
   En la instancia EC2 revisa los logs del worker de autoreparación:
   ```bash
   docker compose logs -f repair
   ```
   Observarás cómo detecta réplicas faltantes y las re-crea en los otros DataNodes.

---

## 5. Resumen de Archivos para AWS en el Repositorio

- [`scripts/setup_aws_pki.py`](file:///c:/Users/teoac/Desktop/DFSha/scripts/setup_aws_pki.py): Generador de PKI con soporte para IPs públicas y dominios de AWS.
- [`scripts/deploy_aws.sh`](file:///c:/Users/teoac/Desktop/DFSha/scripts/deploy_aws.sh): Instalador y orquestador desatendido para instancias EC2 Ubuntu.
- [`docs/GUIA_DESPLIEGUE_AWS.md`](file:///c:/Users/teoac/Desktop/DFSha/docs/GUIA_DESPLIEGUE_AWS.md): Esta guía.
