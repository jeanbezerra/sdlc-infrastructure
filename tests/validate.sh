#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mapfile -d '' files < <(find scripts tests argocd grafana harbor jenkins keycloack nexus opensearch postgresql prometheus redis sonarqube technitium -name '*.sh' -print0)
for file in "${files[@]}"; do bash -n "$file"; done
shellcheck -x "${files[@]}"
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/config-safety.sh
python3 tests/validate-project.py
if [[ ${1:-} == --native ]]; then python3 tests/native-smoke.py; fi
