# Shared "Sign in with Google" logic for Gmail (IMAP) and Google Tasks.
#
# One browser sign-in gives us a token that can cover both reading Gmail and
# writing Google Tasks, so the user never needs an App Password or their own
# Google Cloud project. The OAuth client in credentials.json either comes
# privately from the project owner or from the user's own Google Cloud project
# (see the README). It is gitignored so bots scanning GitHub can't reuse it. The personal part,
# token.json, stays on the user's machine and is gitignored too. With
# SECRET_STORAGE=keychain it is kept in the OS keychain instead of a file.

import json
import logging
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from kcls_secrets import get_secret, get_secret_storage, set_secret

# This file lives in src/, but settings and data files (.env, token.json,
# users.json, logs, ...) stay in the main project folder one level up.
# dirname() twice: src/kcls_x.py -> src/ -> project folder.
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Permissions ("scopes") we can ask Google for.
# Gmail's IMAP server only accepts the full-mail scope.
GMAIL_SCOPE = 'https://mail.google.com/'
TASKS_SCOPE = 'https://www.googleapis.com/auth/tasks'

TRACER_LOGGER = logging.getLogger("kcls.tracer")


def get_client_file_path():
    """Path to the Google OAuth client file (credentials.json)."""
    return os.path.join(project_dir, 'credentials.json')


def get_token_file_path():
    """Path to this user's saved sign-in (created after the first login)."""
    return os.path.join(project_dir, 'token.json')


def is_interactive():
    """Return whether browser-based OAuth can be completed in this process."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def raise_non_interactive_auth_error(cause=None):
    """Explain how to complete Google sign-in when no browser is available."""
    message = (
        "Google sign-in needs an interactive browser login. "
        "Run the script manually once to create or refresh the saved sign-in "
        "(token.json, or the OS keychain with SECRET_STORAGE=keychain) before "
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
    """Save the credentials so later runs skip the browser.

    They go to token.json, or to the OS keychain with SECRET_STORAGE=keychain.
    """
    creds_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }

    if get_secret_storage() == 'keychain':
        # The keychain stores text, so turn the dict into a JSON string.
        set_secret('GOOGLE_TOKEN', json.dumps(creds_data))
        return

    with open(token_path, 'w') as token:
        json.dump(creds_data, token)


def move_token_file_to_keychain(token_path):
    """Copy an old token.json into the keychain, then delete the file.

    This runs once, the first time SECRET_STORAGE=keychain is used, so the
    user doesn't have to sign in again.
    """
    with open(token_path) as token_file:
        token_text = token_file.read()
    set_secret('GOOGLE_TOKEN', token_text)

    # Only delete the file once we know the keychain really kept a copy.
    if get_secret('GOOGLE_TOKEN') != token_text:
        TRACER_LOGGER.warning(
            "Could not confirm token.json was saved in the keychain, "
            "so the file was kept.")
        return token_text

    os.remove(token_path)
    TRACER_LOGGER.info("Moved token.json into the OS keychain.")
    return token_text


def load_saved_credentials(token_path):
    """Load the saved Google sign-in, or return None if there isn't one."""
    if get_secret_storage() != 'keychain':
        if os.path.exists(token_path):
            return Credentials.from_authorized_user_file(token_path)
        return None

    token_text = get_secret('GOOGLE_TOKEN')
    if not token_text and os.path.exists(token_path):
        token_text = move_token_file_to_keychain(token_path)
    if not token_text:
        return None
    # json.loads turns the saved text back into a dict.
    return Credentials.from_authorized_user_info(json.loads(token_text))


def get_google_credentials(include_gmail=True, include_tasks=False):
    """Load saved Google credentials, or open a browser window to sign in.

    include_gmail asks for permission to read Gmail over IMAP.
    include_tasks asks for permission to edit Google Tasks.
    """
    needed_scopes = build_scope_list(include_gmail, include_tasks)
    token_path = get_token_file_path()

    # check if we already logged in (saved in token.json or the keychain).
    # No scopes are passed here, so creds.scopes are the ones actually saved.
    creds = load_saved_credentials(token_path)

    # An older token.json (for example one made before Gmail sign-in
    # existed) may be missing a permission we now need. Signing in again
    # is the only way to add it.
    if creds is not None and not has_all_scopes(creds, needed_scopes):
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
