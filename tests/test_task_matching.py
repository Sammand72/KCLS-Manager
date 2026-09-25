import os
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from kcls_tasker_gtasks import (
    add_hold_to_google,
    get_tasklist_id,
    get_tasklist_name,
    mark_hold_complete_google,
)
from kcls_tasker_todoist import (
    add_hold_to_todoist,
    get_project_id,
    get_project_name,
    mark_hold_complete_todoist,
)
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
        self.assertFalse(titles_match("Little Women", "It"))

    def test_short_or_embedded_hold_titles_do_not_match(self):
        self.assertFalse(titles_match("Dune Messiah", "Dune"))
        self.assertTrue(titles_match("Dune", "Dune"))

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
    def __init__(self, tasks, pages=None):
        self.pending_tasks = tasks
        self.pages = pages
        self.page_index = 0
        self.list_calls = []
        self.completed = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return self

    def execute(self):
        if self.pages is not None:
            page = self.pages[self.page_index]
            self.page_index += 1
            return page
        return {"items": self.pending_tasks}

    def patch(self, **kwargs):
        self.completed.append(kwargs["task"])
        return SimpleNamespace(execute=lambda: {})

    def insert(self, **kwargs):
        self.inserted = kwargs
        return self


class FakeGoogleService:
    def __init__(self, tasks, pages=None):
        self.task_api = FakeGoogleTasks(tasks, pages=pages)

    def tasks(self):
        return self.task_api


class TaskMatchingBackendTests(unittest.TestCase):
    def test_blank_google_tasklist_uses_google_default(self):
        with patch.dict(os.environ, {"GOOGLE_TASKLIST_NAME": ""}):
            self.assertEqual(
                get_tasklist_id(None, get_tasklist_name()), "@default")

    def test_blank_todoist_project_uses_inbox(self):
        with patch.dict(os.environ, {"TODOIST_PROJECT_NAME": ""}):
            self.assertIsNone(get_project_id(None, get_project_name()))

    def test_google_uses_tasklist_name_from_environment(self):
        service = FakeGoogleService([])

        with patch.dict(os.environ, {"GOOGLE_TASKLIST_NAME": "Reading List"}), \
                patch("kcls_hold_gtasks.get_tasklist_id", return_value="list") as get_list:
            add_hold_to_google(
                "A Book", "An Author", "Auburn", "Sam", None,
                service=service,
            )

        get_list.assert_called_once_with(service, "Reading List")

    def test_todoist_uses_project_name_from_environment(self):
        added_tasks = []
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
        )

        with patch.dict(os.environ, {"TODOIST_PROJECT_NAME": "Reading List"}), \
                patch("kcls_hold_todoist.get_project_id", return_value="project") as get_project:
            add_hold_to_todoist(
                "A Book", "An Author", "Auburn", "Sam", None,
                api=api,
            )

        get_project.assert_called_once_with(api, "Reading List")

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

    def test_google_completion_follows_next_page_token(self):
        service = FakeGoogleService([], pages=[
            {
                "items": [],
                "nextPageToken": "page-2",
            },
            {
                "items": [{
                    "id": "page-two-hold",
                    "title": "KCLS book pickup: Little Women",
                    "notes": "Account: Sam",
                }],
            },
        ])

        with patch("kcls_hold_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_hold_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("Little Women", "Sam")

        self.assertEqual(service.task_api.completed, ["page-two-hold"])
        self.assertEqual(service.task_api.list_calls, [
            {
                "tasklist": "list",
                "showCompleted": False,
                "maxResults": 100,
            },
            {
                "tasklist": "list",
                "showCompleted": False,
                "maxResults": 100,
                "pageToken": "page-2",
            },
        ])

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

    def test_google_completion_does_not_complete_short_it_hold(self):
        tasks = [{
            "id": "it-hold",
            "title": "KCLS book pickup: It",
            "notes": "Account: Sam",
        }]
        service = FakeGoogleService(tasks)

        with patch("kcls_hold_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_hold_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("Little Women", "Sam")

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

    def test_unparseable_hold_email_is_marked_read(self):
        message = self.make_message("This is not a hold email")
        store = []
        mail = SimpleNamespace(
            store=lambda email_id, *args: store.append(email_id))

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event") as record_event, \
                patch("kcls_parser.add_hold_to_task_manager") as add_hold:
            process_hold_emails(mail, [b"1"], {}, "google")

        self.assertEqual(store, [b"1"])
        record_event.assert_not_called()
        add_hold.assert_not_called()

    def test_unparseable_receipt_is_marked_read(self):
        message = self.make_message("This is not a checkout receipt")
        store = []
        mail = SimpleNamespace(
            store=lambda email_id, *args: store.append(email_id))

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event") as record_event, \
                patch("kcls_parser.add_due_book_to_task_manager") as add_due, \
                patch("kcls_parser.complete_hold_in_task_manager") as complete:
            process_receipt_emails(mail, [b"1"], {}, "google")

        self.assertEqual(store, [b"1"])
        record_event.assert_not_called()
        add_due.assert_not_called()
        complete.assert_not_called()

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
