#!/usr/bin/env python3
"""Prepare e consuma uma distribuição imutável de artefatos oficiais para o CDN."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "scripts/linux/catalog.json"


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def sha256(path):
    return digest(path)


def request(url):
    if not url.startswith("https://"):
        raise ValueError("Somente HTTPS: " + url)
    response = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "sdlc-infrastructure/1"}), timeout=60
    )
    if not response.url.startswith("https://"):
        response.close()
        raise ValueError("Redirecionamento para protocolo sem TLS")
    return response


def download(url, output, expected=None, algorithm="sha256"):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Destino não pode ser symlink")
    if output.is_file() and expected and digest(output, algorithm) == expected:
        return
    temporary = output.with_name(output.name + ".part")
    if temporary.is_symlink():
        raise ValueError("Arquivo temporário não pode ser symlink")
    for attempt in range(3):
        try:
            with request(url) as response, temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            if expected and digest(temporary, algorithm) != expected:
                raise ValueError("Checksum incorreto: " + output.name)
            temporary.replace(output)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("Caminho inseguro no pacote: " + name)
    return path


def extract(archive, output, strip_root=False, root_prefix=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Extração exige diretório vazio: " + str(output))
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            infos = package.infolist()
            roots = {safe_name(i.filename).parts[0] for i in infos if safe_name(i.filename).parts}
            root = select_root(roots, strip_root, root_prefix)
            for info in infos:
                path = safe_name(info.filename)
                if root:
                    if path.parts[0] != root:
                        continue
                    path = PurePosixPath(*path.parts[1:])
                if not path.parts:
                    continue
                mode = info.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise ValueError("Symlink em ZIP não permitido")
                target = output.joinpath(*path.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(info) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest)
                    target.chmod(0o755 if mode & 0o111 else 0o644)
        return
    with tarfile.open(archive, "r:*") as package:
        members = package.getmembers()
        roots = {safe_name(m.name).parts[0] for m in members if safe_name(m.name).parts}
        root = select_root(roots, strip_root, root_prefix)
        selected = []
        for member in members:
            path = safe_name(member.name)
            if root:
                if path.parts[0] != root:
                    continue
                path = PurePosixPath(*path.parts[1:])
            if not path.parts:
                continue
            member.name = str(path)
            if member.islnk() and root:
                link = safe_name(member.linkname)
                if not link.parts or link.parts[0] != root:
                    raise ValueError("Hardlink fora da raiz do pacote")
                member.linkname = str(PurePosixPath(*link.parts[1:]))
            if member.isdev() or member.isfifo():
                raise ValueError("Arquivo especial no pacote")
            selected.append(member)
        # Python >= 3.12: filter=data impede escapes por symlinks/hardlinks e owners arbitrários.
        package.extractall(output, members=selected, filter="data")


def select_root(roots, strip_root, root_prefix):
    if not strip_root:
        return None
    roots = [root for root in roots if not root_prefix or root.startswith(root_prefix)]
    if len(roots) != 1:
        raise ValueError("Raiz ambígua no arquivo: " + repr(roots))
    return roots[0]


def published_checksum(item):
    if "github_release" in item:
        with request(item["github_release"]) as response:
            release = json.load(response)
        asset = next(a for a in release["assets"] if a["name"] == item["name"])
        digest = asset.get("digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("GitHub não publicou SHA256 para " + item["name"])
        return digest.split(":")[1]
    if "checksum_url" in item:
        with request(item["checksum_url"]) as response:
            checksum = response.read().decode().strip().split()[0]
        length = 128 if item.get("checksum_algorithm") == "sha512" else 64
        if not re.fullmatch(r"[0-9a-fA-F]{" + str(length) + "}", checksum):
            raise ValueError("Checksum publicado inválido")
        return checksum.lower()
    return None


def verify_signature(item, target, output):
    signature = output / (item["name"] + ".asc")
    download(item["signature_url"], signature)
    with tempfile.TemporaryDirectory(prefix=".sdlc-gpg-", dir=output) as temporary:
        key = Path(temporary) / "signing-key.asc"
        download("https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x679F1EE92B19609DE816FDE81DB198F93525EC1A", key)
        keyring = Path(temporary) / "keyring"
        keyring.mkdir(mode=0o700)
        env = dict(os.environ, GNUPGHOME=str(keyring))
        try:
            subprocess.run(["gpg", "--batch", "--import", str(key)], env=env, check=True, stdout=subprocess.DEVNULL)
            result = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", str(signature), str(target)], env=env, check=True, capture_output=True, text=True)
            if "679F1EE92B19609DE816FDE81DB198F93525EC1A" not in result.stdout:
                raise ValueError("Assinatura Sonar não corresponde ao fingerprint fixado")
        finally:
            # Encerra somente o agente deste keyring temporário antes de removê-lo.
            subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prepare(args):
    if sys.platform != "linux" or "26.04" not in Path("/etc/os-release").read_text():
        raise ValueError("Execute a preparação em Ubuntu 26.04 LTS amd64 (VM ou WSL).")
    if subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() != "amd64":
        raise ValueError("Arquitetura exigida: amd64")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text())
    entries = []

    def prepare_one(item):
        expected = published_checksum(item)
        target = output / item["name"]
        print("Baixando " + item["name"], flush=True)
        if expected or not target.is_file():
            download(item["url"], target, expected, item.get("checksum_algorithm", "sha256"))
        verification = item.get("checksum_algorithm", "sha256").upper() + " oficial" if expected else "HTTPS oficial"
        if item.get("signature_url"):
            verify_signature(item, target, output)
            verification = "Assinatura GPG oficial"
        print("Verificado " + item["name"], flush=True)
        return dict(name=item["name"], version=item["version"], url=item["url"], sha256=sha256(target), size=target.stat().st_size, verification=verification)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        entries.extend(executor.map(prepare_one, catalog["binaries"]))
    for group, packages in catalog["ubuntu_packages"].items():
        target = output / ("ubuntu26-" + group + ".tar.gz")
        subprocess.run(["bash", str(REPO / "scripts/linux/prepare-ubuntu-packages.sh"), str(target), *packages], check=True)
        entries.append(dict(name=target.name, version="ubuntu26.04", sha256=sha256(target), size=target.stat().st_size, verification="APT: metadados assinados Ubuntu", url="https://archive.ubuntu.com/ubuntu/"))
    manifest = dict(schema=1, platform="ubuntu26.04-amd64", artifacts=entries)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(e["sha256"] + "  " + e["name"] + "\n" for e in entries))
    print("CDN preparado: " + str(output), flush=True)


def fetch(args):
    manifest = json.loads(Path(args.manifest).read_text())
    if manifest.get("schema") != 1 or manifest.get("platform") != "ubuntu26.04-amd64":
        raise ValueError("Manifesto incompatível")
    entry = next(e for e in manifest["artifacts"] if e["name"] == args.name)
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", args.name) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
        raise ValueError("Entrada insegura no manifesto")
    download(args.base_url.rstrip("/") + "/" + args.name, args.output, entry["sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--output", default=str(REPO / "artifacts/cdn/ubuntu26-amd64"))
    command = sub.add_parser("fetch")
    for key in ["manifest", "base-url", "name", "output"]:
        command.add_argument("--" + key, required=True)
    command = sub.add_parser("extract")
    command.add_argument("--archive", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--strip-root", action="store_true")
    command.add_argument("--root-prefix")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "fetch":
        fetch(args)
    else:
        extract(args.archive, args.output, args.strip_root, args.root_prefix)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, StopIteration, subprocess.CalledProcessError) as error:
        sys.exit("ERRO: " + str(error))
