#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
ACTION=install
# shellcheck source=scripts/linux/common.sh
source "$REPO_DIR/scripts/linux/common.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/service.sh"
main "$@"

