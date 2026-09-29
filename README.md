# KCLS Library Hold Automator

A headless Python automation script that silently monitors an email inbox for King County Library System (KCLS) Hold and Checkout emails. It parses the email for book titles, authors, pickup locations, and deadlines, and automatically pushes them to a Task Manager.

> [!NOTE]
> This script requires you to enable email notifications. When checking out a book, email receipt has to be enabled for the auto-completion of tasks to work.

## Features

* Uses IMAP to find all unread automated library emails matching the relevant subject, not just the latest one.
* Handles multiple books in a single email and calculates days remaining until the deadline.
* Maps different library card numbers to specific family members.
* Adds the pickup library's street address to hold-task notes and descriptions.
* Creates due-date tasks for all books on a checkout receipt, including books that were not holds
* Auto-completes hold tasks when the corresponding book shows up on a checkout receipt
* Matches checkout receipt names to the names in `users.json`
* Designed to run on startup via Windows Task Scheduler/Linux Cron with separate library records and operational logs.

## Logs

The script uses Python's built-in `logging` module, so no extra logging package is required.

* `library_records.jsonl` stores one JSON object per hold or checkout. JSON Lines means each line is a complete record that can be searched or processed by another program.
* `library_tracer.log` stores operational details such as connection attempts, task-manager activity, parsing warnings, and emails marked as read.

Both files are appended to and are created beside the script. Operational messages also appear in the console while the script runs.

Hold completion normalizes titles by lowercasing, removing punctuation (including angle brackets), and collapsing whitespace. Titles must match exactly, or the normalized checkout title must start with a normalized hold title that is at least 10 characters long. This handles KCLS hold emails that cut off subtitles without allowing short titles such as `It` to match `Little Women` or `Dune` to match `Dune Messiah`. Empty titles are skipped. When the checkout user is known, the task's stored account user must match as well.

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
  * **TODOIST_API_TOKEN**- your Todoist personal API token (only needed if `TASK_MANAGER=todoist`)
  * **GOOGLE_TASKLIST_NAME**- The name of the Google Tasks tasklist to push tasks to. If blank or unset, tasks go to Google's default tasklist.
  * **TODOIST_PROJECT_NAME**- The name of the Todoist Project to push tasks to. If blank or unset, tasks go to Todoist's Inbox.
* (**token.json** is created automatically)
* **library_locations.json**- contains the library branch names and addresses used for hold tasks. Each entry has a `name` and an `address` field.
Add the credential and personal-data files to the `.gitignore`; keep the shared library location list available to the parser.

The branch name from the hold email remains in the task's `Location:` line. When a matching address is found, it is added as a separate `Address:` line in Google Tasks notes and Todoist descriptions. Branch names are matched case-insensitively with extra whitespace ignored. If a branch is not listed, the task is still created with its original library name and no address line.

## Task managers

This script can push holds to either Google Tasks or Todoist, but never both at once - set `TASK_MANAGER` in `.env` to pick one. Set `GOOGLE_TASKLIST_NAME` or `TODOIST_PROJECT_NAME` in `.env` to choose a destination. If the selected name is blank or cannot be found, tasks go to the manager's default destination.

### Google Tasks

The method to get the Google Tasks API is complex and will require a guide (You can find several on YouTube). The script needs to generate the token.json so the first run has to be manual. It will open a browser window with a login page.

### Todoist

Get your API token from the Todoist app under Settings -> Integrations -> Developer, and put it in `.env` as `TODOIST_API_TOKEN`. No browser login step is needed.

## Scheduling

The best way to use this script is to run it on a set schedule using your Operating System's task scheduler.The example schedule for both Windows and Linux runs the script 1 minute after every system startup.

### Windows Task Scheduler

Open Task Scheduler and click **Create Task** (do not select Basic Task).

**Triggers tab:** Click **New**, set "Begin the task" to **At startup**, and check **Delay task for:** `1 minute`.
(You can change this trigger to run daily, weekly, or on any custom schedule that fits your needs).
**General tab:** Check **Run whether user is logged on or not** to ensure it runs completely in the background on boot.
**Actions tab:** Click **New**, set "Action" to **Start a program**, and configure it as follows:

* **Program/script:** `cmd.exe`
* **Add arguments:**

```cmd
/c "C:\route\to\KCLS-manager\.venv\Scripts\python.exe C:\route\to\KCLS-manager\kcls_parser.py > NUL 2>> C:\route\to\KCLS-manager\library_crash.log"

```

*Note: In Windows, the virtual environment's Python executable is located in `.venv\Scripts\python.exe`. Standard output is discarded to `NUL`, and `cmd.exe /c` is strictly required to process the output redirection (`> NUL 2>>`).*

Press **OK**, type your password, then finish the setup with any other desired conditions.

### Linux Cron jobs

In your terminal, enter the crontab editing window using

```bash
crontab -e
```

This will open the crontab config file in your distro's text editor. The config contains a lot of info on crontab scheduling. At the bottom of the file, add your schedule. An example schedule is:

```bash
@reboot sleep 60 && /path/to/KCLS-manager/.venv/bin/python /path/to/KCLS-manager/kcls_parser.py >/dev/null 2>>/path/to/KCLS-manager/library_crash.log
```

This example runs the script 1 minute after every startup or reboot. Any regular output is discarded and errors (`2`) are output into `library_crash.log`.

The crontab config can be listed with:

```bash
crontab -l
```

## Miscellaneous

### Current Limitations and future plans

* Checkout due dates are read when the receipt is processed, but the tasks are not updated yet when KCLS renews a loan.
* "Your hold has expired" emails are not parsed yet
* Direct calendar events arent supported yet.
* Current IMAP connection is limited to Gmail, with the only current supported auth being the app password.
* Limited to 2 task managers, with no simultaneous tasks
* The email parser is organized into small functions, but it still processes one email at a time and uses the existing task-manager APIs.

Code partly made with Google Gemini 3.1 Pro and Claude Code
