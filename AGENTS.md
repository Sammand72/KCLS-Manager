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

Four core Python modules plus the test suite:

- [kcls_parser.py](kcls_parser.py) — Main entrypoint. Connects to Gmail over IMAP, searches the inbox for unread KCLS emails (holds, checkout receipts, renewals, check-ins), parses message bodies into structured records, updates/creates tasks, logs library events to `library_records.jsonl`, and marks emails as read.
- [kcls_tasker_gtasks.py](kcls_tasker_gtasks.py) — Google Tasks integration. Handles Google OAuth flow, resolves the target tasklist by name (`GOOGLE_TASKLIST_NAME`), inserts hold and due tasks, renames expired holds, updates renewed due dates, and marks completed tasks.
- [kcls_tasker_todoist.py](kcls_tasker_todoist.py) — Todoist integration. Authenticates with a static API token (`TODOIST_API_TOKEN`), resolves the target project by name (`TODOIST_PROJECT_NAME`), inserts hold and due tasks, renames expired holds, updates renewed due dates, and marks completed tasks.
- [kcls_task_matching.py](kcls_task_matching.py) — Shared title and user normalization and matching logic. Handles substring matching for truncated KCLS titles (requiring >= 10 characters) and validates account user attribution.
- [tests/](tests/) — Unit test suite with offline mocks:
  - `tests/test_kcls_parser.py` — Tests IMAP connection retry/fail logic, logging, location address loading, and body parsing for all email types.
  - `tests/test_task_matching.py` — Tests title/user normalization and task-matching logic across task managers.

`kcls_parser.py` calls the corresponding task functions in `kcls_tasker_gtasks.py` or `kcls_tasker_todoist.py` depending on the `TASK_MANAGER` env var (`google` or `todoist`) — never both simultaneously. Both tasker modules share consistent interfaces so they are interchangeable at call sites.

## Local config, data files, and secrets

| File | Contents | Checked in? |
| --- | --- | --- |
| `.env` | `EMAIL_USER` / `EMAIL_PASS` (monitored Gmail address and App Password), `TASK_MANAGER` (`google` or `todoist`), `TODOIST_API_TOKEN` (for Todoist), `GOOGLE_TASKLIST_NAME`, and `TODOIST_PROJECT_NAME` | No (gitignored) |
| `.env.example` | Template showing required and optional environment variable names | Yes |
| `users.example.json` | Template showing the `users.json` format (email and card-number keys, blank-name example) | Yes |
| `credentials.json` | Google Cloud OAuth 2.0 client secrets (downloaded from Google Cloud Console) | No (gitignored) |
| `token.json` | Cached OAuth token, auto-generated on first run by `authenticate_google_tasks()` | No (gitignored) |
| `users.json` | Flat map of recipient email address (or, for shared addresses, library card number) → name. A blank name means the email itself is the label. Used to attribute every email to a family member | No (gitignored) |
| `library_locations.json` | List of KCLS branch names and street addresses used to enrich hold tasks with branch addresses | Yes |
| `library_records.jsonl` | Append-only event history (holds, checkouts, renewals, check-ins), one JSON object per line | No (gitignored) |
| `library_tracer.log` | Operational log output from the parser and task managers | No (gitignored) |

Keep real secrets and personal data out of any committed files or templates.

## Running and testing

Dependencies are pinned in [requirements.txt](requirements.txt) (generated via `pip freeze`) and installed into a `.venv`:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

When the interpreter "can't find" an installed package, verify whether the shell or editor is pointed at the `.venv` rather than system Python.

### Running unit tests

All tests run offline without connecting to Gmail or external APIs:

```bash
python -m unittest discover -s tests
```

### Linting and formatting

Use `ruff` for linting and formatting:

```bash
ruff check .
```

### First run setup

First run with Google Tasks needs a human at a browser: `authenticate_google_tasks()` in `kcls_tasker_gtasks.py` opens a browser window to create `token.json`. After that it runs unattended (via cron or Windows Task Scheduler). Todoist only requires setting `TODOIST_API_TOKEN` in `.env`.

## Parsing behavior and search criteria

The script executes 4 distinct IMAP search passes for unread emails (`UNSEEN`):

1. **Holds**: `FROM "noreply@kcls.org"` matching subjects `"Hold is Ready"`, `"Holds are Ready"`, `"Hold Has Expired"`, or `"Holds have expired"`.
2. **Checkout receipts**: `FROM "noreply@kcls.org"` matching subject `"Checkout Receipt"`.
3. **Due date renewals**: `FROM "noreply@kcls.org"` matching subject `"Due Dates from King County Library System"`.
4. **Check-in returns**: `FROM "No-Reply-AMH-Notify@kcls.org"` matching subjects `"Checkin"` or `"Items Returned Receipt"`.

- All matching unread emails are processed oldest-to-newest and marked as `\Seen` once handled.
- If an email cannot be parsed (e.g. empty or unexpected format), an error is logged and the email is marked as read to prevent endless retry loops on subsequent runs.
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
