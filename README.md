# KCLS Library Manager

[![Tests](https://github.com/Sammand72/KCLS-Manager/actions/workflows/tests.yml/badge.svg)](https://github.com/Sammand72/KCLS-Manager/actions/workflows/tests.yml)

A headless Python automation script that silently monitors an email inbox for King County Library System (KCLS) emails. It parses the email for book titles, authors, pickup locations, and deadlines, and automatically pushes them to a Task Manager.

> [!NOTE]
> This script requires you to enable email notifications. When checking out/in a book, email receipt has to be enabled for the auto-completion of tasks to work.

## Features

* Uses IMAP to find all unread automated library emails matching the relevant subject, not just the latest one.
* Handles multiple books in a single email and calculates days remaining until the deadline.
* Maps different library card numbers to specific users.
* Adds the pickup library's street address to hold-task notes and descriptions.
* Creates due-date tasks for all books on a checkout receipt, including books that were not holds
* Auto-completes hold tasks when the corresponding book shows up on a checkout receipt
* Auto-updates due tasks when renewal emails show up, and completes the task when there is a check-in email
* Attributes every email to a family member by the address it was sent to, so several family members can auto-forward their KCLS emails into one inbox
* Designed to run on startup via Windows Task Scheduler/Linux Cron with separate library records and operational logs.

## Logs

The script uses Python's built-in `logging` module, so no extra logging package is required.

* `library_records.jsonl` stores one JSON object per hold or checkout. JSON Lines means each line is a complete record that can be searched or processed by another program.
* `library_tracer.log` stores operational details such as connection attempts, task-manager activity, parsing warnings, and emails marked as read.

Both files are appended to and are created beside the script. Operational messages also appear in the console while the script runs.

Hold completion normalizes titles by lowercasing, removing punctuation (including angle brackets), and collapsing whitespace. Titles must match exactly, or the normalized checkout title must start with a normalized hold title that is at least 10 characters long. This handles KCLS hold emails that cut off subtitles without allowing short titles such as `It` to match `Little Women` or `Dune` to match `Dune Messiah`. Empty titles are skipped. When the checkout user is known, the task's stored account user must match as well.

## Dependencies

This project uses [uv](https://docs.astral.sh/uv/) to manage Python and its packages. Install uv first ([other install options](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                        # Linux / macOS
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
```

Then, from the project folder:

```bash
uv sync
```

That one command creates a `.venv` virtual environment (downloading Python 3.12 if you don't have it) and installs the exact package versions recorded in `uv.lock`. Run it again whenever `pyproject.toml` or `uv.lock` changes.

To run anything inside that environment, put `uv run` in front of it (no need to activate the venv):

```bash
uv run src/kcls_parser.py
```

Remember to select the `.venv` interpreter in your editor too (in VS Code: Ctrl+Shift+P -> "Python: Select Interpreter"), otherwise it won't recognize the installed packages even though they're there.

### Adding or upgrading packages

* `uv add some-package` adds a package to `pyproject.toml` and `uv.lock` and installs it.
* `uv remove some-package` takes it out again.
* `uv lock --upgrade` moves every package to its newest allowed version (then run the tests).

Commit `pyproject.toml` and `uv.lock` together. Don't use `pip install` inside the `.venv`, since uv won't know about it and the next `uv sync` removes it.

### Running the tests

The parser tests do not connect to Gmail or either task manager:

```bash
uv run python -m unittest discover -s tests
uv run ruff check .
```

### Other files

* **credentials.json**- the Google OAuth client used by the sign-in window. It is not in the repository, so either ask the project owner to send it to you privately, or [make your own](#getting-your-own-credentialsjson). Put it next to the script. Not needed if you use `EMAIL_LOGIN_TYPE=legacy` with Todoist.
* **users.json**- optional; copy [users.example.json](users.example.json) to
  `users.json` and fill in multiple email receivers' details. Each KCLS email is attributed
  to the address it was originally sent to (its `To:` header):
  * Map each email address to a name. A blank name (`""`) means the email
    address itself is used as the label.
  * Library card numbers are only needed when several receivers share one
    email address. A matching card number (holds and renewals) or "Dear NAME,"
    greeting (checkout receipts) overrides the shared address.
  * If the file is missing or invalid, emails are labelled by their recipient
    address, or as unknown users.

  To monitor several people from one inbox, set up **automatic** forwarding
  (a Gmail forwarding address or filter) from each person's inbox. A manual
  "Forward" changes the sender, so the parser's `noreply@kcls.org` searches
  won't find it.
* **.env**- settings for the script:
  * **SECRET_STORAGE**- `file` (or blank) keeps secrets in `.env` and `token.json`. `keychain` keeps them in your OS keychain instead; see [Storing secrets in the OS keychain](#storing-secrets-in-the-os-keychain).
  * **EMAIL_LOGIN_TYPE**- `default` (or blank) opens a Google sign-in window on the first run. `legacy` logs in with `EMAIL_USER` and `EMAIL_PASS` (a Gmail App Password) instead.
  * **EMAIL_USER** / **EMAIL_PASS**- the monitored Gmail address and its App Password (only needed if `EMAIL_LOGIN_TYPE=legacy`)
  * **TASK_MANAGER**- set to `google` or `todoist` to choose which task manager holds get pushed to (defaults to `google` if unset)
  * **TODOIST_API_TOKEN**- your Todoist personal API token (only needed if `TASK_MANAGER=todoist`)
  * **GOOGLE_TASKLIST_NAME**- The name of the Google Tasks tasklist to push tasks to. If blank or unset, tasks go to Google's default tasklist.
  * **TODOIST_PROJECT_NAME**- The name of the Todoist Project to push tasks to. If blank or unset, tasks go to Todoist's Inbox.
* (**token.json** is created automatically after you sign in. It is personal, so never share or commit it. With `SECRET_STORAGE=keychain` it is kept in the OS keychain instead.)
* **library_locations.json**- contains the library branch names and addresses used for hold tasks. Each entry has a `name` and an `address` field.
Add the credential and personal-data files to the `.gitignore`; keep the shared library location list available to the parser.

The branch name from the hold email remains in the task's `Location:` line. When a matching address is found, it is added as a separate `Address:` line in Google Tasks notes and Todoist descriptions. Branch names are matched case-insensitively with extra whitespace ignored. If a branch is not listed, the task is still created with its original library name and no address line.

## Task managers

This script can push holds to either Google Tasks or Todoist, but never both at once - set `TASK_MANAGER` in `.env` to pick one. Set `GOOGLE_TASKLIST_NAME` or `TODOIST_PROJECT_NAME` in `.env` to choose a destination. If the selected name is blank or cannot be found, tasks go to the manager's default destination.

### Google sign-in

Run the script manually the first time. A browser window opens: sign in to the Gmail account you want monitored and click **Allow**. With `TASK_MANAGER=google`, that same sign-in also covers Google Tasks. The sign-in is saved in `token.json`, so later runs need no browser.

Google shows a "Google hasn't verified this app" screen, because reading Gmail is a restricted permission and this is a personal project. Click **Advanced → Go to KCLS-manager** to continue.

If you upgraded from an older version, the script asks you to sign in again once, to add the Gmail permission. To keep using an App Password for Gmail instead, set `EMAIL_LOGIN_TYPE=legacy`; Google Tasks then asks for its own sign-in.

### Getting your own credentials.json

If you can't get `credentials.json` from the project owner, you can make your own for free. This takes about 10 minutes and is done once.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/), sign in, and create a new project (any name, e.g. `KCLS-manager`).
2. Open **APIs & Services → Library** and enable:
   * **Gmail API** (needed for the sign-in login, `EMAIL_LOGIN_TYPE=default`)
   * **Google Tasks API** (needed if `TASK_MANAGER=google`)
3. Open **Google Auth Platform** (search for "OAuth consent screen" if you can't find it) and click **Get started**:
   * **App information:** any app name, and your email as the user support email.
   * **Audience:** choose **External**.
   * **Contact information:** your email.
4. Under **Data access**, click **Add or remove scopes** and add these two (paste them into "Manually add scopes"):
   * `https://mail.google.com/`
   * `https://www.googleapis.com/auth/tasks`
5. Under **Audience**, click **Publish app** so the status is **In production**. In "Testing" status, the sign-in expires every 7 days and scheduled runs start failing.
6. Under **Clients**, click **Create client**, choose application type **Desktop app**, and create it.
7. Click the download button for the new client, rename the downloaded file to `credentials.json`, and put it in the main project folder (next to `pyproject.toml`, not inside `src/`).

Never commit or share `credentials.json` publicly. It is already in `.gitignore`.

When you sign in for the first time, the warning screen shows your own app name. If the sign-in page says "Something went wrong" after you click **Continue**/**Go to ...**, try again in a private/incognito window.

### Google Tasks

No extra API setup is needed; Google Tasks uses the sign-in described above. If a saved token expires and cannot be refreshed, run the script manually again before the next cron or Task Scheduler run. Unattended runs fail with a clear log message instead of trying to open or wait for a browser.

### Todoist

Get your API token from the Todoist app under Settings -> Integrations -> Developer, and put it in `.env` as `TODOIST_API_TOKEN` (or in the OS keychain, see below). No browser login step is needed.

## Storing secrets in the OS keychain

Instead of keeping your Todoist token, Gmail App Password, and Google sign-in in plain files, you can store them in your operating system's keychain: Windows Credential Manager, the macOS Keychain, or the Linux Secret Service (GNOME Keyring / KWallet).

1. Set `SECRET_STORAGE=keychain` in `.env`.
2. Save the secrets you use with the helper script (values are typed in hidden):

   ```bash
   python src/kcls_secrets.py set todoist-api      # TODOIST_API_TOKEN
   python src/kcls_secrets.py set email-user   # only for EMAIL_LOGIN_TYPE=legacy
   python src/kcls_secrets.py set email-pass   # only for EMAIL_LOGIN_TYPE=legacy
   python src/kcls_secrets.py status           # shows what is saved, never the values
   ```

3. Remove `EMAIL_USER`, `EMAIL_PASS` and `TODOIST_API_TOKEN` from `.env`.

The Google sign-in is handled for you. On the next run, an existing `token.json` is moved into the keychain and the file is deleted; new sign-ins are saved straight to the keychain. To force a fresh Google sign-in, run `python src/kcls_secrets.py delete google-token`. Everything is stored under the name `KCLS-manager`, so you can also find or remove the entries in your OS's password manager.

**Scheduled runs need an unlocked keychain.** The keychain usually only becomes available once you log in, so a scheduled run that starts first waits for it, checking every 30 seconds for up to about 10 minutes. If you still haven't logged in by then, the run stops with a "Could not reach the OS keychain" log message. (A run you start by hand doesn't wait.)

* **Windows:** run the Task Scheduler task as your own user.
* **macOS:** works while you are logged in.
* **Linux:** cron jobs don't know where your desktop session is, so add `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` before the Python command in your crontab line (replace `1000` with the number `id -u` prints):

  ```bash
  @reboot sleep 60 && DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus /path/to/KCLS-manager/.venv/bin/python /path/to/KCLS-manager/src/kcls_parser.py >/dev/null 2>>/path/to/KCLS-manager/library_crash.log
  ```

  On a headless Linux machine with nobody logging in, keep `SECRET_STORAGE=file`.

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
/c "C:\route\to\KCLS-manager\.venv\Scripts\python.exe C:\route\to\KCLS-manager\src\kcls_parser.py > NUL 2>> C:\route\to\KCLS-manager\library_crash.log"

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
@reboot sleep 60 && /path/to/KCLS-manager/.venv/bin/python /path/to/KCLS-manager/src/kcls_parser.py >/dev/null 2>>/path/to/KCLS-manager/library_crash.log
```

This example runs the script 1 minute after every startup or reboot. Any regular output is discarded and errors (`2`) are output into `library_crash.log`.

The crontab config can be listed with:

```bash
crontab -l
```

## Miscellaneous

### Current Limitations and future plans

* All email types are supported but a cancelled hold will not send an email, so the task will have to be manually removed. OVERDUE tasks arent changed.
* Direct calendar events arent supported yet.
* Current IMAP connection is limited to Gmail (Google sign-in, or an App Password in legacy mode).
* Limited to 2 task managers, with no simultaneous tasks
* The email parser is organized into small functions, but it still processes one email at a time and uses the existing task-manager APIs.
* Web scraper version is planned

Code was partly made with Google Gemini 3.1 Pro and Claude Code (AGENTS.md/CLAUDE.md was stale till now).
