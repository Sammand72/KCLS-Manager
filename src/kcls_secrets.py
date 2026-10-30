# Reads and saves the script's secrets (Todoist token, Gmail App Password,
# Google sign-in) either from plain files or from the OS keychain.
#
# SECRET_STORAGE in .env picks where they live:
#   file     = .env and token.json, like before (the default)
#   keychain = Windows Credential Manager, macOS Keychain, or the Linux
#              Secret Service (GNOME Keyring / KWallet)
#
# The keyring library talks to whichever keychain the OS has, so this file
# doesn't need any Windows/macOS/Linux specific code.
#
# Run this file directly to put secrets into the keychain:
#   python kcls_secrets.py set todoist-api
#   python kcls_secrets.py status

import argparse
import getpass
import logging
import os
import time

import keyring
from dotenv import load_dotenv
from keyring.errors import PasswordDeleteError

# This file lives in src/, but settings and data files (.env, token.json,
# users.json, logs, ...) stay in the main project folder one level up.
# dirname() twice: src/kcls_x.py -> src/ -> project folder.
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every keychain entry is saved under this "service" name, so they are easy
# to find (and tell apart from other apps) in the OS's password manager.
SERVICE_NAME = 'KCLS-manager'

# The secrets this script knows about. In file mode the first three are read
# from .env; GOOGLE_TOKEN is the keychain copy of token.json.
SECRET_NAMES = [
    'TODOIST_API_TOKEN',
    'EMAIL_USER',
    'EMAIL_PASS',
    'GOOGLE_TOKEN',
]

# Short names for the command line, so nobody has to type TODOIST_API_TOKEN.
CLI_NAMES = {
    'todoist-api': 'TODOIST_API_TOKEN',
    'email-user': 'EMAIL_USER',
    'email-pass': 'EMAIL_PASS',
    'google-token': 'GOOGLE_TOKEN',
}

VALID_SECRET_STORAGE = ['file', 'keychain']

TRACER_LOGGER = logging.getLogger("kcls.tracer")


def get_secret_storage():
    """Read SECRET_STORAGE from the environment; blank means 'file'."""
    return os.getenv('SECRET_STORAGE', '').strip().lower() or 'file'


def validate_secret_storage(secret_storage):
    """Stop with a clear message if SECRET_STORAGE has a typo."""
    if secret_storage not in VALID_SECRET_STORAGE:
        TRACER_LOGGER.error(
            "Invalid SECRET_STORAGE specified in .env: %s "
            "(use 'file' or 'keychain')", secret_storage)
        raise ValueError(f"Invalid secret storage: {secret_storage}")


def raise_keychain_error(error):
    """Explain the usual fixes when the OS keychain can't be reached."""
    raise RuntimeError(
        "Could not reach the OS keychain. Make sure you are logged in and "
        "the keychain is unlocked (on Linux, cron jobs started before you "
        "log in usually can't reach it), or set SECRET_STORAGE=file in .env."
    ) from error


def look_for_keychain_again():
    """Make keyring forget its earlier search and look for a keychain again.

    keyring searches for a usable keychain only once per run and then
    remembers the answer. If none was running at that moment (for example
    before you logged in), it remembers a "fail" placeholder for good.
    """
    try:
        # get_all_keyring() remembers its list of usable keychains (it is
        # wrapped in jaraco's @once). reset() clears that saved list.
        keyring.backend.get_all_keyring.reset()
    except (AttributeError, KeyError):
        # Nothing was saved yet, or a future keyring version works
        # differently. Either way there is nothing to clear.
        pass
    # Search again and switch to whatever keychain is usable now.
    keyring.core.init_backend()


def wait_for_keychain(max_attempts=20, wait_seconds=30):
    """Wait until the OS keychain can be reached, retrying for a while.

    Right after the computer starts, a scheduled run can begin before you
    have logged in, and the keychain only becomes available after login.
    With the defaults this waits up to about 10 minutes (20 tries, 30
    seconds apart) before giving up.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            # A harmless lookup: it fails if the keychain isn't reachable
            # yet, and it doesn't matter whether anything is saved under it.
            keyring.get_password(SERVICE_NAME, 'EMAIL_USER')
            if attempt > 1:
                TRACER_LOGGER.info("The OS keychain is available now.")
            return
        except Exception as error:  # noqa: BLE001
            if attempt == max_attempts:
                raise_keychain_error(error)
            TRACER_LOGGER.warning(
                "The OS keychain isn't available yet (attempt %s/%s). "
                "Waiting %s seconds...",
                attempt,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
            look_for_keychain_again()


def get_secret(name):
    """Return one secret, or '' if it isn't saved anywhere.

    In file mode this is just the value from .env. In keychain mode it asks
    the OS keychain.
    """
    if get_secret_storage() != 'keychain':
        return os.getenv(name, '')

    try:
        # get_password() returns None when there is no entry with this name.
        return keyring.get_password(SERVICE_NAME, name) or ''
    except Exception as error:  # noqa: BLE001
        # Each OS (and keyring backend) raises its own kinds of errors when
        # the keychain is locked or missing, so we catch them all here.
        raise_keychain_error(error)


def set_secret(name, value):
    """Save one secret in the OS keychain (replacing any older value)."""
    try:
        keyring.set_password(SERVICE_NAME, name, value)
    except Exception as error:  # noqa: BLE001
        raise_keychain_error(error)


def delete_secret(name):
    """Remove one secret from the OS keychain.

    Returns False if there was nothing saved under that name.
    """
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except PasswordDeleteError:
        # Nothing was saved under this name, which is fine.
        return False
    except Exception as error:  # noqa: BLE001
        raise_keychain_error(error)
    return True


def print_status():
    """Show which secrets are in the keychain, without showing their values."""
    print(f"SECRET_STORAGE in .env: {get_secret_storage()}")
    for cli_name, secret_name in CLI_NAMES.items():
        try:
            saved = keyring.get_password(SERVICE_NAME, secret_name)
        except Exception as error:  # noqa: BLE001
            raise_keychain_error(error)
        state = 'saved' if saved else 'not saved'
        print(f"  {cli_name} ({secret_name}): {state}")


def ask_for_value(cli_name):
    """Prompt for a secret's value on the command line."""
    if cli_name == 'email-user':
        # An email address isn't secret, so it's fine to show it while typing.
        return input("Gmail address: ").strip()
    # getpass hides what you type, so the secret doesn't show on screen.
    return getpass.getpass(f"Value for {cli_name} (hidden): ").strip()


def build_argument_parser():
    """Describe the commands this file accepts on the command line."""
    parser = argparse.ArgumentParser(
        description="Save KCLS-manager secrets in the OS keychain.")
    commands = parser.add_subparsers(dest='command', required=True)

    # google-token is left out of "set" on purpose: the Google sign-in
    # window saves it for you.
    set_command = commands.add_parser(
        'set', help="save a secret (you are asked for the value)")
    set_command.add_argument(
        'name', choices=['todoist-api', 'email-user', 'email-pass'])

    delete_command = commands.add_parser(
        'delete', help="remove a saved secret")
    delete_command.add_argument('name', choices=list(CLI_NAMES))

    commands.add_parser(
        'status', help="list which secrets are saved (values stay hidden)")
    return parser


def main(argv=None):
    """Run the set / delete / status commands."""
    # Load .env only so "status" can say which SECRET_STORAGE is selected.
    load_dotenv(dotenv_path=os.path.join(project_dir, '.env'))
    args = build_argument_parser().parse_args(argv)

    if args.command == 'status':
        print_status()
        return

    secret_name = CLI_NAMES[args.name]

    if args.command == 'set':
        value = ask_for_value(args.name)
        if not value:
            print("Nothing entered, so nothing was saved.")
            return
        set_secret(secret_name, value)
        print(f"Saved {args.name} in the OS keychain.")
    elif args.command == 'delete':
        if delete_secret(secret_name):
            print(f"Removed {args.name} from the OS keychain.")
        else:
            print(f"{args.name} was not saved, so there was nothing to remove.")

    if get_secret_storage() != 'keychain':
        print("Note: set SECRET_STORAGE=keychain in .env so the script "
              "reads secrets from the keychain.")


if __name__ == '__main__':
    main()
