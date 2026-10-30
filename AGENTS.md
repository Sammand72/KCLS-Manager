# KCLS-manager

## What this is

A personal automation script that monitors a Gmail inbox via IMAP for King County Library System (KCLS) notification emails and pushes structured entries to Google Tasks or Todoist.

It currently supports:

- **Hold ready for pickup**: creates a pickup task with author, library branch, street address, account user, and due date.
- **Hold expired**: updates/renames existing hold pickup tasks to mark them as `[EXPIRED]`.
- **Checkout receipts**: creates `KCLS book due:` tasks for all checked-out books (including books that were never holds) and auto-completes matching open hold tasks.
- **Due date renewals**: updates the due date on existing open due-date tasks.
- **Check-in receipts**: auto-completes open due-date tasks when books are returned.

The owner is learning Python with this project. Favor the codebase's existing plain, heavily-commented style over clever abstractions — see "Conventions to preserve" below.

## Architecture

Six core Python modules in [src/](src/), plus the test suite. Config and data files (`.env`, `credentials.json`, `token.json`, `users.json`, `library_locations.json`, logs) stay in the project root; each module finds them through its `project_dir` variable (the folder above `src/`).

- [src/kcls_parser.py](src/kcls_parser.py) — Main entrypoint. Connects to Gmail over IMAP, searches the inbox for unread KCLS emails (holds, checkout receipts, renewals, check-ins), parses message bodies into structured records, updates/creates tasks, logs library events to `library_records.jsonl`, and marks emails as read.
- [src/kcls_google_auth.py](src/kcls_google_auth.py) — Shared Google sign-in. `get_google_credentials(include_gmail, include_tasks)` loads/refreshes `token.json` or opens the browser OAuth flow using `credentials.json`, re-asking when a saved token lacks a needed scope. `get_google_email()` reads the signed-in address for IMAP XOAUTH2 login.
- [src/kcls_secrets.py](src/kcls_secrets.py) — Secret storage. `get_secret(name)` reads `TODOIST_API_TOKEN`, `EMAIL_USER`, `EMAIL_PASS` from `.env` (`SECRET_STORAGE=file`, the default) or from the OS keychain via the `keyring` library (`SECRET_STORAGE=keychain`, service name `KCLS-manager`). Also holds the Google token as `GOOGLE_TOKEN` in keychain mode. Run directly (`python src/kcls_secrets.py set|delete|status`) to manage keychain entries.
- [src/kcls_tasker_gtasks.py](src/kcls_tasker_gtasks.py) — Google Tasks integration. Builds the Tasks API client from shared Google credentials, resolves the target tasklist by name (`GOOGLE_TASKLIST_NAME`), inserts hold and due tasks, renames expired holds, updates renewed due dates, and marks completed tasks.
- [src/kcls_tasker_todoist.py](src/kcls_tasker_todoist.py) — Todoist integration. Authenticates with a static API token (`TODOIST_API_TOKEN`), resolves the target project by name (`TODOIST_PROJECT_NAME`), inserts hold and due tasks, renames expired holds, updates renewed due dates, and marks completed tasks.
- [src/kcls_task_matching.py](src/kcls_task_matching.py) — Shared title and user normalization and matching logic. Handles substring matching for truncated KCLS titles (requiring >= 10 characters) and validates account user attribution.
- [tests/](tests/) — Unit test suite with offline mocks:
  - `tests/test_kcls_parser.py` — Tests IMAP connection retry/fail logic, logging, location address loading, and body parsing for all email types.
  - `tests/test_task_matching.py` — Tests title/user normalization and task-matching logic across task managers.
  - `tests/test_google_auth.py` — Tests Google sign-in scopes, token reuse/refresh, re-consent, non-interactive errors, and keychain token storage/migration.
  - `tests/test_secrets.py` — Tests `SECRET_STORAGE` handling, keychain reads/writes (with an in-memory fake keyring), and the helper CLI.

`kcls_parser.py` calls the corresponding task functions in `kcls_tasker_gtasks.py` or `kcls_tasker_todoist.py` depending on the `TASK_MANAGER` env var (`google` or `todoist`) — never both simultaneously. Both tasker modules share consistent interfaces so they are interchangeable at call sites.

## Local config, data files, and secrets

| File | Contents | Checked in? |
| --- | --- | --- |
| `.env` | `SECRET_STORAGE` (`file` = secrets in `.env`/`token.json`, `keychain` = OS keychain; blank means `file`), `EMAIL_LOGIN_TYPE` (`default` = Google sign-in window, `legacy` = App Password; blank means `default`), `EMAIL_USER` / `EMAIL_PASS` (legacy only: monitored Gmail address and App Password), `TASK_MANAGER` (`google` or `todoist`), `TODOIST_API_TOKEN` (for Todoist), `GOOGLE_TASKLIST_NAME`, and `TODOIST_PROJECT_NAME` | No (gitignored) |
| `.env.example` | Template showing required and optional environment variable names | Yes |
| `users.example.json` | Template showing the `users.json` format (email and card-number keys, blank-name example) | Yes |
| `credentials.json` | A "Desktop app" OAuth client: either shared privately by the project owner, or created by the user in their own Google Cloud project (steps in the README). Never commit it: GitHub push protection flags its client secret, and a public copy could be reused by others | No (gitignored) |
| `token.json` | The user's personal Google sign-in (refresh token), auto-generated on first run by `get_google_credentials()`. With `SECRET_STORAGE=keychain` it lives in the OS keychain instead (an existing file is moved in once and deleted) | No (gitignored) |
| `users.json` | Flat map of recipient email address (or, for shared addresses, library card number) → name. A blank name means the email itself is the label. Used to attribute every email to a family member | No (gitignored) |
| `library_locations.json` | List of KCLS branch names and street addresses used to enrich hold tasks with branch addresses | Yes |
| `library_records.jsonl` | Append-only event history (holds, checkouts, renewals, check-ins), one JSON object per line | No (gitignored) |
| `library_tracer.log` | Operational log output from the parser and task managers | No (gitignored) |

Keep real secrets and personal data out of any committed files or templates.

## Running and testing

Dependencies are managed with [uv](https://docs.astral.sh/uv/). [pyproject.toml](pyproject.toml) lists only the packages the code imports directly (plus `ruff` in the `dev` dependency group); [uv.lock](uv.lock) pins every package, including the ones pulled in by others, for all platforms. [.python-version](.python-version) pins Python 3.12. Install everything into `.venv` with:

```bash
uv sync
```

Use `uv add` / `uv remove` to change dependencies (never `pip install` into the `.venv`, since `uv sync` removes anything not in the lockfile), and commit `pyproject.toml` and `uv.lock` together. Run commands with `uv run ...` so they always use the project's `.venv`. `uv sync` also installs `src/` into `.venv` in editable mode (setuptools `package-dir` + `py-modules` in `pyproject.toml`), which is how the tests can `import kcls_parser`; a new module in `src/` must be added to `py-modules`. Scheduled runs (cron / Task Scheduler) call `.venv/bin/python` directly and don't need uv.

When the interpreter "can't find" an installed package, verify whether the shell or editor is pointed at the `.venv` rather than system Python.

### Running unit tests

All tests run offline without connecting to Gmail or external APIs:

```bash
uv run python -m unittest discover -s tests
```

### Linting and formatting

Use `ruff` for linting and formatting:

```bash
uv run ruff check .
```

### First run setup

The first run needs a human at a browser: `get_google_credentials()` in `kcls_google_auth.py` opens one Google sign-in window. It covers Gmail (in `default` login mode) and Google Tasks (when `TASK_MANAGER=google`), and saves `token.json`. After that it runs unattended (via cron or Windows Task Scheduler). Todoist only requires setting `TODOIST_API_TOKEN` in `.env`.

One-time setup in the Google Cloud project behind `credentials.json` (README → "Getting your own credentials.json"): enable the Gmail API and Tasks API, add both scopes to the OAuth consent screen, and set its publishing status to **In production**. In "Testing" status, refresh tokens expire after 7 days and break unattended runs.

## Parsing behavior and search criteria

The script executes 4 distinct IMAP search passes for unread emails (`UNSEEN`):

1. **Holds**: `FROM "noreply@kcls.org"` matching subjects `"Hold is Ready"`, `"Holds are Ready"`, `"Hold Has Expired"`, or `"Holds have expired"`.
2. **Checkout receipts**: `FROM "noreply@kcls.org"` matching subject `"Checkout Receipt"`.
3. **Due date renewals**: `FROM "noreply@kcls.org"` matching subject `"Due Dates from King County Library System"`.
4. **Check-in returns**: `FROM "No-Reply-AMH-Notify@kcls.org"` matching subjects `"Checkin"` or `"Items Returned Receipt"`.

- All matching unread emails are processed oldest-to-newest and marked as `\Seen` once handled.
- If an email cannot be parsed (e.g. empty or unexpected format), an error is logged and the email is marked as read to prevent endless retry loops on subsequent runs.
- Gmail login uses IMAP `XOAUTH2` with the Google sign-in token (`login_to_gmail_oauth()`), or `mail.login()` with an App Password when `EMAIL_LOGIN_TYPE=legacy` (`login_to_gmail()`). Both share the retry logic in `try_gmail_login()`.
- The account user comes from `resolve_user()` in `kcls_parser.py`: a mapped card number (holds/renewals) or a "Dear NAME," greeting (receipts) wins, otherwise the original `To:` recipient address (kept intact by Gmail auto-forwarding) is looked up in `users.json`.
- Title matching normalizes strings (lowercase, strips punctuation and extra whitespace). If titles do not match exactly, a truncated hold title will match a checkout title if it is at least 10 characters long and the checkout title starts with the hold title.

## Conventions to preserve

- Python's standard `logging` module writes operational messages to `library_tracer.log` and stdout, while structured library domain events are appended to `library_records.jsonl`.
- Explanatory inline comments aimed at someone learning Python (comments explain *why* and *what* a line does, rather than just repeating the code).
- Intentional, well-logged `except Exception:` blocks around per-email processing loops so a single malformed email never crashes the entire run.
- snake_case functions and variables; hold/book data passed around as plain dicts, not classes.
- Google Tasks API calls use explicit keyword arguments (e.g., `add_hold_to_google(book_title=..., author=..., ...)`).

## Working with the user

The repository owner is learning Python. When making suggestions or changes:

- Briefly explain *why* a change is recommended.
- Prefer readable, incremental, and well-commented code over dense one-liners or complex abstractions.
- Discuss tradeoffs before introducing new dependencies or frameworks.
