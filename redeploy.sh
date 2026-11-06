#!/usr/bin/env bash
# Safely fast-forward and roll Agora's four ASGI workers one at a time.

set -Eeuo pipefail

readonly REPO_DIR="${AGORA_REPO_DIR:-/home/agora/agora}"
readonly VENV_DIR="${AGORA_VENV_DIR:-${REPO_DIR}/agora_env}"
readonly DEPLOY_BRANCH="${AGORA_DEPLOY_BRANCH:-agora}"
readonly DEPLOY_LOCK="${AGORA_DEPLOY_LOCK:-/var/lock/agora-deploy.lock}"
readonly PROBE_HOST="${AGORA_PROBE_HOST:-agora.students.cs.ubc.ca}"
readonly WORKER_PORTS=(8080 8081 8082 8083)

cd "${REPO_DIR}"

exec 9>"${DEPLOY_LOCK}"
if ! flock --nonblock 9; then
    echo "Another Agora deployment is already running." >&2
    exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Agora virtual environment is missing: ${VENV_DIR}" >&2
    exit 1
fi

if [[ "$(git branch --show-current)" != "${DEPLOY_BRANCH}" ]]; then
    echo "Refusing to deploy a branch other than ${DEPLOY_BRANCH}." >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing to deploy over uncommitted tracked changes." >&2
    exit 1
fi

# Validate the installed reverse-proxy configuration before changing the app.
sudo -n nginx -t

git fetch --prune origin "${DEPLOY_BRANCH}"
if ! git merge-base --is-ancestor HEAD "origin/${DEPLOY_BRANCH}"; then
    echo "The deployed checkout has commits not present on origin/${DEPLOY_BRANCH}." >&2
    exit 1
fi
git merge --ff-only "origin/${DEPLOY_BRANCH}"

"${VENV_DIR}/bin/python" -m pip install \
    --disable-pip-version-check \
    --require-virtualenv \
    --requirement requirements_new.txt
"${VENV_DIR}/bin/python" -m pip check

"${VENV_DIR}/bin/python" manage.py check --deploy
"${VENV_DIR}/bin/python" manage.py makemigrations --check --dry-run
"${VENV_DIR}/bin/python" manage.py collectstatic --noinput
"${VENV_DIR}/bin/python" manage.py migrate --noinput

# Keep three workers available while each process adopts the new release.
for port in "${WORKER_PORTS[@]}"; do
    unit="agora-web@${port}.service"
    sudo -n systemctl restart "${unit}"
    sudo -n systemctl is-active --quiet "${unit}"
    healthy=false
    for _ in {1..10}; do
        # Readiness verifies this worker can reach both PostgreSQL and the
        # configured Channels/Redis path; the root page is not a health probe.
        if curl --fail --silent \
            --max-time 10 \
            --header "Host: ${PROBE_HOST}" \
            --header "X-Forwarded-Proto: https" \
            "http://127.0.0.1:${port}/health/ready/" >/dev/null; then
            healthy=true
            break
        fi
        sleep 1
    done
    if [[ "${healthy}" != true ]]; then
        echo "Worker ${unit} did not become healthy after restart." >&2
        exit 1
    fi
done

sudo -n systemctl reload nginx
echo "Agora deployment completed successfully."
