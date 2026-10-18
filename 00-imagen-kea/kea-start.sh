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