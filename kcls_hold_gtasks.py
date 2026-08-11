# Python3 code to take the hold details from kcls_hold_parser.py into Google Tasks API

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import json

# permission we are asking Google for
SCOPES = ['https://www.googleapis.com/auth/tasks']


def authenticate_google_tasks():
    creds = None
    # check if we already logged in (saved in token.json)
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # if no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # opens browser to click "Allow"
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
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
        with open('token.json', 'w') as token:
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

    print(
        f"Warning: Could not find a list named '{target_name}'. Using default.")
    return '@default'


def add_hold_to_google(book_title, author, location, account_user, deadline_datetime):
    # Connect to Google
    service = authenticate_google_tasks()

    # Google Tasks requires dates to be formatted as an RFC3339 string.
    formatted_date = deadline_datetime.isoformat()

    # Build the data package (the Payload)
    task_payload = {
        'title': f"KCLS book pickup: {book_title}",
        'notes': f"Author: {author}\nLocation: {location}\nAccount: {account_user}",
        'due': formatted_date
    }

    target_list_id = get_tasklist_id(service, "KCLS Library Holds")
    print("Chosen Tasklist ID: "+target_list_id)

    # Push the package to KCLS Library Holds tasklist
    print(f"\nPushing '{book_title}' to Google Tasks...")
    result = service.tasks().insert(
        tasklist=target_list_id, body=task_payload).execute()
    print("Success!")
