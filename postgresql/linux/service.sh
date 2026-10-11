#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=postgresql
SERVICE_VERSION=18-ubuntu26
PROXY_KIND=postgresql
BACKEND_PORT=15432
BACKEND_PORTS="15432"
PUBLIC_PORT=5432
UPSTREAM=http://127.0.0.1:15432
ARTIFACT=''
ROOT_PREFIX=''
PACKAGE_GROUPS="postgresql18"
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

