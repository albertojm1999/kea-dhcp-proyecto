# Monitorización — Errores encontrados y cómo se solucionaron

| # | Problema | Gravedad |
|---|---|---|
| 1 | Prometheus y Grafana no arrancan en GNS3 (`Exited (1)`, `This account is not available`) | Bloqueante |
| 2 | GNS3 no permite cambiar la imagen de un nodo Docker existente | Bloqueante |
| 3 | Los contenedores Kea seguían con la imagen antigua (sin kea-exporter) | Bloqueante |
| 4 | kea-exporter no publica el estado del HA | Limitación |
| 5 | `pip install` en Debian 12 | Prevenido |

---

## 1. Prometheus y Grafana parados con `Exited (1)`

**Síntoma:** en GNS3 los nodos Prometheus-1 y Grafana-1 aparecían como *started*, pero los contenedores llevaban horas
parados:

```
GNS3.Grafana-1...    | Exited (1) 2 hours ago
GNS3.Prometheus-1... | Exited (1) 2 hours ago
```

El log de Grafana decía solo `This account is not available`. El de Prometheus estaba vacío.

**Causa:** las imágenes oficiales definen un usuario sin shell (`USER nobody` en Prometheus y `USER 472` en Grafana).
GNS3 arranca el contenedor como root con su propio `/gns3/init.sh`, que al final lanza el proceso de la imagen así:

```sh
exec su ${GNS3_USER-root} -p -- /gns3/run-cmd.sh "$OLD_PATH" "$@"
```

`su` a un usuario con shell `/sbin/nologin` falla con `This account is not available`, y el contenedor termina.

**Diagnóstico:**
```bash
docker image inspect -f 'User={{.Config.User}}' prom/prometheus:latest grafana/grafana:latest
# User=nobody   User=472
grep -n GNS3_USER /usr/share/gns3/gns3-server/lib/python3*/site-packages/gns3server/compute/docker/resources/init.sh
```

**Solución:** imágenes derivadas que solo cambian el usuario:

```dockerfile
FROM prom/prometheus:latest      # o grafana/grafana:latest
USER root
```

Con `USER root`, GNS3 define `GNS3_USER=root`, y `su root` sí funciona porque root tiene shell. El proceso arranca
como root.

---

## 2. No se puede cambiar la imagen de un nodo Docker en GNS3

**Síntoma:** un `PUT /nodes/<id>` con `"image":"prometheus-gns3:latest"` respondía sin error, pero el nodo seguía con
`prom/prometheus:latest`. Los otros cambios del mismo PUT (adaptadores, consola) sí se aplicaban.

**Causa:** en GNS3 2.2 la imagen de un nodo Docker solo se define al crearlo. No hay forma de cambiarla después, y el
servidor ignora ese campo sin avisar.

Tampoco se puede cambiar el usuario con una variable de entorno: GNS3 descarta las variables del nodo que empiezan por
`GNS3_` (`if not e.startswith("GNS3_")` en `docker_vm.py`).

**Solución:** sustituir los nodos. Se guardó una copia de sus carpetas, se borraron los nodos antiguos (no estaban
conectados ni configurados) y se crearon `Prometheus-1` y `Grafana-1` nuevos con las imágenes derivadas, con el mismo
nombre y en la misma posición.

---

## 3. Los contenedores Kea seguían con la imagen antigua

**Síntoma:** después de reconstruir `kea-dhcp:latest` con kea-exporter, los contenedores `kea-1` y `kea-2` no tenían
`/opt/kea-exporter`.

**Causa:** GNS3 crea el contenedor a partir de la imagen **al crear o cargar el nodo**. Reconstruir la imagen con el
mismo nombre no afecta a los contenedores que ya existen. Un *Stop/Start* tampoco, porque reutiliza el mismo
contenedor.

**Solución:** con el nodo parado, cambiar una propiedad que obligue a GNS3 a **recrear** el contenedor:

```bash
curl -s -u "admin:$P" -X PUT $API/nodes/<ID> -d '{"properties":{"environment":"KEA_EXPORTER_PORT=9547"}}'
docker inspect -f '{{.Image}}' GNS3.kea-1.<proyecto>     # → ya es la imagen nueva
```

Como `/etc/kea` está en un volumen con `.gns3_perms` ya creado, la configuración de Kea **no se perdió**.
Se comprobó con `grep -c database-1 /etc/kea/kea-dhcp4.conf` → `1`.

(Cerrar y volver a abrir el proyecto en GNS3 también recrea los contenedores.)

---

## 4. kea-exporter no publica el estado del HA

**Limitación:** kea-exporter 0.7.1 solo publica las estadísticas de `statistic-get-all` (direcciones y paquetes). No
publica el estado del módulo HA (`HOT-STANDBY`, `PARTNER-DOWN`…).

**Cómo se suple en el panel:**
- `up{job="kea"}` indica qué servidor no responde (alerta `KeaServidorCaido`).
- El panel *Paquetes enviados por servidor* muestra que, en la propuesta 1, los ACK pasan de kea-1 a kea-2 cuando cae
  el primario.
- El estado exacto del HA se puede consultar a mano en el Control Agent:
  ```bash
  docker exec GNS3.kea-1.<proyecto> curl -s -X POST -H 'Content-Type: application/json' \
    -d '{"command":"ha-heartbeat","service":["dhcp4"]}' http://172.16.10.5:8000/
  ```

---

## 5. `pip install` en Debian 12 (prevenido)

Debian 12 marca el Python del sistema como "gestionado externamente" (PEP 668), y `pip install kea-exporter` daría
`error: externally-managed-environment`. Para evitarlo, se instaló en un entorno virtual:

```dockerfile
RUN python3 -m venv /opt/kea-exporter \
 && /opt/kea-exporter/bin/pip install --no-cache-dir kea-exporter==0.7.1
```

Se fija la versión (`==0.7.1`) para que una versión nueva del exportador no cambie las métricas que usa el panel.
