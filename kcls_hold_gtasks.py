# Python3 code to take the hold details from kcls_parser.py into Google Tasks API

import os
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import json
from kcls_task_matching import titles_match, users_match


script_dir = os.path.dirname(os.path.abspath(__file__))

# permission we are asking Google for
SCOPES = ['https://www.googleapis.com/auth/tasks']

# Every task has this in front of the book title, so it has to be removed before comparing with checkout email
TASK_TITLE_PREFIX = "KCLS book pickup: "
DUE_TASK_TITLE_PREFIX = "KCLS book due: "
TRACER_LOGGER = logging.getLogger("kcls.tracer")


def authenticate_google_tasks():
    creds = None
    token_path = os.path.join(script_dir, 'token.json')
    credentials_path = os.path.join(script_dir, 'credentials.json')

    # check if we already logged in (saved in token.json)
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # if no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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
    # asking Google for all tasklists
    results = service.tasklists().list().execute()
    tasklists = results.get('items', [])

    # Looping through to find the matching name
    for tasklist in tasklists:
        if tasklist['title'] == target_name:
            return tasklist['id']  # We found the secret ID!

    TRACER_LOGGER.warning(
        f"Warning: Could not find a list named '{target_name}'. Using default.")
    return '@default'


def add_hold_to_google(
    book_title, author, location, account_user, deadline_datetime,
        service=None, target_list_id=None, address=""):
    if service is None:
        service = authenticate_google_tasks()

    # Build the data package (the Payload)
    notes = f"Author: {author}\nLocation: {location}"
    if address:
        notes += f"\nAddress: {address}"
    notes += f"\nAccount: {account_user}"
    task_payload = {
        'title': f"{TASK_TITLE_PREFIX}{book_title}",
        'notes': notes,
    }

    # If the email didn't list a pickup date, deadline_datetime is None - prevents crashing on None.isoformat()
    if deadline_datetime is not None:
        # Google Tasks requires dates to be formatted as an RFC3339 string.
        task_payload['due'] = deadline_datetime.isoformat()

    if target_list_id is None:
        target_list_id = get_tasklist_id(service, "KCLS Library Holds")
    TRACER_LOGGER.info("Chosen Tasklist ID: %s", target_list_id)

    # Push the package to KCLS Library Holds tasklist
    TRACER_LOGGER.info("Pushing '%s' to Google Tasks", book_title)
    result = service.tasks().insert(
        tasklist=target_list_id, body=task_payload).execute()
    TRACER_LOGGER.info("Google Tasks hold created successfully")


def add_due_book_to_google(
        book_title, author, account_user, due_datetime, service=None,
        target_list_id=None):
    if service is None:
        service = authenticate_google_tasks()

    task_payload = {
        'title': f"{DUE_TASK_TITLE_PREFIX}{book_title}",
        'notes': f"Author: {author}\nAccount: {account_user}",
    }

    if due_datetime is not None:
        task_payload['due'] = due_datetime.isoformat()

    if target_list_id is None:
        target_list_id = get_tasklist_id(service, "KCLS Library Holds")
    TRACER_LOGGER.info("Chosen Tasklist ID: %s", target_list_id)
    TRACER_LOGGER.info("Pushing '%s' due date to Google Tasks", book_title)
    service.tasks().insert(
        tasklist=target_list_id, body=task_payload).execute()
    TRACER_LOGGER.info("Google Tasks due-date task created successfully")


def mark_hold_complete_google(
        book_title, checkout_user="Unknown user", service=None,
        target_list_id=None):
    if service is None:
        service = authenticate_google_tasks()

    if target_list_id is None:
        target_list_id = get_tasklist_id(service, "KCLS Library Holds")

    # showCompleted=False so we only ever match against still-pending pickups
    results = service.tasks().list(
        tasklist=target_list_id, showCompleted=False).execute()
    tasks = results.get('items', [])

    for task in tasks:
        if not task['title'].lower().strip().startswith(
                TASK_TITLE_PREFIX.lower()):
            continue

        # removeprefix drops "KCLS book pickup: " off the front
        task_title = task['title'].lower().strip().removeprefix(
            TASK_TITLE_PREFIX.lower())

        if titles_match(book_title, task_title) and users_match(
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

    TRACER_LOGGER.info(
        f"\nNo matching Google Tasks hold found for '{book_title}' (probably wasn't on hold).")
