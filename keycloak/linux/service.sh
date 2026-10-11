#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=keycloak
SERVICE_VERSION=26.8.0
PROXY_KIND=http
BACKEND_PORT=18084
BACKEND_PORTS="18084 19084 7800 57800"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:18084
ARTIFACT="keycloak-26.8.0.tar.gz"
ROOT_PREFIX=''
PACKAGE_GROUPS="java21"
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

