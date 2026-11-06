#!/usr/bin/env bash
set -euo pipefail

# Operate on the release this script belongs to, whatever it is called and
# wherever it is installed. A hardcoded path silently overrides the unit's
# WorkingDirectory, which is how this came to run against the old checkout
# while the unit correctly named the release.
cd "$(dirname "$(readlink -f "$0")")/.."

fail() {
  echo "Agora deployment preflight failed: $1" >&2
  exit 1
}

required=(
  DJANGO_SECRET_KEY
  DATABASE_URL
  DATABASE_CONN_MAX_AGE
  REDIS_URL
  DJANGO_ALLOWED_HOSTS
  DJANGO_CSRF_TRUSTED_ORIGINS
  AGORA_PROBE_HOST
  DJANGO_STATIC_ROOT
  DJANGO_MEDIA_ROOT
  DJANGO_LOG_FILE
  DJANGO_EVENTS_LOG_FILE
  DJANGO_DEBUG_LOG_FILE
  DJANGO_REQUEST_LOG_FILE
  DJANGO_HSTS_SECONDS
  DJANGO_EMAIL_BACKEND
  DEFAULT_FROM_EMAIL
)

for variable_name in "${required[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    fail "required setting is missing: ${variable_name}"
  fi
done

case "${DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS:-0}" in
  1|true|TRUE|yes|YES|on|ON)
    fail "DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS must not be enabled"
    ;;
esac

case "${DJANGO_DEBUG:-0}" in
  1|true|TRUE|yes|YES|on|ON)
    fail "DJANGO_DEBUG must not be enabled"
    ;;
esac

case "${DATABASE_URL}" in
  postgres://*|postgresql://*) ;;
  *) fail "the supported production database is PostgreSQL" ;;
esac

case "${REDIS_URL}" in
  redis://*|rediss://*) ;;
  *) fail "REDIS_URL must use redis:// or rediss://" ;;
esac

# 0 is the production value: persistent per-thread connections leak under
# Daphne and exhaust PostgreSQL; the psycopg pool manages connections instead.
if ! [[ "${DATABASE_CONN_MAX_AGE}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  fail "DATABASE_CONN_MAX_AGE must be a non-negative integer"
fi

secret_key_lower="${DJANGO_SECRET_KEY,,}"
if (( ${#DJANGO_SECRET_KEY} < 50 )) ||
  [[ "${secret_key_lower}" == *insecure* ||
     "${secret_key_lower}" == *replace* ||
     "${secret_key_lower}" == *placeholder* ||
     "${secret_key_lower}" == *changeme* ||
     "${secret_key_lower}" == *example* ]]; then
  fail "DJANGO_SECRET_KEY must be at least 50 characters and not a placeholder"
fi

if [[ "${DJANGO_ALLOWED_HOSTS}" == *"*"* ||
      "${DJANGO_ALLOWED_HOSTS}" == *"://"* ||
      "${DJANGO_ALLOWED_HOSTS}" == *"/"* ]]; then
  fail "DJANGO_ALLOWED_HOSTS must contain explicit hostnames only"
fi

if [[ "${AGORA_PROBE_HOST}" == *"*"* ||
      "${AGORA_PROBE_HOST}" == *":"* ||
      "${AGORA_PROBE_HOST}" == *"/"* ||
      "${AGORA_PROBE_HOST}" == *","* ]]; then
  fail "AGORA_PROBE_HOST must be one explicit hostname"
fi

probe_host_allowed=false
IFS=',' read -r -a allowed_hosts <<< "${DJANGO_ALLOWED_HOSTS}"
for allowed_host in "${allowed_hosts[@]}"; do
  allowed_host="${allowed_host//[[:space:]]/}"
  if [[ "${allowed_host}" == "${AGORA_PROBE_HOST}" ]]; then
    probe_host_allowed=true
    break
  fi
done
if [[ "${probe_host_allowed}" != true ]]; then
  fail "AGORA_PROBE_HOST must be listed in DJANGO_ALLOWED_HOSTS"
fi

if [[ "${DJANGO_CSRF_TRUSTED_ORIGINS}" == *"*"* ]]; then
  fail "DJANGO_CSRF_TRUSTED_ORIGINS must not contain a wildcard"
fi
IFS=',' read -r -a csrf_origins <<< "${DJANGO_CSRF_TRUSTED_ORIGINS}"
for csrf_origin in "${csrf_origins[@]}"; do
  csrf_origin="${csrf_origin//[[:space:]]/}"
  csrf_origin="${csrf_origin%/}"
  case "${csrf_origin}" in
    https://*) csrf_host="${csrf_origin#https://}" ;;
    *) fail "production CSRF trusted origins must use HTTPS" ;;
  esac
  if [[ -z "${csrf_host}" || "${csrf_host}" == *"/"* ||
        "${csrf_host}" == *":"* || "${csrf_host}" == *","* ]]; then
    fail "DJANGO_CSRF_TRUSTED_ORIGINS must contain explicit origins only"
  fi
  csrf_host_allowed=false
  for allowed_host in "${allowed_hosts[@]}"; do
    allowed_host="${allowed_host//[[:space:]]/}"
    if [[ "${allowed_host}" == "${csrf_host}" ]]; then
      csrf_host_allowed=true
      break
    fi
  done
  if [[ "${csrf_host_allowed}" != true ]]; then
    fail "every CSRF trusted origin host must be in DJANGO_ALLOWED_HOSTS"
  fi
done

if ! [[ "${DJANGO_HSTS_SECONDS}" =~ ^[0-9]+$ ]]; then
  fail "DJANGO_HSTS_SECONDS must be zero or a positive integer"
fi

default_from_lower="${DEFAULT_FROM_EMAIL,,}"
if [[ "${DEFAULT_FROM_EMAIL}" != *@* || "${default_from_lower}" == *".invalid"* ]]; then
  fail "DEFAULT_FROM_EMAIL must be a non-example email address"
fi

if [[ "${DJANGO_EMAIL_BACKEND}" == "django.core.mail.backends.smtp.EmailBackend" ]]; then
  for smtp_variable in \
    EMAIL_HOST \
    EMAIL_PORT \
    EMAIL_USE_TLS \
    EMAIL_HOST_USER \
    EMAIL_HOST_PASSWORD; do
    if [[ -z "${!smtp_variable:-}" ]]; then
      fail "SMTP backend requires ${smtp_variable}"
    fi
  done
  if ! [[ "${EMAIL_PORT}" =~ ^[1-9][0-9]*$ ]] || (( EMAIL_PORT > 65535 )); then
    fail "EMAIL_PORT must be a valid TCP port"
  fi
  case "${EMAIL_USE_TLS}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *) fail "the supported SMTP topology requires EMAIL_USE_TLS=true" ;;
  esac
  smtp_credentials_lower="${EMAIL_HOST_USER,,}:${EMAIL_HOST_PASSWORD,,}"
  if [[ "${smtp_credentials_lower}" == *replace* ||
        "${smtp_credentials_lower}" == *placeholder* ||
        "${smtp_credentials_lower}" == *changeme* ||
        "${smtp_credentials_lower}" == *example* ]]; then
    fail "SMTP credentials must not contain example placeholders"
  fi
fi

# Checked by property rather than by literal value. The deployed instance uses
# /home/agora/agora/static and /home/agora/agora/mta2/uploads, so requiring the
# example paths verbatim refused to start on the host this targets.
for storage_path in "${DJANGO_STATIC_ROOT}" "${DJANGO_MEDIA_ROOT}"; do
  if [[ "${storage_path}" != /* ]]; then
    fail "Django storage paths must be absolute: ${storage_path}"
  fi
done

# Nginx serves the static root directly, so uploads underneath it would bypass
# the application's own authorization.
if [[ "${DJANGO_MEDIA_ROOT}" == "${DJANGO_STATIC_ROOT}" ||
      "${DJANGO_MEDIA_ROOT}" == "${DJANGO_STATIC_ROOT}"/* ]]; then
  fail "Uploads must not live under the Nginx-served static root: ${DJANGO_MEDIA_ROOT}"
fi

if [[ ! -d "${DJANGO_STATIC_ROOT}" || ! -r "${DJANGO_STATIC_ROOT}" ]]; then
  fail "static root is missing or unreadable"
fi
if [[ ! -d "${DJANGO_MEDIA_ROOT}" || ! -w "${DJANGO_MEDIA_ROOT}" ]]; then
  fail "media root is missing or not writable"
fi

for log_variable in \
  DJANGO_LOG_FILE \
  DJANGO_EVENTS_LOG_FILE \
  DJANGO_DEBUG_LOG_FILE \
  DJANGO_REQUEST_LOG_FILE; do
  log_path="${!log_variable}"
  log_parent="$(dirname -- "${log_path}")"
  if [[ "${log_parent}" != "/var/log/agora" ||
        ! -d "${log_parent}" || ! -w "${log_parent}" ]]; then
    fail "${log_variable} must name a writable file under /var/log/agora"
  fi
done

# Report a missing interpreter as such. Folding it into the version check
# below reports "Python 3.11 or newer is required" for a release whose
# virtualenv is simply somewhere else, which is a misleading place to start.
if [[ ! -x ./.venv/bin/python ]]; then
  fail "No interpreter at ./.venv/bin/python (running from $(pwd))"
fi

if ! ./.venv/bin/python -c \
  'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  fail "Python 3.11 or newer is required"
fi

./.venv/bin/python manage.py check --deploy
./.venv/bin/python manage.py makemigrations --check --dry-run
./.venv/bin/python manage.py migrate --check
./.venv/bin/python -c 'import mta2.asgi'
