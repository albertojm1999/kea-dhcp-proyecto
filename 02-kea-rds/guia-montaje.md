# Propuesta 2 — Failover de Kea DHCP usando una base de datos RDS compartida

> **Propuesta 2 de la práctica: failover usando una base de datos RDS.**
> Los dos servidores Kea trabajan **sin el módulo de alta disponibilidad**. Están activos a la vez y comparten **una sola
> base de datos** MySQL en AWS RDS. El estado común de los leases lo guarda la base de datos, no un protocolo entre
> servidores.

En esta carpeta:
- `errores-y-soluciones.md`
- `config/`: backup de la configuración de cada servidor.
- `gen-config.py`: genera la configuración.
- `activar.sh`: carga esta propuesta en los contenedores.

Usa la misma imagen (`../00-imagen-kea/`), la misma topología, los mismos nodos `kea-1`/`kea-2` y los mismos relays que
la propuesta 1 (`../01-kea-ha/guia-montaje.md`, §1, §3, §6, §7 y §8). Aquí solo se describe lo que cambia.

---

## 1. Cómo funciona

```
          relays de los 7 routers (unicast a los dos servidores)
                 │                                 │
           ┌─────▼─────┐                     ┌─────▼─────┐
           │  kea-1    │   (sin HA, no se    │  kea-2    │
           │172.16.10.5│    hablan entre sí) │172.16.40.5│
           └─────┬─────┘                     └─────┬─────┘
                 │      MySQL 3306 (vía Gambia/NAT)│
                 └──────────────┬──────────────────┘
                        ┌───────▼────────┐
                        │  AWS RDS MySQL │
                        │  base: kea     │  ← UNA tabla lease4 para los dos
                        └────────────────┘
```

- **Los dos servidores reciben todas las peticiones**, porque los relays mandan cada petición a los dos, y **los dos
  responden**. El cliente se queda con la primera oferta que le llega.
- **Antes de asignar una IP, Kea la escribe en la base.** Si los dos servidores intentan dar la misma dirección a la vez,
  el segundo `INSERT` falla por clave duplicada y Kea prueba otra dirección. **La base de datos hace de árbitro.**
- **Si cae un servidor,** el otro ya tiene todos los leases porque comparten la base: renueva las mismas IPs sin
  sincronizar nada.
- **Comparado con la propuesta 1:** no hay estados HA (`HOT-STANDBY`, `PARTNER-DOWN`…), ni puerto 8001, ni detección
  de caída. El failover es inmediato, porque los dos están siempre activos. A cambio, **el punto único de fallo pasa a
  ser la base de datos** (ver §6).

| | Propuesta 1 (módulo HA) | Propuesta 2 (BD compartida) |
|---|---|---|
| Hook | `libdhcp_lease_cmds.so` + `libdhcp_ha.so` | ninguno |
| Bases de datos | `kea1` y `kea2` (una por servidor) | `kea` (compartida) |
| Servidores que responden | solo el primario (el otro espera) | los dos a la vez |
| Detección de caída | heartbeat, ~10 s | no hace falta |
| Sincronización | lease updates por HTTP (8001) | la propia base de datos |
| Punto único de fallo | ninguno (cada uno tiene su BD) | la base de datos RDS |

---

## 2. Base de datos

Se usa la base `kea`, que ya existía vacía y sobre la que el usuario `kea` ya tenía permisos. Solo hay que cargar el
esquema:

```bash
H=database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com
docker run --rm kea-dhcp bash -c "
  mariadb -h $H -u admin -p'<PASS_ADMIN>' -e \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='kea';\" &&
  kea-admin db-init mysql -h $H -u admin -p '<PASS_ADMIN>' -n kea &&
  mariadb -h $H -u kea -p'<PASS_KEA>' -e 'SELECT version,minor FROM kea.schema_version;'"
# → 0 tablas antes · Schema version reported after initialization: 22.2
```

Si partes de cero:

```sql
CREATE DATABASE kea;
CREATE USER 'kea'@'%' IDENTIFIED BY '<PASS_KEA>';
GRANT ALL PRIVILEGES ON kea.* TO 'kea'@'%';
```

---

## 3. Configuración de Kea

```bash
cd kea-dhcp-proyecto/02-kea-rds
python3 gen-config.py '<PASS_KEA>'      # genera config/kea-1 y config/kea-2
```

Diferencias con la propuesta 1 en `kea-dhcp4.conf`:

| Parámetro | Valor | Por qué |
|---|---|---|
| `lease-database.name` | `"kea"` **en los dos** | Base compartida. |
| `hooks-libraries` | *(no hay)* | Sin módulo HA. |
| `allocator` | `"random"` | Cada servidor recorre el pool en orden aleatorio, así es raro que los dos elijan la misma IP a la vez. Con `iterative` los dos empezarían siempre por `.100` y chocarían en todas las asignaciones. |
| `offer-lifetime` | `60` | La **oferta** se guarda en la BD durante 60 s, para que el otro servidor no ofrezca esa IP a otro cliente. Sin esto, la IP solo se escribe en el ACK. |
| `authoritative` | `true` | Si un cliente pide una IP que en la base es de otro, se le contesta NAK y vuelve a empezar. |
| `subnet4` / pools | **iguales** en los dos (`.100 - .200`) | Gracias a la BD compartida, los dos pueden repartir todo el rango. |

Lo demás (subredes con `id` 1–7, router `.10`, DNS, tiempos, `on-fail: serve-retry-continue`, logs, socket en
`/var/run/kea`) es igual que en la propuesta 1. `kea-ctrl-agent.conf` también es igual.

Validar:

```bash
for s in kea-1 kea-2; do
  docker run --rm -v "$PWD/config/$s:/etc/kea:ro" kea-dhcp bash -c '
    mkdir -p /run/kea /var/log/kea
    kea-dhcp4 -t /etc/kea/kea-dhcp4.conf >/dev/null 2>&1; echo "'$s' rc=$?"'
done
```

---

## 4. Activar la propuesta

Los nodos `kea-1` y `kea-2` de GNS3 son los mismos que en la propuesta 1. Solo cambia la configuración que llevan
dentro:

```bash
./activar.sh
```

El script copia `config/kea-N/*.conf` dentro de cada contenedor con `docker exec -i ... cat >` (no con `docker cp`, ver
`../01-kea-ha/errores-y-soluciones.md` #4), valida la configuración con `kea-dhcp4 -t` y reinicia `kea-dhcp4`,
`kea-ctrl-agent` y `kea-exporter`. El bucle de `kea-start.sh` los vuelve a lanzar a los ~10 s.

Comprobar que los dos usan la base compartida:

```bash
for n in 1 2; do docker logs --since 1m GNS3.kea-$n.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 2>&1 \
  | grep -E 'MYSQL_DB|DHCP4_STARTED' | cut -c1-150; done
# DHCPSRV_MYSQL_DB opening MySQL lease database: ... name=kea ...
# DHCP4_STARTED Kea DHCPv4 server version 2.6.5 started
```

Para volver a la propuesta 1: `../01-kea-ha/activar.sh`.

> No hace falta tocar los routers: los relays ya envían cada petición a los dos servidores. Granada solo relaya a
> kea-1, pero kea-2 recibe directamente el broadcast de su LAN (ver la propuesta 1, §7.3).

---

## 5. Verificación

### 5.1 Reparto de IPs

Después de reiniciar los 7 hosts (*Stop* + *Start* en GNS3):

```
Tiflis      172.16.10.110/24 internet-OK
Accra       172.16.20.167/24 internet-OK
Libreville  172.16.30.145/24 internet-OK
Dover       172.16.40.134/24 internet-OK
Atenas      172.16.50.104/24 internet-OK
Conakry     172.16.60.158/24 internet-OK
Flores      172.16.70.116/24 internet-OK
```

Las IPs ya no salen en orden (`.100`, `.101`…) por el asignador `random`.

### 5.2 Los dos servidores están activos

```bash
for n in 1 2; do printf "kea-$n ACK enviados: "
  docker exec GNS3.kea-$n.ddbb5ebf-6207-41d5-95fe-f5e16a380d85 sh -c \
    "curl -s localhost:9547/metrics | grep 'packets_sent_total{operation=\"ack\"}' | awk '{print \$2}'"
done
# kea-1 ACK enviados: 3.0
# kea-2 ACK enviados: 5.0       ← los dos han confirmado leases
```

### 5.3 La tabla de leases compartida

```bash
docker run --rm kea-dhcp mariadb -h database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com -u kea -p'<PASS_KEA>' -e \
  "SELECT INET_NTOA(address) ip, HEX(hwaddr) mac, state, valid_lifetime, expire FROM kea.lease4 ORDER BY address;"
```

```
ip              mac            state  valid_lifetime  expire
172.16.10.110   0242EF8B3300   0      3600            19:12:20   ← lease real
172.16.40.134   024235977800   0      3600            19:12:26   ← lease real
172.16.40.136   024235977800   0      60              18:13:22   ← oferta del servidor que "perdió"
...
```

Las filas con `valid_lifetime = 60` son las **ofertas** del servidor que no fue elegido por el cliente
(`offer-lifetime`). Caducan a los 60 s y Kea las recupera solo. Es el coste de tener dos servidores activos a la vez.

---

## 6. Prueba de failover

```bash
# 1) Anotar las IPs actuales de los 7 hosts
# 2) Parar kea-1
curl -s -u "admin:$P" -X POST $API/nodes/45815ec3-3e43-4af5-863f-f0bd1acecbd9/stop
# 3) Reiniciar los 7 hosts (Stop + Start): piden su IP anterior (INIT-REBOOT)
# 4) Comparar
```

Resultado obtenido (kea-1 parado a las 20:13:22):

```
host        antes             después
Tiflis      172.16.10.110/24  172.16.10.110/24  igual
Accra       172.16.20.167/24  172.16.20.167/24  igual
Libreville  172.16.30.145/24  172.16.30.145/24  igual
Dover       172.16.40.134/24  172.16.40.134/24  igual
Atenas      172.16.50.104/24  172.16.50.104/24  igual
Conakry     172.16.60.158/24  172.16.60.158/24  igual
Flores      172.16.70.116/24  172.16.70.116/24  igual
kea-2 leases asignados en la prueba: 7
```

kea-2 confirmó los 7 leases, incluidos los que había dado kea-1, porque los encontró en la base compartida. Ningún
cliente cambió de IP.

Al volver a arrancar kea-1, se conecta a la base y sigue sirviendo sin sincronizar nada.

> Un cliente que solo **renueva** (sin reiniciar) envía la renovación en unicast al servidor que le dio la IP. Si ese
> servidor está caído, al llegar a T2 (`rebind-timer`, 1800 s) el cliente pasa a broadcast, el relay lo envía a los
> dos, y el servidor que queda le renueva la misma IP.

---

## 7. Alta disponibilidad de la base de datos (cursos de AWS)

En esta propuesta **la base de datos es el punto único de fallo**: si RDS cae, ninguno de los dos servidores puede
asignar leases. Con `on-fail: serve-retry-continue` siguen funcionando, pero sin guardar nada.

Lo que se ve en los cursos de AWS para evitarlo:

| Medida | Qué aporta |
|---|---|
| **RDS Multi-AZ** | Una réplica en espera en otra zona de disponibilidad con replicación síncrona. Si la principal cae, AWS cambia el **mismo endpoint** a la réplica en 1–2 min, sin tocar la configuración de Kea. Es la opción adecuada aquí. |
| **Read replicas** | Solo sirven para lectura y la replicación es asíncrona. **No** sirven para Kea, que necesita escribir. |
| **Amazon Aurora MySQL** | Almacenamiento replicado en 3 AZ y failover más rápido (~30 s). Tiene el mismo endpoint de escritura. |
| **Backups automáticos / snapshots** | Restauración a un momento concreto (PITR). Con los backups activados, el binlog está activo, y Kea necesita `log_bin_trust_function_creators = ON` en el *parameter group*. |
| **Security Group** | Limitar el puerto 3306 a la IP pública de salida (NAT de Gambia), no a `0.0.0.0/0`. |
| **Usuario de mínimos privilegios** | Kea usa `kea` (solo su base), no `admin`. |

Para comprobar si la instancia es Multi-AZ: consola de AWS → RDS → *database-1* → pestaña *Configuration* → campo
*Multi-AZ*. Con la CLI:

```bash
aws rds describe-db-instances --db-instance-identifier database-1 --query 'DBInstances[0].MultiAZ'
```

---

## 8. Monitorización

kea-exporter, Prometheus y Grafana funcionan igual con esta propuesta (ver `../03-monitorizacion/`). En el panel
"Servidor que está respondiendo" se ven **los dos servidores enviando ACK a la vez**. En la propuesta 1, en cambio, solo
envía el primario.
