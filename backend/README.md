# CodeWise V2 backend

This Django 5.2 / Django REST Framework service provides authenticated, workspace-scoped repository submissions, synchronous workflow executions, agent results, and human approval APIs. Production deployment has not been provisioned; see [production readiness](../docs/production-readiness.md) for blockers and operator requirements.

## Local development

Use Python 3.11+ and the project virtual environment. From this directory in PowerShell:

```powershell
$env:DJANGO_DEBUG = "true"
python manage.py migrate
python manage.py runserver
```

The SQLite database is the local development and test default. Production settings fail closed unless PostgreSQL, HTTPS/security settings, a private persistent snapshot volume, and the workflow runtime factory are configured. See [.env.example](.env.example); never commit populated environment files or place provider credentials in frontend variables.

## API and authentication

The versioned API is under `/api/v1/`. `GET /health/` is process liveness; `GET /ready/` checks database connectivity and configured workflow runtime. Session login requires a CSRF token from `GET /session/csrf/`; authenticated endpoints use Django sessions and workspace membership. Login/logout, workspace discovery, submission/execution, results, and approval are implemented. Execution currently runs synchronously in the request process.

Run locally at `http://127.0.0.1:8000`. The Vite development server proxies `/api/` to this address. Production should use same-origin HTTPS hosting; no CORS policy or production web-server configuration is included.

## Initial workspace provisioning (Windows CMD)

After applying migrations, provision the one initial workspace for an existing active Django superuser from `backend/`:

```bat
python manage.py migrate
python manage.py provision_initial_workspace --username admin --name "CodeWise Workspace" --description "Initial CodeWise workspace"
```

The command assigns that account the `owner` role. Repeating the same command is idempotent. It rejects inactive/non-superuser accounts, mismatched reruns, existing ambiguous workspace data, and users already attached to another workspace. `manage.py` does not load `.env`; set the required environment variables in CMD or your deployment environment before running Django commands. For local development, use `set DJANGO_DEBUG=true` in the same CMD window.

## Checks and tests

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Tests use fake/offline providers. Do not configure paid or live provider credentials for test runs.
