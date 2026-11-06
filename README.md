# Agora

Agora is an in-class participation tracking and grading system: live hand
raising and selection, participation records and grading schemes, polls,
messages, and SAML sign-in. It is described in the
[Agora paper](https://www.cs.ubc.ca/~kevinlb/papers/2024-Agora.pdf).

Agora grew out of the [MTA](https://www.cs.ubc.ca/~kevinlb/papers/2024-SIGCSE-MTA2.pdf)
peer-review codebase. The assignment, submission, peer-review, evaluation,
calibration, and gradebook runtime code has been removed; its migration modules
remain only so an existing database can be upgraded without deleting historic
tables or data.

## Start here

- Compatibility-only migration policy: [`docs/COMPATIBILITY_MIGRATIONS.md`](docs/COMPATIBILITY_MIGRATIONS.md)
- Environment template: [`.env.example`](.env.example)
- Production setup: [`prod_setup.txt`](prod_setup.txt)
- Hardened systemd units: [`deploy/systemd/`](deploy/systemd/)
- Classroom microphone: [`docs/CLASSROOM_MICROPHONE.md`](docs/CLASSROOM_MICROPHONE.md)

## Local development

Use Python 3.11 or newer, install `requirements_new.txt`, copy the values from
`.env.example` into your local environment, and explicitly enable the
isolated local defaults:

```text
DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS=1
SAML2_INTEGRATION_ENABLED=0
```

Then run migrations and the complete test suite:

```text
python manage.py migrate
python manage.py test
```

Production intentionally fails closed when required secrets, hostnames, or the
database URL are missing. See [`prod_setup.txt`](prod_setup.txt) before
deploying.
