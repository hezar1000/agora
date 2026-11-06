#!/bin/bash
# Migrate the database, collect static files, and switch the site to a
# release built by deploy/build-release.sh.
#
#   sudo bash /home/agora/agora-releases/release-<commit>/deploy/activate-release.sh \
#       /home/agora/agora-releases/release-<commit>
#
# Take and verify a database backup first: migrations are not undone by
# switching back to an older release.
set -euo pipefail

NEW="${1:?usage: activate-release.sh <release directory>}"
LINK="${AGORA_CURRENT:-/srv/agora/current}"
ENV_FILE="${AGORA_ENV_FILE:-/etc/agora/agora.env}"
RUNAS="${AGORA_USER:-agora}"
PORTS="${AGORA_PORTS:-8090 8091 8092 8093}"

[ -d "$NEW" ] || { echo "no such release: $NEW" >&2; exit 1; }
PREVIOUS=$(readlink -f "$LINK" 2>/dev/null || true)
cd "$NEW"
set -a; . "$ENV_FILE"; set +a
export DJANGO_SETTINGS_MODULE=mta2.settings
run() { runuser -u "$RUNAS" -- "$@"; }

echo "== dependencies =="
# The virtualenv is shared, so a release whose requirements changed must not
# go live until the packages have been installed (docs/DEPLOYING.md).
if run .venv/bin/pip install --dry-run --no-input --requirement requirements_new.txt \
    | grep -q '^Would install'; then
  echo "  the shared virtualenv does not match requirements_new.txt" >&2
  echo "  update it first:  sudo -u $RUNAS $NEW/.venv/bin/pip install -r $NEW/requirements_new.txt" >&2
  exit 1
fi
echo "  ok"

echo "== migrate =="
run .venv/bin/python manage.py migrate --noinput | tail -3

echo "== collect static =="
run .venv/bin/python manage.py collectstatic --noinput | tail -1
# runuser resets the umask from the login defaults, so collectstatic writes
# 0640 files that nginx (www-data) cannot read.
chmod -R o+rX "$DJANGO_STATIC_ROOT"
UNREADABLE=$(find "$DJANGO_STATIC_ROOT" -type f ! -perm -o+r | wc -l)
[ "$UNREADABLE" -eq 0 ] || {
  echo "  refusing to switch: nginx cannot read $UNREADABLE static files" >&2
  exit 1
}

echo "== switch =="
ln -sfn "$NEW" "$LINK"
systemctl restart agora-web.target
sleep 8
for port in $PORTS; do
  printf '  agora-web@%s %s\n' "$port" "$(systemctl is-active "agora-web@$port" || true)"
done

echo "== serving =="
for path in / /health/ready/ /static/css/agora.css; do
  printf '  %-22s HTTP %s\n' "$path" "$(curl -sS -o /dev/null -w '%{http_code}' \
    --resolve "$AGORA_PROBE_HOST:443:127.0.0.1" "https://$AGORA_PROBE_HOST$path")"
done

echo "== live =="
readlink -f "$LINK"
echo "== to roll back =="
echo "  sudo ln -sfn $PREVIOUS $LINK && sudo systemctl restart agora-web.target"
