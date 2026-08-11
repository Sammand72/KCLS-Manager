# KCLS Library Hold Automator

A headless Python automation script that silently monitors an email inbox for King County Library System (KCLS) "Hold is Ready" emails. It parses the email for book titles, authors, pickup locations, and deadlines, and automatically pushes them to a specific Google Tasks list. 

## Features
* Uses IMAP to find unread automated library emails.
* Handles multiple books in a single email and calculates days remaining until the deadline.
* Maps different library card numbers to specific family members.
* Designed to run silently on startup via Windows Task Scheduler/Linux Cron with a log routed.

## Dependencies
You will need Python 3 installed, along with the following libraries:
```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib beautifulsoup4 dateparser python-dotenv
```
Note: On linux instead of pip you may use apt. 

## Other files
* **credentials.json**- contains Google Cloud OAuth 2.0 Client ID
*  **users.json**- contains account numbers mapped to user
* **.env**- contains email id and app password for the email being monitored
* (**token.json** is created automatically)
Add all 4 of these to the .gitignore

## Miscellaneous
The script needs to generate the token.json so the first run has to be manual. It will open a browser window with a login page. The name of the tasklist I've set it to is "KCLS Library Holds". It can be changed to anything inside add_hold_to_google in kcls_hold_gtasks.py.

Code partly made with Google Gemini 3.1 Pro 