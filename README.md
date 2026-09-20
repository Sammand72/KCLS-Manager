# KCLS Library Hold Automator

A headless Python automation script that silently monitors an email inbox for King County Library System (KCLS) "Hold is Ready" emails. It parses the email for book titles, authors, pickup locations, and deadlines, and automatically pushes them to a specific Google Tasks list.

It also watches for KCLS "Checkout Receipt" emails. Each checked-out book gets a `KCLS book due:` task with its author, due date, and library account user. When a receipt comes in, the script also matches each checked-out book against pending hold tasks (by title) and marks matching holds complete.

## Features

* Uses IMAP to find all unread automated library emails matching the relevant subject, not just the latest one.
* Handles multiple books in a single email and calculates days remaining until the deadline.
* Maps different library card numbers to specific family members.
* Creates due-date tasks for all books on a checkout receipt, including books that were not holds
* Auto-completes hold tasks when the corresponding book shows up on a checkout receipt (THIS REQUIRES YOU TO CLICK ON 'EMAIL RECIEPT' WHEN CHECKING BOOKS OUT)
* Matches checkout receipt names to the names in `users.json`
* Designed to run silently on startup via Windows Task Scheduler/Linux Cron with a log routed.

## Dependencies

You will need Python 3 installed. It's recommended to use a virtual environment so these packages stay separate from your system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Remember to select the `.venv` interpreter in your editor too (in VS Code: Ctrl+Shift+P -> "Python: Select Interpreter"), otherwise it won't recognize the installed packages even though they're there.

### Running the tests

The parser tests do not connect to Gmail or either task manager:

```bash
.venv/bin/python -m unittest discover -s tests
```

Note: On linux instead of pip you may use apt to install the underlying libraries system-wide, but that won't work inside a venv.

### Other files

* **credentials.json**- contains Google Cloud OAuth 2.0 Client ID
* **users.json**- contains account numbers mapped to user
* **.env**- contains email id and app password for the email being monitored, plus:
  * **TASK_MANAGER**- set to `google` or `todoist` to choose which task manager holds get pushed to (defaults to `google` if unset)
  * **TODOIST_API_TOKEN**- your Todoist personal API token (only needed if TASK_MANAGER=todoist)
* (**token.json** is created automatically)
Add all 4 of these to the .gitignore

## Task managers

This script can push holds to either Google Tasks or Todoist, but never both at once - set `TASK_MANAGER` in `.env` to pick one.

### Google Tasks

The script needs to generate the token.json so the first run has to be manual. It will open a browser window with a login page. The name of the tasklist I've set it to is "KCLS Library Holds". It can be changed to anything inside add_hold_to_google in kcls_hold_gtasks.py.

### Todoist

Get your API token from the Todoist app under Settings -> Integrations -> Developer, and put it in `.env` as `TODOIST_API_TOKEN`. No browser login step is needed. The name of the project I've set it to is "KCLS Stuff". It can be changed to anything inside add_hold_to_todoist in kcls_hold_todoist.py. If no project with that name is found, the hold is pushed to your Todoist Inbox instead.

## Miscellaneous

### Current Limitations and future plans

* Checkout due dates are read when the receipt is processed, but the tasks are not updated yet when KCLS renews a loan.
* "Your hold has expired" emails are excluded from the hold search (so they're left unread, not misparsed) but aren't parsed/handled yet - that's still a future update.
* The email parser is organized into small functions, but it still processes one email at a time and uses the existing task-manager APIs.

Code partly made with Google Gemini 3.1 Pro and Claude Code
