#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=sonarqube
SERVICE_VERSION=26.9.0.129388
PROXY_KIND=http
BACKEND_PORT=19000
BACKEND_PORTS="19000 19001"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:19000
ARTIFACT="sonarqube-26.9.0.129388.zip"
ROOT_PREFIX=''
PACKAGE_GROUPS="java21"
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

