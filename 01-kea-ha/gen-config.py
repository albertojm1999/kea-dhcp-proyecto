#!/usr/bin/env python3
# Genera kea-dhcp4.conf y kea-ctrl-agent.conf para el par HA (hot-standby).
# Uso: python3 gen-config.py <password-bd-kea>
import json
import os
import sys

RDS = "database-1.c7egi2ykw5yv.us-east-1.rds.amazonaws.com"
DB_USER = "kea"
DB_PASS = sys.argv[1] if len(sys.argv) > 1 else "__KEA_DB_PASSWORD__"
HOOKS = "/usr/lib/x86_64-linux-gnu/kea/hooks"

SERVERS = {
    "kea-1": {"ip": "172.16.10.5", "db": "kea1", "role": "primary"},
    "kea-2": {"ip": "172.16.40.5", "db": "kea2", "role": "standby"},
}
HA_PORT = 8001    # listener HTTP dedicado del HA (dentro de kea-dhcp4)
CA_PORT = 8000    # Control Agent, para administración

# Las 7 LAN del proyecto: 172.16.X0.0/24, gateway .10, pool .100-.200
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
    me = SERVERS[name]
    return {"Dhcp4": {
        "interfaces-config": {"interfaces": ["eth0"]},
        "control-socket": {"socket-type": "unix",
                           "socket-name": "/var/run/kea/kea4-ctrl-socket"},
        "lease-database": {
            "type": "mysql", "host": RDS, "port": 3306,
            "name": me["db"], "user": DB_USER, "password": DB_PASS,
            "connect-timeout": 10,
            # Si RDS no responde, sigue reintentando en vez de morir
            "max-reconnect-tries": 100, "reconnect-wait-time": 5000,
            "on-fail": "serve-retry-continue", "retry-on-startup": True,
        },
        "valid-lifetime": 3600,
        "renew-timer": 900,
        "rebind-timer": 1800,
        "option-data": [{"name": "domain-name-servers", "data": "8.8.8.8, 1.1.1.1"}],
        "hooks-libraries": [
            {"library": f"{HOOKS}/libdhcp_lease_cmds.so"},
            {"library": f"{HOOKS}/libdhcp_ha.so", "parameters": {
                "high-availability": [{
                    "this-server-name": name,
                    "mode": "hot-standby",
                    # Fallo detectado en ~10 s sin esperar a clientes sin respuesta
                    "heartbeat-delay": 3000,
                    "max-response-delay": 10000,
                    "max-ack-delay": 5000,
                    "max-unacked-clients": 0,
                    "multi-threading": {"enable-multi-threading": True,
                                        "http-dedicated-listener": True,
                                        "http-listener-threads": 0,
                                        "http-client-threads": 0},
                    "peers": [
                        {"name": n, "url": f"http://{s['ip']}:{HA_PORT}/",
                         "role": s["role"], "auto-failover": True}
                        for n, s in SERVERS.items()
                    ],
                }],
            }},
        ],
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
        os.chmod(path, 0o640)
    print("escrito", d)
