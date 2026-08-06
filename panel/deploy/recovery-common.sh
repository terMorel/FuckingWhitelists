#!/usr/bin/env bash

# Shared, side-effect-free helpers for the recovery scripts.

recovery_fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

recovery_require_command() {
  command -v "$1" >/dev/null 2>&1 || recovery_fail "required command is missing: $1"
}

recovery_validate_archive() {
  local archive="$1"

  python3 - "$archive" <<'PY'
import posixpath
import sys
import tarfile
from pathlib import PurePosixPath

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle.getmembers():
        name = member.name
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {name}")
        if not path.parts or path.parts[0] not in {"meta", "rootfs"}:
            raise SystemExit(f"unexpected top-level archive path: {name}")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise SystemExit(f"unsupported special file in archive: {name}")
        if member.issym() or member.islnk():
            link = member.linkname
            if link.startswith("/"):
                raise SystemExit(f"absolute archive link target: {name} -> {link}")
            resolved = PurePosixPath(posixpath.normpath(posixpath.join(str(path.parent), link)))
            if ".." in resolved.parts or not resolved.parts or resolved.parts[0] != path.parts[0]:
                raise SystemExit(f"escaping archive link target: {name} -> {link}")
PY
}

recovery_verify_encrypted_checksum() {
  local bundle="$1"
  local expected="$2"
  local actual
  local expected_lower

  [[ ${#expected} -eq 64 ]] || recovery_fail "--expected-sha256 must contain exactly 64 hexadecimal characters"
  case "$expected" in
    *[!0-9A-Fa-f]*) recovery_fail "--expected-sha256 must contain exactly 64 hexadecimal characters" ;;
  esac
  actual="$(sha256sum "$bundle" | awk '{print $1}')"
  actual="$(printf '%s' "$actual" | tr 'A-F' 'a-f')"
  expected_lower="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
  [[ "$actual" == "$expected_lower" ]] || recovery_fail "encrypted bundle SHA-256 does not match the separately stored value"
}

recovery_verify_tree() {
  local tree="$1"
  local version
  local required

  [[ -d "$tree/meta" && -d "$tree/rootfs" ]] || recovery_fail "invalid recovery tree"
  [[ -f "$tree/meta/bundle-version" ]] || recovery_fail "bundle version is missing"
  version="$(tr -d '\r\n' < "$tree/meta/bundle-version")"
  [[ "$version" == "1" ]] || recovery_fail "unsupported bundle version: $version"
  [[ -f "$tree/meta/manifest.sha256" ]] || recovery_fail "checksum manifest is missing"
  [[ -f "$tree/meta/hysteria-binary-path" ]] || recovery_fail "Hysteria binary metadata is missing"

  for required in \
    rootfs/etc/hysteria/config.yaml \
    rootfs/etc/hysteria/users.json \
    rootfs/usr/local/sbin/hy-access \
    rootfs/etc/hyboard/hyboard.env \
    rootfs/var/lib/hyboard/hyboard.db; do
    [[ -f "$tree/$required" ]] || recovery_fail "required recovery file is missing: $required"
  done

  local hysteria_binary
  hysteria_binary="$(tr -d '\r\n' < "$tree/meta/hysteria-binary-path")"
  case "$hysteria_binary" in
    /usr/bin/hysteria|/usr/local/bin/hysteria) ;;
    *) recovery_fail "unexpected Hysteria binary path in bundle: $hysteria_binary" ;;
  esac
  [[ -f "$tree/rootfs$hysteria_binary" ]] || recovery_fail "archived Hysteria binary is missing"

  (
    cd "$tree"
    sha256sum --check meta/manifest.sha256 >/dev/null
  ) || recovery_fail "bundle checksum verification failed"
}
