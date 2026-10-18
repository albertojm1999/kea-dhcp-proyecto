# Prometheus para GNS3: la imagen oficial corre como "nobody" (sin shell) y el
# init.sh de GNS3 no puede lanzar el proceso con ese usuario -> se ejecuta como root.
FROM prom/prometheus:latest
USER root
