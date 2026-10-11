#!/usr/bin/env bash
# Adiciona registros declarativos; não redefine configurações feitas no painel.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
SERVICE=technitium
# shellcheck source=scripts/linux/common.sh
source "$REPO_DIR/scripts/linux/common.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/service.sh"
ACTION=configure
if [[ ${1:-} == --help || $# -eq 0 ]]; then
    echo 'Uso: sudo bash technitium/linux/configure.sh --records /caminho/registros.json [--replace] [--password-file /caminho/senha] [--otp 123456]'
    exit 0
fi
records=''; password_file=/opt/sdlc/technitium/secrets/admin_password; replace=(); otp=()
while (($#)); do
    case "$1" in
        --records) [[ $# -ge 2 ]] || die 'Falta o caminho JSON.'; records=$2; shift ;;
        --password-file) [[ $# -ge 2 ]] || die 'Falta o caminho da senha.'; password_file=$2; shift ;;
        --otp) [[ $# -ge 2 ]] || die 'Falta o OTP.'; otp=(--otp "$2"); shift ;;
        --replace) replace=(--replace) ;;
        *) die "Argumento desconhecido: $1" ;;
    esac
    shift
done
check_host
check_paths
[[ -f $SDLC_DIR/.installed ]] || die 'Instale o Technitium primeiro.'
read_config "$SDLC_DIR/install.env"
[[ $records == /* && -f $records && ! -L $records ]] || die 'Use um JSON absoluto existente, sem symlink.'
python3 "$SCRIPT_DIR/configure.py" apply --records "$records" --password-file "$password_file" --cidrs "$DNS_CIDRS" "${replace[@]}" "${otp[@]}"
