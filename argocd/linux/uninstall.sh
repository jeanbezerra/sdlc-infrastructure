#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'Argo CD: instalador/desinstalador pendente.' 'O servidor Argo CD requer Kubernetes; implantação adiada por decisão do projeto.' 'Nenhuma alteração será realizada. Consulte argocd/README.md.' >&2
exit 2

