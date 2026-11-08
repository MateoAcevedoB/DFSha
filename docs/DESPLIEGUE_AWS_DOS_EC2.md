# DFSha en dos instancias EC2

La configuración de esta prueba coloca el gateway, el control, etcd y `dn1` a `dn3` en una EC2 A. `dn4` corre en una EC2 B de la misma VPC. El gateway del despliegue de prueba escucha en `127.0.0.1:19443` de A.

## Preparación

1. Copia el repositorio a un directorio nuevo en ambas instancias. Instala Docker y Docker Compose en las dos. La EC2 A también necesita Python 3 con `cryptography` para generar certificados.
2. En los grupos de seguridad, permite TCP 8000 en B desde la IP privada de A y TCP 8101–8103 en A desde la IP privada de B. Limita SSH a los orígenes administrativos autorizados.
3. En las dos instancias, desde la raíz del proyecto, genera la configuración con `python3 scripts/setup_aws_two_ec2.py IP_PRIVADA_A IP_PRIVADA_B`.
4. Genera la CA y los certificados **una vez** en A con `python3 scripts/setup_aws_pki.py 127.0.0.1`. Si reanudas el mismo despliegue, conserva el directorio `runtime`.
5. Por SSH, copia de A a B únicamente `runtime/tls/dn4/server.crt`, `runtime/tls/dn4/server.key`, `runtime/trust/ca.crt`, `runtime/secrets/capability.key` y `runtime/secrets/receipt/dn4.key`. Usa las mismas rutas relativas en B. Conserva `runtime` con permisos 700. Dentro de ese directorio, los directorios montados `runtime/tls/dn4` y `runtime/trust` necesitan permisos 755 y los archivos montados permisos 644 para que el usuario del contenedor pueda leerlos.

## Arranque

En cada instancia, construye una vez la imagen con `sudo docker build -t dfsha-demo:local .`. Después ejecuta el comando correspondiente desde la raíz del proyecto.

```bash
# EC2 A
sudo docker compose --project-directory . -p dfsha-ec2-demo \
  -f runtime/compose.aws-control.json up -d --no-build --wait

# EC2 B
sudo docker compose --project-directory . -p dfsha-ec2-demo \
  -f runtime/compose.aws-storage.json up -d --no-build --wait
```

## Prueba de recuperación

En A, ejecuta la fase inicial y revisa en B que sus registros contengan los bloques que la prueba reporta en `BLOCKS_CONFIRMED_ON_DN4`. Después detén **solo la EC2 B** desde la consola de AWS, espera a que muestre `stopped` y ejecuta la fase final en A.

```bash
# En A, con las dos EC2 activas
sudo docker exec -i dfsha-ec2-demo-cn1-1 python - before < scripts/aws_demo_probe.py

# En B, antes de detenerla
sudo docker logs dfsha-ec2-demo-dn4-1

# En A, con B en estado stopped
sudo docker exec -i dfsha-ec2-demo-cn1-1 python - after < scripts/aws_demo_probe.py
```

El resultado esperado es `MATCH True`. Después inicia B y comprueba desde A que `curl --cacert runtime/trust/ca.crt https://127.0.0.1:19443/data/dn4/health/live` vuelva a responder.
