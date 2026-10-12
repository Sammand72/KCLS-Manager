# Shared "Sign in with Google" logic for Gmail (IMAP) and Google Tasks.
#
# One browser sign-in gives us a token that can cover both reading Gmail and
# writing Google Tasks, so the user never needs an App Password or their own
# Google Cloud project. The OAuth client in credentials.json either comes
# privately from the project owner or from the user's own Google Cloud project
# (see the README). It is gitignored so bots scanning GitHub can't reuse it. The personal part,
# token.json, stays on the user's machine and is gitignored too.

import json
import logging
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

script_dir = os.path.dirname(os.path.abspath(__file__))

# Permissions ("scopes") we can ask Google for.
# Gmail's IMAP server only accepts the full-mail scope.
GMAIL_SCOPE = 'https://mail.google.com/'
TASKS_SCOPE = 'https://www.googleapis.com/auth/tasks'

TRACER_LOGGER = logging.getLogger("kcls.tracer")


def get_client_file_path():
    """Path to the Google OAuth client file (credentials.json)."""
    return os.path.join(script_dir, 'credentials.json')


def get_token_file_path():
    """Path to this user's saved sign-in (created after the first login)."""
    return os.path.join(script_dir, 'token.json')


def is_interactive():
    """Return whether browser-based OAuth can be completed in this process."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def raise_non_interactive_auth_error(cause=None):
    """Explain how to complete Google sign-in when no browser is available."""
    message = (
        "Google sign-in needs an interactive browser login. "
        "Run the script manually once to create or refresh token.json before "
        "running it from cron or Task Scheduler."
    )
    if cause is not None:
        raise RuntimeError(message) from cause
    raise RuntimeError(message)


def build_scope_list(include_gmail, include_tasks):
    """List the permissions this run needs."""
    scopes = []
    if include_gmail:
        scopes.append(GMAIL_SCOPE)
    if include_tasks:
        scopes.append(TASKS_SCOPE)
    return scopes


def has_all_scopes(creds, needed_scopes):
    """Check whether saved credentials already cover every needed permission."""
    granted_scopes = set(creds.scopes or [])
    return set(needed_scopes).issubset(granted_scopes)


def save_credentials(creds, token_path):
    """Write the credentials to token.json so later runs skip the browser."""
    creds_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }

    with open(token_path, 'w') as token:
        json.dump(creds_data, token)


def get_google_credentials(include_gmail=True, include_tasks=False):
    """Load saved Google credentials, or open a browser window to sign in.

    include_gmail asks for permission to read Gmail over IMAP.
    include_tasks asks for permission to edit Google Tasks.
    """
    needed_scopes = build_scope_list(include_gmail, include_tasks)
    token_path = get_token_file_path()
    creds = None

    # check if we already logged in (saved in token.json).
    # No scopes are passed here, so creds.scopes are the ones actually saved.
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path)

        # An older token.json (for example one made before Gmail sign-in
        # existed) may be missing a permission we now need. Signing in again
        # is the only way to add it.
        if not has_all_scopes(creds, needed_scopes):
            TRACER_LOGGER.info(
                "Saved Google sign-in is missing a permission- please sign in again to grant it. "
                "Running in a private window/ incognito mode may fix unspecified errors. ")
            # Keep any permissions the old token already had, so switching
            # settings later doesn't force yet another sign-in.
            for scope in creds.scopes or []:
                if scope not in needed_scopes:
                    needed_scopes.append(scope)
            creds = None

    if creds and creds.valid:
        return creds

    # The access token only lasts about an hour; the refresh token lets us
    # get a new one without bothering the user.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as error:
            TRACER_LOGGER.exception("Could not refresh Google credentials.")
            if not is_interactive():
                raise_non_interactive_auth_error(error)
            creds = None
    elif not is_interactive():
        raise_non_interactive_auth_error()
    else:
        creds = None

    if creds is None:
        # opens browser to sign in and click "Allow"
        flow = InstalledAppFlow.from_client_secrets_file(
            get_client_file_path(), needed_scopes)
        creds = flow.run_local_server(port=0)

    save_credentials(creds, token_path)
    return creds


def get_google_email(creds):
    """Ask Gmail which address the user signed in with.

    IMAP's OAuth login needs the address, and this way the user doesn't have
    to type it into .env.
    """
    gmail = build('gmail', 'v1', credentials=creds)
    profile = gmail.users().getProfile(userId='me').execute()
    return profile['emailAddress']
