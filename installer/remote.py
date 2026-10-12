#!/usr/bin/env python3
"""Entrada compatível do executor Linux; o recurso empacotado é autônomo."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "src/sdlc_installer/resources/remote.py"), run_name="__main__")
