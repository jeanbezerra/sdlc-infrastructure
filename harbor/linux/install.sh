#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'Harbor: instalador/desinstalador pendente.' 'A distribuição suportada de Harbor requer containers/Kubernetes; implantação adiada por decisão do projeto.' 'Nenhuma alteração será realizada. Consulte harbor/README.md.' >&2
exit 2

