#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=redis
SERVICE_VERSION=8-ubuntu26
PROXY_KIND=redis
BACKEND_PORT=16379
BACKEND_PORTS="16379"
PUBLIC_PORT=6380
UPSTREAM=http://127.0.0.1:16379
ARTIFACT=''
ROOT_PREFIX=''
PACKAGE_GROUPS="redis"
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

