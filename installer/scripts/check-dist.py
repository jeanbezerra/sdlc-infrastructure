"""Verifica conteúdo público, recursos e entrypoint dos artefatos gerados por uv build."""

import argparse
import json
import tarfile
import zipfile
from pathlib import Path

command = argparse.ArgumentParser(description=__doc__)
command.add_argument("directory", nargs="?", default=str(Path(__file__).resolve().parents[1] / "dist"))
args = command.parse_args()
directory = Path(args.directory)
(wheel,) = directory.glob("sdlc_installer-*.whl")
(source,) = directory.glob("sdlc_installer-*.tar.gz")
forbidden = {
    ".venv",
    ".state",
    "private",
    "artifacts",
    "__pycache__",
    "inventory.json",
    "operation.log",
    "result.json",
}
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    assert all(not forbidden.intersection(Path(name).parts) for name in names)
    assert all(not name.endswith((".key", ".pem", ".env")) for name in names)
    assert "sdlc_installer/resources/remote.py" in names
    assert "sdlc_installer/registration.py" in names
    assert "sdlc_installer/discovery.py" in names
    assert "sdlc_installer/interactive.py" in names
    catalog = json.loads(archive.read("sdlc_installer/resources/catalog.json"))
    assert len(catalog["native"]) == 10
    (entry,) = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
    assert "sdlc-installer = sdlc_installer.cli:entrypoint" in archive.read(entry).decode()
    (metadata,) = [name for name in names if name.endswith(".dist-info/METADATA")]
    assert "Requires-Dist: paramiko" in archive.read(metadata).decode()
    assert "Requires-Dist: rich" in archive.read(metadata).decode()
with tarfile.open(source, "r:gz") as archive:
    names = archive.getnames()
    assert all(not forbidden.intersection(Path(name).parts) for name in names)
    assert any(name.endswith("/uv.lock") for name in names)
    assert any(name.endswith("/pyproject.toml") for name in names)
    assert any(name.endswith("/start.cmd") for name in names)
print(f"Wheel e sdist validados: {wheel.name}, {source.name}")
