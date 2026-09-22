# Scan KCLS emails and add library holds to Google Tasks or Todoist.
from datetime import datetime
import email
import imaplib
import json
import sys
import os
import re
import time
from email import policy
import dateparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from kcls_hold_gtasks import (
    add_due_book_to_google,
    add_hold_to_google,
    mark_hold_complete_google,
)
from kcls_hold_todoist import (
    add_due_book_to_todoist,
    add_hold_to_todoist,
    mark_hold_complete_todoist,
)


script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)


# These labels help tell an empty value apart from the next label.
KNOWN_LABELS = ("Title", "Account:", "Author", "Pickup Location", "Pickup by")


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
    """Fetch complete email from Gmail and turn it into a message object."""
    status, data = mail.fetch(email_id, '(RFC822)')
    raw_email_bytes = data[0][1]
    return email.message_from_bytes(raw_email_bytes, policy=policy.default)


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
            current_hold["deadline"] = dateparser.parse(
                deadline_text,
                settings={
                    'TIMEZONE': 'US/Pacific',
                    'RETURN_AS_TIMEZONE_AWARE': True,
                },
            )
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
            current_book["due_date"] = dateparser.parse(
                due_date_text,
                settings={
                    'TIMEZONE': 'US/Pacific',
                    'RETURN_AS_TIMEZONE_AWARE': True,
                },
            )

    if "title" in current_book:
        checked_out_books.append(current_book)

    return checked_out_books


def connect_to_gmail(max_retries=10, wait_seconds=15):
    """Connect to Gmail, retrying when the internet is not ready yet."""
    for attempt in range(max_retries):
        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            print("Successfully connected to the internet!")
            return mail
        except Exception:
            print(
                f"Internet not ready yet (Attempt {attempt + 1}/{max_retries}). "
                f"Waiting {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)

    print(
        f"Could not connect to the internet after {max_retries} attempts. Exiting script.")
    raise SystemExit


def search_unread_holds(mail):
    """Find unread KCLS emails saying that a hold is ready."""
    status, messages = mail.search(
        None,
        '(FROM "noreply@kcls.org")',
        'UNSEEN',
        '(OR (SUBJECT "Hold is Ready") (SUBJECT "Holds are Ready"))',
    )
    return messages[0].split()


def search_unread_receipts(mail):
    """Find unread KCLS checkout receipt emails."""
    status, messages = mail.search(
        None,
        '(FROM "noreply@kcls.org")',
        'UNSEEN',
        '(SUBJECT "Checkout Receipt")',
    )
    return messages[0].split()


def add_hold_to_task_manager(task_manager, hold):
    """Send one hold to the task manager selected in the environment."""
    if task_manager == 'todoist':
        add_hold_to_todoist(
            book_title=hold['title'],
            author=hold['author'],
            location=hold['location'],
            account_user=hold['user'],
            deadline_datetime=hold['deadline'],
        )
    else:
        add_hold_to_google(
            book_title=hold['title'],
            author=hold['author'],
            location=hold['location'],
            account_user=hold['user'],
            deadline_datetime=hold['deadline'],
        )


def complete_hold_in_task_manager(task_manager, book):
    """Mark one checked-out book complete in the selected task manager."""
    if task_manager == 'todoist':
        mark_hold_complete_todoist(book_title=book['title'])
    else:
        mark_hold_complete_google(book_title=book['title'])


def add_due_book_to_task_manager(task_manager, book):
    """Add one checked-out book as a due-date task."""
    if task_manager == 'todoist':
        add_due_book_to_todoist(
            book_title=book['title'],
            author=book['author'],
            account_user=book['user'],
            due_datetime=book['due_date'],
        )
    else:
        add_due_book_to_google(
            book_title=book['title'],
            author=book['author'],
            account_user=book['user'],
            due_datetime=book['due_date'],
        )


def process_hold_emails(mail, email_ids, user_mapping, task_manager):
    """Parse and process all unread hold emails."""
    if not email_ids:
        print("No unread KCLS hold mails found!")
        return

    print(f"Found {len(email_ids)} unread hold email(s)")
    hold_count = 0

    for email_id in email_ids:
        message = fetch_message(mail, email_id)
        print(f"\n{message['subject']}")
        holds = parse_hold_email(message, user_mapping)

        if not holds:
            print(message.get_content())
            continue

        print(f"--- Library Hold Details (Count: {len(holds)}) ---\n")
        for hold in holds:
            hold_count += 1
            print(f"Hold {hold_count}")
            print(f"Account:  {hold['account_number']} ({hold['user']})")
            print(f"Book:     {hold['title']}")
            print(f"Author:   {hold['author']}")
            print(f"Location: {hold['location']}")
            print(f"Deadline: {hold['str_deadline']}\n")
            add_hold_to_task_manager(task_manager, hold)

        mail.store(email_id, '+FLAGS', '\\Seen')
        print("Email marked as READ")


def process_receipt_emails(mail, email_ids, user_mapping, task_manager):
    """Parse and process all unread checkout receipt emails."""
    if not email_ids:
        print("No unread KCLS checkout receipt mails found!")
        return

    print(f"Found {len(email_ids)} unread checkout receipt email(s)")

    for email_id in email_ids:
        message = fetch_message(mail, email_id)
        print(f"\n{message['subject']}")
        checked_out_books = parse_checkout_receipt(message, user_mapping)

        if not checked_out_books:
            print(message.get_content())
            continue

        print(
            f"--- Checkout Receipt Details (Count: {len(checked_out_books)}) ---\n"
        )
        for book in checked_out_books:
            print(f"Book:   {book['title']}")
            print(f"Author: {book.get('author', 'Unknown')}\n")
            add_due_book_to_task_manager(task_manager, book)
            complete_hold_in_task_manager(task_manager, book)

        mail.store(email_id, '+FLAGS', '\\Seen')
        print("Checkout receipt email marked as READ")


def main():
    """Run the email scanner from start to finish."""
    today = datetime.now().astimezone()
    print(
        f"\n\nStarting on {today}\n({today.strftime('%A, %B %d, %Y at %I:%M %p')})")

    env_path = os.path.join(script_dir, '.env')
    json_path = os.path.join(script_dir, 'users.json')

    load_dotenv(dotenv_path=env_path)
    task_manager = os.getenv('TASK_MANAGER', 'google').lower()
    print(f"Selected Task Manager: {task_manager}")

    with open(json_path, 'r') as file:
        user_mapping = json.load(file)

    mail = connect_to_gmail()
    try:
        mail.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS'))
        mail.select('inbox')
        process_hold_emails(
            mail,
            search_unread_holds(mail),
            user_mapping,
            task_manager,
        )
        process_receipt_emails(
            mail,
            search_unread_receipts(mail),
            user_mapping,
            task_manager,
        )
    finally:
        mail.logout()


if __name__ == '__main__':
    main()
