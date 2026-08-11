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
from kcls_hold_gtasks import add_hold_to_google
from kcls_hold_todoist import add_hold_to_todoist

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
print(f"Uploading tasks to "+task_manager)

# to choose mailbox- default is inbox
mail.select('inbox')

# None is defualt charset, status is OK or NO
status, messages = mail.search(
    None, '(FROM "noreply@kcls.org")', 'UNSEEN', '(SUBJECT "Your Hold")')

# messages[0] is a byte string of email IDs, we split it into a list of individual email IDs
# .split() makes a list separated by spaces into one separated by commas
email_ids = messages[0].split()

if email_ids:
    latest_email_id = email_ids[-1]  # -1 is latest, 0 is oldest

    # RFC822 is standard and means whole email
    status, data = mail.fetch(latest_email_id, '(RFC822)')

    # extract raw email bytes from data list in given position
    raw_email_bytes = data[0][1]

    # 'message_from_bytes' instead of 'message_from_binary_file' since already in memory
    msg = email.message_from_bytes(raw_email_bytes, policy=policy.default)

    # Load the users configuration file
    with open('users.json', 'r') as file:
        user_mapping = json.load(file)

    print(f"\n\n{msg['subject']}")
    # print("\n--- Email Body ---")

    # multipart= txt+html, singlepart= txt or html only
    if msg.is_multipart():

        all_holds = []

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

                    # If current book already has a title we go to next book and save current book to all_holds
                    if current_line == "Title":
                        if "title" in current_hold:
                            all_holds.append(current_hold)
                            current_hold = {}  # empty current_book to start new book

                        current_hold["title"] = lines[i+1]
                        # print(f"Title:    {current_hold['title']}")

                    if current_line == "Account:":
                        account_number = lines[i+1]
                        current_hold["account_number"] = account_number

                        # Looks up the account number in users.json, defaults to "Unknown user" if user not found
                        user = user_mapping.get(account_number, "Unknown user")

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
                    # add the last book to the list of all_holds
                    all_holds.append(current_hold)

                break

    if all_holds:
        print(f"\n\n--- Library Hold Details (Count: {len(all_holds)}) ---\n")
        hold_count = 1
        for hold in all_holds:
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

            mail.store(latest_email_id, '+FLAGS', '\\Seen')
            print("Email marked as READ")
    else:
        print(msg.get_content())


else:
    print("No unread KCLS hold mails found!")

mail.logout()
