# Python3 code to take the hold details from kcls_parser.py into Google Tasks API

import json
import logging
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from kcls_task_matching import (
    DUE_TASK_TITLE_PREFIX,
    EXPIRED_TASK_TITLE_PREFIX,
    TASK_TITLE_PREFIX,
    build_due_notes,
    build_hold_notes,
    is_same_task,
    strip_task_prefix,
    titles_match,
    users_match,
)

script_dir = os.path.dirname(os.path.abspath(__file__))

# permission we are asking Google for
SCOPES = ['https://www.googleapis.com/auth/tasks']

TRACER_LOGGER = logging.getLogger("kcls.tracer")


def get_tasklist_name():
    """Get the Google Tasks tasklist name from the environment."""
    return os.getenv('GOOGLE_TASKLIST_NAME', '').strip()


def is_interactive():
    """Return whether browser-based OAuth can be completed in this process."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def raise_non_interactive_auth_error(cause=None):
    """Explain how to complete Google OAuth when no browser is available."""
    message = (
        "Google Tasks authentication needs an interactive browser login. "
        "Run the script manually once to create or refresh token.json before "
        "running it from cron or Task Scheduler."
    )
    if cause is not None:
        raise RuntimeError(message) from cause
    raise RuntimeError(message)


def authenticate_google_tasks():
    """Load saved Google credentials or complete the OAuth login flow."""
    creds = None
    token_path = os.path.join(script_dir, 'token.json')
    credentials_path = os.path.join(script_dir, 'credentials.json')

    # check if we already logged in (saved in token.json)
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # if no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as error:
                TRACER_LOGGER.exception(
                    "Could not refresh Google Tasks credentials.")
                if not is_interactive():
                    raise_non_interactive_auth_error(error)
                creds = None
        else:
            if not is_interactive():
                raise_non_interactive_auth_error()

        if creds is None:
            # opens browser to click "Allow"
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        creds_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }

        # Save the manually built dictionary to the file
        with open(token_path, 'w') as token:
            json.dump(creds_data, token)

    # build and return API
    return build('tasks', 'v1', credentials=creds)


def get_tasklist_id(service, target_name):
    """Find a Google Tasks list by name, or use Google's default list."""
    if not target_name:
        return '@default'

    # asking Google for all tasklists
    results = service.tasklists().list().execute()
    tasklists = results.get('items', [])

    # Looping through to find the matching name
    for tasklist in tasklists:
        if tasklist['title'] == target_name:
            return tasklist['id']  # We found the secret ID!

    TRACER_LOGGER.warning(
        "Warning: Could not find a list named '%s'. Using default.",
        target_name,
    )
    return '@default'


def resolve_google_target(service, target_list_id):
    """Fill in the Google service and tasklist ID when a caller didn't pass them.

    kcls_parser.py normally passes both (so we only log in once per run),
    but each task function can still be called on its own.
    """
    if service is None:
        service = authenticate_google_tasks()

    if target_list_id is None:
        target_list_id = get_tasklist_id(service, get_tasklist_name())

    return service, target_list_id


def iter_open_google_tasks(service, target_list_id):
    """Go through every open (not completed) task in the tasklist.

    Google returns at most 100 tasks per request, so we follow nextPageToken
    until there are no pages left. Because this function uses `yield`, it is
    a generator: a `for` loop over it gets one task at a time, and the next
    page is only requested once the loop has used up the current one.
    """
    page_token = None
    while True:
        list_request_args = {
            'tasklist': target_list_id,
            'showCompleted': False,
            'maxResults': 100,
        }
        if page_token:
            list_request_args['pageToken'] = page_token

        results = service.tasks().list(**list_request_args).execute()
        # `yield from` hands out each task in this page, one at a time.
        yield from results.get('items', [])

        page_token = results.get('nextPageToken')
        if not page_token:
            return


def find_open_task(
        service, target_list_id, title_prefix, book_title, account_user):
    """Return an open task already created for this book and user, or None.

    Used before inserting, so an email that is retried after a failure
    doesn't create a second copy of a task that was already added.
    """
    for task in iter_open_google_tasks(service, target_list_id):
        task_title = strip_task_prefix(task['title'], title_prefix)
        if task_title is None:
            continue

        if is_same_task(
                task_title=task_title,
                task_notes=task.get('notes', ''),
                book_title=book_title,
                account_user=account_user):
            return task

    return None


def add_hold_to_google(
    book_title, author, location, account_user, deadline_datetime,
        service=None, target_list_id=None, address=""):
    """Create a Google Tasks pickup task for a library hold."""
    service, target_list_id = resolve_google_target(service, target_list_id)

    # Skip the insert if this hold already has an open pickup task.
    existing_task = find_open_task(
        service=service,
        target_list_id=target_list_id,
        title_prefix=TASK_TITLE_PREFIX,
        book_title=book_title,
        account_user=account_user,
    )
    if existing_task is not None:
        TRACER_LOGGER.info(
            "Google Tasks hold for '%s' already exists; not adding it again",
            book_title,
        )
        return

    # Build the data package (the Payload)
    task_payload = {
        'title': f"{TASK_TITLE_PREFIX}{book_title}",
        'notes': build_hold_notes(
            author=author,
            location=location,
            address=address,
            account_user=account_user,
        ),
    }

    # If the email didn't list a pickup date, deadline_datetime is None - prevents crashing on None.isoformat()
    if deadline_datetime is not None:
        # Google Tasks requires dates to be formatted as an RFC3339 string.
        task_payload['due'] = deadline_datetime.isoformat()

    TRACER_LOGGER.info("Chosen Tasklist ID: %s", target_list_id)

    # Push the package to KCLS Library Holds tasklist
    TRACER_LOGGER.info("Pushing '%s' to Google Tasks", book_title)
    service.tasks().insert(
        tasklist=target_list_id, body=task_payload).execute()
    TRACER_LOGGER.info("Google Tasks hold created successfully")


def expire_hold_google(
        book_title, account_user, service=None, target_list_id=None):
    """Rename the matching normal pickup task instead of creating a task."""
    service, target_list_id = resolve_google_target(service, target_list_id)

    for task in iter_open_google_tasks(service, target_list_id):
        task_title = strip_task_prefix(task['title'], TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=book_title,
                hold_title=task_title) and users_match(
                account_user, task.get('notes', '')):
            expired_title = f"{EXPIRED_TASK_TITLE_PREFIX}{task_title}"
            service.tasks().patch(
                tasklist=target_list_id,
                task=task['id'],
                body={'title': expired_title},
            ).execute()
            TRACER_LOGGER.info(
                "Renamed '%s' to '%s' in Google Tasks",
                task['title'], expired_title)
            return


def add_due_book_to_google(
        book_title, author, account_user, due_datetime, service=None,
        target_list_id=None):
    """Create a Google Tasks task for a book's checkout due date."""
    service, target_list_id = resolve_google_target(service, target_list_id)

    # If this book already has an open due task (a retried email, or the
    # same book checked out again), just refresh its due date instead.
    existing_task = find_open_task(
        service=service,
        target_list_id=target_list_id,
        title_prefix=DUE_TASK_TITLE_PREFIX,
        book_title=book_title,
        account_user=account_user,
    )
    if existing_task is not None:
        if due_datetime is not None:
            service.tasks().patch(
                tasklist=target_list_id,
                task=existing_task['id'],
                body={'due': due_datetime.isoformat()},
            ).execute()
        TRACER_LOGGER.info(
            "Google Tasks due task for '%s' already exists; "
            "updated its due date instead of adding it again",
            book_title,
        )
        return

    task_payload = {
        'title': f"{DUE_TASK_TITLE_PREFIX}{book_title}",
        'notes': build_due_notes(author=author, account_user=account_user),
    }

    if due_datetime is not None:
        task_payload['due'] = due_datetime.isoformat()

    TRACER_LOGGER.info("Chosen Tasklist ID: %s", target_list_id)
    TRACER_LOGGER.info("Pushing '%s' due date to Google Tasks", book_title)
    service.tasks().insert(
        tasklist=target_list_id, body=task_payload).execute()
    TRACER_LOGGER.info("Google Tasks due-date task created successfully")


def update_due_book_google(
        book_title, account_user, due_datetime, service=None,
        target_list_id=None):
    """Update the matching open Google Tasks due-date task."""
    service, target_list_id = resolve_google_target(service, target_list_id)

    for task in iter_open_google_tasks(service, target_list_id):
        task_title = strip_task_prefix(task['title'], DUE_TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if not titles_match(
                checkout_title=task_title,
                hold_title=book_title):
            continue
        if account_user != "Unknown user" and not users_match(
                account_user, task.get('notes', '')):
            continue

        service.tasks().patch(
            tasklist=target_list_id,
            task=task['id'],
            body={'due': due_datetime.isoformat()},
        ).execute()
        TRACER_LOGGER.info(
            "Updated due date for '%s' in Google Tasks", task['title'])
        return

    TRACER_LOGGER.info(
        "No matching Google Tasks due task found to renew for '%s'",
        book_title,
    )


def mark_hold_complete_google(
        book_title, checkout_user="Unknown user", service=None,
        target_list_id=None):
    """Find a matching open pickup task and mark it complete."""
    service, target_list_id = resolve_google_target(service, target_list_id)

    # Only open tasks are listed, so we never re-complete an old pickup.
    for task in iter_open_google_tasks(service, target_list_id):
        task_title = strip_task_prefix(task['title'], TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=book_title,
                hold_title=task_title) and users_match(
                checkout_user, task.get('notes', '')):
            TRACER_LOGGER.info(
                "Marking '%s' complete in Google Tasks", task['title'])
            service.tasks().patch(
                tasklist=target_list_id,
                task=task['id'],
                body={'status': 'completed'}
            ).execute()
            TRACER_LOGGER.info(
                "Google Tasks hold marked complete successfully")
            return


def complete_due_book_google(
        book_title, account_user="Unknown user", service=None,
        target_list_id=None):
    """Find a matching open due-date task and mark it complete.

    A task for the same account user is preferred, but any task with a
    matching title is used as a fallback, because a shared email address
    can't always tell family members apart.
    """
    service, target_list_id = resolve_google_target(service, target_list_id)

    title_matches = []
    for task in iter_open_google_tasks(service, target_list_id):
        task_title = strip_task_prefix(task['title'], DUE_TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=task_title,
                hold_title=book_title):
            title_matches.append(task)

    # next() returns the first task for this user, or None if there isn't one.
    task_to_complete = next(
        (
            task for task in title_matches
            if users_match(account_user, task.get('notes', ''))
        ),
        None,
    )
    if task_to_complete is None and title_matches:
        task_to_complete = title_matches[0]

    if task_to_complete is not None:
        service.tasks().patch(
            tasklist=target_list_id,
            task=task_to_complete['id'],
            body={'status': 'completed'},
        ).execute()
        TRACER_LOGGER.info(
            "Marked returned book '%s' complete in Google Tasks",
            task_to_complete['title'])
        return

    TRACER_LOGGER.info(
        "No matching Google Tasks due task found for returned book '%s'",
        book_title,
    )
