# Kea DHCP en alta disponibilidad (hot-standby) con MySQL en AWS RDS — Guía de montaje

Guía para montar desde cero dos servidores **ISC Kea DHCPv4 2.6** en contenedores Docker dentro del proyecto GNS3
`Servicios pruebas`. Hay un servidor **primario** y uno de **reserva**: si cae el primario, el de reserva sigue dando IPs.
Los leases se guardan en **MySQL en AWS RDS**.

> **Propuesta 1 de la práctica: failover usando el módulo de alta disponibilidad (hook `libdhcp_ha.so`).**
> La propuesta 2 (failover usando solo la base de datos RDS) está en `../02-kea-rds/`, y la monitorización
> (kea-exporter + Prometheus + Grafana) en `../03-monitorizacion/`.

En esta carpeta:
- `errores-y-soluciones.md`: los problemas encontrados durante el montaje.
- `config/`: la configuración de cada servidor (backup).
- `gen-config.py`: genera la configuración.
- `activar.sh`: carga esta propuesta en los contenedores.

La imagen Docker, común a las dos propuestas, está en `../00-imagen-kea/`.

---

## 1. Arquitectura

```
                       Internet ── Cloud1 ── Gambia (MikroTik, NAT)
                                                │
                     ┌──────────── red troncal 172.16.1.0/x, 172.16.2.0/x (OSPF) ────────────┐
                     │                                                                        │
  Georgia(MikroTik)  Ghana(OpenWrt)  Gabon(MikroTik)  Granada(VyOS)  Grecia(OpenWrt)  Guinea(Cisco)  Guatemala(VyOS)
        │                 │               │                │               │               │              │
   SW-GEORGIA        SW-GHANA        SW-GABON        SW-GRANADA       SW-GRECIA       SW-GUINEA     SW-GUATEMALA
   │       │              │               │           │       │            │               │              │
 kea-1   Tiflis         Accra        Libreville     kea-2   Dover        Atenas         Conakry        Flores
 .10.5
                                                     .40.5
```

| LAN | Switch | Router (SO) | Interfaz LAN del router | Gateway | Host |
|---|---|---|---|---|---|
| 172.16.10.0/24 | SW-GEORGIA | Georgia (MikroTik 7.23) | ether8 | 172.16.10.10 | Tiflis + **kea-1** |
| 172.16.20.0/24 | SW-GHANA | Ghana (OpenWrt 24.10) | eth7 (UCI `e7`) | 172.16.20.10 | Accra |
| 172.16.30.0/24 | SW-GABON | Gabon (MikroTik 7.23) | ether8 | 172.16.30.10 | Libreville |
| 172.16.40.0/24 | SW-GRANADA | Granada (VyOS rolling) | eth7 | 172.16.40.10 | Dover + **kea-2** |
| 172.16.50.0/24 | SW-GRECIA | Grecia (OpenWrt 24.10) | eth7 (UCI `e7`) | 172.16.50.10 | Atenas |
| 172.16.60.0/24 | SW-GUINEA | Guinea (Cisco 7200) | FastEthernet5/0 | 172.16.60.10 | Conakry |
| 172.16.70.0/24 | SW-GUATEMALA | Guatemala (VyOS rolling) | eth7 | 172.16.70.10 | Flores |

| Servidor | IP | Switch / puerto | Rol HA | Base de datos en RDS |
|---|---|---|---|---|
| kea-1 | 172.16.10.5/24 (gw .10) | SW-GEORGIA puerto 2 | primary | `kea1` |
| kea-2 | 172.16.40.5/24 (gw .40.10) | SW-GRANADA puerto 2 | standby | `kea2` |

**Puertos usados:**
- **UDP 67:** DHCP (clientes y relays).
- **TCP 8001:** comunicación HA entre los dos Kea (listener HTTP dedicado dentro de `kea-dhcp4`).
- **TCP 8000:** Control Agent, para administración.
- **TCP 3306:** MySQL en RDS, a través de Gambia con NAT.

**Decisiones de diseño:**
- **Los dos Kea van en LAN y routers distintos.** Así, si cae un router o un switch, el otro Kea sigue funcionando.
- **Cada Kea tiene su propia base de datos** (`kea1` y `kea2`) en el mismo RDS. Es lo que recomienda ISC para HA: cada servidor
  tiene su propio almacén y los dos se sincronizan entre sí con el hook `libdhcp_ha.so`. Compartir una sola base con HA
  duplica las escrituras.
- **Cada router hace de relay hacia los dos Kea**, salvo Granada (ver §7.3).

---

## 2. Requisitos previos

- GNS3 2.2 con Docker en el host.
- Una instancia de RDS MySQL 8.x **accesible desde internet**, con un Security Group que permita la IP pública desde la que
  salen los Kea (Gambia hace NAT a través de Cloud1).
  - Endpoint: `database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com`
  - Usuario administrador: `admin`
  - Usuario de servicio: `kea`
- Con el binlog activado (RDS lo activa si hay backups), el esquema de Kea necesita `log_bin_trust_function_creators = ON`
  en el *parameter group* para poder crear sus triggers y procedimientos. En esta instancia ya estaba en `ON`.

Para comprobar desde el host que el RDS responde:

```bash
timeout 6 bash -c 'cat < /dev/null > /dev/tcp/database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com/3306' && echo ABIERTO
```

---

## 3. Imagen Docker

Carpeta de trabajo: `/home/alberto/Escritorio/kea-docker`. La copia de referencia está en `../00-imagen-kea/`.

La imagen incluye también **kea-exporter**, que expone las métricas de Kea en el puerto 9547 para la monitorización
(ver `../03-monitorizacion/`).

### 3.1 `dockerfile`

```dockerfile
FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg apt-transport-https lsb-release \
      iproute2 ifupdown iputils-ping procps mariadb-client python3-venv \
 && curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-6/setup.deb.sh' | bash \
 && apt-get install -y --no-install-recommends \
      isc-kea-dhcp4-server isc-kea-ctrl-agent isc-kea-admin isc-kea-hooks \
 && rm -rf /var/lib/apt/lists/*

# Exportador de métricas de Kea para Prometheus (puerto 9547)
RUN python3 -m venv /opt/kea-exporter \
 && /opt/kea-exporter/bin/pip install --no-cache-dir kea-exporter==0.7.1

COPY kea-start.sh /usr/local/bin/kea-start.sh
RUN chmod +x /usr/local/bin/kea-start.sh

CMD ["/usr/local/bin/kea-start.sh"]
```

> No hay que añadir `isc-kea-mysql`: ese paquete no existe en Kea 2.6, porque el soporte de MySQL viene dentro de
> `isc-kea-common`. Aparece a partir de Kea 3.0.

### 3.2 `kea-start.sh`

Espera a que GNS3 configure `eth0`, crea las carpetas de trabajo y mantiene vivos `kea-ctrl-agent` y `kea-exporter`
(en segundo plano) y `kea-dhcp4` (en primer plano). Si alguno se cae, lo relanza a los 10 s.

```bash
#!/bin/bash

echo "Esperando IP en eth0..."
until ip -4 addr show eth0 2>/dev/null | grep -q "inet "; do sleep 1; done

mkdir -p /run/kea /var/log/kea

# Control Agent (canal por el que los dos Kea se hablan para el HA)
( while true; do
    kea-ctrl-agent -c /etc/kea/kea-ctrl-agent.conf
    echo "kea-ctrl-agent se detuvo, reintento en 10 s"
    sleep 10
  done ) &

# Exportador de métricas para Prometheus (lee el socket de control de kea-dhcp4)
( sleep 5; while true; do
    /opt/kea-exporter/bin/kea-exporter -a 0.0.0.0 -p 9547 /var/run/kea/kea4-ctrl-socket
    echo "kea-exporter se detuvo, reintento en 10 s"
    sleep 10
  done ) &

# Servidor DHCP
while true; do
  kea-dhcp4 -c /etc/kea/kea-dhcp4.conf
  echo "kea-dhcp4 se detuvo (revisa /var/log/kea/kea-dhcp4.log), reintento en 10 s"
  sleep 10
done
```

> En el diseño final el HA no pasa por el Control Agent, sino por el listener dedicado del puerto 8001. El CA queda
> solo para administración.

### 3.3 Construir

```bash
cd /home/alberto/Escritorio/kea-docker
docker build -t kea-dhcp .
```

Para ver los paquetes Kea disponibles en el repositorio (útil si cambia la versión):

```bash
docker run --rm debian:12-slim bash -c "apt-get update -qq >/dev/null && \
  apt-get install -y -qq curl ca-certificates gnupg apt-transport-https >/dev/null 2>&1 && \
  curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-6/setup.deb.sh' | bash >/dev/null 2>&1 && \
  apt-cache search isc-kea"
```

---

## 4. Base de datos en RDS

Todo se hace desde la propia imagen, que ya incluye `mariadb-client` y `kea-admin`.

```bash
H=database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com

# 1) Ver qué hay
docker run --rm kea-dhcp mariadb -h $H -u admin -p'<PASS_ADMIN>' -e "SHOW DATABASES; SELECT user,host FROM mysql.user;"

# 2) Crear las bases y cargar el esquema de Kea (versión de esquema 22.2 para Kea 2.6)
docker run --rm kea-dhcp bash -c "
  mariadb -h $H -u admin -p'<PASS_ADMIN>' -e 'CREATE DATABASE kea1; CREATE DATABASE kea2;' &&
  kea-admin db-init mysql -h $H -u admin -p '<PASS_ADMIN>' -n kea1 &&
  kea-admin db-init mysql -h $H -u admin -p '<PASS_ADMIN>' -n kea2"

# 3) Usuario de servicio (en este proyecto ya existía con estos permisos)
docker run --rm kea-dhcp mariadb -h $H -u admin -p'<PASS_ADMIN>' -e "
  CREATE USER IF NOT EXISTS 'kea'@'%' IDENTIFIED BY '<PASS_KEA>';
  GRANT ALL PRIVILEGES ON kea1.* TO 'kea'@'%';
  GRANT ALL PRIVILEGES ON kea2.* TO 'kea'@'%';"

# 4) Comprobar
docker run --rm kea-dhcp mariadb -h $H -u kea -p'<PASS_KEA>' -e "
  SELECT 'kea1',version,minor FROM kea1.schema_version;
  SELECT 'kea2',version,minor FROM kea2.schema_version;"
```

> El usuario `kea` tenía permisos también sobre una base `kea`, que estaba vacía y no se ha usado.

---

## 5. Configuración de Kea

Los archivos se generan con `gen-config.py`, para que los dos servidores sean idénticos salvo en el nombre, la IP y la base
de datos. Uso:

```bash
cd kea-dhcp-proyecto/01-kea-ha
python3 gen-config.py '<PASS_KEA>'     # genera config/kea-1/ y config/kea-2/
```

### 5.1 Puntos clave de `kea-dhcp4.conf`

| Bloque | Valor | Motivo |
|---|---|---|
| `interfaces-config` | `["eth0"]` (socket raw por defecto) | Atiende el broadcast directo de su propia LAN y el unicast de los relays. |
| `control-socket` | `/var/run/kea/kea4-ctrl-socket` | Kea 2.6.5 **solo admite sockets dentro de `/var/run/kea`**. |
| `lease-database` | `type: mysql`, `host: <RDS>`, `name: kea1`/`kea2`, `user: kea` | Los leases se guardan en RDS. |
| | `on-fail: serve-retry-continue`, `retry-on-startup: true`, `max-reconnect-tries: 100`, `reconnect-wait-time: 5000` | Si RDS no responde, sigue reintentando en vez de pararse. |
| `subnet4` | id 1..7 → `172.16.X0.0/24`, pool `.100 - .200`, `routers .10` | Los `id` tienen que ser iguales en los dos servidores para el HA. |
| `option-data` | DNS `8.8.8.8, 1.1.1.1` | |
| Tiempos | valid 3600 s, renew 900 s, rebind 1800 s | |
| `hooks-libraries` | `libdhcp_lease_cmds.so` + `libdhcp_ha.so` | `lease_cmds` es obligatorio para el HA. |

Parámetros del HA:

```json
"this-server-name": "kea-1",            // "kea-2" en el otro
"mode": "hot-standby",
"heartbeat-delay": 3000,
"max-response-delay": 10000,             // tras ~10 s sin respuesta del compañero → PARTNER-DOWN
"max-ack-delay": 5000,
"max-unacked-clients": 0,                // no espera a ver clientes sin respuesta para declarar la caída
"multi-threading": { "enable-multi-threading": true, "http-dedicated-listener": true,
                     "http-listener-threads": 0, "http-client-threads": 0 },
"peers": [
  { "name": "kea-1", "url": "http://172.16.10.5:8001/", "role": "primary", "auto-failover": true },
  { "name": "kea-2", "url": "http://172.16.40.5:8001/", "role": "standby", "auto-failover": true }
]
```

### 5.2 `kea-ctrl-agent.conf`

Escucha en la IP del propio servidor, puerto 8000, y se conecta al socket de `dhcp4`. No usa `kea-api-password`.

### 5.3 Validar antes de desplegar

```bash
for s in kea-1 kea-2; do
  docker run --rm -v "$PWD/config/$s:/etc/kea:ro" kea-dhcp bash -c '
    mkdir -p /run/kea /var/log/kea
    kea-dhcp4 -t /etc/kea/kea-dhcp4.conf      >/dev/null 2>&1; echo "'$s' dhcp4 rc=$?"
    kea-ctrl-agent -t /etc/kea/kea-ctrl-agent.conf >/dev/null 2>&1; echo "'$s' ca rc=$?"'
done
```

Si sale `rc=0`, la configuración es válida.

---

## 6. Nodos en GNS3

### 6.1 Crear los nodos

**Desde la interfaz gráfica:**
1. *Edit → Preferences → Docker containers → New*. Imagen `kea-dhcp:latest`, 1 adaptador, consola telnet.
   En *Additional directories to make persistent* añade `/etc/kea` y `/var/log/kea`.
2. Arrastra dos instancias, llámalas `kea-1` y `kea-2`, y conéctalas:
   - kea-1 eth0 ↔ SW-GEORGIA puerto 2
   - kea-2 eth0 ↔ SW-GRANADA puerto 2

**Desde la API REST** (la forma usada aquí; la contraseña está en `~/.config/GNS3/2.2/gns3_server.conf`):

```bash
P=$(grep -E '^password' ~/.config/GNS3/2.2/gns3_server.conf | sed 's/^password *= *//')
API=http://localhost:3080/v2/projects/ddbb5ebf-6207-41d5-95fe-f5e16a380d85   # proyecto "Servicios"

# Crear nodo (devuelve node_id)
curl -s -u "admin:$P" -X POST $API/nodes -d '{
  "name":"kea-1","node_type":"docker","compute_id":"local","x":-880,"y":40,
  "symbol":":/symbols/server.svg",
  "properties":{"image":"kea-dhcp:latest","adapters":1,"console_type":"telnet",
                "extra_volumes":["/etc/kea","/var/log/kea"]}}'

# Enlace nodo ↔ switch (puerto 2 del switch)
curl -s -u "admin:$P" -X POST $API/links -d '{"nodes":[
  {"node_id":"<ID_KEA1>","adapter_number":0,"port_number":0},
  {"node_id":"86e47e83-f1d2-4318-a8db-3bf8a5f33d63","adapter_number":0,"port_number":2}]}'   # SW-GEORGIA
# Para kea-2 se usa SW-GRANADA = 98198315-2965-4917-b4ca-9a78964cb4a5
```

IDs de los nodos creados: kea-1 = `45815ec3-3e43-4af5-863f-f0bd1acecbd9`, kea-2 = `f4bde005-0151-4470-aaa9-1867d5e3c9fe`.

### 6.2 Red de cada Kea (`/etc/network/interfaces`)

Se edita desde GNS3 (*clic derecho → Edit config*) o directamente en
`project-files/docker/<node_id>/etc/network/interfaces`:

```
auto eth0
iface eth0 inet static
	address 172.16.10.5          # 172.16.40.5 en kea-2
	netmask 255.255.255.0
	gateway 172.16.10.10         # 172.16.40.10 en kea-2
	up echo nameserver 8.8.8.8 > /etc/resolv.conf
```

El DNS es necesario para resolver el endpoint del RDS.

### 6.3 Copiar la configuración de Kea (después del primer arranque)

> ⚠️ **Importante:** en el **primer arranque**, GNS3 rellena los volúmenes persistentes con el contenido de la imagen y
> **sobrescribe** cualquier `.conf` que hubieras dejado antes. Por eso hay que arrancar el nodo una vez y **después**
> copiar la configuración. A partir de ahí ya no se sobrescribe (se crea el archivo `.gns3_perms`).

La forma más rápida es usar el script de esta carpeta, que hace exactamente lo que se ve más abajo:

```bash
./activar.sh        # carga config/kea-1 y config/kea-2 en los contenedores y reinicia Kea
```

Paso a paso:

```bash
# Arrancar
curl -s -u "admin:$P" -X POST $API/nodes/<ID>/start

# Copiar la configuración DENTRO del contenedor (docker cp NO sirve aquí, ver errores)
for n in 1 2; do C=GNS3.kea-$n.ddbb5ebf-6207-41d5-95fe-f5e16a380d85
  for f in kea-dhcp4.conf kea-ctrl-agent.conf; do
    docker exec -i $C sh -c "cat > /etc/kea/$f" < config/kea-$n/$f
  done
  docker exec $C sh -c 'chown _kea:_kea /etc/kea/*.conf; chmod 640 /etc/kea/*.conf;
                        pkill kea-dhcp4; pkill kea-ctrl-agent'   # el bucle de kea-start.sh los relanza
done
```

### 6.4 Comprobar que arrancan

```bash
docker logs --tail 40 GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85
```

Tienen que aparecer `DHCPSRV_MYSQL_DB opening MySQL lease database ... name=kea1` y la secuencia
`WAITING → SYNCING → READY → HOT-STANDBY` en los dos servidores.

---

## 7. Relays DHCP en los routers

Las peticiones DHCP son broadcast y no atraviesan routers, así que cada router reenvía las de su LAN a los Kea.
Credenciales: las de cada router del laboratorio (no se publican en el repositorio).
Para entrar, abre la consola desde GNS3 (doble clic) o usa `telnet localhost <puerto>`.

| Router | Puerto de consola (sesión actual) |
|---|---|
| Guinea | 5013 |
| Georgia | 5015 |
| Gambia | 5016 |
| Grecia | 5017 |
| Ghana | 5019 |
| Gabon | 5021 |
| Guatemala | 5044 |
| Granada | 5046 |

### 7.1 MikroTik RouterOS 7 — Georgia y Gabon

Comprobar primero:

```
/ip address print
/ip dhcp-server print      # no debe haber un servidor DHCP en ether8
/ip firewall filter print
```

Georgia:

```
/ip dhcp-relay add name=relay-kea interface=ether8 dhcp-server=172.16.10.5,172.16.40.5 local-address=172.16.10.10 disabled=no
/ip dhcp-relay print detail
```

Gabon:

```
/ip dhcp-relay add name=relay-kea interface=ether8 dhcp-server=172.16.10.5,172.16.40.5 local-address=172.16.30.10 disabled=no
```

RouterOS guarda los cambios automáticamente.

### 7.2 VyOS — Guatemala

```
configure
set service dhcp-relay listen-interface eth7
set service dhcp-relay upstream-interface eth0
set service dhcp-relay upstream-interface eth1
set service dhcp-relay upstream-interface eth2
set service dhcp-relay upstream-interface eth3
set service dhcp-relay upstream-interface eth4
set service dhcp-relay upstream-interface eth5
set service dhcp-relay upstream-interface eth6
set service dhcp-relay server 172.16.10.5
set service dhcp-relay server 172.16.40.5
commit
save
exit
```

Comprobar:

```
show configuration commands | grep dhcp-relay
ps ax | grep [d]hcrelay
```

`upstream-interface` son todas las interfaces que van hacia otros routers. `listen-interface` es la LAN.

### 7.3 VyOS — Granada (⚠️ solo hacia kea-1)

Granada tiene a **kea-2 en su propia LAN**. Si relaya también a 172.16.40.5, `dhcrelay` vuelve a capturar las peticiones
que otros routers envían a kea-2 cuando salen por `eth7`, y se forma un bucle (ver errores). kea-2 ya recibe directamente
el broadcast de esa LAN, así que basta con relayar a kea-1:

```
configure
set service dhcp-relay listen-interface eth7
set service dhcp-relay upstream-interface eth0
set service dhcp-relay upstream-interface eth1
set service dhcp-relay upstream-interface eth2
set service dhcp-relay upstream-interface eth3
set service dhcp-relay upstream-interface eth4
set service dhcp-relay upstream-interface eth5
set service dhcp-relay upstream-interface eth6
set service dhcp-relay server 172.16.10.5
commit
save
exit
```

### 7.4 Cisco IOS — Guinea

```
enable
configure terminal
 interface FastEthernet5/0
  ip helper-address 172.16.10.5
  ip helper-address 172.16.40.5
 end
write memory          (pulsa Enter en el [confirm])
show running-config interface FastEthernet5/0
```

### 7.5 OpenWrt 24.10 (dnsmasq) — Ghana y Grecia

En estos routers la LAN es la interfaz UCI `e7` (dispositivo `eth7`), y el firewall (`fw4`) está **desactivado**. Si lo
activas, tienes que permitir la entrada de UDP 67.

> ⚠️ **No pongas `option interface`** en las secciones `relay`: con ese campo, dnsmasq 2.93 recibe la respuesta de Kea
> pero no se la reenvía al cliente.

Ghana (en Grecia, cambia `172.16.20.10` por `172.16.50.10`):

```sh
for s in 172.16.10.5 172.16.40.5; do
  r=$(uci add dhcp relay)
  uci set dhcp.$r.local_addr='172.16.20.10'
  uci set dhcp.$r.server_addr="$s"
done
uci commit dhcp
/etc/init.d/dnsmasq restart
```

Que en `/etc/config/dhcp` queda así:

```
config relay
	option local_addr '172.16.20.10'
	option server_addr '172.16.10.5'

config relay
	option local_addr '172.16.20.10'
	option server_addr '172.16.40.5'
```

Comprobar:

```sh
uci show dhcp | grep relay
grep -h dhcp-relay /var/etc/dnsmasq.conf.*
#   dhcp-relay=172.16.20.10,172.16.10.5      ← sin ",eth7" al final
#   dhcp-relay=172.16.20.10,172.16.40.5
/etc/init.d/firewall enabled && echo FW_ENABLED || echo FW_DISABLED
```

---

## 8. Clientes en DHCP

Cada host alpine tiene en `project-files/docker/<node_id>/etc/network/interfaces`:

```
auto eth0
iface eth0 inet dhcp
```

(Los originales con IP fija `.20` están guardados como `interfaces.estatica.bak` en la misma carpeta.)

> Para que el cambio surta efecto hay que **parar y arrancar** el nodo (*Stop* + *Start*). La opción *Reload* de GNS3
> reinicia el contenedor pero no le vuelve a conectar `eth0`.

---

## 9. Verificación

```bash
# IP de cada host y salida a internet
for n in Tiflis Accra Libreville Dover Atenas Conakry Flores; do
  printf '%-11s ' $n
  docker exec GNS3.$n.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 sh -c \
    "ip -4 -o addr show eth0 | awk '{print \$4}'; ping -c1 -W2 8.8.8.8 >/dev/null && echo OK || echo FALLO" | tr '\n' ' '
  echo
done

# Leases en RDS (en kea1 y replicados en kea2)
docker run --rm kea-dhcp mariadb -h database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com -u kea -p'<PASS_KEA>' -e "
  SELECT INET_NTOA(address) ip, HEX(hwaddr) mac, expire FROM kea1.lease4 WHERE state=0 ORDER BY address;
  SELECT COUNT(*) activos_kea2 FROM kea2.lease4 WHERE state=0;"

# Estado del HA
docker logs GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 2>&1 | grep HA_STATE_TRANSITION | tail -3

# Estado del HA por API (desde dentro de un Kea)
docker exec GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"command":"ha-heartbeat","service":["dhcp4"]}' http://172.16.10.5:8000/

# ¿De dónde vienen las peticiones? (sirve para detectar bucles de relay)
docker exec GNS3.kea-1.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 sh -c \
  "grep PACKET_RECEIVED /var/log/kea/kea-dhcp4.log | sed -E 's/.*(DHCP[A-Z]+ .* received from [0-9.]+).*/\1/' | sort | uniq -c"
```

Resultado obtenido:

```
Tiflis      172.16.10.100/24 internet-OK
Accra       172.16.20.144/24 internet-OK
Libreville  172.16.30.102/24 internet-OK
Dover       172.16.40.102/24 internet-OK
Atenas      172.16.50.104/24 internet-OK
Conakry     172.16.60.101/24 internet-OK
Flores      172.16.70.101/24 internet-OK
kea1: 7 leases activos · kea2: 7 leases activos
```

---

## 10. Prueba de conmutación (failover)

```bash
# 1) Parar el primario
curl -s -u "admin:$P" -X POST $API/nodes/45815ec3-3e43-4af5-863f-f0bd1acecbd9/stop

# 2) En ~10 s kea-2 pasa a PARTNER-DOWN y empieza a servir
docker logs --since 30s GNS3.kea-2.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 2>&1 | grep -E 'HA_STATE|HEARTBEAT'

# 3) Reiniciar un cliente (stop + start) y comprobar que kea-2 le da la IP
docker logs --since 30s GNS3.kea-2.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 2>&1 | grep DHCP4_LEASE_ALLOC

# 4) Volver a levantar el primario: se sincroniza con kea-2 y los dos vuelven a HOT-STANDBY
curl -s -u "admin:$P" -X POST $API/nodes/45815ec3-3e43-4af5-863f-f0bd1acecbd9/start
```

Resultado obtenido:

```
19:46:28  kea-1 parado
17:46:36  kea-2: HOT-STANDBY → PARTNER-DOWN                    (~8 s)
17:46:55  kea-2: lease 172.16.30.102 has been allocated (Libreville)
17:47:35  kea-1: WAITING → SYNCING, applied 1 leases received from the partner
17:47:40  kea-1: READY → HOT-STANDBY ; kea-2: PARTNER-DOWN → HOT-STANDBY
```

---

## 11. Mantenimiento

- **Logs:** `/var/log/kea/kea-dhcp4.log` y `/var/log/kea/kea-ctrl-agent.log` dentro de cada contenedor. Son persistentes:
  están en `project-files/docker/<id>/var/log/kea`.
- **Cambiar la contraseña del usuario `kea`:**
  1. Cámbiala en el RDS (`ALTER USER 'kea'@'%' IDENTIFIED BY '...'`).
  2. Ejecuta `python3 gen-config.py '<nueva>'`.
  3. Copia los archivos como en §6.3.
- **Restaurar desde el backup / volver a esta propuesta:** `./activar.sh`. Copia `config/kea-N/*.conf` dentro de los
  contenedores. `config/kea-N/interfaces` va en `project-files/docker/<id>/etc/network/interfaces`.
- **Pasar a la propuesta 2:** `../02-kea-rds/activar.sh`. Solo puede haber una propuesta activa a la vez, porque las
  dos usan los mismos contenedores `kea-1` y `kea-2`.
- **Seguridad pendiente:**
  - Las contraseñas actuales son débiles.
  - El RDS está abierto a internet: limita el Security Group a tu IP pública.
  - `kea-dhcp4.conf` lleva la contraseña de la BD en texto plano.
