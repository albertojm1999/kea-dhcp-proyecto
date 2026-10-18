# Imagen Docker de Kea (común a las propuestas 1 y 2)

| Archivo | Qué es |
|---|---|
| `dockerfile` | Debian 12 + Kea 2.6.5 (repo ISC de Cloudsmith) + `mariadb-client` + kea-exporter 0.7.1 en un venv |
| `kea-start.sh` | Espera a `eth0`, y lanza y vigila `kea-ctrl-agent`, `kea-exporter` (:9547) y `kea-dhcp4` |

Construir (la carpeta de trabajo original es `/home/alberto/Escritorio/kea-docker`):

```bash
cd kea-dhcp-proyecto/00-imagen-kea
docker build -t kea-dhcp -f dockerfile .
```

La imagen **no lleva configuración**. Cada propuesta carga la suya con su `activar.sh`:
- `../01-kea-ha/activar.sh`: propuesta 1, módulo HA.
- `../02-kea-rds/activar.sh`: propuesta 2, base de datos RDS compartida.

Puertos: 67/udp (DHCP), 8000/tcp (Control Agent), 8001/tcp (HA, solo en la propuesta 1), 9547/tcp (kea-exporter).

Si reconstruyes la imagen, los nodos de GNS3 ya creados no la usan hasta que se recrean (ver
`../03-monitorizacion/errores-y-soluciones.md` #3).
