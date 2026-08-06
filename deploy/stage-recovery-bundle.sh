#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/recovery-common.sh
. "$SCRIPT_DIR/recovery-common.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo bash deploy/stage-recovery-bundle.sh \
    --bundle FILE.age \
    --identity AGE-IDENTITY-FILE \
    --expected-sha256 HEX \
    --output /root/hyboard-restore-stage-NAME
EOF
}

bundle=""
identity=""
output=""
expected_sha256=""
while (($#)); do
  case "$1" in
    --bundle) bundle="${2:-}"; shift 2 ;;
    --identity) identity="${2:-}"; shift 2 ;;
    --expected-sha256) expected_sha256="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) recovery_fail "unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || recovery_fail "run this script as root"
[[ -f "$bundle" ]] || recovery_fail "bundle not found: $bundle"
[[ -f "$identity" ]] || recovery_fail "age identity not found: $identity"
case "$output" in
  /root/hyboard-restore-stage-*) ;;
  *) recovery_fail "--output must match /root/hyboard-restore-stage-*" ;;
esac
[[ ! -e "$output" ]] || recovery_fail "refusing to overwrite: $output"
for command in age tar sha256sum mktemp python3; do recovery_require_command "$command"; done
recovery_verify_encrypted_checksum "$bundle" "$expected_sha256"

umask 077
work_dir="$(mktemp -d /tmp/hyboard-stage.XXXXXXXX)"
cleanup() {
  case "$work_dir" in /tmp/hyboard-stage.*) rm -rf -- "$work_dir" ;; esac
}
trap cleanup EXIT INT TERM

age --decrypt --identity "$identity" --output "$work_dir/recovery.tar.gz" "$bundle"
recovery_validate_archive "$work_dir/recovery.tar.gz"
install -d -m 0700 "$work_dir/tree"
tar -C "$work_dir/tree" -xzf "$work_dir/recovery.tar.gz"
recovery_verify_tree "$work_dir/tree"
mv "$work_dir/tree" "$output"
chmod 0700 "$output"
printf 'Verified recovery data staged at: %s\n' "$output"
printf 'No live configuration has been changed.\n'
