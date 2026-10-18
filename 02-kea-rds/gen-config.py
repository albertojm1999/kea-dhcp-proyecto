#!/usr/bin/env python3
# Propuesta 2: failover usando una base de datos RDS compartida (sin hook HA).
# Los dos servidores están activos a la vez y comparten la base "kea"; la base de
# datos es la que mantiene el estado común de los leases.
# Uso: python3 gen-config.py <password-bd-kea>
import json
import os
import sys

RDS = "database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com"
DB_NAME = "kea"            # UNA sola base para los dos servidores
DB_USER = "kea"
DB_PASS = sys.argv[1] if len(sys.argv) > 1 else "__KEA_DB_PASSWORD__"

SERVERS = {
    "kea-1": {"ip": "172.16.10.5"},
    "kea-2": {"ip": "172.16.40.5"},
}
CA_PORT = 8000

# Mismas 7 LAN y mismos pools en los dos servidores: la base de datos hace de
# árbitro (un INSERT duplicado falla y Kea prueba otra dirección).
SUBNETS = [
    {
        "id": n,
        "subnet": f"172.16.{n}0.0/24",
        "pools": [{"pool": f"172.16.{n}0.100 - 172.16.{n}0.200"}],
        "option-data": [{"name": "routers", "data": f"172.16.{n}0.10"}],
    }
    for n in range(1, 8)
]


def dhcp4(name):
    return {"Dhcp4": {
        "interfaces-config": {"interfaces": ["eth0"]},
        "control-socket": {"socket-type": "unix",
                           "socket-name": "/var/run/kea/kea4-ctrl-socket"},
        "lease-database": {
            "type": "mysql", "host": RDS, "port": 3306,
            "name": DB_NAME, "user": DB_USER, "password": DB_PASS,
            "connect-timeout": 10,
            "max-reconnect-tries": 100, "reconnect-wait-time": 5000,
            "on-fail": "serve-retry-continue", "retry-on-startup": True,
        },
        # Cada servidor recorre el pool en orden aleatorio: menos choques entre ellos
        "allocator": "random",
        # La oferta se guarda en la BD 60 s: el otro servidor no ofrece la misma IP a otro cliente
        "offer-lifetime": 60,
        # Si un cliente pide (INIT-REBOOT) una IP que no es suya, se le responde NAK
        "authoritative": True,
        "valid-lifetime": 3600,
        "renew-timer": 900,
        "rebind-timer": 1800,
        "option-data": [{"name": "domain-name-servers", "data": "8.8.8.8, 1.1.1.1"}],
        "subnet4": SUBNETS,
        "loggers": [{
            "name": "kea-dhcp4",
            "output-options": [{"output": "/var/log/kea/kea-dhcp4.log",
                                "maxsize": 10485760, "maxver": 3},
                               {"output": "stdout"}],
            "severity": "INFO",
        }],
    }}


def ctrl_agent(name):
    return {"Control-agent": {
        "http-host": SERVERS[name]["ip"],
        "http-port": CA_PORT,
        "control-sockets": {"dhcp4": {"socket-type": "unix",
                                      "socket-name": "/var/run/kea/kea4-ctrl-socket"}},
        "loggers": [{"name": "kea-ctrl-agent",
                     "output-options": [{"output": "/var/log/kea/kea-ctrl-agent.log"}],
                     "severity": "INFO"}],
    }}


out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
for name in SERVERS:
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    for fname, data in (("kea-dhcp4.conf", dhcp4(name)),
                        ("kea-ctrl-agent.conf", ctrl_agent(name))):
        path = os.path.join(d, fname)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(path, 0o600)
    print("escrito", d)
