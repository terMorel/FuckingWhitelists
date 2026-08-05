#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR=${1:-}
case "$BACKUP_DIR" in
  /root/hyboard-backups/remote-access-preinstall-*) ;;
  *)
    echo "Refusing unexpected backup path" >&2
    exit 2
    ;;
esac

install -d -m 0700 "$BACKUP_DIR"
ss -H -lntup > "$BACKUP_DIR/listeners.txt"
systemctl is-active \
  hysteria-server.service \
  hyboard.service \
  hyboard-helper.service \
  hyboard-expire.timer \
  x-ui.service > "$BACKUP_DIR/services.txt"
sha256sum /etc/hysteria/users.json > "$BACKUP_DIR/hysteria-users.sha256"
wg show wg0 peers > "$BACKUP_DIR/wg0-peers.txt"
dpkg-query -W > "$BACKUP_DIR/packages.txt"

if [[ -e /etc/nginx ]]; then
  cp -a /etc/nginx "$BACKUP_DIR/nginx.before"
fi
if [[ -e /etc/letsencrypt ]]; then
  cp -a /etc/letsencrypt "$BACKUP_DIR/letsencrypt.before"
fi

printf '%s\n' \
  'TCP 80 and 8443 were free; TCP 443 was owned by Xray before installation.' \
  > "$BACKUP_DIR/README.txt"

find "$BACKUP_DIR" -maxdepth 2 -printf '%M %u:%g %p\n'
