# Monitorización de Kea DHCP — kea-exporter + Prometheus + Grafana

Monitorización de los servidores Kea (sirve para la **propuesta 1** y la **propuesta 2**):

```
 kea-1 (172.16.10.5)  ── kea-exporter :9547 ─┐
                                              ├──► Prometheus-1 (172.16.30.30:9090) ──► Grafana-1 (172.16.30.31:3000)
 kea-2 (172.16.40.5)  ── kea-exporter :9547 ─┘          alertas                          panel "Kea DHCP"
```

En esta carpeta:
- `imagenes/`: los Dockerfile de Prometheus y Grafana para GNS3.
- `config/prometheus/`: `prometheus.yml` y las reglas de alerta.
- `config/grafana/`: la fuente de datos, el proveedor de paneles y el panel JSON.
- `config/red/`: el `interfaces` de cada nodo.
- `errores-y-soluciones.md`

Versiones: kea-exporter **0.7.1**, Prometheus **3.15.0**, Grafana **13.2.4**.

---

## 1. kea-exporter (dentro de los contenedores Kea)

[kea-exporter](https://github.com/mweinelt/kea-exporter) pide las estadísticas a Kea (`statistic-get-all`) por el socket de control y las publica en
formato Prometheus en `http://<ip>:9547/metrics`.

Está **dentro de la imagen de Kea** (`../00-imagen-kea/`), así que cada servidor publica sus propias métricas.

**dockerfile** (añadido):
```dockerfile
# python3-venv se añade a la lista de apt-get install
RUN python3 -m venv /opt/kea-exporter \
 && /opt/kea-exporter/bin/pip install --no-cache-dir kea-exporter==0.7.1
```

**kea-start.sh** (añadido, con el mismo esquema de relanzar si se cae):
```bash
( sleep 5; while true; do
    /opt/kea-exporter/bin/kea-exporter -a 0.0.0.0 -p 9547 /var/run/kea/kea4-ctrl-socket
    echo "kea-exporter se detuvo, reintento en 10 s"
    sleep 10
  done ) &
```

- Usa el **socket unix** de `kea-dhcp4` (`/var/run/kea/kea4-ctrl-socket`), así que no depende del Control Agent.
- Se usa un *venv* porque Debian 12 no deja hacer `pip install` sobre el Python del sistema (PEP 668).

### 1.1 Aplicar la imagen nueva a los nodos ya creados

Después de `docker build -t kea-dhcp .`, los contenedores de GNS3 siguen usando la imagen anterior. Para que GNS3 los
**recree** sin perder `/etc/kea` (que está en un volumen), se paran y se cambia una propiedad del nodo:

```bash
curl -s -u "admin:$P" -X POST $API/nodes/<ID_KEA>/stop
curl -s -u "admin:$P" -X PUT  $API/nodes/<ID_KEA> -d '{"properties":{"environment":"KEA_EXPORTER_PORT=9547"}}'
curl -s -u "admin:$P" -X POST $API/nodes/<ID_KEA>/start

docker inspect -f '{{.Image}}' GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85   # debe coincidir con la imagen nueva
```

### 1.2 Comprobar

```bash
docker exec GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 curl -s localhost:9547/metrics | grep '^kea_' | head
```

Métricas que publica:

| Métrica | Etiquetas | Significado |
|---|---|---|
| `kea_dhcp4_addresses_total` | `subnet`, `subnet_id`, `pool` | Tamaño del pool (101 por subred) |
| `kea_dhcp4_addresses_assigned_total` | `subnet`, `subnet_id`, `pool` | Direcciones asignadas |
| `kea_dhcp4_addresses_declined_total` | `subnet`, `pool` | Declinadas por el cliente (IP duplicada) |
| `kea_dhcp4_addresses_reclaimed_total` | `subnet`, `pool` | Leases caducados recuperados |
| `kea_dhcp4_packets_received_total` | `operation` (discover, request, release, decline, inform, …) | Paquetes recibidos |
| `kea_dhcp4_packets_sent_total` | `operation` (offer, ack, nak) | Paquetes enviados |
| `kea_dhcp4_leases_reused_total`, `kea_dhcp4_reservation_conflicts_total` | | |

> Cada métrica de dirección aparece dos veces: con `pool="172.16.X0.100-172.16.X0.200"` (por pool) y con `pool=""`
> (total de la subred). Las consultas usan `pool=""`.

> kea-exporter 0.7.1 **no publica el estado del HA** (`HOT-STANDBY`, `PARTNER-DOWN`…). El failover se ve en Grafana
> de dos formas: el servidor caído pasa a `up = 0`, y los ACK pasan de un servidor al otro.

---

## 2. Imágenes de Prometheus y Grafana para GNS3

Las imágenes oficiales **no arrancan en GNS3**, porque se ejecutan con un usuario sin shell (ver errores #1). Se usan
dos imágenes derivadas que solo cambian el usuario a root:

`imagenes/prometheus/Dockerfile`
```dockerfile
FROM prom/prometheus:latest
USER root
```

`imagenes/grafana/Dockerfile`
```dockerfile
FROM grafana/grafana:latest
USER root
```

```bash
cd kea-dhcp-proyecto/03-monitorizacion
docker build -t prometheus-gns3 imagenes/prometheus
docker build -t grafana-gns3    imagenes/grafana
```

---

## 3. Nodos en GNS3

| Nodo | Imagen | IP | Switch / puerto | Volumen persistente | Consola |
|---|---|---|---|---|---|
| Prometheus-1 | `prometheus-gns3:latest` | 172.16.30.30/24, gw .30.10 | SW-GABON puerto 2 | `/etc/prometheus` | HTTP 9090 |
| Grafana-1 | `grafana-gns3:latest` | 172.16.30.31/24, gw .30.10 | SW-GABON puerto 3 | `/etc/grafana` | HTTP 3000 |

Van en la LAN de Gabon, que es distinta de las de los dos Kea, con IPs fijas fuera del pool (`.100–.200`).

> GNS3 2.2 **no permite cambiar la imagen** de un nodo Docker ya creado (errores #2). Por eso los nodos Prometheus-1 y
> Grafana-1 que había en el proyecto (sin conectar y con el contenedor parado con error) se **sustituyeron** por nodos
> nuevos con el mismo nombre y en la misma posición.

Creación por la API (igual que los Kea):

```bash
P=$(grep -E '^password' ~/.config/GNS3/2.2/gns3_server.conf | sed 's/^password *= *//')
API=http://localhost:3080/v2/projects/ddbb5ebf-6207-41d5-95fe-f5e16a380d85
SWGABON=38ce02cc-55ff-4019-8af4-35b047313d38

curl -s -u "admin:$P" -X POST $API/nodes -d '{"name":"Prometheus-1","node_type":"docker","compute_id":"local",
  "x":1147,"y":-637,"console_type":"http",
  "properties":{"image":"prometheus-gns3:latest","adapters":1,"console_http_port":9090,"console_http_path":"/",
                "extra_volumes":["/etc/prometheus"]}}'
# Grafana-1: igual con "grafana-gns3:latest", console_http_port 3000, extra_volumes ["/etc/grafana"], x 992 y -640

curl -s -u "admin:$P" -X POST $API/links -d '{"nodes":[
  {"node_id":"<ID_PROMETHEUS>","adapter_number":0,"port_number":0},
  {"node_id":"'$SWGABON'","adapter_number":0,"port_number":2}]}'          # Grafana → port_number 3
```

IDs actuales: Prometheus-1 = `a88bf251-36ff-47a4-87c6-76784f234dd6`, Grafana-1 = `9eca917d-3627-4c0f-9c92-f74979a86f7b`.

**Red** (`config/red/*.interfaces` → `project-files/docker/<id>/etc/network/interfaces`):
```
auto eth0
iface eth0 inet static
	address 172.16.30.30          # .31 en Grafana
	netmask 255.255.255.0
	gateway 172.16.30.10
	up echo nameserver 8.8.8.8 > /etc/resolv.conf
```

**Primer arranque y después configuración** (en el primer arranque GNS3 rellena los volúmenes con el contenido de la
imagen, ver `../01-kea-ha/errores-y-soluciones.md` #3):

```bash
curl -s -u "admin:$P" -X POST $API/nodes/<ID>/start      # primer arranque
CP=GNS3.Prometheus-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85
CG=GNS3.Grafana-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85
M=config

docker exec -i $CP sh -c 'cat > /etc/prometheus/prometheus.yml' < $M/prometheus/prometheus.yml
docker exec -i $CP sh -c 'cat > /etc/prometheus/kea-alertas.yml' < $M/prometheus/kea-alertas.yml
docker exec $CP promtool check config /etc/prometheus/prometheus.yml

docker exec $CG mkdir -p /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards /etc/grafana/dashboards
docker exec -i $CG sh -c 'cat > /etc/grafana/provisioning/datasources/prometheus.yml' < $M/grafana/provisioning/datasources/prometheus.yml
docker exec -i $CG sh -c 'cat > /etc/grafana/provisioning/dashboards/kea.yml'         < $M/grafana/provisioning/dashboards/kea.yml
docker exec -i $CG sh -c 'cat > /etc/grafana/dashboards/kea-dhcp.json'                < $M/grafana/dashboards/kea-dhcp.json

# Reiniciar los dos (Stop + Start)
```

Para recargar Prometheus sin reiniciarlo después de cambiar `prometheus.yml`:
`docker exec $CP sh -c 'pkill -HUP prometheus'`

---

## 4. Prometheus

`config/prometheus/prometheus.yml`:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/kea-alertas.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: kea
    scrape_interval: 10s
    static_configs:
      - targets: ["172.16.10.5:9547"]
        labels: { servidor: kea-1 }
      - targets: ["172.16.40.5:9547"]
        labels: { servidor: kea-2 }
```

`config/prometheus/kea-alertas.yml`: tres alertas (validadas con `promtool check rules`):

| Alerta | Condición | Severidad |
|---|---|---|
| `KeaServidorCaido` | `up{job="kea"} == 0` durante 30 s | critical |
| `KeaPoolCasiLleno` | uso de una subred > 80 % durante 1 min | warning |
| `KeaDireccionesDeclinadas` | algún DECLINE en los últimos 10 min (posible IP duplicada) | warning |

Comprobar los targets:
```bash
docker exec $CP wget -qO- 'http://localhost:9090/api/v1/targets?state=active' | python3 -m json.tool | grep -E '"(scrapeUrl|health)"'
# http://172.16.10.5:9547/metrics  up
# http://172.16.40.5:9547/metrics  up
```

---

## 5. Grafana

Todo se carga solo al arrancar, mediante *provisioning*:
- `provisioning/datasources/prometheus.yml`: la fuente de datos `Prometheus` (uid `prometheus-kea`), apuntando a
  `http://172.16.30.30:9090`.
- `provisioning/dashboards/kea.yml`: carga los JSON de `/etc/grafana/dashboards` en la carpeta **DHCP**.
- `dashboards/kea-dhcp.json`: el panel **Kea DHCP** (uid `kea-dhcp`, se refresca cada 10 s).

Contenido del panel:

| Panel | Consulta (PromQL) |
|---|---|
| Estado de los servidores Kea | `up{job="kea"}` → EN MARCHA / CAÍDO |
| Servidor que está respondiendo (ACK/min) | `sum by (servidor) (rate(kea_dhcp4_packets_sent_total{operation="ack"}[5m])) * 60` |
| Direcciones asignadas (total) | `sum(max by (subnet) (kea_dhcp4_addresses_assigned_total{pool=""}))` |
| Uso de cada pool (%) | `100 * max by (subnet)(assigned{pool=""}) / max by (subnet)(total{pool=""})` |
| Direcciones asignadas por subred | `max by (subnet) (kea_dhcp4_addresses_assigned_total{pool=""})` |
| Paquetes recibidos por tipo | `rate(kea_dhcp4_packets_received_total[2m]) * 60` por servidor y tipo |
| Paquetes enviados por servidor — **aquí se ve el failover** | `rate(kea_dhcp4_packets_sent_total[2m]) * 60` por servidor y tipo |
| Direcciones declinadas y recuperadas | `addresses_declined_total`, `addresses_reclaimed_total` |

Se usa `max by (subnet)` porque los dos servidores informan de la misma subred, y así no se cuenta dos veces.

### Acceso desde el navegador del host

GNS3 publica la consola HTTP de cada nodo en un puerto de `localhost` (columna *Console* del nodo; cambia si se
cierra y se abre el proyecto):

| Servicio | URL (sesión actual) | Credenciales |
|---|---|---|
| Grafana | http://localhost:5036 → *Dashboards → DHCP → Kea DHCP* | `admin` / `admin` (pide cambiarla la primera vez) |
| Prometheus | http://localhost:5034 → *Status → Targets*, *Alerts* | — |

---

## 6. Verificación realizada

- **Targets:** `kea-1` y `kea-2` en `up`.
- **Grafana:** la fuente de datos responde `Successfully queried the Prometheus API` y el panel aparece en la carpeta
  DHCP.
- **Uso de los pools:** 1 de 101 direcciones por subred (0,99 %) con un cliente en cada LAN.
- **Prueba de caída** (propuesta 1): se paró kea-1 a las 20:06:21.
  ```
  KeaServidorCaido kea-1 firing      ← la alerta se activa
  kea-1 up= 0   kea-2 up= 1
  ```
  Al volver a arrancarlo: `kea-1 up= 1`, y el HA vuelve a `HOT-STANDBY`.
- **Propuesta 2:** las métricas de ACK muestran a los dos servidores respondiendo a la vez (kea-1: 3, kea-2: 5).
