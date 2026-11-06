# Agora

Agora is an in-class participation tracking and grading system: live hand
raising and selection, participation records and grading schemes, polls,
messages, and SAML sign-in. It is described in the
[Agora paper](https://www.cs.ubc.ca/~kevinlb/papers/2024-Agora.pdf).

Agora grew out of the [MTA](https://www.cs.ubc.ca/~kevinlb/papers/2024-SIGCSE-MTA2.pdf)
peer-review codebase; MTA itself lives in its own repository.

## Documentation

- [Deploying Agora](docs/DEPLOYING.md): server setup, configuration, releases,
  operations, and adapting Agora to another institution's sign-in.
- [Classroom microphone](docs/CLASSROOM_MICROPHONE.md)
- [`.env.example`](.env.example): every production setting.
- [`deploy/`](deploy/): nginx config, systemd units, preflight checks, and the
  release scripts.

## Local development

Use Python 3.11 or newer, with a Redis server running locally (live lectures
and sign-in rate limits go through it):

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_new.txt
export DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS=1 DJANGO_DEBUG=1 \
       SAML2_INTEGRATION_ENABLED=0 MFA_ENFORCED=0
python manage.py migrate
python manage.py createsuperuser
python manage.py make_participation_demo --instructor <that username>
python manage.py runserver
```

`DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS` gives SQLite, Redis on localhost and a
throwaway secret key, so nothing else needs configuring. The demo
command fills two courses with students and lectures (`--remove` takes them
out again). Run the tests with `python manage.py test`; CI also runs them
against PostgreSQL.

Production refuses to start when required secrets, hostnames, or the database
URL are missing; see [Deploying Agora](docs/DEPLOYING.md).
