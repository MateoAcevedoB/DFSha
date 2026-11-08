# Evidencia de comunicación y recuperación en AWS Academy

**Fecha** 8 de noviembre de 2026, hora de Bogotá (UTC−5)  
**Región** `us-east-1`

Los identificadores y las direcciones privadas se abreviaron en esta copia pública.

| Instancia EC2 | ID | IP privada | Servicios de la prueba |
| --- | --- | --- | --- |
| DFSha-Cluster-AWS | `i-…c5e52` | `172.31.x.A` | Gateway, nodos de control, etcd y `dn1`–`dn3` |
| DFSha-Demo-DN4 | `i-…f81aa` | `172.31.x.B` | Nodo de datos `dn4` |

## Comunicación entre instancias

A las **16:46:33**, desde la instancia de control se cargó `/two-ec2-1.bin`, un archivo de **32 MiB** dividido en **8 bloques**. La descarga inicial produjo el mismo SHA-256 que el archivo original. El manifiesto de descarga identificó **6 bloques** con una réplica en `dn4`.

En la EC2 de `dn4`, los registros del contenedor mostraron solicitudes `PUT` desde la IP privada de la instancia de control. Estas dos líneas corresponden a bloques identificados en el manifiesto de esa carga.

```text
172.31.x.A:48976 - "PUT /blocks/bb94586f2a39f83da2aaf8f65134b61257ff7d6bd18a19e593c5da791ad12f15?replica=true HTTP/1.1" 200 OK
172.31.x.A:52518 - "PUT /blocks/2d2bbc1eeba285ce7a959f4724fcb246fe0b7f151c92addadf1dfa20558da94f?replica=true HTTP/1.1" 200 OK
```

## Recuperación con la EC2 de almacenamiento detenida

A las **16:47:28**, AWS confirmó que la EC2 de `dn4` estaba en estado `stopped` y que la instancia de control seguía en `running`.

A las **16:48:03**, la ruta de salud de `dn4` devolvió **HTTP 504** desde el gateway. En esa misma ejecución, DFSha descargó de nuevo `/two-ec2-1.bin` usando los nodos restantes.

```text
SHA256_ORIGINAL  7dd43d00d553d03fa2baafefc89b218a18b29e4dd0df1abbd4653c162555713c
SHA256_BEFORE    7dd43d00d553d03fa2baafefc89b218a18b29e4dd0df1abbd4653c162555713c
SHA256_AFTER     7dd43d00d553d03fa2baafefc89b218a18b29e4dd0df1abbd4653c162555713c
MATCH True
```

## Restablecimiento

Se reinició la EC2 de almacenamiento. A las **16:48:41**, la ruta de salud respondió `{"node":"dn4","status":"alive","block_storage_implemented":true}`.

La prueba verifica la recuperación ante la caída de la EC2 de almacenamiento. El gateway y los metadatos permanecieron disponibles en la instancia de control durante el ensayo.
