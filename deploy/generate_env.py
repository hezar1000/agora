#!/usr/bin/env python3
"""Build /etc/agora/agora.env and the SAML material from the deployed settings.

Run on the Agora host, as the account that can read the live checkout:

    ~/agora/agora_env/bin/python deploy/generate_env.py \
        --out ~/agora-releases/staging-in/etc-agora

Secrets are read from the deployed settings module and written straight to
disk. Nothing secret is printed, so the output is safe to paste into a ticket.

Values are single-quoted. The runbook sources this file with `set -a`, and an
unquoted value containing a space runs as a command while a secret containing
$, ( or & is silently rewritten. systemd's EnvironmentFile accepts the same
quoting, so one form serves both readers.

The database password is generated here. This deployment starts on a new
PostgreSQL database, so the role does not exist yet; the command prints the
SQL to create it without printing the password itself.
"""

import argparse
import os
import re
import secrets
import string
import sys
from pathlib import Path
from urllib.parse import quote

PUBLIC_HOST = "agora.students.cs.ubc.ca"
DATABASE_NAME = "agora"
DATABASE_USER = "agora"


def _quote_values(text):
    """Single-quote every assignment so the file is shell- and systemd-safe."""
    lines = []
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            lines.append(line)
            continue
        key, _, value = line.partition("=")
        lines.append("%s='%s'" % (key, value.replace("'", "'\\''")))
    return "\n".join(lines) + "\n"


def _existing_values(path):
    """Values already in use, so regenerating does not invalidate them.

    Re-running this must not mint a new database password: the role already
    exists with the old one, and the application would simply stop
    authenticating. Reusing the secret key likewise avoids signing out every
    session for an unrelated change.
    """
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _token(length):
    # Alphanumeric plus - and _ deliberately: punctuation in these values has
    # repeatedly broken the readers around them and buys nothing.
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-checkout", default="/home/agora/agora")
    parser.add_argument("--settings", default="mta2.settings")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    sys.path.insert(0, args.live_checkout)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)
    import django

    django.setup()
    from django.conf import settings

    live = Path(args.live_checkout)
    out = Path(args.out)
    (out / "saml").mkdir(parents=True, exist_ok=True)

    existing = _existing_values(out / "agora.env")
    secret_key = existing.get("DJANGO_SECRET_KEY") or _token(64)
    database_url = existing.get("DATABASE_URL")
    database_password = None
    if not database_url:
        database_password = _token(40)
        database_url = "postgresql://%s:%s@127.0.0.1:5432/%s" % (
            quote(DATABASE_USER, safe=""),
            quote(database_password, safe=""),
            DATABASE_NAME,
        )

    # Redis requires authentication on this host, so the URL must carry the
    # credentials the deployed settings already hold. A bare
    # redis://127.0.0.1:6379/0 authenticates as nobody and the channel layer
    # fails with NOAUTH, which the readiness probe reports only as 503 because
    # it deliberately leaks no connection detail.
    redis_url = str(getattr(settings, "REDIS_URL", "") or "")
    if not redis_url:
        raise SystemExit(
            "REDIS_URL is not set in the deployed settings; refusing to "
            "generate an environment whose channel layer cannot authenticate."
        )

    (out / "saml" / "sp.crt").write_text((live / ".saml.crt").read_text())
    sp_key = out / "saml" / "sp.key"
    sp_key.write_text((live / ".saml.key").read_text())
    os.chmod(sp_key, 0o600)

    import mta2.saml_settings as saml_settings

    idp = getattr(saml_settings, "SAML_IDP_PUBLIC_CERT", "").strip()
    if idp and not idp.startswith("-----BEGIN"):
        idp = "-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % idp
    (out / "saml" / "idp.crt").write_text(idp)

    def value(name, default=""):
        return str(getattr(settings, name, default) or default)

    def flag(name, default):
        return str(bool(getattr(settings, name, default))).lower()

    env = f"""# Agora production environment.
# Generated by deploy/generate_env.py from the deployed settings.
DJANGO_SECRET_KEY={secret_key}
DATABASE_URL={database_url}
DATABASE_CONN_MAX_AGE=0
REDIS_URL={redis_url}
DJANGO_ALLOWED_HOSTS={PUBLIC_HOST}
DJANGO_CSRF_TRUSTED_ORIGINS=https://{PUBLIC_HOST}
AGORA_PROBE_HOST={PUBLIC_HOST}
DJANGO_DEBUG=false
DJANGO_HSTS_SECONDS=300
DJANGO_HSTS_INCLUDE_SUBDOMAINS=false
DJANGO_HSTS_PRELOAD=false
DJANGO_STATIC_ROOT=/home/agora/agora-data/staticfiles
DJANGO_MEDIA_ROOT=/home/agora/agora-data/media
DJANGO_LOG_FILE=/var/log/agora/django.log
DJANGO_EVENTS_LOG_FILE=/var/log/agora/events.log
DJANGO_DEBUG_LOG_FILE=/var/log/agora/debug.log
DJANGO_REQUEST_LOG_FILE=/var/log/agora/requests.log
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST={value("EMAIL_HOST", "smtp-relay.brevo.com")}
EMAIL_PORT={value("EMAIL_PORT", "587")}
EMAIL_USE_TLS={flag("EMAIL_USE_TLS", True)}
EMAIL_HOST_USER={value("EMAIL_HOST_USER")}
EMAIL_HOST_PASSWORD={value("EMAIL_HOST_PASSWORD")}
DEFAULT_FROM_EMAIL={value("DEFAULT_FROM_EMAIL", "agora.help@ubc.ca")}
SIGNUP_ENABLED=false
GUEST_SESSIONS_ENABLED=false
OPEN_COURSE_CREATION=false
SAML2_INTEGRATION_ENABLED=true
SAML_ENTITY_ID=https://{PUBLIC_HOST}/
SAML_SP_PUBLIC_CERT_FILE=/etc/agora/saml/sp.crt
SAML_SP_PRIVATE_KEY_FILE=/etc/agora/saml/sp.key
SAML_IDP_PUBLIC_CERT_FILE=/etc/agora/saml/idp.crt
SAML_TECHNICAL_CONTACT_EMAIL=agora.help@ubc.ca
SAML_SUPPORT_CONTACT_EMAIL=agora.help@ubc.ca
"""

    env_path = out / "agora.env"
    env_path.write_text(_quote_values(env))
    os.chmod(env_path, 0o600)

    sql_path = out / "create-database.sql"
    if database_password is None:
        print("reused the existing database credentials and secret key")
    else:
        sql_path.write_text(
            "-- Run as the postgres superuser. Contains the generated password.\n"
            "CREATE ROLE %s LOGIN PASSWORD '%s';\n"
            "CREATE DATABASE %s OWNER %s ENCODING 'UTF8';\n"
            % (DATABASE_USER, database_password.replace("'", "''"),
               DATABASE_NAME, DATABASE_USER)
        )
        os.chmod(sql_path, 0o600)

    print("wrote %s (mode 0600)" % env_path)
    print("wrote %s/saml/{sp.crt,sp.key,idp.crt}" % out)
    if database_password is not None:
        print("wrote %s (mode 0600) -- run it as the postgres superuser" % sql_path)
    print("database: %s as role %s on 127.0.0.1:5432" % (DATABASE_NAME, DATABASE_USER))
    print("static root: /home/agora/agora-data/staticfiles")
    print("media root:  /home/agora/agora-data/media")
    print("smtp host configured: %s" % bool(value("EMAIL_HOST")))


if __name__ == "__main__":
    main()
