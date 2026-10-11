#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=prometheus
SERVICE_VERSION=3.15.0
PROXY_KIND=http
BACKEND_PORT=19090
BACKEND_PORTS="19090"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:19090
ARTIFACT="prometheus-3.15.0.linux-amd64.tar.gz"
ROOT_PREFIX=''
PACKAGE_GROUPS=""
UNIT_EXTRA=''
BASIC_AUTH=true
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

