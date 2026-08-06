#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 SERVER_IP TEMPLATE BACKUP_DIR" >&2
  exit 2
fi

SERVER_IP=$1
TEMPLATE=$2
BACKUP_DIR=$3

printf '%s\n' "$SERVER_IP" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'
test -r "$TEMPLATE"
test -r /etc/letsencrypt/live/hyboard-ip/fullchain.pem
test -r /etc/letsencrypt/live/hyboard-ip/privkey.pem
test -r /etc/nginx/hyboard-client-ca.crt
case "$BACKUP_DIR" in
  /root/hyboard-backups/remote-access-preinstall-*) ;;
  *)
    echo "Refusing unexpected backup path" >&2
    exit 2
    ;;
esac

if ss -H -ltn '( sport = :8443 )' | grep -q .; then
  echo "TCP 8443 is already in use" >&2
  exit 1
fi

install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy

if [[ -e /etc/nginx/sites-enabled/default ]]; then
  mv /etc/nginx/sites-enabled/default "$BACKUP_DIR/nginx-default.enabled"
fi

rollback() {
  rm -f /etc/nginx/sites-enabled/hyboard
  if [[ -e "$BACKUP_DIR/nginx-default.enabled" ]]; then
    mv "$BACKUP_DIR/nginx-default.enabled" /etc/nginx/sites-enabled/default
  fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
}
trap rollback ERR

sed "s/__SERVER_IP__/$SERVER_IP/g" "$TEMPLATE" \
  > /etc/nginx/sites-available/hyboard
chmod 0644 /etc/nginx/sites-available/hyboard
ln -s /etc/nginx/sites-available/hyboard /etc/nginx/sites-enabled/hyboard

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -Eeuo pipefail' \
  'nginx -t' \
  'systemctl reload nginx' \
  > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

nginx -t
systemctl reload nginx
trap - ERR
