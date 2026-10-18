# Kea DHCP — failover y monitorización

Sustitución de `isc-dhcp-server` por **ISC Kea 2.6** en un laboratorio GNS3 (8 routers MikroTik / OpenWrt / VyOS /
Cisco y 7 LAN), con las dos propuestas de failover del enunciado y su monitorización con **kea-exporter**:

- **Propuesta 1:** failover con el módulo de alta disponibilidad de Kea (`libdhcp_ha.so`, modo *hot-standby*).
- **Propuesta 2:** failover usando una base de datos MySQL en **AWS RDS** compartida por los dos servidores.
- **Monitorización:** kea-exporter → Prometheus → Grafana, con alertas.

Cada carpeta tiene su `guia-montaje.md` (los pasos seguidos) y su `errores-y-soluciones.md`.

```
kea-dhcp-proyecto/
├── 00-imagen-kea/        Imagen Docker de Kea (común a las dos propuestas)
├── 01-kea-ha/            Propuesta 1: failover con el módulo de alta disponibilidad
│   ├── guia-montaje.md   ← montaje completo desde cero (topología, RDS, GNS3, relays de los 7 routers…)
│   ├── errores-y-soluciones.md
│   ├── config/           backup: kea-1/ y kea-2/ (kea-dhcp4.conf, kea-ctrl-agent.conf, interfaces)
│   ├── gen-config.py
│   └── activar.sh
├── 02-kea-rds/           Propuesta 2: failover usando la base de datos RDS compartida
│   ├── guia-montaje.md
│   ├── errores-y-soluciones.md
│   ├── config/
│   ├── gen-config.py
│   └── activar.sh
└── 03-monitorizacion/    kea-exporter + Prometheus + Grafana
    ├── guia-montaje.md
    ├── errores-y-soluciones.md
    ├── imagenes/         Dockerfile de prometheus-gns3 y grafana-gns3
    └── config/           prometheus/, grafana/, red/
```

**Orden de lectura para montarlo de cero:** `00-imagen-kea` → `01-kea-ha/guia-montaje.md` (contiene la topología,
la base de datos, los nodos y los relays, que son comunes) → `02-kea-rds/guia-montaje.md` → `03-monitorizacion/guia-montaje.md`.

## Qué cubre del enunciado

| Requisito | Dónde | Estado |
|---|---|---|
| Failover usando el módulo de alta disponibilidad | `01-kea-ha` | Hecho y probado (caída del primario → el secundario toma el control en ~8 s; resincronización al volver) |
| Failover usando una base de datos RDS | `02-kea-rds` | Hecho y probado (con kea-1 caído, los 7 clientes conservan su IP gracias a kea-2) |
| Monitorización (kea-exporter) | `03-monitorizacion` | Hecho y probado (Prometheus recoge las métricas de los dos Kea, panel en Grafana, alerta de caída verificada) |

## Estado actual del laboratorio

- **Propuesta activa: 1 (HA).** Para cambiar:
  ```bash
  ./02-kea-rds/activar.sh     # pasar a la propuesta 2
  ./01-kea-ha/activar.sh      # volver a la propuesta 1
  ```
  Solo puede haber una activa a la vez, porque las dos usan los mismos contenedores `kea-1` (172.16.10.5) y `kea-2`
  (172.16.40.5).
- **Bases de datos en RDS:** `kea1` y `kea2` (propuesta 1), `kea` (propuesta 2).
- **Grafana:** http://localhost:5036 (`admin`/`admin`). **Prometheus:** http://localhost:5034. Los puertos los asigna
  GNS3 y pueden cambiar al reabrir el proyecto.

## Contraseñas

En este repositorio **no hay contraseñas**. En los `config/*/kea-dhcp4.conf`, la de la base de datos aparece como
`CAMBIAR_PASSWORD_BD`. Antes de cargar una propuesta, regenera la configuración con la contraseña real:

```bash
cd 01-kea-ha && python3 gen-config.py '<password-usuario-kea>' && ./activar.sh
```

Recomendaciones de seguridad aplicables al despliegue:
- Limitar el Security Group del RDS a la IP pública de salida del laboratorio.
- Usar el usuario `kea` (solo con permisos sobre sus bases) y no `admin`.
- Activar Multi-AZ en RDS para la propuesta 2.
