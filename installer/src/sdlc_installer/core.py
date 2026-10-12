"""Inventário, validação e montagem de payloads locais."""

import hashlib
import io
import json
import re
import tarfile
from importlib.resources import files
from pathlib import Path

from .paths import repository

try:
    ROOT = repository()
except ValueError:
    ROOT = Path.cwd()
CATALOG = json.loads(files("sdlc_installer").joinpath("resources/catalog.json").read_text(encoding="utf-8"))
FILE_KEYS = {"TLS_CERT_PATH", "TLS_KEY_PATH", "DB_PASSWORD_FILE", "DB_CA_PATH", "DNS_RECORDS_PATH"}
RUN_ID = re.compile(r"[a-f0-9]{24}")


class InstallerError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise InstallerError(message)


def regular_file(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"Arquivo ausente ou symlink: {path}")
    require(path.stat().st_size <= 8 * 1024 * 1024, f"Arquivo maior que 8 MiB: {path}")
    return path


def metadata(service):
    require(service in CATALOG["native"], CATALOG["pending"].get(service, "Serviço desconhecido: " + service))
    folder = service
    if service == "keycloak" and not (ROOT / folder).is_dir():
        folder = "keycloack"
    source = regular_file(ROOT / folder / "linux/service.sh").read_text(encoding="utf-8")
    result = dict(service=service, folder=folder)
    for key in (
        "SERVICE_VERSION",
        "PROXY_KIND",
        "BACKEND_PORTS",
        "PUBLIC_PORT",
        "ARTIFACT",
        "PACKAGE_GROUPS",
    ):
        match = re.search(r"^" + key + r"=(.*)$", source, re.MULTILINE)
        require(match is not None, "Metadado ausente: " + key)
        result[key.lower()] = match[1].strip().strip("\"'")
    return result


def local_path(base, value):
    require(isinstance(value, str) and value, "Caminho local deve ser texto não vazio.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def inventory(path):
    data = json.loads(regular_file(path).read_text(encoding="utf-8"))
    return validate_inventory(data)


def validate_inventory(data):
    require(
        isinstance(data, dict) and set(data) == {"schema", "hosts", "deployments"} and data["schema"] == 1,
        "Inventário exige schema=1, hosts e deployments.",
    )
    require(
        isinstance(data["hosts"], dict) and isinstance(data["deployments"], dict),
        "Hosts/deployments devem ser objetos.",
    )
    for name, host in data["hosts"].items():
        require(
            isinstance(host, dict)
            and set(host) <= {"address", "port", "user", "identity_file", "known_hosts", "host_key_sha256"},
            "Host inválido; senhas não pertencem ao inventário: " + name,
        )
        require(
            isinstance(host.get("address"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", host["address"]),
            "Address inválido.",
        )
        require(
            isinstance(host.get("user"), str) and re.fullmatch(r"[a-z_][a-z0-9_-]*", host["user"]),
            "User inválido.",
        )
        require(
            type(host.get("port", 22)) is int and 1 <= host.get("port", 22) <= 65535, "Porta SSH inválida."
        )
        if host.get("host_key_sha256"):
            require(
                re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", host["host_key_sha256"]),
                "Fingerprint SHA256 inválido.",
            )
    return data


def inventory_host(path, name):
    data = inventory(path)
    require(name in data["hosts"], "Host ausente no inventário: " + name)
    host = dict(data["hosts"][name])
    for key in ("identity_file", "known_hosts"):
        if host.get(key):
            host[key] = str(local_path(Path(path).parent, host[key]).absolute())
    return host


def env_file(path):
    values = {}
    for line in regular_file(path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]+)=(.*)", line)
        require(match and match[1] not in values, "Configuração exige KEY=value sem duplicatas.")
        values[match[1]] = match[2]
    return values


def deployment(args, prepare=False):
    path = Path(args.inventory).absolute()
    data = inventory(path)
    require(args.deployment in data["deployments"], "Deployment ausente: " + args.deployment)
    item = data["deployments"][args.deployment]
    require(
        isinstance(item, dict)
        and set(item) <= {"host", "service", "config", "manifest", "overrides", "files"},
        "Deployment inválido.",
    )
    require(item.get("host") in data["hosts"], "Host ausente no inventário.")
    meta = metadata(item.get("service", ""))
    host = dict(data["hosts"][item["host"]])
    for key in ("identity_file", "known_hosts"):
        if host.get(key):
            host[key] = str(regular_file(local_path(path.parent, host[key])).absolute())
    request = dict(
        action=args.action,
        service=meta["service"],
        metadata=meta,
        keep_data=getattr(args, "keep_data", False),
        ssh_connection="",
    )
    files = {}
    if prepare and args.action == "install":
        config = env_file(local_path(path.parent, item.get("config")))
        known_keys = set(env_file(ROOT / meta["folder"] / "linux/.env.example"))
        require(
            set(config) <= known_keys,
            "Chave não suportada no .env; use o modelo do serviço e arquivos para segredos.",
        )
        overrides, inputs = item.get("overrides", {}), item.get("files", {})
        require(
            isinstance(overrides, dict) and isinstance(inputs, dict), "Overrides/files devem ser objetos."
        )
        require(set(overrides) <= set(config), "Override exige uma chave já presente no .env.")
        config.update(overrides)
        require(
            str(host.get("port", 22)) == config.get("SSH_PORT", "22"),
            "SSH_PORT do serviço difere da conexão SSH.",
        )
        target = "/opt/sdlc-remote/" + meta["service"]
        files["inputs/manifest.json"] = regular_file(
            local_path(path.parent, item.get("manifest"))
        ).read_bytes()
        manifest = json.loads(files["inputs/manifest.json"])
        require(
            manifest.get("schema") == 1 and manifest.get("platform") == "ubuntu26.04-amd64",
            "Manifesto/plataforma inválidos.",
        )
        names = {entry["name"] for entry in manifest["artifacts"]}
        required = {"ubuntu26-base.tar.gz"} | {
            "ubuntu26-" + group + ".tar.gz" for group in meta["package_groups"].split()
        }
        if meta["artifact"]:
            required.add(meta["artifact"])
        require(required <= names, "Manifesto não contém todos os artefatos do serviço.")
        config["MANIFEST_PATH"] = target + "/inputs/manifest.json"
        require(
            set(inputs) <= FILE_KEYS and set(inputs) <= set(config),
            "Chave de files não suportada pelo serviço.",
        )
        for key in FILE_KEYS & set(config):
            require(
                not config[key] or key in inputs,
                f"Informe o arquivo local em files.{key}; caminhos remotos livres não são aceitos.",
            )
        for key, source in inputs.items():
            relative = "inputs/" + key.lower()
            files[relative] = regular_file(local_path(path.parent, source)).read_bytes()
            config[key] = target + "/" + relative
        for key, value in config.items():
            require(
                isinstance(value, str) and not re.search(r"[\s\"\'`$;\\]", value),
                "Valor literal inválido em " + key,
            )
        files["inputs/service.env"] = "".join(f"{key}={value}\n" for key, value in config.items()).encode()
        request["config"] = config
        if meta["service"] == "technitium":
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "dns_config", ROOT / "technitium/linux/configure.py"
            )
            dns = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dns)
            dns.read_records(
                local_path(path.parent, inputs.get("DNS_RECORDS_PATH")),
                config["DOMAIN"],
                config["DNS_LISTEN_IPV4"],
            )
    return host, request, files


def bundle(request, inputs):
    files = dict(inputs)
    folder = request["metadata"]["folder"]
    for directory in (ROOT / "scripts/linux", ROOT / folder / "linux"):
        for path in directory.rglob("*"):
            require(not path.is_symlink(), "Symlink no código: " + str(path))
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.name not in (".env", ".env.example")
            ):
                require(
                    path.suffix in (".sh", ".py", ".json", ".sql", ".groovy"),
                    "Arquivo inesperado no código: " + str(path),
                )
                content = regular_file(path).read_bytes()
                if path.suffix in (".sh", ".py", ".groovy", ".sql"):
                    content = content.replace(b"\r\n", b"\n")
                files["project/" + path.relative_to(ROOT).as_posix()] = content
    files["request.json"] = json.dumps(request, ensure_ascii=False).encode()
    checksums = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    files["checksums.json"] = json.dumps(checksums).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(content), 0o600
            archive.addfile(entry, io.BytesIO(content))
    require(output.tell() < 20 * 1024 * 1024, "Payload maior que 20 MiB.")
    return output.getvalue()
