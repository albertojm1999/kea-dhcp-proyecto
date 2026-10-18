# Propuesta 2 — Errores encontrados y cómo se solucionaron

Esta propuesta reutiliza la imagen, los nodos y los relays de la propuesta 1. Los problemas de esa parte (imagen, GNS3,
bucle de relay en Granada, dnsmasq en OpenWrt…) están en `../01-kea-ha/errores-y-soluciones.md`. Aquí solo aparece lo
nuevo.

| # | Problema | Gravedad |
|---|---|---|
| 1 | `activar.sh` se mata a sí mismo con `pkill -f kea-exporter` | Bloqueante |
| 2 | Filas "de más" en `kea.lease4` (ofertas duplicadas) | Esperado (no es un fallo) |
| 3 | Elección de asignador y de `offer-lifetime` | Diseño |

---

## 1. `activar.sh` termina con código 143

**Síntoma:** al ejecutar `./activar.sh`, el script terminaba sin mensajes y con `Exit code 143` (128 + 15 = SIGTERM),
después de copiar los archivos en kea-1 y antes de llegar a kea-2.

**Causa:** para reiniciar el exportador, el script ejecutaba dentro del contenedor:

```bash
docker exec $C sh -c '... pkill kea-dhcp4; pkill kea-ctrl-agent; pkill -f kea-exporter; true'
```

`pkill -f` busca el patrón en **la línea de comandos completa** de cada proceso, y la del propio `sh -c '... pkill -f
kea-exporter ...'` también contiene el texto `kea-exporter`. `pkill` mataba el shell que lo ejecutaba, `docker exec`
devolvía 143 y `set -e` paraba el script.

**Solución:** el truco del corchete en la expresión regular:

```bash
pkill -f "[k]ea-exporter -a"
```

La expresión `[k]ea-exporter` encaja con el texto `kea-exporter` del proceso real, pero no con la línea de comandos del
shell, que contiene literalmente `[k]ea-exporter` (después de `]` viene `ea`, no `kea`). Se corrigió en los dos
`activar.sh` (propuesta 1 y 2).

---

## 2. Filas "de más" en la tabla de leases

**Observación:** con 7 clientes, `kea.lease4` tenía 11 filas. Algunos clientes aparecían dos veces con IPs distintas:

```
172.16.40.134   024235977800   valid_lifetime 3600   ← lease real
172.16.40.136   024235977800   valid_lifetime 60     ← ¿?
```

**Causa:** los relays envían cada DISCOVER **a los dos servidores**. Los dos consultan la base casi a la vez, ninguno
encuentra un lease para ese cliente, y cada uno elige una IP al azar (`allocator: random`) y la guarda como **oferta**
(`offer-lifetime: 60`). El cliente acepta solo una oferta. La del otro servidor se queda en la base como fila de
60 s.

**No es un fallo:** es el coste de tener dos servidores activos sobre la misma base. Esas filas caducan a los 60 s y Kea
las recupera solo (`addresses_reclaimed_total` en las métricas). Con 101 direcciones por pool no afecta a la capacidad.
Si molestara, se podría bajar `offer-lifetime`, o hacer que uno de los servidores conteste algo más tarde.

---

## 3. Por qué `random` y `offer-lifetime` (decisión de diseño)

Con la configuración por defecto (asignador `iterative` y `offer-lifetime: 0`), los dos servidores:
1. recorren el pool en el mismo orden (`.100`, `.101`, …), así que eligen **la misma IP** para clientes distintos;
2. no la escriben en la base hasta el ACK, así que pueden **ofrecer la misma IP a dos clientes**.

Al final la base lo resolvería: el segundo `INSERT` falla por clave duplicada y ese cliente recibe NAK y vuelve a
empezar. Pero sería a costa de NAKs y reintentos. Con `random` las colisiones son poco probables, y con
`offer-lifetime` la IP queda reservada desde la oferta.
