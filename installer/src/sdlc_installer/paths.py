"""Descoberta explícita do projeto, independente da instalação do wheel."""

import os
from pathlib import Path


def is_repository(path: Path) -> bool:
    return (path / "scripts/linux/common.sh").is_file() and (path / "grafana/linux/service.sh").is_file()


def repository(explicit: str | None = None) -> Path:
    selected = explicit or os.environ.get("SDLC_REPO")
    if selected:
        path = Path(selected).expanduser().resolve()
        if not is_repository(path):
            raise ValueError(f"Repositório SDLC inválido: {path}. Informe --repo com a raiz do projeto.")
        return path
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for path in (start, *start.parents):
            if is_repository(path):
                return path
    raise ValueError("Projeto SDLC não encontrado. Informe --repo ou SDLC_REPO com a raiz do repositório.")
