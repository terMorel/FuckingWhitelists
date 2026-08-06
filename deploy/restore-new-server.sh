#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/recovery-common.sh
. "$SCRIPT_DIR/recovery-common.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo bash deploy/restore-new-server.sh \
    --stage /root/hyboard-restore-stage-NAME \
    --repo /root/FuckingWhitelists \
    --apply

This script intentionally works only on a clean target. It restores Hysteria and
HyBoard core data. Nginx, Let's Encrypt, client PKI, SSH/UFW/sysctl, Xray/3x-ui
and WireGuard are left staged for explicit review because they are host-specific.
EOF
}

stage=""
repo=""
apply=0
while (($#)); do
  case "$1" in
    --stage) stage="${2:-}"; shift 2 ;;
    --repo) repo="${2:-}"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) recovery_fail "unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || recovery_fail "run this script as root"
[[ $apply -eq 1 ]] || recovery_fail "refusing to change the server without --apply"
case "$stage" in /root/hyboard-restore-stage-*) ;; *) recovery_fail "unexpected staging path" ;; esac
[[ -d "$stage" ]] || recovery_fail "staging directory not found: $stage"
[[ -f "$repo/deploy/install.sh" ]] || recovery_fail "repository install script not found: $repo/deploy/install.sh"
recovery_verify_tree "$stage"
recovery_require_command systemctl

if systemctl is-active --quiet hysteria-server.service || systemctl is-active --quiet hyboard.service; then
  recovery_fail "Hysteria or HyBoard is already active; this script is only for a clean server"
fi

for target in \
  /etc/hysteria/config.yaml \
  /etc/hyboard/hyboard.env \
  /var/lib/hyboard/hyboard.db \
  /root/hysteria-access; do
  [[ ! -e "$target" ]] || recovery_fail "target is not clean; refusing to overwrite: $target"
done

hysteria_binary="$(tr -d '\r\n' < "$stage/meta/hysteria-binary-path")"
if [[ -e "$hysteria_binary" ]]; then
  [[ -f "$hysteria_binary" ]] || recovery_fail "Hysteria target is not a regular file: $hysteria_binary"
  cmp --silent "$stage/rootfs$hysteria_binary" "$hysteria_binary" || \
    recovery_fail "installed Hysteria binary differs from the verified bundle"
else
  install -d -m 0755 "$(dirname -- "$hysteria_binary")"
  install -o root -g root -m 0755 "$stage/rootfs$hysteria_binary" "$hysteria_binary"
fi

printf 'Restoring Hysteria core to the clean server...\n'
install -d -m 0755 /etc/hysteria /usr/local/sbin
cp -a -- "$stage/rootfs/etc/hysteria/." /etc/hysteria/
install -o root -g root -m 0750 "$stage/rootfs/usr/local/sbin/hy-access" /usr/local/sbin/hy-access
if [[ -d "$stage/rootfs/root/hysteria-access" ]]; then
  cp -a -- "$stage/rootfs/root/hysteria-access" /root/hysteria-access
fi
if [[ -f "$stage/rootfs/etc/systemd/system/hysteria-server.service" ]]; then
  install -o root -g root -m 0644 \
    "$stage/rootfs/etc/systemd/system/hysteria-server.service" \
    /etc/systemd/system/hysteria-server.service
fi
systemctl daemon-reload
systemctl enable --now hysteria-server.service
systemctl is-active --quiet hysteria-server.service || recovery_fail "Hysteria failed to start; inspect journalctl -u hysteria-server"

printf 'Installing HyBoard application and service units...\n'
# Seed the saved env before install.sh so it does not generate and print a temporary
# administrator password that will immediately be replaced by the restored one.
install -d -o root -g root -m 0750 /etc/hyboard
install -o root -g root -m 0600 "$stage/rootfs/etc/hyboard/hyboard.env" /etc/hyboard/hyboard.env
bash "$repo/deploy/install.sh"
systemctl stop \
  hyboard-monitor.timer hyboard-expire.timer \
  hyboard-monitor.service hyboard-expire.service \
  hyboard.service hyboard-helper.service 2>/dev/null || true
install -d -o root -g hyboard -m 0750 /etc/hyboard
install -o root -g hyboard -m 0640 "$stage/rootfs/etc/hyboard/hyboard.env" /etc/hyboard/hyboard.env
install -d -o hyboard -g hyboard -m 0750 /var/lib/hyboard
install -o hyboard -g hyboard -m 0640 "$stage/rootfs/var/lib/hyboard/hyboard.db" /var/lib/hyboard/hyboard.db
systemctl enable --now hyboard-helper.service hyboard-expire.timer hyboard-monitor.timer
systemctl restart hyboard.service
systemctl is-active --quiet hyboard.service || recovery_fail "HyBoard failed to start; inspect journalctl -u hyboard"

printf '\nCore restore completed.\n'
printf 'Management-plane TLS and client certificates were NOT applied. Reissue the server certificate for the new address, then configure Nginx using SERVER_MIGRATION.md.\n'
printf 'Do not delete the encrypted bundle or staging directory until client connectivity and traffic accounting are verified.\n'
