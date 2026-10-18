# Errores encontrados y cómo se solucionaron

Registro de los problemas que aparecieron al montar Kea HA con MySQL en RDS dentro de GNS3, en el orden en que
surgieron. Cada uno indica el síntoma, la causa y la solución.

| # | Fase | Problema | Gravedad |
|---|---|---|---|
| 1 | Build | `isc-kea-mysql` no existe en Kea 2.6 | Bloqueante |
| 2 | Configuración | Ruta del socket de control rechazada | Bloqueante |
| 3 | GNS3 | El primer arranque sobrescribe `/etc/kea` | Bloqueante |
| 4 | GNS3 | `docker cp` no escribe en el volumen | Bloqueante |
| 5 | Hosts | `$(hostname)` no se expande en `interfaces` | Menor |
| 6 | Hosts | *Reload* de GNS3 deja el contenedor sin `eth0` | Bloqueante |
| 7 | Relay VyOS | Bucle de relay en Granada (636 copias de un DISCOVER) | Grave |
| 8 | Relay OpenWrt | dnsmasq no pasa las respuestas al cliente | Bloqueante |
| 9 | Consolas | Inconvenientes al configurar los routers | Menor |

---

## 1. `E: Unable to locate package isc-kea-mysql`

**Síntoma:** `docker build` falla en el `apt-get install`:

```
E: Unable to locate package isc-kea-mysql
The command '/bin/sh -c apt-get update && ...' returned a non-zero code: 100
```

**Causa:** en el repositorio de **Kea 2.6** de Cloudsmith no existe el paquete `isc-kea-mysql`. En 2.6 el soporte de MySQL
viene dentro de `isc-kea-common`, que depende de `libmariadb3`. El paquete `isc-kea-mysql` aparece a partir de Kea 3.0.

**Diagnóstico:**

```bash
docker run --rm debian:12-slim bash -c "... setup.deb.sh | bash ... && apt-cache search isc-kea"
apt-cache show isc-kea-common | grep Depends     # → libmariadb3, libpq5 ...
```

**Solución:** quitar `isc-kea-mysql` del `dockerfile`. Basta con
`isc-kea-dhcp4-server isc-kea-ctrl-agent isc-kea-admin isc-kea-hooks`.

---

## 2. `invalid path specified: '/run/kea', supported path is '/var/run/kea'`

**Síntoma:** al validar con `kea-ctrl-agent -t`:

```
DCTL_CONFIG_CHECK_COMPLETE ... result: failure(1),
text=invalid path specified: '/run/kea', supported path is '/var/run/kea'
```

**Causa:** desde Kea 2.6.x hay una restricción de seguridad: los sockets de control unix **solo** pueden estar dentro de
`/var/run/kea` (aunque en Debian `/var/run` sea un enlace a `/run`, compara la ruta tal cual).

**Solución:** usar `"socket-name": "/var/run/kea/kea4-ctrl-socket"` en `kea-dhcp4.conf` y en `kea-ctrl-agent.conf`.
Después, validar los dos archivos y comprobar el **código de salida**, no solo el texto:

```bash
kea-dhcp4 -t /etc/kea/kea-dhcp4.conf; echo rc=$?
kea-ctrl-agent -t /etc/kea/kea-ctrl-agent.conf; echo rc=$?
```

---

## 3. Los Kea arrancan con la configuración de ejemplo del paquete

**Síntoma:** después de dejar los `.conf` en `project-files/docker/<id>/etc/kea/` y arrancar los nodos, los logs muestran:

```
ERROR CTRL_AGENT_CONFIG_FAIL ... Expected a file at path '/etc/kea/kea-api-password'
DHCPSRV_CFGMGR_NEW_SUBNET4 a new subnet has been added to configuration: 192.0.2.0/24
DHCPSRV_MEMFILE_DB opening memory file lease database
DHCPSRV_NO_SOCKETS_OPEN no interface configured to listen to DHCP traffic
```

Es decir, usaban la subred de ejemplo `192.0.2.0/24`, guardaban los leases en archivo y no en MySQL, y el Control Agent
pedía un archivo de contraseña. Además, el host ya no dejaba leer la carpeta (`Permiso denegado`), porque pasó a ser
propiedad de `_kea`.

**Causa:** en el **primer arranque** de un nodo Docker, el `init.sh` de GNS3 copia en cada volumen persistent
(`extra_volumes`) el contenido que trae la imagen en esa ruta. Eso sustituyó nuestros archivos por los de ejemplo del
paquete `isc-kea`. Después crea `.gns3_perms` y ya no vuelve a hacerlo.

**Solución:** arrancar el nodo una vez y **después** escribir la configuración dentro del contenedor (ver el error 4 sobre
cómo hacerlo) y reiniciar los procesos de Kea. A partir de ahí la configuración se conserva entre reinicios. Se
comprobó parando y arrancando `kea-1`: `grep -c database-1 /etc/kea/kea-dhcp4.conf` → `1`.

---

## 4. `docker cp` no sobrescribe los archivos de `/etc/kea`

**Síntoma:** después de `docker cp config/kea-1/kea-dhcp4.conf <contenedor>:/etc/kea/kea-dhcp4.conf`, el archivo del
contenedor seguía siendo el de ejemplo (22473 bytes, fecha de 2025). `grep database-1` devolvía `0`, y `docker cp` no
mostraba ningún error.

**Causa:** GNS3 monta `/etc/kea` **dos veces**: en `/gns3volumes/etc/kea` y en `/etc/kea`, las dos sobre el mismo
directorio del host. `docker cp` escribe en la capa del contenedor y no en el bind-mount, así que el cambio no se ve.

```
/dev/nvme1n1p3 on /gns3volumes/etc/kea type ext4 (rw,relatime)
/dev/nvme1n1p3 on /etc/kea type ext4 (rw,relatime)
```

**Solución:** escribir el archivo desde dentro del contenedor pasándolo por la entrada estándar:

```bash
docker exec -i $C sh -c "cat > /etc/kea/kea-dhcp4.conf" < config/kea-1/kea-dhcp4.conf
docker exec $C sh -c 'chown _kea:_kea /etc/kea/*.conf; chmod 640 /etc/kea/*.conf; pkill kea-dhcp4; pkill kea-ctrl-agent'
```

El bucle de `kea-start.sh` vuelve a lanzar los procesos con la configuración nueva.

---

## 5. `$(hostname)` no se expande en `/etc/network/interfaces`

**Síntoma / causa:** al pasar los hosts alpine a DHCP se añadió `hostname $(hostname)`. El `ifup` de busybox no ejecuta
sustituciones de shell, así que habría enviado el texto literal `$(hostname)`.

**Solución:** quitar esa línea y dejar solo:

```
auto eth0
iface eth0 inet dhcp
```

---

## 6. *Reload* de GNS3 deja los hosts sin `eth0`

**Síntoma:** después de un *Reload* de los 7 hosts (API `POST /nodes/<id>/reload`):

```
ip: can't find device 'eth0'
```

`ps` mostraba que `init.sh` se quedaba esperando en un `usleep`, y el log de GNS3 decía `Docker container 'Accra' restarted`.

**Causa:** con nodos Docker, *Reload* reinicia el contenedor (`docker restart`) pero GNS3/ubridge **no vuelve a conectar**
la interfaz. El `init.sh` de GNS3 espera para siempre a que aparezca `eth0`.

**Solución:** usar siempre ***Stop* y luego *Start*** (`POST /nodes/<id>/stop` y luego `POST /nodes/<id>/start`).

---

## 7. Bucle de relay en Granada (VyOS)

**Síntoma:** 4 de los 7 hosts obtenían IP, pero Accra (Ghana) y Atenas (Grecia) no. En el log de kea-1 aparecían
decenas de copias del mismo DISCOVER (mismo `tid`) en milisegundos, y kea-1 respondía con un OFFER distinto a cada una
(`.128`, `.129`, ...). Al contarlas por origen:

```bash
grep -E 'PACKET_RECEIVED|PACKET_SEND' /var/log/kea/kea-dhcp4.log | ... | sort | uniq -c
```
```
    636 DHCPDISCOVER (type 1) received from 172.16.1.14 to 172.16.10.5     ← ¡172.16.1.14 = Granada eth4!
     68 DHCPOFFER (type 2) from 172.16.10.5:67 to 172.16.20.10:67
     62 DHCPOFFER (type 2) from 172.16.10.5:67 to 172.16.50.10:67
     36 DHCPDISCOVER (type 1) received from 172.16.20.10 to 172.16.10.5
     35 DHCPDISCOVER (type 1) received from 172.16.50.10 to 172.16.10.5
```

**Causa:** kea-2 (172.16.40.5) está **en la LAN de Granada** (`eth7`), y Granada relayaba a los dos Kea.
1. Grecia y Ghana envían las peticiones relayadas en unicast a 172.16.40.5. Granada las enruta y salen por `eth7`.
2. El `dhcrelay` de ISC que usa VyOS escucha en `eth7` (`-id eth7`) con un socket de paquetes (LPF), que **también ve el
   tráfico saliente**. Toma esas peticiones por peticiones de un cliente y las vuelve a relayar a 172.16.10.5 y a
   172.16.40.5.
3. La copia hacia 172.16.40.5 vuelve a salir por `eth7` y se repite el ciclo, hasta el límite de saltos (`-c 10`).

El resultado era una avalancha de DISCOVER y OFFER.

**Solución:** en Granada, relayar **solo a kea-1**. kea-2 ya recibe directamente el broadcast de su propia LAN.

```
configure
delete service dhcp-relay server 172.16.40.5
commit
save
```

Después del cambio, cada petición que va a kea-2 todavía genera **una** copia extra hacia kea-1
(`40 DISCOVER from 172.16.1.14` frente a 20 de cada origen). Es inofensivo: el bucle ya no se repite.

**Efecto secundario:** las ofertas generadas durante la avalancha reservaron IPs del pool por un tiempo, y por eso Accra
obtuvo `172.16.20.144` y no una IP más baja. Caducan solas.

> En Georgia (MikroTik, con kea-1 en su LAN) no pasa, porque el relay de RouterOS no captura el tráfico saliente.

---

## 8. OpenWrt: dnsmasq recibe el OFFER pero no se lo pasa al cliente

**Síntoma:** después de arreglar el bucle, Accra y Atenas seguían sin IP. kea-1 recibía los DISCOVER desde
`172.16.20.10` / `172.16.50.10` y respondía. En Grecia:
- `netstat -anu` mostraba dnsmasq escuchando en `0.0.0.0:67`.
- El contador `Udp InDatagrams` de `/proc/net/snmp` subía (1176 → 1211 en 8 s), así que **los OFFER llegaban al router**.
- `ip route get 172.16.10.5` era correcto, y el firewall (`fw4`) estaba desactivado.

dnsmasq tampoco registra el tráfico que relaya, así que el log no ayudaba.

**Causa:** la sección UCI `config relay` llevaba `option interface 'e7'`, y el init de OpenWrt la traduce a:

```
dhcp-relay=172.16.50.10,172.16.10.5,eth7
```

En dnsmasq 2.93, el tercer campo (interfaz) cambia cómo trata el relay las respuestas, y no reenviaba las de Kea al
cliente.

**Solución:** quitar `interface` de las secciones relay. dnsmasq deduce la interfaz a partir de `local_addr`:

```sh
uci delete dhcp.@relay[0].interface
uci delete dhcp.@relay[1].interface
uci commit dhcp
/etc/init.d/dnsmasq restart
grep -h dhcp-relay /var/etc/dnsmasq.conf.*
# dhcp-relay=172.16.50.10,172.16.10.5
# dhcp-relay=172.16.50.10,172.16.40.5
```

Atenas obtuvo `172.16.50.104` en cuanto se aplicó el cambio en Grecia. Lo mismo en Ghana: Accra → `172.16.20.144`.

---

## 9. Inconvenientes menores con las consolas de los routers

Los routers se configuraron por su consola telnet de GNS3 (`localhost:<puerto>`) con un pequeño cliente en Python, porque
Python 3.13 ya no incluye `telnetlib`. Por el camino aparecieron estos detalles:

| Situación | Solución |
|---|---|
| La consola no muestra `Login:` hasta recibir un Enter | Enviar siempre `\r` antes de esperar el prompt. |
| Gabon ya tenía la sesión iniciada (no salía `Login:`) | Comprobar primero el estado de la consola y saltar el login si hace falta. |
| VyOS: los `set` no se aplicaban | El cliente trataba como comentario las líneas que empezaban por `#`, que es justo el prompt de configuración de VyOS. Se escapó como `\#`. Conviene comprobar siempre con `show configuration commands \| grep dhcp-relay`. |
| Cisco: `write memory` se queda esperando | Pregunta `Overwrite the previous NVRAM configuration?[confirm]` porque la NVRAM se escribió con otra versión de IOS. Hay que responder con Enter y esperar `[OK]`. |
| OpenWrt: `ip -4 -br addr` no funciona | El `ip` de BusyBox no admite `-br`. Usar `uci show network` o `ip addr`. |
| OpenWrt: la salida se corta con `sleep` | Terminar el comando con `echo FIN` y esperar a ese texto en lugar del prompt. |

---

## Otras observaciones (no eran errores)

- **El RDS ya tenía una base `kea` vacía y un usuario `kea`** con permisos sobre `kea`, `kea1` y `kea2`. Se aprovecharon
  `kea1` y `kea2` en lugar de crear nombres nuevos, y no se tocó `kea`.
- **`log_bin_trust_function_creators`** ya estaba en `ON` en el RDS. Si estuviera en `OFF` con el binlog activo,
  `kea-admin db-init` fallaría al crear los triggers (error 1419) y habría que cambiarlo en el *parameter group*.
- **El aviso `Extraneous comma`** que aparece en los primeros logs viene del `kea-dhcp4.conf` de ejemplo del paquete, no
  de nuestra configuración.
- **`kea-start.sh` no captura SIGTERM.** Si acaba siendo PID 1, la parada del contenedor puede tardar unos segundos,
  hasta que Docker usa SIGKILL. No afecta al funcionamiento.
