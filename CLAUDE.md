# KCLS-manager

## What this is

A personal automation script that monitors a Gmail inbox via IMAP for King County Library System (KCLS) notification emails and pushes structured entries to a Google Tasks list. Right now it only understands "your hold is ready for pickup" emails; the plan is to extend it to also handle "hold expired," "due date reminder," "overdue," "item checked out," and similar KCLS notices.

The owner is learning Python with this project. Favor the codebase's existing plain, heavily-commented style over clever abstractions — see "Conventions to preserve" below.

## Architecture

Three files:

- [kcls_hold_parser.py](kcls_hold_parser.py) — entrypoint. Connects to Gmail over IMAP, searches the inbox for unread KCLS emails, parses each matching email's HTML body (via BeautifulSoup) into structured "hold" records, and marks each email as read once its holds are processed.
- [kcls_hold_gtasks.py](kcls_hold_gtasks.py) — Google Tasks integration. Handles Google OAuth, resolves the target tasklist by name, and inserts each hold as a task (`add_hold_to_google`).
- [kcls_hold_todoist.py](kcls_hold_todoist.py) — Todoist integration. Authenticates with a static API token (no OAuth flow), resolves the target project by name, and inserts each hold as a task (`add_hold_to_todoist`).

`kcls_hold_parser.py` calls `add_hold_to_google()` or `add_hold_to_todoist()` once per hold it finds, depending on the `TASK_MANAGER` env var — never both. Both functions share the same parameter names (`book_title`, `author`, `location`, `account_user`, `deadline_datetime`) so they're interchangeable at the call site. There's no other entrypoint or module.

## Local config/secrets (all gitignored, none checked in)

| File | Contents |
|---|---|
| `.env` | `EMAIL_USER` / `EMAIL_PASS` — the monitored Gmail address and a Gmail App Password, used for IMAP login. Also `TASK_MANAGER` (`google` or `todoist`, defaults to `google`) and `TODOIST_API_TOKEN` (only needed when `TASK_MANAGER=todoist`) |
| `credentials.json` | Google Cloud OAuth 2.0 client secret (downloaded from Google Cloud Console) |
| `token.json` | Cached OAuth token, auto-generated on first run by `authenticate_google_tasks()` |
| `users.json` | Flat map of library account number (string) → first name, used to attribute a hold to a family member |
| `library_bot_log.txt` | Runtime log output when run headless via cron/Task Scheduler |

There's no `.env.example` or template for any of these. If one gets added later, keep real secrets out of it.

## Running it

Dependencies are pinned in [requirements.txt](requirements.txt) (generated via `pip freeze`) and installed into a `.venv` (gitignored, not committed) — see the setup steps in [README.md](README.md). No `pyproject.toml`. When the interpreter "can't find" an installed package, it's almost always that the terminal/editor is pointed at the wrong Python (system vs. `.venv`), not a genuinely missing install — check `which python` / the editor's selected interpreter before reinstalling anything.

First run needs a human at a browser: `authenticate_google_tasks()` in `kcls_hold_gtasks.py` opens a login page to create `token.json`. After that it can run unattended (that's the point — it's designed for cron/Task Scheduler).

## Current parsing behavior & known limitations

This matters most for the planned expansion to new email types:

- The IMAP search in `kcls_hold_parser.py` is hardcoded to `FROM "noreply@kcls.org"`, `UNSEEN`, `OR (SUBJECT "hold is available") (SUBJECT "holds are available")` — that subject filter is effectively "the one email type this script currently knows about." IMAP `SUBJECT` search is a substring match, so this phrasing was deliberately chosen to exclude "Your hold has expired..." emails, which also contain "Your Hold" as a substring.
- All unread matching emails are processed per run (oldest first), not just the latest one — same for the checkout-receipt search further down the file.
- The body parser is a line-by-line state machine looking for a fixed label set specific to the "hold ready" layout: `Title`, `Account:`, `Author`, `Pickup Location`, `Pickup by`. Other KCLS email types will have different labels/layouts and won't parse correctly through this same scanner as-is.
- When extending to new email types, the natural approach is a per-type subject search plus a per-type label set/dispatch, rather than trying to generalize the existing scanner in place — the current one is intentionally simple and tied to one layout.

## Conventions to preserve

- Plain `print()` statements for status/progress — no logging framework.
- Explanatory inline comments aimed at someone learning Python (comments explain *why*/*what a line does*, not just restate the code).
- Minimal error handling — only the startup IMAP retry loop; no broad try/except elsewhere.
- snake_case functions and variables; hold data passed around as plain dicts, not classes.
- Google Tasks calls use explicit keyword arguments (see `add_hold_to_google(book_title=..., author=..., ...)`).

## Working with the user

They're a Python beginner. When making non-trivial suggestions, briefly explain *why*, prefer incremental and readable changes over "idiomatic but opaque" ones, and flag the tradeoff before introducing new dependencies or frameworks (logging libraries, ORMs, test frameworks, etc.) rather than adding them silently.
