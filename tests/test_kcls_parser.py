import json
import tempfile
import unittest
from email.message import EmailMessage
from datetime import datetime

from kcls_parser import (
    close_logging,
    configure_logging,
    get_tracer_logger,
    parse_checkout_receipt,
    parse_hold_email,
    record_library_event,
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
            """Dear SAMARTH,

You checked out the following items:

  1. Title: Diary of a wimpy kid Partypooper
     Author: Kinney, Jeff
     Call Number: J KINNEY
     Barcode: 31000059677172
     Due Date: 10/06/2026
"""
        )

        books = parse_checkout_receipt(message, {"123": "Samarth"})

        self.assertEqual(books[0]["title"], "Diary of a wimpy kid Partypooper")
        self.assertEqual(books[0]["author"], "Kinney, Jeff")
        self.assertEqual(books[0]["user"], "Samarth")
        self.assertEqual(books[0]["due_date"].strftime(
            "%Y-%m-%d"), "2026-10-06")

    def test_checkout_unknown_user_and_missing_due_date(self):
        message = self.make_message(
            """Dear Someone Else,
Title: Book Without Due Date
Author:
"""
        )

        books = parse_checkout_receipt(message, {"123": "Samarth"})

        self.assertEqual(books[0]["user"], "Unknown user")
        self.assertEqual(books[0]["author"], "Unknown")
        self.assertIsNone(books[0]["due_date"])

    def test_empty_message_returns_no_records(self):
        message = self.make_message("")

        self.assertEqual(parse_hold_email(message, {}), [])
        self.assertEqual(parse_checkout_receipt(message, {}), [])

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
