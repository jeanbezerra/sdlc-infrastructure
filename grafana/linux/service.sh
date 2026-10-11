#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=grafana
SERVICE_VERSION=13.2.3
PROXY_KIND=http
BACKEND_PORT=13000
BACKEND_PORTS="13000"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:13000
ARTIFACT="grafana-13.2.3.linux-amd64.tar.gz"
ROOT_PREFIX=''
PACKAGE_GROUPS=""
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

