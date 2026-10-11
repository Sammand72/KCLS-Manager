# Scan KCLS emails and add library holds to Google Tasks or Todoist.
import email
import imaplib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from email import policy

import dateparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from kcls_tasker_gtasks import (
    add_due_book_to_google,
    add_hold_to_google,
    authenticate_google_tasks,
    complete_due_book_google,
    expire_hold_google,
    get_tasklist_id,
    get_tasklist_name,
    mark_hold_complete_google,
    update_due_book_google,
)
from kcls_tasker_todoist import (
    add_due_book_to_todoist,
    add_hold_to_todoist,
    authenticate_todoist,
    complete_due_book_todoist,
    expire_hold_todoist,
    get_project_id,
    get_project_name,
    mark_hold_complete_todoist,
    update_due_book_todoist,
)

script_dir = os.path.dirname(os.path.abspath(__file__))


# These labels help tell an empty value apart from the next label.
KNOWN_LABELS = (
    "Title", "Account:", "Author", "Pickup Location", "Pickup by", "Due Date",
)
TRACER_LOGGER_NAME = "kcls.tracer"
RECORD_LOGGER_NAME = "kcls.records"


def configure_logging(log_directory):
    """Send operational messages and library records to separate outputs."""
    tracer_logger = logging.getLogger(TRACER_LOGGER_NAME)
    record_logger = logging.getLogger(RECORD_LOGGER_NAME)

    for logger in (tracer_logger, record_logger):
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    tracer_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    tracer_file_handler = logging.FileHandler(
        os.path.join(log_directory, "library_tracer.log"),
        encoding="utf-8",
    )
    tracer_file_handler.setFormatter(tracer_formatter)
    tracer_logger.addHandler(tracer_file_handler)

    if sys.stdout.isatty():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(tracer_formatter)
        tracer_logger.addHandler(console_handler)

    record_handler = logging.FileHandler(
        os.path.join(log_directory, "library_records.jsonl"),
        encoding="utf-8",
    )
    record_handler.setFormatter(logging.Formatter("%(message)s"))
    record_logger.addHandler(record_handler)


def close_logging():
    """Close the file and console handlers created by configure_logging."""
    for logger_name in (TRACER_LOGGER_NAME, RECORD_LOGGER_NAME):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()


def record_library_event(event_type, record):
    """Write one library event as one JSON object in the records file."""
    event = {
        "event_type": event_type,
        "processed_at": datetime.now().astimezone().isoformat(),
    }
    event.update(record)
    logging.getLogger(RECORD_LOGGER_NAME).info(
        json.dumps(event, default=lambda value: value.isoformat())
    )


def get_tracer_logger():
    """Return the logger used for operational messages."""
    return logging.getLogger(TRACER_LOGGER_NAME)


def normalize_location_name(location_name):
    """Normalize library names for matching email text to the location list."""
    return " ".join(location_name.lower().split())


def build_location_address_mapping(location_records):
    """Build a normalized library-name-to-address mapping."""
    return {
        normalize_location_name(record.get("name", "")): record.get(
            "address", "").strip()
        for record in location_records
        if record.get("name", "").strip()
    }


def load_location_addresses(file_path):
    """Load library addresses, continuing without them if the file is invalid."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return build_location_address_mapping(json.load(file))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        get_tracer_logger().warning(
            "Could not load library locations from %s: %s. "
            "Continuing without addresses.",
            file_path,
            error,
        )
        return {}


def load_user_mapping(file_path):
    """Load account users, continuing with unknown users if it is invalid."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            user_mapping = json.load(file)

        if not isinstance(user_mapping, dict):
            raise TypeError("expected a JSON object")

        return user_mapping
    except (OSError, ValueError, TypeError) as error:
        get_tracer_logger().warning(
            "Could not load user mapping from %s: %s. "
            "Continuing with unknown users.",
            file_path,
            error,
        )
        return {}


def get_location_address(location_name, location_addresses):
    """Return a branch address, or an empty string when it is not listed."""
    return location_addresses.get(normalize_location_name(location_name), "")


def value_after(lines, index):
    """Return the line after a label, unless the next line is another label."""
    if index + 1 < len(lines) and lines[index + 1] not in KNOWN_LABELS:
        return lines[index + 1]

    return ""


def html_to_text(raw_html):
    """Remove HTML tags and keep separate email elements on separate lines."""
    soup = BeautifulSoup(raw_html, 'html.parser')
    return soup.get_text(separator='\n', strip=True)


def get_message_text(message):
    """Get the HTML text when available, otherwise use the plain-text body."""
    raw_html = None
    raw_text = None

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html" and raw_html is None:
                raw_html = part.get_content()
            elif part.get_content_type() == "text/plain" and raw_text is None:
                raw_text = part.get_content()
    elif message.get_content_type() == "text/html":
        raw_html = message.get_content()
    elif message.get_content_type() == "text/plain":
        raw_text = message.get_content()

    if raw_html:
        return html_to_text(raw_html)

    return raw_text or ""


def fetch_message(mail, email_id):
    """Fetch complete email from IMAP Gmail and turn it into a message object."""
    # BODY.PEEK[] downloads the whole email without changing its flags.
    # Plain RFC822 (or BODY[]) would make Gmail mark it read right away,
    # so an email that fails halfway would never be retried.
    # We mark it read ourselves with mail.store() once it is fully handled.
    status, data = mail.fetch(email_id, '(BODY.PEEK[])')
    if status != 'OK':
        get_tracer_logger().error(
            "Could not fetch email %s from IMAP Gmail (status: %s)",
            email_id,
            status,
        )
        return None

    raw_email_bytes = data[0][1]
    return email.message_from_bytes(raw_email_bytes, policy=policy.default)


def parse_kcls_date(date_text):
    """Turn a KCLS date like "October 15, 2026" into a timezone-aware datetime.

    KCLS dates are in Seattle local time. Returns None if the text isn't a date.
    """
    return dateparser.parse(
        date_text,
        settings={
            'TIMEZONE': 'America/Los_Angeles',
            'RETURN_AS_TIMEZONE_AWARE': True,
        },
    )


def parse_hold_email(message, user_mapping):
    """Parse all ready-for-pickup holds from one email."""
    lines = get_message_text(message).splitlines()
    holds = []
    current_hold = {}
    account_number = ""
    user = "Unknown user"

    for index, current_line in enumerate(lines):
        if current_line == "Title":
            if "title" in current_hold:
                holds.append(current_hold)

            current_hold = {
                "title": value_after(lines, index),
                "account_number": account_number,
                "user": user,
                "author": "Unknown",
                "location": "",
                "deadline": None,
                "str_deadline": "",
            }

        elif current_line == "Account:":
            account_number = value_after(lines, index)
            user = user_mapping.get(account_number, "Unknown user")

        elif current_line == "Author" and "title" in current_hold:
            current_hold["author"] = value_after(lines, index) or "Unknown"

        elif current_line == "Pickup Location" and "title" in current_hold:
            current_hold["location"] = value_after(lines, index)

        elif current_line == "Pickup by" and "title" in current_hold:
            deadline_text = value_after(lines, index)
            current_hold["deadline"] = parse_kcls_date(deadline_text)
            current_hold["str_deadline"] = deadline_text

    if "title" in current_hold:
        holds.append(current_hold)

    return holds


def parse_checkout_receipt(message, user_mapping):
    """Parse checked-out books, due dates, and the user from one receipt."""
    lines = get_message_text(message).splitlines()
    checked_out_books = []
    current_book = {}
    user = "Unknown user"

    for current_line in lines:
        current_line = current_line.strip()
        greeting_match = re.match(
            r"Dear\s+(.+),$", current_line, re.IGNORECASE)
        if greeting_match:
            receipt_name = greeting_match.group(1).strip()
            user = next(
                (
                    mapped_user
                    for mapped_user in user_mapping.values()
                    if mapped_user.lower() == receipt_name.lower()
                ),
                "Unknown user",
            )
            break

    for current_line in lines:
        current_line = current_line.strip()
        if "Title:" in current_line:
            if "title" in current_book:
                checked_out_books.append(current_book)
                current_book = {}

            current_book = {
                "title": current_line.split("Title:", 1)[1].strip(),
                "author": "Unknown",
                "due_date": None,
                "user": user,
            }

        elif "Author:" in current_line:
            if "title" in current_book:
                current_book["author"] = current_line.split(
                    "Author:", 1)[1].strip() or "Unknown"

        elif "Due Date:" in current_line and "title" in current_book:
            due_date_text = current_line.split("Due Date:", 1)[1].strip()
            current_book["due_date"] = parse_kcls_date(due_date_text)

    if "title" in current_book:
        checked_out_books.append(current_book)

    return checked_out_books


def parse_due_date_email(message, user_mapping):
    """Parse renewed book titles, accounts, and due dates from one email."""
    lines = [line.strip() for line in get_message_text(message).splitlines()]
    renewed_books = []
    current_book = {}
    account_number = ""
    user = "Unknown user"

    for index, current_line in enumerate(lines):
        if current_line == "Title":
            if "title" in current_book:
                renewed_books.append(current_book)

            current_book = {
                "title": value_after(lines, index),
                "account_number": account_number,
                "user": user,
                "due_date": None,
            }
        elif current_line == "Account:":
            account_number = value_after(lines, index)
            user = user_mapping.get(account_number, "Unknown user")
        elif current_line in ("Due Date", "Date Due") and "title" in current_book:
            due_date_text = value_after(lines, index)
            current_book["due_date"] = parse_kcls_date(due_date_text)

    if "title" in current_book:
        renewed_books.append(current_book)

    return renewed_books


def parse_checkin_email(message):
    """Parse returned book titles from a plain-text KCLS check-in email."""
    lines = [line.strip() for line in get_message_text(message).splitlines()]
    try:
        checkin_index = next(
            index for index, line in enumerate(lines)
            if line.lower() == "checkin"
        )
    except StopIteration:
        return []

    books = []
    block_lines = []
    for line in lines[checkin_index + 1:]:
        if line.lower().startswith("number of checked in items"):
            break
        if re.fullmatch(r"-{3,}", line):
            if block_lines:
                barcode_index = next(
                    (
                        index for index, block_line in enumerate(block_lines)
                        if block_line.isdigit()
                    ),
                    None,
                )
                if barcode_index is not None:
                    title = " ".join(block_lines[:barcode_index]).strip()
                    if " / " in title:
                        title = title.split(" / ", 1)[0].rstrip(" .")
                    if title:
                        books.append(title)
                block_lines = []
            continue
        if line:
            block_lines.append(line)

    return books


def connect_to_gmail(max_retries=10, wait_seconds=15):
    """Connect to Gmail, retrying when the internet is not ready yet."""
    for attempt in range(max_retries):
        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            get_tracer_logger().info("Connected to the mail server.")
            return mail
        except Exception:  # noqa: BLE001
            get_tracer_logger().warning(
                "Mail server not ready yet (Attempt %s/%s). "
                "Waiting %s seconds...",
                attempt + 1,
                max_retries,
                wait_seconds,
            )
            time.sleep(wait_seconds)

    get_tracer_logger().error(
        "Could not connect to mail server after %s attempts. Exiting script.",
        max_retries,
    )
    raise SystemExit


def login_to_gmail(mail, username, password, max_attempts=3, wait_seconds=15):
    """Log in to Gmail, retrying temporary IMAP errors but not bad credentials."""
    for attempt in range(max_attempts):
        try:
            mail.login(username, password)
            get_tracer_logger().info("Logged in to the mail server.")
            return
        except imaplib.IMAP4.error as error:
            error_text = str(error).upper()
            authentication_failed = any(
                message in error_text
                for message in (
                    "AUTHENTICATIONFAILED",
                    "INVALID CREDENTIALS",
                    "LOGIN FAILED",
                )
            )
            if authentication_failed:
                get_tracer_logger().error(
                    "Could not authenticate with the mail server. "
                    "Check EMAIL_USER and EMAIL_PASS."
                )
                raise SystemExit

            if attempt + 1 == max_attempts:
                get_tracer_logger().error(
                    "Could not log in to mail server after %s attempts. "
                    "Exiting script.",
                    max_attempts,
                )
                raise SystemExit

            get_tracer_logger().warning(
                "Mail server login failed temporarily "
                "(Attempt %s/%s). Waiting %s seconds...",
                attempt + 1,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)


def search_unread_emails(mail, from_address, subject_query, email_kind):
    """Find unread emails from one sender whose subject matches the query.

    email_kind is only used in the log message, e.g. "hold" or "check-in".
    Returns a list of IMAP email IDs, or an empty list if the search failed.
    """
    status, messages = mail.search(
        None,
        f'(FROM "{from_address}")',
        'UNSEEN',
        subject_query,
    )
    if status != 'OK' or not messages:
        get_tracer_logger().error(
            "Could not search for unread %s emails in IMAP Gmail (status: %s)",
            email_kind,
            status,
        )
        return []

    # IMAP returns one string of space-separated IDs, like b"3 7 12".
    return messages[0].split()


def search_unread_holds(mail):
    """Find unread KCLS emails about ready or expired holds."""
    return search_unread_emails(
        mail,
        from_address="noreply@kcls.org",
        subject_query=(
            '(OR (OR (SUBJECT "Hold is Ready") (SUBJECT "Holds are Ready")) '
            '(OR (SUBJECT "Hold Has Expired") (SUBJECT "Holds have expired")))'
        ),
        email_kind="hold",
    )


def search_unread_receipts(mail):
    """Find unread KCLS checkout receipt emails."""
    return search_unread_emails(
        mail,
        from_address="noreply@kcls.org",
        subject_query='(SUBJECT "Checkout Receipt")',
        email_kind="checkout receipt",
    )


def search_unread_due_dates(mail):
    """Find unread KCLS emails containing renewed book due dates."""
    return search_unread_emails(
        mail,
        from_address="noreply@kcls.org",
        subject_query='(SUBJECT "Due Dates")',
        email_kind="renewal",
    )


def search_unread_checkins(mail):
    """Find unread KCLS emails about returned books."""
    return search_unread_emails(
        mail,
        from_address="No-Reply-AMH-Notify@kcls.org",
        subject_query='(OR (SUBJECT "Checkin") (SUBJECT "Items Returned Receipt"))',
        email_kind="check-in",
    )


def validate_task_manager(task_manager):
    """Reject task-manager names that this script does not support."""
    if task_manager not in ('todoist', 'google'):
        get_tracer_logger().error(
            "Invalid task manager specified in .env: %s", task_manager)
        raise ValueError(f"Invalid task manager: {task_manager}")


def create_task_manager_context(task_manager):
    """Authenticate once and resolve the destination used by this run."""
    validate_task_manager(task_manager)

    if task_manager == 'todoist':
        api = authenticate_todoist()
        return {
            'api': api,
            'target_project_id': get_project_id(api, get_project_name()),
        }

    service = authenticate_google_tasks()
    return {
        'service': service,
        'target_list_id': get_tasklist_id(service, get_tasklist_name()),
    }


def add_task_context(task_arguments, task_context):
    """Add the shared login/list details (if any) to a task call's arguments."""
    if task_context is not None:
        task_arguments.update(task_context)
    return task_arguments


def add_hold_to_task_manager(
        task_manager, hold, task_context=None, location_address=""):
    """Send one hold to the task manager selected in the environment."""
    validate_task_manager(task_manager)

    if hold.get('expired', False):
        expire_arguments = add_task_context({
            'book_title': hold['title'],
            'account_user': hold['user'],
        }, task_context)
        # Pick the right function first, then call it once with the arguments.
        expire_fn = (
            expire_hold_todoist if task_manager == 'todoist'
            else expire_hold_google
        )
        expire_fn(**expire_arguments)
        return

    task_arguments = add_task_context({
        'book_title': hold['title'],
        'author': hold['author'],
        'location': hold['location'],
        'account_user': hold['user'],
        'deadline_datetime': hold['deadline'],
        'address': location_address,
    }, task_context)
    add_fn = (
        add_hold_to_todoist if task_manager == 'todoist'
        else add_hold_to_google
    )
    add_fn(**task_arguments)


def complete_hold_in_task_manager(task_manager, book, task_context=None):
    """Mark one checked-out book complete in the selected task manager."""
    validate_task_manager(task_manager)

    task_arguments = add_task_context({
        'book_title': book['title'],
        'checkout_user': book['user'],
    }, task_context)
    complete_fn = (
        mark_hold_complete_todoist if task_manager == 'todoist'
        else mark_hold_complete_google
    )
    complete_fn(**task_arguments)


def add_due_book_to_task_manager(task_manager, book, task_context=None):
    """Add one checked-out book as a due-date task."""
    validate_task_manager(task_manager)

    task_arguments = add_task_context({
        'book_title': book['title'],
        'author': book['author'],
        'account_user': book['user'],
        'due_datetime': book['due_date'],
    }, task_context)
    add_fn = (
        add_due_book_to_todoist if task_manager == 'todoist'
        else add_due_book_to_google
    )
    add_fn(**task_arguments)


def update_due_book_in_task_manager(task_manager, book, task_context=None):
    """Update one existing due-date task with a renewed due date."""
    validate_task_manager(task_manager)

    task_arguments = add_task_context({
        'book_title': book['title'],
        'account_user': book['user'],
        'due_datetime': book['due_date'],
    }, task_context)
    update_fn = (
        update_due_book_todoist if task_manager == 'todoist'
        else update_due_book_google
    )
    update_fn(**task_arguments)


def complete_due_book_in_task_manager(
        task_manager, book_title, task_context=None):
    """Mark one existing due-date task complete after a book is returned."""
    validate_task_manager(task_manager)

    task_arguments = add_task_context(
        {'book_title': book_title}, task_context)
    complete_fn = (
        complete_due_book_todoist if task_manager == 'todoist'
        else complete_due_book_google
    )
    complete_fn(**task_arguments)


def process_hold_emails(
        mail, email_ids, user_mapping, task_manager, task_context=None,
        location_addresses=None):
    """Parse and process all unread hold emails."""
    if not email_ids:
        get_tracer_logger().info("No unread KCLS hold mails found!")
        return

    get_tracer_logger().info(
        "Found %s unread hold email(s)", len(email_ids))
    hold_count = 0

    for email_id in email_ids:
        try:
            message = fetch_message(mail, email_id)
            if message is None:
                continue

            get_tracer_logger().info(
                "Processing hold email: %s", message['subject'])
            is_expired = "expired" in (message['subject'] or '').lower()
            holds = parse_hold_email(message, user_mapping)

            if not holds:
                get_tracer_logger().error(
                    "No holds could be parsed from email: %s; marking it "
                    "as read to prevent repeated retries",
                    message['subject'],
                )
                mail.store(email_id, '+FLAGS', '\\Seen')
                continue

            get_tracer_logger().info(
                "Parsed %d library hold(s) from email", len(holds))
            for hold in holds:
                if not hold['title'].strip():
                    get_tracer_logger().warning(
                        "Skipping hold with an empty title in email: %s",
                        message['subject'],
                    )
                    continue

                hold_count += 1
                get_tracer_logger().info(
                    "Hold %d: %s by %s for %s at %s, pickup by %s",
                    hold_count,
                    hold['title'],
                    hold['author'],
                    hold['user'],
                    hold['location'],
                    hold['str_deadline'],
                )
                record_library_event(
                    "hold_expired" if is_expired else "hold_ready",
                    {
                        "title": hold['title'],
                        "author": hold['author'],
                        "account_number": hold['account_number'],
                        "user": hold['user'],
                        "pickup_location": hold['location'],
                        "pickup_by": hold['deadline'],
                    },
                )
                hold['expired'] = is_expired
                location_address = get_location_address(
                    hold['location'], location_addresses or {})
                add_hold_to_task_manager(
                    task_manager, hold, task_context, location_address)

            mail.store(email_id, '+FLAGS', '\\Seen')
            get_tracer_logger().info("Hold email marked as read")
        except Exception:  # noqa: BLE001
            get_tracer_logger().exception(
                "Failed to process hold email %s", email_id)


def process_receipt_emails(
        mail, email_ids, user_mapping, task_manager, task_context=None):
    """Parse and process all unread checkout receipt emails."""
    if not email_ids:
        get_tracer_logger().info("No unread KCLS checkout receipt mails found!")
        return

    get_tracer_logger().info(
        "Found %s unread checkout receipt email(s)", len(email_ids))

    for email_id in email_ids:
        try:
            message = fetch_message(mail, email_id)
            if message is None:
                continue

            get_tracer_logger().info(
                "Processing checkout receipt: %s", message['subject'])
            checked_out_books = parse_checkout_receipt(message, user_mapping)

            if not checked_out_books:
                get_tracer_logger().error(
                    "No checkout books could be parsed from email: %s; "
                    "marking it as read to prevent repeated retries",
                    message['subject'],
                )
                mail.store(email_id, '+FLAGS', '\\Seen')
                continue

            get_tracer_logger().info(
                "Parsed %d checked-out book(s) from receipt",
                len(checked_out_books),
            )
            for book in checked_out_books:
                if not book['title'].strip():
                    get_tracer_logger().warning(
                        "Skipping checkout with an empty title in email: %s",
                        message['subject'],
                    )
                    continue

                get_tracer_logger().info(
                    "Checkout: %s by %s for %s, due %s",
                    book['title'],
                    book.get('author', 'Unknown'),
                    book['user'],
                    book['due_date'],
                )
                record_library_event(
                    "checkout",
                    {
                        "title": book['title'],
                        "author": book.get('author', 'Unknown'),
                        "user": book['user'],
                        "due_date": book['due_date'],
                    },
                )
                add_due_book_to_task_manager(
                    task_manager, book, task_context)
                complete_hold_in_task_manager(
                    task_manager, book, task_context)

            mail.store(email_id, '+FLAGS', '\\Seen')
            get_tracer_logger().info("Checkout receipt marked as read")
        except Exception:  # noqa: BLE001
            get_tracer_logger().exception(
                "Failed to process checkout receipt %s", email_id)


def process_due_date_emails(
        mail, email_ids, user_mapping, task_manager, task_context=None):
    """Parse renewal emails and update their existing due-date tasks."""
    if not email_ids:
        get_tracer_logger().info("No unread KCLS renewal emails found!")
        return

    for email_id in email_ids:
        try:
            message = fetch_message(mail, email_id)
            if message is None:
                continue

            renewed_books = parse_due_date_email(message, user_mapping)
            if not renewed_books:
                get_tracer_logger().warning(
                    "No renewed books could be parsed from email: %s; "
                    "marking it as read to prevent repeated retries",
                    message['subject'],
                )
            for book in renewed_books:
                if not book['title'].strip() or book['due_date'] is None:
                    continue

                record_library_event(
                    "due_date_updated",
                    {
                        "title": book['title'],
                        "user": book['user'],
                        "due_date": book['due_date'],
                    },
                )
                update_due_book_in_task_manager(
                    task_manager, book, task_context)

            mail.store(email_id, '+FLAGS', '\\Seen')
        except Exception:  # noqa: BLE001
            get_tracer_logger().exception(
                "Failed to process renewal email %s", email_id)


def process_checkin_emails(
        mail, email_ids, task_manager, task_context=None):
    """Parse returned books and complete their existing due-date tasks."""
    if not email_ids:
        get_tracer_logger().info("No unread KCLS check-in emails found!")
        return

    for email_id in email_ids:
        try:
            message = fetch_message(mail, email_id)
            if message is None:
                continue

            returned_books = parse_checkin_email(message)
            if not returned_books:
                get_tracer_logger().warning(
                    "No returned books could be parsed from email: %s; "
                    "marking it as read to prevent repeated retries",
                    message['subject'],
                )
            for book_title in returned_books:
                record_library_event(
                    "checkin",
                    {"title": book_title},
                )
                complete_due_book_in_task_manager(
                    task_manager, book_title, task_context)

            mail.store(email_id, '+FLAGS', '\\Seen')
        except Exception:  # noqa: BLE001
            get_tracer_logger().exception(
                "Failed to process check-in email %s", email_id)


def main():
    """Run the email scanner from start to finish."""
    today = datetime.now().astimezone()
    configure_logging(script_dir)
    get_tracer_logger().info(
        "\n\nStarting on %s",
        today.strftime('%A, %B %d, %Y at %I:%M %p'),
    )

    env_path = os.path.join(script_dir, '.env')
    json_path = os.path.join(script_dir, 'users.json')
    locations_path = os.path.join(script_dir, 'library_locations.json')

    load_dotenv(dotenv_path=env_path)
    task_manager = os.getenv('TASK_MANAGER', 'google').strip().lower()
    get_tracer_logger().info("Selected Task Manager: %s", task_manager)
    validate_task_manager(task_manager)

    user_mapping = load_user_mapping(json_path)
    location_addresses = load_location_addresses(locations_path)

    mail = connect_to_gmail()
    try:
        login_to_gmail(mail, os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS'))
        mail.select('inbox')
        hold_email_ids = search_unread_holds(mail)
        receipt_email_ids = search_unread_receipts(mail)
        due_date_email_ids = search_unread_due_dates(mail)
        checkin_email_ids = search_unread_checkins(mail)
        task_context = None
        if (hold_email_ids or receipt_email_ids or due_date_email_ids or
                checkin_email_ids):
            try:
                task_context = create_task_manager_context(task_manager)
            except Exception:  # noqa: BLE001
                get_tracer_logger().exception(
                    "Could not initialize the %s task manager. "
                    "No emails were processed.",
                    task_manager,
                )
                return
        process_hold_emails(
            mail,
            hold_email_ids,
            user_mapping,
            task_manager,
            task_context,
            location_addresses,
        )
        process_receipt_emails(
            mail,
            receipt_email_ids,
            user_mapping,
            task_manager,
            task_context,
        )
        process_due_date_emails(
            mail,
            due_date_email_ids,
            user_mapping,
            task_manager,
            task_context,
        )
        process_checkin_emails(
            mail,
            checkin_email_ids,
            task_manager,
            task_context,
        )
    finally:
        mail.logout()
        close_logging()


if __name__ == '__main__':
    main()
