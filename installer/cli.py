#!/usr/bin/env python3
"""Entrada compatível; prefira uv run --project installer sdlc-installer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from sdlc_installer.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
