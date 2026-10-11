#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=opensearch
SERVICE_VERSION=3.9.0
PROXY_KIND=http
BACKEND_PORT=19200
BACKEND_PORTS="19200 19300"
PUBLIC_PORT=443
UPSTREAM=https://127.0.0.1:19200
ARTIFACT="opensearch-3.9.0-linux-x64.tar.gz"
ROOT_PREFIX=''
PACKAGE_GROUPS=""
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

