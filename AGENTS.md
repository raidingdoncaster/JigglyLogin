# Repository Guidelines

## Project Structure & Module Organization
- `app.py` is the single Flask entrypoint covering routing, Supabase access, and Google Sheets helpers.
- Client assets live under `templates/` and `static/`; policy pages sit in `templates/policies/`, while media lands in gitignored `uploads/`.
- Operational scripts (`deploy.sh`, `logs*.sh`, `Dockerfile`, `Procfile`) in the repo root mirror the Cloud Run pipeline—update them alongside infra changes.

## Build, Test, and Development Commands
- Bootstrap locally with `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Run the server via `python app.py` for debug mode or `gunicorn app:app` to emulate production.
- Deploy by ensuring `chmod +x deploy.sh`, then running `./deploy.sh`; inspect recent revisions using `gcloud run revisions list --service jigglylogin --region europe-west1`.
- Tail logs quickly with `./logs-errors.sh` (errors) or `./logs-debug.sh` (full stream).

## Coding Style & Naming Conventions
- Follow PEP 8: 4 spaces, snake_case for functions, ALL_CAPS for feature toggles like `USE_SUPABASE`; limit routes to clear verbs/nouns (`/dashboard`, `/pwa-flag`).
- Template names are lowercase-hyphenated (`templates/policies/privacy-policy.html`), and related static assets should mirror that slug.
- Keep helper logic isolated in small functions with succinct docstrings when behaviour is non-obvious; prefer Flask blueprints if you split `app.py` later.

## Testing Guidelines
- No automated suite exists yet; when adding logic-heavy features, introduce Pytest cases under `tests/` using route-focused filenames (`test_dashboard.py`).
- Mock Google Sheets and Supabase interactions to avoid touching production data; use fixture JSON under `tests/fixtures/` once created.
- Until tests land, perform manual smoke checks for trainer login, dashboard metrics, policy rendering, and stamp submission flows before merging.

## Commit & Pull Request Guidelines
- Commit messages are short, imperative, and lower-case (`Add Passport tiles`, `Auto-deploy update (...)`); reserve the timestamped pattern for `deploy.sh` runs.
- Scope commits around a single change surface (template, route, or script) to simplify rollbacks.
- Pull requests must note affected routes, new environment variables (`SUPABASE_URL`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`), and any Cloud Run follow-up.
- Include screenshots or GIFs when altering templates or static assets that impact UI states.

## Security & Configuration Tips
- Keep secrets in environment variables; never commit service-account JSON or Supabase keys—Cloud Run supports inline secrets for `GOOGLE_APPLICATION_CREDENTIALS_JSON`.
- Validate and sanitize files saved in `uploads/`; the existing Pillow/Tesseract checks should be preserved or extended.
- Feature toggles (`USE_SUPABASE`, `MAINTENANCE_MODE`) gate external services—double-check their defaults before deployment.
