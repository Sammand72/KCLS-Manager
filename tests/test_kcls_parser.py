import imaplib
import json
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kcls_parser import (
    build_location_address_mapping,
    close_logging,
    configure_logging,
    create_task_manager_context,
    get_location_address,
    get_recipient_email,
    get_tracer_logger,
    load_location_addresses,
    load_user_mapping,
    main,
    parse_checkin_email,
    parse_checkout_receipt,
    parse_due_date_email,
    parse_hold_email,
    record_library_event,
    resolve_user,
    search_unread_checkins,
    search_unread_due_dates,
    search_unread_emails,
    search_unread_holds,
    search_unread_receipts,
    validate_task_manager,
)


class ParserTests(unittest.TestCase):
    def make_message(self, body, content_type="text/plain"):
        message = EmailMessage()
        message["Subject"] = "Test KCLS email"
        if content_type == "text/html":
            message.set_content("This is the plain-text fallback.")
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
        return message

    def test_parse_multiple_holds_and_account_names(self):
        message = self.make_message(
            """Account:
12345
Title
The First Book
Author
Jane Author
Pickup Location
Downtown Library
Pickup by
September 25, 2026
Title
The Second Book
Author
John Writer
Pickup Location
North Library
Pickup by
September 28, 2026
"""
        )

        holds = parse_hold_email(message, {"12345": "Sam"})

        self.assertEqual(len(holds), 2)
        self.assertEqual(holds[0]["title"], "The First Book")
        self.assertEqual(holds[0]["user"], "Sam")
        self.assertEqual(holds[1]["title"], "The Second Book")
        self.assertIsNotNone(holds[1]["deadline"])

    def test_missing_author_does_not_use_next_label_as_author(self):
        message = self.make_message(
            """Account:
99999
Title
Book Without Author
Author
Pickup Location
Central Library
Pickup by
September 30, 2026
"""
        )

        holds = parse_hold_email(message, {})

        self.assertEqual(holds[0]["author"], "Unknown")
        self.assertEqual(holds[0]["location"], "Central Library")

    def test_parse_html_hold_email(self):
        message = self.make_message(
            "<p>Account:</p><p>12345</p><p>Title</p><p>HTML Book</p>"
            "<p>Author</p><p>HTML Author</p><p>Pickup Location</p>"
            "<p>East Library</p><p>Pickup by</p><p>October 1, 2026</p>",
            content_type="text/html",
        )

        holds = parse_hold_email(message, {"12345": "Sam"})

        self.assertEqual(holds[0]["title"], "HTML Book")
        self.assertEqual(holds[0]["location"], "East Library")

    def test_parse_multiple_checkout_books(self):
        message = self.make_message(
            """Title: First Checkout
Author: First Author
Title: Second Checkout
Author: Second Author
"""
        )

        books = parse_checkout_receipt(message, {})

        self.assertEqual(
            books,
            [
                {
                    "title": "First Checkout",
                    "author": "First Author",
                    "due_date": None,
                    "user": "Unknown user",
                },
                {
                    "title": "Second Checkout",
                    "author": "Second Author",
                    "due_date": None,
                    "user": "Unknown user",
                },
            ],
        )

    def test_parse_checkout_user_and_due_date(self):
        message = self.make_message(
            """Dear JOHN,

You checked out the following items:

  1. Title: Diary of a wimpy kid Partypooper
     Author: Kinney, Jeff
     Call Number: J KINNEY
     Barcode: 31000059677172
     Due Date: 10/06/2026
"""
        )

        books = parse_checkout_receipt(message, {"123": "John"})

        self.assertEqual(books[0]["title"], "Diary of a wimpy kid Partypooper")
        self.assertEqual(books[0]["author"], "Kinney, Jeff")
        self.assertEqual(books[0]["user"], "John")
        self.assertEqual(books[0]["due_date"].strftime(
            "%Y-%m-%d"), "2026-10-06")

    def test_parse_due_date_html_email(self):
        message = self.make_message(
            "<p>Account:</p><p>12345</p><p>Title</p><p>HTML Book</p>"
            "<p>Date Due</p>"
            "<p>October 15, 2026</p>",
            content_type="text/html",
        )

        books = parse_due_date_email(message, {"12345": "Sam"})

        self.assertEqual(books[0]["title"], "HTML Book")
        self.assertEqual(books[0]["user"], "Sam")
        self.assertEqual(books[0]["due_date"].strftime(
            "%Y-%m-%d"), "2026-10-15")

    def test_parse_multiple_due_date_html_books(self):
        message = self.make_message(
            "<p>Item(s) Renewed:</p>"
            "<p>Title</p><p>First Renewed Book</p>"
            "<p>Date Due</p><p>11/3/2026</p>"
            "<p>Title</p><p>Second Renewed Book</p>"
            "<p>Date Due</p><p>11/3/2026</p>",
            content_type="text/html",
        )

        books = parse_due_date_email(message, {})

        self.assertEqual(
            [book["title"] for book in books],
            ["First Renewed Book", "Second Renewed Book"],
        )

    def test_parse_checkin_email_books_between_dashes(self):
        message = self.make_message(
            """  2026-09-08

Crossroads Library
425-644-6203

Checkin

-------------------------

Everything you need to ace pre-algebra & algebra 1 in one big fat notebook : the complete high school study guide

31000060537324

-------------------------

Everything you need to ace algebra 2 in one big fat notebook

31000060535914

-------------------------

Number of checked in items 2
"""
        )

        self.assertEqual(
            parse_checkin_email(message),
            [
                "Everything you need to ace pre-algebra & algebra 1 in one big fat notebook : the complete high school study guide",
                "Everything you need to ace algebra 2 in one big fat notebook",
            ],
        )

    def test_checkout_unknown_user_and_missing_due_date(self):
        message = self.make_message(
            """Dear Someone Else,
Title: Book Without Due Date
Author:
"""
        )

        books = parse_checkout_receipt(message, {"123": "John"})

        self.assertEqual(books[0]["user"], "Unknown user")
        self.assertEqual(books[0]["author"], "Unknown")
        self.assertIsNone(books[0]["due_date"])

    def test_recipient_email_names_the_user(self):
        message = self.make_message("Title: Emailed Book\n")
        message["To"] = "Sam Reader <SAM@Example.com>"

        books = parse_checkout_receipt(message, {"sam@example.com": "Sam"})

        self.assertEqual(books[0]["user"], "Sam")

    def test_recipient_email_without_name_is_used_as_label(self):
        message = self.make_message("Title: Emailed Book\n")
        message["To"] = "sam@example.com"

        self.assertEqual(
            resolve_user(message, {"sam@example.com": ""}),
            "sam@example.com",
        )
        # An address missing from users.json is still a useful label.
        self.assertEqual(resolve_user(message, {}), "sam@example.com")

    def test_no_recipient_or_mapping_is_unknown_user(self):
        message = self.make_message("Title: Emailed Book\n")

        self.assertEqual(resolve_user(message, {}), "Unknown user")

    def test_mapped_recipient_is_preferred_among_several(self):
        message = self.make_message("")
        message["To"] = "someone@example.com, sam@example.com"

        self.assertEqual(
            get_recipient_email(message, {"sam@example.com": "Sam"}),
            "sam@example.com",
        )

    def test_card_number_overrides_shared_email(self):
        message = self.make_message(
            "Account:\n111\nTitle\nCarol's Book\n")
        message["To"] = "family@example.com"
        user_mapping = {"family@example.com": "Family", "111": "Carol"}

        holds = parse_hold_email(message, user_mapping)

        self.assertEqual(holds[0]["user"], "Carol")

    def test_unmapped_card_number_falls_back_to_email(self):
        message = self.make_message(
            "Account:\n999\nTitle\nRenewed Book\nDue Date\n"
            "October 15, 2026\n")
        message["To"] = "family@example.com"

        books = parse_due_date_email(
            message, {"family@example.com": "Family"})

        self.assertEqual(books[0]["user"], "Family")

    def test_greeting_overrides_shared_email(self):
        message = self.make_message("Dear DAVE,\nTitle: Dave's Book\n")
        message["To"] = "family@example.com"
        user_mapping = {"family@example.com": "Family", "222": "Dave"}

        books = parse_checkout_receipt(message, user_mapping)

        self.assertEqual(books[0]["user"], "Dave")

    def test_empty_message_returns_no_records(self):
        message = self.make_message("")

        self.assertEqual(parse_hold_email(message, {}), [])
        self.assertEqual(parse_checkout_receipt(message, {}), [])

    def test_location_addresses_match_case_and_whitespace(self):
        addresses = build_location_address_mapping([
            {"name": "  Auburn  ", "address": "1102 Auburn Way S"},
        ])

        self.assertEqual(
            get_location_address("AUBURN", addresses),
            "1102 Auburn Way S",
        )

    def test_unknown_location_has_no_address(self):
        addresses = build_location_address_mapping([
            {"name": "Auburn", "address": "1102 Auburn Way S"},
        ])

        self.assertEqual(get_location_address("Not Listed", addresses), "")

    def test_missing_location_file_returns_empty_mapping(self):
        addresses = load_location_addresses("does-not-exist.json")

        self.assertEqual(addresses, {})

    def test_malformed_location_file_returns_empty_mapping(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", encoding="utf-8") as file:
            file.write("not valid json")
            file.flush()

            addresses = load_location_addresses(file.name)

        self.assertEqual(addresses, {})

    def test_missing_user_file_returns_empty_mapping(self):
        self.assertEqual(load_user_mapping("does-not-exist.json"), {})

    def test_malformed_user_file_returns_empty_mapping(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", encoding="utf-8") as file:
            file.write("not valid json")
            file.flush()

            users = load_user_mapping(file.name)

        self.assertEqual(users, {})

    def test_user_file_must_contain_a_mapping(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", encoding="utf-8") as file:
            json.dump(["Sam"], file)
            file.flush()

            users = load_user_mapping(file.name)

        self.assertEqual(users, {})

    def test_imap_fetch_failure_returns_no_message(self):
        mail = SimpleNamespace(fetch=lambda *args: ("NO", []))

        from kcls_parser import fetch_message

        self.assertIsNone(fetch_message(mail, b"1"))

    def test_imap_fetch_peeks_so_email_stays_unread(self):
        fetch_calls = []

        def fake_fetch(*args):
            fetch_calls.append(args)
            return ("OK", [(b"1 (BODY[] {0}", b"Subject: Hi\r\n\r\nBody")])

        from kcls_parser import fetch_message

        message = fetch_message(SimpleNamespace(fetch=fake_fetch), b"1")

        self.assertEqual(fetch_calls, [(b"1", "(BODY.PEEK[])")])
        self.assertEqual(message["subject"], "Hi")

    def test_imap_search_failure_returns_no_email_ids(self):
        mail = SimpleNamespace(search=lambda *args: ("NO", [b"1 2"]))

        self.assertEqual(search_unread_holds(mail), [])
        self.assertEqual(search_unread_receipts(mail), [])
        self.assertEqual(search_unread_due_dates(mail), [])
        self.assertEqual(search_unread_checkins(mail), [])

    def test_search_unread_emails_builds_imap_criteria(self):
        searches = []
        mail = SimpleNamespace(
            search=lambda *args: (searches.append(args) or ("OK", [b"4 9"])))

        email_ids = search_unread_emails(
            mail,
            from_address="noreply@kcls.org",
            subject_query='(SUBJECT "Due Dates")',
            email_kind="renewal",
        )

        self.assertEqual(email_ids, [b"4", b"9"])
        self.assertEqual(
            searches[0],
            (None, '(FROM "noreply@kcls.org")', 'UNSEEN',
             '(SUBJECT "Due Dates")'),
        )

    def test_checkin_search_uses_both_subject_variants(self):
        searches = []
        mail = SimpleNamespace(
            search=lambda *args: (searches.append(args) or ("OK", [b"1"])))

        self.assertEqual(search_unread_checkins(mail), [b"1"])
        search_text = " ".join(str(argument) for argument in searches[0])
        self.assertIn('FROM "No-Reply-AMH-Notify@kcls.org"', search_text)
        self.assertIn('SUBJECT "Checkin"', search_text)
        self.assertIn('SUBJECT "Items Returned Receipt"', search_text)

    def test_hold_search_includes_ready_and_expired_subjects(self):
        searches = []
        mail = SimpleNamespace(
            search=lambda *args: (searches.append(args) or ("OK", [b"1 2"])))

        self.assertEqual(search_unread_holds(mail), [b"1", b"2"])
        search_text = " ".join(str(argument) for argument in searches[0])
        self.assertIn('SUBJECT "Hold is Ready"', search_text)
        self.assertIn('SUBJECT "Holds are Ready"', search_text)
        self.assertIn('SUBJECT "Hold Has Expired"', search_text)
        self.assertIn('SUBJECT "Holds have expired"', search_text)

    def test_imap_login_succeeds_on_first_attempt(self):
        from kcls_parser import login_to_gmail

        mail = SimpleNamespace(login=lambda *args: ("OK", []))

        login_to_gmail(mail, "user", "password", wait_seconds=0)

    def test_imap_login_retries_temporary_failure(self):
        from kcls_parser import login_to_gmail

        login_attempts = []

        def login(*args):
            if not login_attempts:
                login_attempts.append(args)
                raise imaplib.IMAP4.error("temporary failure")
            return ("OK", [])

        mail = SimpleNamespace(login=login)

        with patch("kcls_parser.time.sleep") as sleep:
            login_to_gmail(mail, "user", "password", wait_seconds=0)

        sleep.assert_called_once_with(0)

    def test_imap_login_stops_after_temporary_failures(self):
        from kcls_parser import login_to_gmail

        mail = SimpleNamespace(
            login=lambda *args: (_ for _ in ()).throw(
                imaplib.IMAP4.error("temporary failure")
            )
        )

        with (
            patch("kcls_parser.time.sleep") as sleep,
            self.assertRaises(SystemExit),
        ):
            login_to_gmail(mail, "user", "password", wait_seconds=0)

        self.assertEqual(sleep.call_count, 2)

    def test_imap_login_does_not_retry_authentication_failure(self):
        from kcls_parser import login_to_gmail

        login_calls = []

        def login(*args):
            login_calls.append(args)
            raise imaplib.IMAP4.error(
                "[AUTHENTICATIONFAILED] Invalid credentials"
            )

        mail = SimpleNamespace(login=login)

        with (
            patch("kcls_parser.time.sleep") as sleep,
            self.assertRaises(SystemExit),
        ):
            login_to_gmail(mail, "user", "password", wait_seconds=0)

        self.assertEqual(len(login_calls), 1)
        sleep.assert_not_called()

    def test_invalid_task_manager_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_task_manager("unsupported")

    def test_google_context_resolves_tasklist(self):
        service = object()

        with (
            patch("kcls_parser.authenticate_google_tasks", return_value=service),
            patch("kcls_parser.get_tasklist_name", return_value="Reading"),
            patch("kcls_parser.get_tasklist_id", return_value="list-id"),
        ):
            context = create_task_manager_context("google")

        self.assertEqual(context, {
            "service": service,
            "target_list_id": "list-id",
        })

    def test_main_logs_task_manager_failure_and_closes_mail(self):
        mail = SimpleNamespace(
            select=Mock(return_value=("OK", [b"3"])),
            logout=Mock(),
        )

        with (
            patch.dict("os.environ", {
                "TASK_MANAGER": "google",
                "EMAIL_USER": "someone@example.com",
                "EMAIL_PASS": "app password",
            }),
            patch("kcls_parser.load_dotenv"),
            patch("kcls_parser.configure_logging"),
            patch("kcls_parser.close_logging") as close_logging_mock,
            patch("kcls_parser.connect_to_gmail", return_value=mail),
            patch("kcls_parser.login_to_gmail"),
            patch("kcls_parser.search_unread_holds", return_value=[b"1"]),
            patch("kcls_parser.search_unread_receipts", return_value=[]),
            patch("kcls_parser.search_unread_due_dates", return_value=[]),
            patch("kcls_parser.search_unread_checkins", return_value=[]),
            patch("kcls_parser.load_user_mapping", return_value={}) as user_loader,
            patch("kcls_parser.load_location_addresses", return_value={}),
            patch(
                "kcls_parser.create_task_manager_context",
                side_effect=RuntimeError("expired token"),
            ),
            patch("kcls_parser.get_tracer_logger") as logger_mock,
        ):
            main()

        mail.logout.assert_called_once()
        close_logging_mock.assert_called_once()
        user_loader.assert_called_once()
        logger_mock.return_value.exception.assert_called_once()

    def test_main_stops_before_connecting_without_email_login(self):
        with (
            patch.dict("os.environ", {
                "TASK_MANAGER": "google",
                "EMAIL_USER": "",
                "EMAIL_PASS": "",
            }),
            patch("kcls_parser.load_dotenv"),
            patch("kcls_parser.configure_logging"),
            patch("kcls_parser.close_logging") as close_logging_mock,
            patch("kcls_parser.connect_to_gmail") as connect_mock,
            patch("kcls_parser.load_user_mapping", return_value={}),
            patch("kcls_parser.load_location_addresses", return_value={}),
            patch("kcls_parser.get_tracer_logger") as logger_mock,
        ):
            main()

        connect_mock.assert_not_called()
        close_logging_mock.assert_called_once()
        logger_mock.return_value.error.assert_called_once()

    def test_main_stops_when_inbox_cannot_be_opened(self):
        mail = SimpleNamespace(
            select=Mock(return_value=("NO", [b"mailbox missing"])),
            logout=Mock(),
        )

        with (
            patch.dict("os.environ", {
                "TASK_MANAGER": "google",
                "EMAIL_USER": "someone@example.com",
                "EMAIL_PASS": "app password",
            }),
            patch("kcls_parser.load_dotenv"),
            patch("kcls_parser.configure_logging"),
            patch("kcls_parser.close_logging") as close_logging_mock,
            patch("kcls_parser.connect_to_gmail", return_value=mail),
            patch("kcls_parser.login_to_gmail"),
            patch("kcls_parser.search_unread_holds") as search_mock,
            patch("kcls_parser.load_user_mapping", return_value={}),
            patch("kcls_parser.load_location_addresses", return_value={}),
            patch("kcls_parser.get_tracer_logger"),
        ):
            main()

        search_mock.assert_not_called()
        mail.logout.assert_called_once()
        close_logging_mock.assert_called_once()

    def test_library_records_use_json_lines_and_separate_tracer_output(self):
        with tempfile.TemporaryDirectory() as log_directory:
            try:
                configure_logging(log_directory)
                get_tracer_logger().info("Operational status")
                record_library_event(
                    "hold_ready",
                    {
                        "title": "Test Book",
                        "pickup_by": datetime(2026, 9, 25, 12, 30),
                    },
                )

                with open(
                        f"{log_directory}/library_records.jsonl",
                        "r",
                        encoding="utf-8") as records_file:
                    record = json.loads(records_file.readline())

                with open(
                        f"{log_directory}/library_tracer.log",
                        "r",
                        encoding="utf-8") as tracer_file:
                    tracer_output = tracer_file.read()

                self.assertEqual(record["event_type"], "hold_ready")
                self.assertEqual(record["title"], "Test Book")
                self.assertEqual(record["pickup_by"], "2026-09-25T12:30:00")
                self.assertIn("Operational status", tracer_output)
                self.assertNotIn("Operational status", json.dumps(record))
            finally:
                close_logging()


if __name__ == "__main__":
    unittest.main()
