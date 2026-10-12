"""Sondagens de DNS/SSH sem autenticação, limitadas aos destinos informados."""

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import core
from .transport import fingerprint, ssh_module


def targets(values, limit=256):
    core.require(1 <= limit <= 4096, "--max-hosts deve estar entre 1 e 4096.")
    result = {}
    for value in values:
        if "/" in value:
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError:
                raise core.InstallerError("Rede inválida: " + value) from None
            count = network.num_addresses
            if network.version == 4 and network.prefixlen < 31:
                count -= 2
            elif network.version == 6 and network.prefixlen < 127:
                count -= 1
            core.require(count <= limit, "Rede excede --max-hosts; informe uma faixa menor.")
            candidates = (str(address) for address in network.hosts())
        else:
            core.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", value), "Destino inválido: " + value)
            candidates = [value]
        for address in candidates:
            result[address] = None
            core.require(len(result) <= limit, "Destinos excedem --max-hosts.")
    return list(result)


def trust(host, key):
    if host.get("host_key_sha256"):
        return "verified" if fingerprint(key) == host["host_key_sha256"] else "mismatch"
    known = Path(host["known_hosts"]) if host.get("known_hosts") else Path.home() / ".ssh/known_hosts"
    if not known.is_file():
        return "unknown"
    keys = ssh_module().HostKeys(str(core.regular_file(known)))
    hostname = host["address"] if host.get("port", 22) == 22 else f"[{host['address']}]:{host['port']}"
    entry = keys.lookup(hostname)
    if not entry or key.get_name() not in entry:
        return "unknown"
    return "verified" if entry[key.get_name()] == key else "mismatch"


def probe(host, timeout):
    started = time.monotonic()
    record = dict(
        address=host["address"],
        port=host.get("port", 22),
        state="unreachable",
        resolved_addresses=[],
        ssh_trust="unknown",
    )
    try:
        addresses = socket.getaddrinfo(host["address"], record["port"], type=socket.SOCK_STREAM)
        record["resolved_addresses"] = sorted({entry[4][0] for entry in addresses})
        paramiko = ssh_module()
        with socket.create_connection((host["address"], record["port"]), timeout=timeout) as connection:
            record["state"] = "tcp_only"
            transport = paramiko.Transport(connection)
            try:
                transport.start_client(timeout=timeout)
                key = transport.get_remote_server_key()
                record.update(
                    state="reachable",
                    fingerprint=fingerprint(key),
                    key_type=key.get_name(),
                    ssh_trust=trust(host, key),
                )
                if record["ssh_trust"] == "mismatch":
                    record.update(
                        state="key_mismatch", error="Fingerprint SSH diverge da confiança cadastrada."
                    )
            finally:
                transport.close()
    except Exception as error:
        record["error"] = str(error)
    record["duration_ms"] = round((time.monotonic() - started) * 1000)
    return record


def scan(hosts, timeout, workers=16):
    core.require(1 <= workers <= 64, "--workers deve estar entre 1 e 64.")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda host: probe(host, timeout), hosts))


def host_records(args):
    data = core.inventory(args.inventory)
    names = args.hosts or sorted(data["hosts"])
    core.require(all(name in data["hosts"] for name in names), "Host ausente no inventário.")
    records = []
    for name in names:
        source = data["hosts"][name]
        row = dict(
            host=name,
            address=source["address"],
            port=source.get("port", 22),
            user=source["user"],
            state="registered",
            deployments=sorted(key for key, item in data["deployments"].items() if item.get("host") == name),
        )
        if args.check:
            try:
                row.update(probe(core.inventory_host(args.inventory, name), args.connect_timeout))
            except Exception as error:
                row.update(state="failed", error=str(error))
        records.append(row)
    return records
