"""Cadastro local de hosts e deployments, sem edição manual do JSON."""

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from . import core


def read_inventory(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        return core.inventory(path)
    return dict(schema=1, hosts={}, deployments={})


def save_inventory(path, data, original):
    """Valida antes de substituir e recusa alterações concorrentes observáveis."""
    core.validate_inventory(data)
    path = Path(path)
    core.require(not path.is_symlink(), "Inventário não pode ser symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".inventory-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        core.require(not path.is_symlink(), "Inventário não pode ser symlink.")
        current = core.regular_file(path).read_bytes() if path.exists() else None
        core.require(current == original, "Inventário mudou durante o cadastro; execute novamente.")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def snapshot(path):
    path = Path(path)
    return core.regular_file(path).read_bytes() if path.exists() or path.is_symlink() else None


def relative_path(base, value):
    path = Path(value).expanduser().absolute()
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # Discos diferentes no Windows.
        return str(path)


def assignments(values, label):
    result = {}
    for assignment in values:
        key, separator, value = assignment.partition("=")
        core.require(
            separator and key and (value or label == "--set") and key not in result,
            f"{label} exige KEY=value sem duplicatas.",
        )
        result[key] = value
    return result


def put(records, name, value, replace):
    core.require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name), "Nome inválido para cadastro.")
    previous = records.get(name)
    core.require(
        previous is None or previous == value or replace,
        f"Cadastro {name} já existe; use --replace para alterar.",
    )
    records[name] = value
    return "created" if previous is None else "unchanged" if previous == value else "updated"


def register(args):
    path = Path(args.inventory)
    original = snapshot(path)
    data = read_inventory(path)
    host = dict(data["hosts"].get(args.host, {}))
    host.update(address=args.address, user=args.user)
    host["port"] = args.port if args.port is not None else host.get("port", 22)
    for key in ("identity_file", "known_hosts", "host_key_sha256"):
        value = getattr(args, key)
        if value:
            if key != "host_key_sha256":
                value = relative_path(path.parent, core.regular_file(Path(value).expanduser()))
            host[key] = value
    state = put(data["hosts"], args.host, host, args.replace)
    core.validate_inventory(data)
    name = None
    if args.service:
        meta = core.metadata(args.service)
        config_path = (
            Path(args.config).expanduser().absolute()
            if args.config
            else core.ROOT / meta["folder"] / "linux/.env.example"
        )
        config = core.env_file(config_path)
        keys = set(core.env_file(core.ROOT / meta["folder"] / "linux/.env.example"))
        core.require(set(config) <= keys, "Chave não suportada no .env do serviço.")
        overrides = assignments(args.set_values, "--set")
        inputs = assignments(args.files, "--file")
        core.require(set(overrides) <= set(config), "Override exige uma chave já presente no .env.")
        core.require(
            set(inputs) <= core.FILE_KEYS & set(config), "Chave de --file não suportada pelo serviço."
        )
        for key, value in dict(
            DOMAIN=args.domain, ACCESS_CIDRS=args.cidrs, SSH_CIDRS=args.ssh_cidrs or args.cidrs
        ).items():
            if value:
                overrides[key] = value
        overrides["SSH_PORT"] = str(host["port"])
        effective = {**config, **overrides}
        core.require(
            args.config or args.domain or "DOMAIN" in overrides, "Informe --domain ou um --config próprio."
        )
        core.require(
            args.config or args.cidrs or {"ACCESS_CIDRS", "SSH_CIDRS"} <= set(overrides),
            "Informe --cidrs ou um --config próprio.",
        )
        for key, value in effective.items():
            core.require(
                isinstance(value, str) and not re.search(r"[\s\"\'`$;\\]", value),
                "Valor literal inválido em " + key,
            )
        domain = effective.get("DOMAIN", "")
        core.require(
            len(domain) <= 253
            and "." in domain
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")
            ),
            "DOMAIN exige um nome DNS válido em minúsculas.",
        )
        for key in ("ACCESS_CIDRS", "SSH_CIDRS"):
            for value in effective.get(key, "").split(","):
                try:
                    network = ipaddress.ip_network(value, strict=True)
                except ValueError:
                    raise core.InstallerError("CIDR inválido em " + key) from None
                core.require(
                    key != "ACCESS_CIDRS" or meta["proxy_kind"] == "http" or network.prefixlen > 0,
                    "PostgreSQL/Redis exigem ACCESS_CIDRS restritos; /0 não é permitido.",
                )
        if args.service == "technitium":
            if "DNS_LISTEN_IPV4" not in overrides and not args.config:
                try:
                    address = ipaddress.IPv4Address(host["address"])
                except ValueError:
                    raise core.InstallerError(
                        "Informe --set DNS_LISTEN_IPV4=IP_FIXO para o host DNS."
                    ) from None
                overrides["DNS_LISTEN_IPV4"] = str(address)
            if args.cidrs and "DNS_CIDRS" not in overrides:
                overrides["DNS_CIDRS"] = args.cidrs
        inputs = {
            key: relative_path(path.parent, core.regular_file(Path(value).expanduser()))
            for key, value in inputs.items()
        }
        manifest = (
            Path(args.manifest).expanduser().absolute()
            if args.manifest
            else core.ROOT / "artifacts/cdn/ubuntu26-amd64/manifest.json"
        )
        item = dict(
            host=args.host,
            service=args.service,
            config=relative_path(path.parent, config_path),
            manifest=relative_path(path.parent, manifest),
            overrides=overrides,
            files=inputs,
        )
        name = args.deployment or f"{args.service}-{args.host}"
        put(data["deployments"], name, item, args.replace)
    else:
        core.require(
            not any(
                (
                    args.domain,
                    args.cidrs,
                    args.ssh_cidrs,
                    args.config,
                    args.manifest,
                    args.deployment,
                    args.set_values,
                    args.files,
                )
            ),
            "Opções do deployment exigem --service.",
        )
    if original is None or json.loads(original) != data:
        save_inventory(path, data, original)
    pending = []
    known = (
        core.local_path(path.parent, host["known_hosts"])
        if host.get("known_hosts")
        else Path.home() / ".ssh/known_hosts"
    )
    if not host.get("host_key_sha256") and not known.is_file():
        pending.append(
            "Confiança SSH: use um known_hosts confiável ou registre --host-key-sha256 após conferir a chave."
        )
    if name:
        try:
            core.deployment(
                SimpleNamespace(inventory=str(path), deployment=name, action="install"), prepare=True
            )
        except (core.InstallerError, ValueError, KeyError) as error:
            pending.append(str(error))
    return dict(
        state=state,
        inventory=str(path),
        host=args.host,
        address=host["address"],
        deployments=[name] if name else [],
        pending=pending,
    )


def register_discovered(args, records):
    original = snapshot(args.inventory)
    data = read_inventory(args.inventory)
    core.require(args.user, "--register exige --user.")
    for record in records:
        matching = [
            name
            for name, host in data["hosts"].items()
            if host["address"] == record["address"] and host.get("port", 22) == record["port"]
        ]
        if matching:
            name = matching[0]
            core.require(
                data["hosts"][name]["user"] == args.user, "Host já cadastrado com outro usuário: " + name
            )
        else:
            name = "host-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", record["address"])
            if record["port"] != 22:
                name += f"-ssh{record['port']}"
            put(
                data["hosts"],
                name,
                dict(address=record["address"], port=record["port"], user=args.user),
                False,
            )
        record.update(host=name, registered=True)
    if records and (original is None or json.loads(original) != data):
        save_inventory(args.inventory, data, original)
