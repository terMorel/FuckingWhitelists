#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
APP_DIR=/opt/hyboard
ENV_DIR=/etc/hyboard
DATA_DIR=/var/lib/hyboard
SERVICE_USER=hyboard

test -x /usr/local/sbin/hy-access || { echo "Missing /usr/local/sbin/hy-access" >&2; exit 1; }
test -r /etc/hysteria/users.json || { echo "Missing /etc/hysteria/users.json" >&2; exit 1; }

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -m 0755 "$APP_DIR" "$ENV_DIR"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
cp -a "$SOURCE_DIR/hyboard" "$APP_DIR/"
install -d -m 0755 "$APP_DIR/deploy"
install -o root -g root -m 0755 "$SOURCE_DIR/deploy/set-password.py" "$APP_DIR/deploy/set-password.py"
cp "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/HYBOARD.md" "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir "$APP_DIR"

install -o root -g root -m 0755 "$SOURCE_DIR/deploy/hyboard-helper.py" /usr/local/libexec/hyboard-helper
install -o root -g root -m 0755 "$SOURCE_DIR/deploy/hyboard-helperd.py" /usr/local/libexec/hyboard-helperd

ADMIN_PASSWORD=
if [[ ! -f "$ENV_DIR/hyboard.env" ]]; then
  ADMIN_PASSWORD=${HYBOARD_ADMIN_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}
  SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  ADMIN_HASH=$(HYBOARD_ADMIN_PASSWORD="$ADMIN_PASSWORD" "$APP_DIR/.venv/bin/python" -c 'from argon2 import PasswordHasher; import os; print(PasswordHasher().hash(os.environ["HYBOARD_ADMIN_PASSWORD"]))')
  umask 077
  {
    printf 'HYBOARD_BACKEND=helper\n'
    printf 'HYBOARD_DB=%s/hyboard.db\n' "$DATA_DIR"
    printf 'HYBOARD_HELPER_SOCKET=/run/hyboard/helper.sock\n'
    printf 'HYBOARD_SESSION_SECRET=%s\n' "$SESSION_SECRET"
    printf 'HYBOARD_ADMIN_PASSWORD_HASH=%s\n' "$ADMIN_HASH"
    printf 'HYBOARD_COOKIE_SECURE=0\n'
    printf 'HYBOARD_BIND=127.0.0.1\n'
    printf 'HYBOARD_PORT=28474\n'
  } > "$ENV_DIR/hyboard.env"
fi
chown root:"$SERVICE_USER" "$ENV_DIR/hyboard.env"
chmod 0640 "$ENV_DIR/hyboard.env"
if grep -q '^HYBOARD_HELPER=' "$ENV_DIR/hyboard.env"; then
  sed -i 's#^HYBOARD_HELPER=.*#HYBOARD_HELPER_SOCKET=/run/hyboard/helper.sock#' "$ENV_DIR/hyboard.env"
elif ! grep -q '^HYBOARD_HELPER_SOCKET=' "$ENV_DIR/hyboard.env"; then
  printf 'HYBOARD_HELPER_SOCKET=/run/hyboard/helper.sock\n' >> "$ENV_DIR/hyboard.env"
fi

install -o root -g root -m 0644 "$SOURCE_DIR/deploy/hyboard.service" /etc/systemd/system/hyboard.service
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/hyboard-helper.service" /etc/systemd/system/hyboard-helper.service
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/hyboard-expire.service" /etc/systemd/system/hyboard-expire.service
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/hyboard-expire.timer" /etc/systemd/system/hyboard-expire.timer

systemctl daemon-reload
systemctl enable hyboard-helper.service hyboard.service hyboard-expire.timer
systemctl restart hyboard-helper.service
systemctl restart hyboard.service
systemctl start hyboard-expire.timer
rm -f /etc/sudoers.d/hyboard

for _attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:28474/healthz >/dev/null; then
    break
  fi
  sleep 0.25
done
curl --fail --silent --show-error http://127.0.0.1:28474/healthz >/dev/null

echo
echo "HyBoard is ready on 127.0.0.1:28474"
if [[ -n "$ADMIN_PASSWORD" ]]; then
  echo "Admin password: $ADMIN_PASSWORD"
else
  echo "Admin password: unchanged"
fi
echo "Open an SSH tunnel: ssh -L 28474:127.0.0.1:28474 root@SERVER_IP"
echo "Then visit: http://127.0.0.1:28474"
