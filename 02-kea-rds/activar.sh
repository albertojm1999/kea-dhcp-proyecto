#!/bin/bash
# Carga en kea-1 y kea-2 (GNS3) la configuración de ESTA carpeta y reinicia Kea.
# Uso: ./activar.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PROYECTO=ddbb5ebf-6207-41d5-95fe-f5e16a380d85
for n in 1 2; do
  C=GNS3.kea-$n.$PROYECTO
  for f in kea-dhcp4.conf kea-ctrl-agent.conf; do
    docker exec -i "$C" sh -c "cat > /etc/kea/$f" < "$DIR/config/kea-$n/$f"
  done
  docker exec "$C" sh -c 'chown _kea:_kea /etc/kea/*.conf; chmod 640 /etc/kea/*.conf
                          kea-dhcp4 -t /etc/kea/kea-dhcp4.conf >/dev/null
                          pkill kea-dhcp4; pkill kea-ctrl-agent; pkill -f "[k]ea-exporter -a"; true'
  echo "kea-$n: configuración de $(basename "$DIR") cargada"
done
echo "kea-start.sh relanza los procesos en ~10 s"
