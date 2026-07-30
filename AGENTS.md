# AGENTS.md

## Cursor Cloud specific instructions

Pusula is an early-stage Python analysis pipeline (see `README.md` and
`ROADMAP.md`). There is **no web/app server yet** (FastAPI is planned but not
implemented). The runnable pieces today are the `pytest` suite and
`scripts/zoho_smoke.py`.

### Python / dependencies
- The repo targets Python 3.11 (`.cursorrules`), but the VM ships only Python
  3.12. The code is compatible with 3.12, so the venv uses `python3`.
- The startup update script creates/refreshes a virtualenv at `.venv` and
  installs `requirements.txt` plus `pytest` (a test-only dep not in
  `requirements.txt`). Run tools via `.venv/bin/python`.

### PostgreSQL (required for DB tests and the identity module)
- A local PostgreSQL 16 cluster is installed with role/db `pusula` (password
  `pusula`) and the schema already applied. This persists in the VM snapshot,
  but the server process is **not auto-started on boot** — start it each session
  with: `sudo pg_ctlcluster 16 main start`.
- Connection string: `postgresql://pusula:pusula@localhost:5432/pusula`.
- Re-apply schema if needed (idempotent, uses `IF NOT EXISTS`):
  `psql "$DATABASE_URL" -f pusula/db/schema.sql`.

### DATABASE_URL — do not create a `.env`
- `.cursorrules` forbids reading/writing `.env`. Provide config by **exporting**
  env vars instead. For DB work: `export DATABASE_URL=postgresql://pusula:pusula@localhost:5432/pusula`.
- `scripts/zoho_smoke.py` calls `load_dotenv()`; with no `.env` it simply uses
  the current environment, so exported vars work.

### Test / lint / run
- Tests: `.venv/bin/python -m pytest eval/`. The `resolve_thread` DB tests
  **auto-skip** when `DATABASE_URL` is unset; export it (and start Postgres) to
  run all 16 tests.
- Lint: none configured (no ruff/flake8/mypy). Use
  `.venv/bin/python -m compileall pusula eval scripts` as a syntax check.
- Zoho smoke: `.venv/bin/python scripts/zoho_smoke.py` needs real Zoho OAuth
  secrets (`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`,
  `ZOHO_ACCOUNTS_DOMAIN`, `ZOHO_API_DOMAIN`). Without them it exits cleanly with
  "Eksik ortam değişkenleri".
