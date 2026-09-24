import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from kcls_hold_gtasks import add_hold_to_google, mark_hold_complete_google
from kcls_hold_todoist import add_hold_to_todoist, mark_hold_complete_todoist
from kcls_parser import (
    add_hold_to_task_manager,
    process_hold_emails,
    process_receipt_emails,
)
from kcls_task_matching import (
    extract_task_user,
    titles_match,
    users_match,
)


class MatcherTests(unittest.TestCase):
    def test_empty_titles_do_not_match(self):
        self.assertFalse(titles_match("", "Anything"))
        self.assertFalse(titles_match("Anything", ""))

    def test_short_title_does_not_match_inside_longer_hold_title(self):
        self.assertFalse(titles_match("It", "Little Women"))

    def test_cut_off_hold_title_matches_full_checkout_title(self):
        self.assertTrue(titles_match(
            "Everything you need to ace English Language Arts in one big fat "
            "notebook : the complete middle school study guide",
            "Everything You Need To Ace English Language Arts In One Big Fat "
            "Notebook :",
        ))

    def test_angle_bracket_and_punctuation_are_ignored(self):
        self.assertTrue(titles_match(
            "The social animal : the hidden sources of love, character, and achievement",
            "The Social Animal : The Hidden Sources Of Love, Character, And Achievement<",
        ))

    def test_accents_are_preserved(self):
        self.assertTrue(titles_match("Café Society", "CAFÉ SOCIETY"))
        self.assertFalse(titles_match("Cafe Society", "Café Society"))

    def test_user_matching_requires_same_known_user(self):
        self.assertEqual(extract_task_user(
            "Author: Someone\nAccount: Sam"), "Sam")
        self.assertTrue(users_match("sam", "Account: Sam"))
        self.assertFalse(users_match("Sam", "Account: Alex"))
        self.assertFalse(users_match("Sam", "Author: Someone"))
        self.assertTrue(users_match("Unknown user", "Account: Alex"))


class FakeGoogleTasks:
    def __init__(self, tasks):
        self.pending_tasks = tasks
        self.completed = []

    def list(self, **kwargs):
        return self

    def execute(self):
        return {"items": self.pending_tasks}

    def patch(self, **kwargs):
        self.completed.append(kwargs["task"])
        return self

    def insert(self, **kwargs):
        self.inserted = kwargs
        return self


class FakeGoogleService:
    def __init__(self, tasks):
        self.task_api = FakeGoogleTasks(tasks)

    def tasks(self):
        return self.task_api


class TaskMatchingBackendTests(unittest.TestCase):
    def test_google_hold_includes_library_address_in_notes(self):
        service = FakeGoogleService([])
        service.task_api.inserted = None

        add_hold_to_google(
            "A Book", "An Author", "Auburn", "Sam", None,
            service=service, target_list_id="list",
            address="1102 Auburn Way S, Auburn, WA 98002",
        )

        notes = service.task_api.inserted["body"]["notes"]
        self.assertIn("Location: Auburn", notes)
        self.assertIn("Address: 1102 Auburn Way S, Auburn, WA 98002", notes)
        self.assertIn("Account: Sam", notes)

    def test_todoist_hold_includes_library_address_in_description(self):
        added_tasks = []
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
        )

        add_hold_to_todoist(
            "A Book", "An Author", "Auburn", "Sam", None,
            api=api, target_project_id="project",
            address="1102 Auburn Way S, Auburn, WA 98002",
        )

        description = added_tasks[0]["description"]
        self.assertIn("Location: Auburn", description)
        self.assertIn(
            "Address: 1102 Auburn Way S, Auburn, WA 98002", description)
        self.assertIn("Account: Sam", description)

    def test_google_completion_uses_title_and_user_matching(self):
        tasks = [
            {
                "id": "wrong-user",
                "title": "KCLS book pickup: Little Women",
                "notes": "Author: Louisa May Alcott\nAccount: Alex",
            },
            {
                "id": "right-user",
                "title": "KCLS book pickup: Little Women",
                "notes": "Author: Louisa May Alcott\nAccount: Sam",
            },
        ]
        service = FakeGoogleService(tasks)

        with patch("kcls_hold_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_hold_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("Little Women", "Sam")

        self.assertEqual(service.task_api.completed, ["right-user"])

    def test_google_completion_does_not_match_empty_or_short_title(self):
        tasks = [{
            "id": "hold",
            "title": "KCLS book pickup: Little Women",
            "notes": "Account: Sam",
        }]
        service = FakeGoogleService(tasks)

        with patch("kcls_hold_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_hold_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("It", "Sam")
            mark_hold_complete_google("", "Sam")

        self.assertEqual(service.task_api.completed, [])

    def test_todoist_completion_uses_title_and_user_matching(self):
        tasks = [
            SimpleNamespace(
                id="wrong-user",
                content="KCLS book pickup: Little Women",
                description="Account: Alex",
            ),
            SimpleNamespace(
                id="right-user",
                content="KCLS book pickup: Little Women",
                description="Account: Sam",
            ),
        ]
        api = SimpleNamespace(
            get_tasks=lambda project_id: [tasks],
            complete_task=lambda task_id: completed.append(task_id),
        )
        completed = []

        with patch("kcls_hold_todoist.authenticate_todoist", return_value=api), \
                patch("kcls_hold_todoist.get_project_id", return_value="project"):
            mark_hold_complete_todoist("Little Women", "Sam")

        self.assertEqual(completed, ["right-user"])

    def make_message(self, body):
        message = EmailMessage()
        message["Subject"] = "Test KCLS email"
        message.set_content(body)
        return message

    def test_blank_checkout_title_has_no_task_side_effects(self):
        message = self.make_message("Title: \nAuthor: Unknown\n")
        mail = SimpleNamespace(store=lambda *args: None)

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event") as record_event, \
                patch("kcls_parser.add_due_book_to_task_manager") as add_due, \
                patch("kcls_parser.complete_hold_in_task_manager") as complete:
            process_receipt_emails(mail, [b"1"], {}, "google")

        record_event.assert_not_called()
        add_due.assert_not_called()
        complete.assert_not_called()

    def test_blank_hold_title_has_no_task_side_effects(self):
        message = self.make_message(
            "Account:\n123\nTitle\nAuthor\nPickup Location\nLibrary\n"
        )
        mail = SimpleNamespace(store=lambda *args: None)

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event") as record_event, \
                patch("kcls_parser.add_hold_to_task_manager") as add_hold:
            process_hold_emails(mail, [b"1"], {}, "google")

        record_event.assert_not_called()
        add_hold.assert_not_called()

    def test_failed_hold_email_stays_unread_and_next_email_is_processed(self):
        message = self.make_message(
            "Account:\n123\nTitle\nA Book\nAuthor\nAn Author\n")
        store = []
        mail = SimpleNamespace(
            store=lambda email_id, *args: store.append(email_id))

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event"), \
                patch("kcls_parser.add_hold_to_task_manager") as add_hold:
            add_hold.side_effect = [RuntimeError("temporary failure"), None]
            process_hold_emails(mail, [b"failed", b"successful"], {}, "google")

        self.assertEqual(store, [b"successful"])
        self.assertEqual(add_hold.call_count, 2)

    def test_shared_task_context_is_forwarded_to_google(self):
        hold = {
            "title": "A Book",
            "author": "An Author",
            "location": "Central Library",
            "user": "Sam",
            "deadline": None,
        }
        service = object()

        with patch("kcls_parser.add_hold_to_google") as add_hold:
            add_hold_to_task_manager(
                "google",
                hold,
                {"service": service, "target_list_id": "list-id"},
            )

        self.assertIs(add_hold.call_args.kwargs["service"], service)
        self.assertEqual(
            add_hold.call_args.kwargs["target_list_id"], "list-id")


if __name__ == "__main__":
    unittest.main()
