#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/recovery-common.sh
. "$SCRIPT_DIR/recovery-common.sh"

usage() {
  printf 'Usage: bash deploy/verify-recovery-bundle.sh --bundle FILE.age --identity AGE-IDENTITY-FILE --expected-sha256 HEX\n'
}

bundle=""
identity=""
expected_sha256=""
while (($#)); do
  case "$1" in
    --bundle) bundle="${2:-}"; shift 2 ;;
    --identity) identity="${2:-}"; shift 2 ;;
    --expected-sha256) expected_sha256="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) recovery_fail "unknown argument: $1" ;;
  esac
done

[[ -f "$bundle" ]] || recovery_fail "bundle not found: $bundle"
[[ -f "$identity" ]] || recovery_fail "age identity not found: $identity"
for command in age tar sha256sum mktemp python3; do recovery_require_command "$command"; done
recovery_verify_encrypted_checksum "$bundle" "$expected_sha256"

umask 077
work_dir="$(mktemp -d /tmp/hyboard-verify.XXXXXXXX)"
cleanup() {
  case "$work_dir" in /tmp/hyboard-verify.*) rm -rf -- "$work_dir" ;; esac
}
trap cleanup EXIT INT TERM

age --decrypt --identity "$identity" --output "$work_dir/recovery.tar.gz" "$bundle"
recovery_validate_archive "$work_dir/recovery.tar.gz"
install -d -m 0700 "$work_dir/tree"
tar -C "$work_dir/tree" -xzf "$work_dir/recovery.tar.gz"
recovery_verify_tree "$work_dir/tree"

printf 'Recovery bundle is valid.\n'
printf 'Created (UTC): %s\n' "$(tr -d '\r\n' < "$work_dir/tree/meta/created-at-utc")"
printf 'Archived files: %s\n' "$(find "$work_dir/tree/rootfs" -type f | wc -l)"
