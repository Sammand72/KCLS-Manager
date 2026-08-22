# Python3 code to scan my inbox for emails from King County Library System, find hold details and upload to Google tasks

import time
import json
import imaplib
import email
from email import policy
from bs4 import BeautifulSoup
from datetime import datetime
import dateparser
import os
from dotenv import load_dotenv
from kcls_hold_gtasks import add_hold_to_google, mark_hold_complete_google
from kcls_hold_todoist import add_hold_to_todoist, mark_hold_complete_todoist

today = datetime.now().astimezone()
print(
    f"\n\nStarting on {today}\n({today.strftime("%A, %B %d, %Y at %I:%M %p")})")

# In case internet fails
max_retries = 10
for attempt in range(max_retries):
    try:
        # to connect to Gmail with SSL (Secure Sockets Layer) for security
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        print("Successfully connected to the internet!")
        break
    except Exception as e:
        print(
            f"Internet not ready yet (Attempt {attempt + 1}/{max_retries}). Waiting 15 seconds...")
        time.sleep(15)
else:
    print("Could not connect to the internet after 10 attempts. Exiting script.")
    exit()

load_dotenv()
email_address = os.getenv('EMAIL_USER')
app_password = os.getenv('EMAIL_PASS')
mail.login(email_address, app_password)

# Which task manager to push holds to: "google" or "todoist"
task_manager = os.getenv('TASK_MANAGER', 'google').lower()
print(f"Selected Task Manager: "+task_manager)

# to choose mailbox- default is inbox
mail.select('inbox')

# None is defualt charset, status is OK or NO
status, messages = mail.search(
    None, '(FROM "noreply@kcls.org")', 'UNSEEN',
    '(OR (SUBJECT "Hold is Ready") (SUBJECT "Holds are Ready"))')

# messages[0] is a byte string of email IDs, we split it into a list of individual email IDs
# .split() makes a list separated by spaces into one separated by commas
email_ids = messages[0].split()

if email_ids:
    print(f"Found {len(email_ids)} unread hold email(s)")

    # Load the users configuration file
    with open('users.json', 'r') as file:
        user_mapping = json.load(file)

    hold_count = 0

    # go through every unread hold email, oldest first, instead of only the latest one
    for current_email_id in email_ids:

        # RFC822 is standard and means whole email
        status, data = mail.fetch(current_email_id, '(RFC822)')

        # extract raw email bytes from data list in given position
        raw_email_bytes = data[0][1]

        # 'message_from_bytes' instead of 'message_from_binary_file' since already in memory
        msg = email.message_from_bytes(raw_email_bytes, policy=policy.default)

        print(f"\n{msg['subject']}")
        # print("\n--- Email Body ---")

        # holds found in this specific email
        email_holds = []

        # multipart= txt+html, singlepart= txt or html only
        if msg.is_multipart():

            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    raw_html = part.get_content()

                    # parsed
                    soup = BeautifulSoup(raw_html, 'html.parser')

                    # separator='\n' makes sure there is a new line where tags used to be, strip removes all tags
                    clean_text = soup.get_text(separator='\n', strip=True)

                    # print(clean_text)

                    current_hold = {}

                    # Convert the text into a list of lines so we can loop through them
                    lines = clean_text.splitlines()

                    # loop which finds label and grabs line under that with actual data
                    for i in range(len(lines)):
                        current_line = lines[i]

                        # If current book already has a title we go to next book and save current book to email_holds
                        if current_line == "Title":
                            if "title" in current_hold:
                                email_holds.append(current_hold)
                                current_hold = {}  # empty current_book to start new book

                            current_hold["title"] = lines[i+1]
                            # print(f"Title:    {current_hold['title']}")

                        if current_line == "Account:":
                            account_number = lines[i+1]
                            current_hold["account_number"] = account_number

                            # Looks up the account number in users.json, defaults to "Unknown user" if user not found
                            user = user_mapping.get(
                                account_number, "Unknown user")

                            current_hold["user"] = user
                            # print(f"Account:  {current_hold['account_number']} ({user})")

                        elif current_line == "Author":
                            current_hold["author"] = lines[i+1]
                            # print(f"Author:   {current_hold['author']}")

                        elif current_line == "Pickup Location":
                            current_hold["location"] = lines[i+1]
                            # print(f"Location: {current_hold['location']}")

                        elif current_line == "Pickup by":
                            current_hold["deadline"] = dateparser.parse(
                                lines[i+1], settings={'TIMEZONE': 'US/Pacific', 'RETURN_AS_TIMEZONE_AWARE': True})

                            current_hold['str_deadline'] = lines[i+1]
                            # print(f"Deadline: {lines[i+1]} (Pickup in {(current_hold['deadline'] - today).days} days)")

                    if "title" in current_hold:
                        # add the last book to the list of email_holds
                        email_holds.append(current_hold)

                    break

        if email_holds:
            print(
                f"--- Library Hold Details (Count: {len(email_holds)}) ---\n")
            for hold in email_holds:
                hold_count += 1
                print(f"Hold {hold_count}")
                print(f"Account:  {hold['account_number']} ({hold['user']})")
                print(f"Book:     {hold['title']}")
                print(f"Author:   {hold['author']}")
                print(f"Location: {hold['location']}")
                print(
                    f"Deadline: {hold['str_deadline']}\n")

                if task_manager == 'todoist':
                    add_hold_to_todoist(
                        book_title=hold['title'],
                        author=hold['author'],
                        location=hold['location'],
                        account_user=hold['user'],
                        deadline_datetime=hold['deadline']
                    )
                else:
                    add_hold_to_google(
                        book_title=hold['title'],
                        author=hold['author'],
                        location=hold['location'],
                        account_user=hold['user'],
                        deadline_datetime=hold['deadline']
                    )

            mail.store(current_email_id, '+FLAGS', '\\Seen')
            print("Email marked as READ")
        else:
            print(msg.get_content())

else:
    print("No unread KCLS hold mails found!")

# --- Checkout receipts: find checked-out books and mark any matching hold task complete ---

status, receipt_messages = mail.search(
    None, '(FROM "noreply@kcls.org")', 'UNSEEN', '(SUBJECT "Checkout Receipt")')

receipt_email_ids = receipt_messages[0].split()

if receipt_email_ids:
    print(f"Found {len(receipt_email_ids)} unread checkout receipt email(s)")

    # go through every unread checkout receipt email, oldest first, instead of only the latest one
    for current_receipt_id in receipt_email_ids:

        status, data = mail.fetch(current_receipt_id, '(RFC822)')
        raw_email_bytes = data[0][1]
        receipt_msg = email.message_from_bytes(
            raw_email_bytes, policy=policy.default)

        print(f"\n{receipt_msg['subject']}")

        checked_out_books = []

        # Unlike the hold email, this one isn't always multipart (txt+html) -
        # it can arrive as a single text/html part, or even a single text/plain
        # part with no HTML at all, so we handle whichever one shows up
        raw_html = None
        raw_text = None
        if receipt_msg.is_multipart():
            for part in receipt_msg.walk():
                if part.get_content_type() == "text/html":
                    raw_html = part.get_content()
                elif part.get_content_type() == "text/plain":
                    raw_text = part.get_content()
        elif receipt_msg.get_content_type() == "text/html":
            raw_html = receipt_msg.get_content()
        elif receipt_msg.get_content_type() == "text/plain":
            raw_text = receipt_msg.get_content()

        if raw_html:
            # HTML body - strip the tags down to plain text first
            soup = BeautifulSoup(raw_html, 'html.parser')
            clean_text = soup.get_text(separator='\n', strip=True)
        else:
            clean_text = raw_text

        if clean_text:
            current_book = {}

            for current_line in clean_text.splitlines():
                # On this email the label and its value are on the same line,
                # e.g. "1. Title: The social animal : the hidden sources of love..."
                # so we split on the label instead of reading the next line
                if "Title:" in current_line:
                    if "title" in current_book:
                        checked_out_books.append(current_book)
                        current_book = {}  # empty current_book to start new book

                    current_book["title"] = current_line.split("Title:", 1)[
                        1].strip()

                elif "Author:" in current_line:
                    current_book["author"] = current_line.split("Author:", 1)[
                        1].strip()

            if "title" in current_book:
                # add the last book to the list of checked_out_books
                checked_out_books.append(current_book)

        if checked_out_books:
            print(
                f"--- Checkout Receipt Details (Count: {len(checked_out_books)}) ---\n")
            for book in checked_out_books:
                print(f"Book:   {book['title']}")
                print(f"Author: {book.get('author', 'Unknown')}\n")

                if task_manager == 'todoist':
                    mark_hold_complete_todoist(book_title=book['title'])
                else:
                    mark_hold_complete_google(book_title=book['title'])

            mail.store(current_receipt_id, '+FLAGS', '\\Seen')
            print("Checkout receipt email marked as READ")
        else:
            print(receipt_msg.get_content())

else:
    print("No unread KCLS checkout receipt mails found!")

mail.logout()
