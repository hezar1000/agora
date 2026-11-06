# Deploying Agora

This is how Agora runs in production at UBC, written so that another
institution can stand up its own copy. Everything here assumes one Ubuntu
server; adjust paths if you like, but the files in `deploy/` assume the layout
below.

## What runs

- **nginx** terminates HTTPS, serves static files, and proxies HTTP and
  WebSocket traffic to the workers. Config: `deploy/nginx.conf`.
- **Four Daphne (ASGI) workers** on ports 8090–8093, managed by systemd
  (`deploy/systemd/agora-web@.service`, grouped by `agora-web.target`). Each
  runs `deploy/preflight.sh` first and refuses to start on unsafe settings.
- **A one-minute timer** (`agora-live-sessions.timer`) that opens and closes
  scheduled lectures.
- **PostgreSQL** (14 or newer) for data, **Redis** for WebSocket fan-out and
  shared rate-limit counters.
- **SMTP** for password resets and exports, and optionally **SAML** single
  sign-on through your institution's identity provider.

## Layout

| Path | What |
| --- | --- |
| `/home/agora/agora` | git checkout of this repository, used to make release tarballs |
| `/home/agora/agora-releases/release-<commit>` | one directory per deployed commit |
| `/srv/agora/current` | symlink to the live release |
| `/home/agora/venv` | virtualenv shared by every release (linked as `.venv`) |
| `/home/agora/agora-data/staticfiles` | collected static files, served by nginx |
| `/home/agora/agora-data/media` | uploads (profile photos), served only through Django |
| `/etc/agora/agora.env` | settings and secrets, root-owned |
| `/etc/agora/saml/` | SAML certificates and key, if SAML is on |
| `/var/log/agora/` | application logs |

## 1. Prepare the host

Install nginx, Redis, PostgreSQL, Python 3.11 or newer with `venv`, git, curl,
and the xmlsec1 development libraries (needed by the SAML library if pip
cannot use a prebuilt wheel).

```sh
sudo adduser --system --group --home /home/agora agora
sudo chmod 0711 /home/agora            # nginx must traverse to the static root
sudo -u agora mkdir -p /home/agora/agora-releases \
    /home/agora/agora-data/staticfiles /home/agora/agora-data/media
sudo chmod 0755 /home/agora/agora-data /home/agora/agora-data/staticfiles
sudo chmod 0750 /home/agora/agora-data/media
sudo mkdir -p /srv/agora /var/log/agora /etc/agora/saml
sudo chown agora:agora /srv/agora /var/log/agora
sudo chown root:agora /etc/agora /etc/agora/saml
sudo chmod 0750 /etc/agora /etc/agora/saml
```

Database and Redis:

```sh
sudo -u postgres createuser --pwprompt agora
sudo -u postgres createdb --owner agora agora
```

Keep Redis bound to localhost (the Ubuntu default).

## 2. Get the code and the virtualenv

```sh
sudo -u agora git clone https://github.com/hezar1000/agora.git /home/agora/agora
sudo -u agora python3 -m venv /home/agora/venv
sudo -u agora /home/agora/venv/bin/pip install -r /home/agora/agora/requirements_new.txt
```

Every release shares this virtualenv. When `requirements_new.txt` changes,
run the same `pip install` against the new release's copy before activating
it; `deploy/activate-release.sh` refuses to switch while packages are missing.

## 3. Configure

```sh
sudo cp /home/agora/agora/.env.example /etc/agora/agora.env
sudo chown root:agora /etc/agora/agora.env
sudo chmod 0640 /etc/agora/agora.env
sudoedit /etc/agora/agora.env
```

Replace every placeholder. The ones that matter most:

- `DJANGO_SECRET_KEY`: 50+ random characters, e.g.
  `python3 -c 'import secrets; print(secrets.token_urlsafe(64))'`.
- `DATABASE_URL`, `REDIS_URL`.
- `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (`https://` + the same
  hosts), `AGORA_PROBE_HOST` (one of those hosts; the health probes use it).
- `DJANGO_STATIC_ROOT` and `DJANGO_MEDIA_ROOT` as in the layout above. Media
  must not be inside the static root.
- SMTP: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS=true`, `EMAIL_HOST_USER`,
  `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`.
- `DATABASE_CONN_MAX_AGE=0`. Persistent connections leak under Daphne and
  exhausted PostgreSQL's connection slots during a lecture; connections are
  pooled by psycopg instead (`DATABASE_POOL_MAX_SIZE`, default 18 per worker,
  keeps four workers under PostgreSQL's default `max_connections=100`).
- Leave `DJANGO_HSTS_SECONDS=0` until HTTPS is confirmed working, then raise
  it.

Sign-in options:

- **Password accounts** always exist. With `MFA_ENFORCED=true` (the default)
  each password account must enrol an authenticator app.
- **SAML single sign-on**: set `SAML2_INTEGRATION_ENABLED=true` and see
  [Sign-in through your institution](#sign-in-through-your-institution).
  With it off, set `SAML2_INTEGRATION_ENABLED=false` and people use password
  accounts only.
- `SIGNUP_ENABLED` lets people make their own password accounts,
  `GUEST_SESSIONS_ENABLED` offers a throwaway demonstration class on the
  sign-in page, and `OPEN_COURSE_CREATION` lets any account create a course
  (otherwise only superusers and existing instructors can).

## 4. First release

Releases are built from a commit of the checkout:

```sh
cd /home/agora/agora
sudo -u agora git pull
SHA=$(git rev-parse --short=8 HEAD)
sudo -u agora git archive -o /home/agora/agora-releases/agora-$SHA.tar $SHA
sudo bash deploy/build-release.sh $SHA /home/agora/agora-releases/agora-$SHA.tar
```

Install the services and the site (once):

```sh
R=/home/agora/agora-releases/release-$SHA
sudo cp $R/deploy/systemd/agora-web@.service $R/deploy/systemd/agora-web.target \
        $R/deploy/systemd/agora-live-sessions.service \
        $R/deploy/systemd/agora-live-sessions.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable agora-web.target agora-live-sessions.timer

sudo cp $R/deploy/nginx.conf /etc/nginx/sites-available/agora.conf
sudoedit /etc/nginx/sites-available/agora.conf     # your hostname
sudo ln -s /etc/nginx/sites-available/agora.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
```

The config expects a Let's Encrypt certificate, and nginx will not load it
until the certificate exists. Get one first with nginx stopped, then let
renewals use the webroot the config serves:

```sh
sudo systemctl stop nginx
sudo certbot certonly --standalone -d <host>
sudo mkdir -p /var/www/letsencrypt
sudo nginx -t && sudo systemctl start nginx
sudo certbot reconfigure --cert-name <host> --webroot -w /var/www/letsencrypt
```

Activate the release, which migrates, collects static files, switches
`/srv/agora/current`, and starts the workers:

```sh
sudo bash $R/deploy/activate-release.sh $R
sudo systemctl start agora-live-sessions.timer
```

Create the first administrator:

```sh
cd /srv/agora/current
sudo bash -c 'set -a; . /etc/agora/agora.env; set +a;
  runuser -u agora -- .venv/bin/python manage.py createsuperuser'
```

Sign in as that account and create a course. Its settings page shows an
instructor enrolment code; an instructor who enters it on the course list
joins as an instructor, and from then on can create courses of their own.

## Updating

1. Back up the database (`pg_dump`) and confirm the backup is readable.
   Migrations are not undone by switching back to an older release.
2. Build the new commit as in step 4 (`git pull`, `git archive`,
   `build-release.sh`).
3. If `requirements_new.txt` changed, `pip install` it into the shared
   virtualenv.
4. `sudo bash $R/deploy/activate-release.sh $R`. It prints the command that
   switches back to the previous release.

`build-release.sh` keeps the live release and two older ones.

## Operations

- `https://<host>/health/live/` answers if a worker can serve HTTP;
  `/health/ready/` also checks the database and Redis. Each worker unit waits
  for `/health/live/` on its own port before systemd counts it as started.
- The live-session timer should log "Live-session maintenance completed."
  every minute: `journalctl -u agora-live-sessions.service --since -15min`.
  A failed run, or a gap longer than two minutes, means lectures are not
  opening or closing on schedule.
- Worker logs: `journalctl -u 'agora-web@*'`. Application logs:
  `/var/log/agora/`. Access log: `/var/log/nginx/agora.access.log`, which
  records paths without query strings because SAML puts signed messages in
  the query.
- Do not add a `/media/` location to nginx: photos are served by Django,
  which checks who is asking.

## Sign-in through your institution

Agora speaks SAML 2.0. UBC's settings are in the code; another institution
changes these:

1. **Identity provider.** In `mta2/saml_settings.py`, under `SAML_PROVIDERS`,
   set the `idp` block: `entityId`, the `singleSignOnService` URL, and the
   `singleLogoutService` URL from your provider's metadata. Put the provider's
   signing certificate in `/etc/agora/saml/idp.crt`.
2. **Agora's own key pair.** Generate one and point `SAML_SP_PUBLIC_CERT_FILE`
   and `SAML_SP_PRIVATE_KEY_FILE` at it:

   ```sh
   sudo openssl req -x509 -newkey rsa:3072 -nodes -days 3650 \
       -subj "/CN=<host>" -keyout /etc/agora/saml/sp.key -out /etc/agora/saml/sp.crt
   sudo chown root:agora /etc/agora/saml/*
   sudo chmod 0640 /etc/agora/saml/*
   ```
3. **Register Agora with your provider** as a service provider: entity ID
   `https://<host>/` (`SAML_ENTITY_ID`), assertion consumer service
   `https://<host>/sso/saml/?acs` (HTTP-POST), single logout
   `https://<host>/sso/saml/?sls` (HTTP-Redirect), and the certificate from
   step 2. Agora needs these attributes released:

   | Attribute | OID | Used as |
   | --- | --- | --- |
   | `uid` | `urn:oid:0.9.2342.19200300.100.1.1` | the stable identity (required) |
   | `givenName` | `urn:oid:2.5.4.42` | first name |
   | `sn` | `urn:oid:2.5.4.4` | last name |
   | `mail` | `urn:oid:0.9.2342.19200300.100.1.3` | email |
   | student number | UBC: `urn:mace:dir:attribute-def:ubcEduStudentNumber` | username, when present |

   The student number is optional: without it, usernames are the `uid`.
   To use your institution's equivalent, change that attribute name in
   `mta2/saml_settings.py` (`SAML_USERS_MAP`, `attributeMapping`) and in
   `custom_saml_acs` in `mta2/views.py`.
4. **Signatures.** The `security` block in `mta2/saml_settings.py` matches
   what UBC's provider sends (a signed Response around an encrypted
   Assertion). If yours signs differently, adjust `wantMessagesSigned` and
   `wantAssertionsSigned` to match; the comment there explains the trade-off.

## Institution-specific wording

The interface names UBC and its sign-in service (CWL) in a few places. For
another institution, edit:

- `peer_account/templates/user-signup-and-login.html` and
  `peer_account/templates/cwl-login.html`: the sign-in button and text.
- `peer_account/templates/account/password_reset_form.html` and
  `password_reset_done.html`: "CWL accounts have no password".
- `peer_account/forms.py`: "As UBC has it" on the profile form.
- `peer_home/templates/index.html`: footer links to UBC policies.
- `peer_course/templates/course-list.html`: the participation guidelines,
  which cite UBC policies and `agora.help@ubc.ca`.
- `peer_account/templates/about.html`, `peer_home/templates/500.html`: support
  addresses.
- `peer_course/templates/agora.html`: the export dialog asks for a UBC email.

Times are shown in each course's time zone, which defaults to
`America/Vancouver` (`course_timezone` in `peer_course/models.py`, `TIME_ZONE`
in `mta2/settings.py`).

## Known limitations

- The base interface still loads jQuery 3.1.1, jQuery UI 1.12.1 and Bootstrap
  3.3.7, which predate published XSS fixes. Upgrading them touches nearly
  every live control, so it needs a full browser pass in staging.
- The Content-Security-Policy allows `unsafe-inline` for scripts and styles
  because the interface uses inline code throughout, and allows HTTPS images
  because poll authors can embed them.
