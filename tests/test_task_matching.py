import os
import unittest
from datetime import UTC, date, datetime
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import patch

from kcls_parser import (
    add_hold_to_task_manager,
    process_checkin_emails,
    process_due_date_emails,
    process_hold_emails,
    process_receipt_emails,
)
from kcls_task_matching import (
    TASK_TITLE_PREFIX,
    build_hold_notes,
    extract_task_user,
    is_same_task,
    strip_task_prefix,
    titles_match,
    users_match,
)
from kcls_tasker_gtasks import (
    add_due_book_to_google,
    add_hold_to_google,
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
    complete_due_book_todoist,
    expire_hold_todoist,
    get_project_id,
    get_project_name,
    mark_hold_complete_todoist,
    update_due_book_todoist,
)


class MatcherTests(unittest.TestCase):
    def test_empty_titles_do_not_match(self):
        self.assertFalse(titles_match(
            checkout_title="", hold_title="Anything"))
        self.assertFalse(titles_match(
            checkout_title="Anything", hold_title=""))

    def test_short_title_does_not_match_inside_longer_hold_title(self):
        self.assertFalse(titles_match(
            checkout_title="It", hold_title="Little Women"))
        self.assertFalse(titles_match(
            checkout_title="Little Women", hold_title="It"))

    def test_short_or_embedded_hold_titles_do_not_match(self):
        self.assertFalse(titles_match(
            checkout_title="Dune Messiah", hold_title="Dune"))
        self.assertTrue(titles_match(
            checkout_title="Dune", hold_title="Dune"))

    def test_cut_off_hold_title_matches_full_checkout_title(self):
        self.assertTrue(titles_match(
            checkout_title=(
                "Everything you need to ace English Language Arts in one big fat "
                "notebook : the complete middle school study guide"),
            hold_title=(
                "Everything You Need To Ace English Language Arts In One Big Fat "
                "Notebook :"),
        ))

    def test_angle_bracket_and_punctuation_are_ignored(self):
        self.assertTrue(titles_match(
            checkout_title=(
                "The social animal : the hidden sources of love, character, "
                "and achievement"),
            hold_title=(
                "The Social Animal : The Hidden Sources Of Love, Character, "
                "And Achievement<"),
        ))

    def test_accents_are_preserved(self):
        self.assertTrue(titles_match(
            checkout_title="Café Society", hold_title="CAFÉ SOCIETY"))
        self.assertFalse(titles_match(
            checkout_title="Cafe Society", hold_title="Café Society"))

    def test_user_matching_requires_same_known_user(self):
        self.assertEqual(extract_task_user(
            "Author: Someone\nAccount: Sam"), "Sam")
        self.assertTrue(users_match("sam", "Account: Sam"))
        self.assertFalse(users_match("Sam", "Account: Alex"))
        self.assertFalse(users_match("Sam", "Author: Someone"))
        self.assertFalse(users_match("Unknown user", "Account: Alex"))

    def test_strip_task_prefix_returns_book_title_or_none(self):
        self.assertEqual(
            strip_task_prefix("KCLS book pickup: A Book", TASK_TITLE_PREFIX),
            "A Book")
        self.assertEqual(
            strip_task_prefix("  kcls BOOK pickup: A Book ", TASK_TITLE_PREFIX),
            "A Book")
        self.assertIsNone(
            strip_task_prefix("Buy milk", TASK_TITLE_PREFIX))
        # Expired pickups have their own prefix and must not count as pickups.
        self.assertIsNone(strip_task_prefix(
            "EXPIRED KCLS book pickup: A Book", TASK_TITLE_PREFIX))

    def test_hold_notes_account_line_can_be_read_back(self):
        notes = build_hold_notes(
            author="An Author", location="Auburn",
            address="1102 Auburn Way S", account_user="Sam")
        self.assertEqual(extract_task_user(notes), "Sam")
        self.assertIn("Address: 1102 Auburn Way S", notes)
        self.assertNotIn(
            "Address:", build_hold_notes("A", "Auburn", "", "Sam"))

    def test_same_task_needs_exact_title_and_same_user(self):
        self.assertTrue(is_same_task(
            "A Book!", "Author: X\nAccount: Sam", "a book", "sam"))
        self.assertTrue(is_same_task(
            "A Book", "Account: Unknown user", "A Book", "Unknown user"))
        self.assertFalse(is_same_task(
            "A Book", "Account: Alex", "A Book", "Sam"))
        # A truncated title is a different task for duplicate checks.
        self.assertFalse(is_same_task(
            "Everything you need to ace", "Account: Sam",
            "Everything you need to ace algebra", "Sam"))


class FakeGoogleTasks:
    def __init__(self, tasks, pages=None):
        self.pending_tasks = tasks
        self.pages = pages
        self.page_index = 0
        self.list_calls = []
        self.completed = []
        self.updated = []

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
        if kwargs["body"].get("status") == "completed":
            self.completed.append(kwargs["task"])
        else:
            self.updated.append(kwargs)
        return SimpleNamespace(execute=dict)

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
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list") as get_list:
            add_hold_to_google(
                "A Book", "An Author", "Auburn", "Sam", None,
                service=service,
            )

        get_list.assert_called_once_with(service, "Reading List")

    def test_todoist_uses_project_name_from_environment(self):
        added_tasks = []
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
            get_tasks=lambda project_id: [[]],
        )

        with patch.dict(os.environ, {"TODOIST_PROJECT_NAME": "Reading List"}), \
                patch("kcls_tasker_todoist.get_project_id", return_value="project") as get_project:
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

    def test_google_hold_is_not_added_twice(self):
        service = FakeGoogleService([{
            "id": "sam-hold",
            "title": "KCLS book pickup: A Book",
            "notes": "Author: An Author\nAccount: Sam",
        }])
        service.task_api.inserted = None

        add_hold_to_google(
            "A Book", "An Author", "Auburn", "Sam", None,
            service=service, target_list_id="list",
        )

        self.assertIsNone(service.task_api.inserted)

    def test_google_hold_for_another_user_is_still_added(self):
        service = FakeGoogleService([{
            "id": "alex-hold",
            "title": "KCLS book pickup: A Book",
            "notes": "Account: Alex",
        }])
        service.task_api.inserted = None

        add_hold_to_google(
            "A Book", "An Author", "Auburn", "Sam", None,
            service=service, target_list_id="list",
        )

        self.assertIsNotNone(service.task_api.inserted)

    def test_google_existing_due_task_gets_new_date_not_duplicate(self):
        service = FakeGoogleService([{
            "id": "sam-due",
            "title": "KCLS book due: A Book",
            "notes": "Author: An Author\nAccount: Sam",
        }])
        service.task_api.inserted = None
        due = datetime(2026, 11, 3, tzinfo=UTC)

        add_due_book_to_google(
            "A Book", "An Author", "Sam", due,
            service=service, target_list_id="list",
        )

        self.assertIsNone(service.task_api.inserted)
        self.assertEqual(service.task_api.updated[0]["task"], "sam-due")
        self.assertEqual(
            service.task_api.updated[0]["body"]["due"], due.isoformat())

    def test_todoist_hold_is_not_added_twice(self):
        added_tasks = []
        task = SimpleNamespace(
            id="sam-hold",
            content="KCLS book pickup: A Book",
            description="Author: An Author\nAccount: Sam",
        )
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
            get_tasks=lambda project_id: [[task]],
        )

        add_hold_to_todoist(
            "A Book", "An Author", "Auburn", "Sam", None,
            api=api, target_project_id="project",
        )

        self.assertEqual(added_tasks, [])

    def test_todoist_existing_due_task_gets_new_date_not_duplicate(self):
        added_tasks = []
        updated_tasks = []
        task = SimpleNamespace(
            id="sam-due",
            content="KCLS book due: A Book",
            description="Author: An Author\nAccount: Sam",
        )
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
            get_tasks=lambda project_id: [[task]],
            update_task=lambda *args, **kwargs: updated_tasks.append(
                (args, kwargs)),
        )

        add_due_book_to_todoist(
            "A Book", "An Author", "Sam",
            datetime(2026, 11, 3, tzinfo=UTC),
            api=api, target_project_id="project",
        )

        self.assertEqual(added_tasks, [])
        self.assertEqual(
            updated_tasks, [(("sam-due",), {"due_date": date(2026, 11, 3)})])

    def test_google_expired_hold_renames_matching_account_task(self):
        service = FakeGoogleService([{
            "id": "sam-hold",
            "title": "KCLS book pickup: A Book",
            "notes": "Account: Sam",
        }])

        expire_hold_google(
            "A Book", "Sam", service=service, target_list_id="list")

        self.assertEqual(
            service.task_api.updated[0]["body"]["title"],
            "EXPIRED KCLS book pickup: A Book",
        )

    def test_todoist_hold_includes_library_address_in_description(self):
        added_tasks = []
        api = SimpleNamespace(
            add_task=lambda **kwargs: added_tasks.append(kwargs),
            get_tasks=lambda project_id: [[]],
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

    def test_todoist_expired_hold_renames_matching_account_task(self):
        updated_tasks = []
        task = SimpleNamespace(
            id="sam-hold",
            content="KCLS book pickup: A Book",
            description="Account: Sam",
        )
        api = SimpleNamespace(
            get_tasks=lambda project_id: [[task]],
            update_task=lambda *args, **kwargs: updated_tasks.append(
                (args, kwargs)),
        )

        expire_hold_todoist(
            "A Book", "Sam", api=api, target_project_id="project")

        self.assertEqual(
            updated_tasks[0][1]["content"],
            "EXPIRED KCLS book pickup: A Book",
        )

    def test_expired_hold_does_not_rename_another_account_task(self):
        task = SimpleNamespace(
            id="alex-hold",
            content="KCLS book pickup: A Book",
            description="Account: Alex",
        )
        updated_tasks = []
        api = SimpleNamespace(
            get_tasks=lambda project_id: [[task]],
            update_task=lambda *args, **kwargs: updated_tasks.append(
                (args, kwargs)),
        )

        expire_hold_todoist(
            "A Book", "Sam", api=api, target_project_id="project")

        self.assertEqual(updated_tasks, [])

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

        with patch("kcls_tasker_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("Little Women", "Sam")

        self.assertEqual(service.task_api.completed, ["right-user"])

    def test_google_due_date_update_uses_title_and_user_matching(self):
        tasks = [
            {
                "id": "wrong-user",
                "title": "KCLS book due: A Very Long Book",
                "notes": "Account: Alex",
            },
            {
                "id": "right-user",
                "title": "KCLS book due: A Very Long Book Full Edition",
                "notes": "Account: Sam",
            },
        ]
        service = FakeGoogleService(tasks)
        from datetime import datetime

        update_due_book_google(
            "A Very Long Book", "Sam", datetime(2026, 10, 15),
            service=service, target_list_id="list")

        self.assertEqual(service.task_api.updated[0]["task"], "right-user")
        self.assertEqual(
            service.task_api.updated[0]["body"]["due"],
            "2026-10-15T00:00:00",
        )

    def test_todoist_due_date_update_uses_title_and_user_matching(self):
        from datetime import datetime

        tasks = [
            SimpleNamespace(
                id="right-user",
                content="KCLS book due: A Very Long Book Full Edition",
                description="Account: Sam",
            ),
        ]
        updates = []
        api = SimpleNamespace(
            get_tasks=lambda project_id: [tasks],
            update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        )

        update_due_book_todoist(
            "A Very Long Book", "Sam", datetime(2026, 10, 15),
            api=api, target_project_id="project")

        self.assertEqual(updates[0][0], ("right-user",))
        self.assertEqual(updates[0][1]["due_date"].isoformat(), "2026-10-15")

    def test_google_checkin_completes_matching_due_task_only(self):
        tasks = [
            {
                "id": "pickup",
                "title": "KCLS book pickup: The Mark of Athena",
                "notes": "Account: Sam",
            },
            {
                "id": "due",
                "title": "KCLS book due: The Mark of Athena",
                "notes": "Author: Rick Riordan\nAccount: Sam",
            },
        ]
        service = FakeGoogleService(tasks)

        complete_due_book_google(
            "The mark of Athena",
            service=service, target_list_id="list")

        self.assertEqual(service.task_api.completed, ["due"])

    def test_todoist_checkin_completes_matching_due_task(self):
        task = SimpleNamespace(
            id="due",
            content="KCLS book due: The Mark of Athena",
            description="Account: Sam",
        )
        completed = []
        api = SimpleNamespace(
            get_tasks=lambda project_id: [[task]],
            complete_task=lambda task_id: completed.append(task_id),
        )

        complete_due_book_todoist(
            "The mark of Athena",
            api=api, target_project_id="project")

        self.assertEqual(completed, ["due"])

    def test_google_checkin_prefers_due_task_for_same_user(self):
        tasks = [
            {
                "id": "other-user",
                "title": "KCLS book due: The Mark of Athena",
                "notes": "Author: Rick Riordan\nAccount: Alex",
            },
            {
                "id": "same-user",
                "title": "KCLS book due: The Mark of Athena",
                "notes": "Author: Rick Riordan\nAccount: Sam",
            },
        ]
        service = FakeGoogleService(tasks)

        complete_due_book_google(
            "The mark of Athena", "Sam",
            service=service, target_list_id="list")

        self.assertEqual(service.task_api.completed, ["same-user"])

    def test_google_checkin_falls_back_to_title_for_shared_email(self):
        tasks = [
            {
                "id": "due",
                "title": "KCLS book due: The Mark of Athena",
                "notes": "Author: Rick Riordan\nAccount: Carol",
            },
        ]
        service = FakeGoogleService(tasks)

        # "Family" is the label of a shared email address, so it won't
        # match Carol's task, but the book should still be completed.
        complete_due_book_google(
            "The mark of Athena", "Family",
            service=service, target_list_id="list")

        self.assertEqual(service.task_api.completed, ["due"])

    def test_todoist_checkin_prefers_due_task_for_same_user(self):
        tasks = [
            SimpleNamespace(
                id="other-user",
                content="KCLS book due: The Mark of Athena",
                description="Account: Alex",
            ),
            SimpleNamespace(
                id="same-user",
                content="KCLS book due: The Mark of Athena",
                description="Account: Sam",
            ),
        ]
        completed = []
        api = SimpleNamespace(
            get_tasks=lambda project_id: [tasks],
            complete_task=lambda task_id: completed.append(task_id),
        )

        complete_due_book_todoist(
            "The mark of Athena", "Sam",
            api=api, target_project_id="project")

        self.assertEqual(completed, ["same-user"])

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

        with patch("kcls_tasker_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list"):
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

        with patch("kcls_tasker_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list"):
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

        with patch("kcls_tasker_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list"):
            mark_hold_complete_google("Little Women", "Sam")

        self.assertEqual(service.task_api.completed, [])

    def test_google_completion_ignores_expired_hold_tasks(self):
        tasks = [{
            "id": "expired-hold",
            "title": "EXPIRED KCLS book pickup: Little Women",
            "notes": "Account: Sam",
        }]
        service = FakeGoogleService(tasks)

        with patch("kcls_tasker_gtasks.authenticate_google_tasks", return_value=service), \
                patch("kcls_tasker_gtasks.get_tasklist_id", return_value="list"):
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

        with patch("kcls_tasker_todoist.authenticate_todoist", return_value=api), \
                patch("kcls_tasker_todoist.get_project_id", return_value="project"):
            mark_hold_complete_todoist("Little Women", "Sam")

        self.assertEqual(completed, ["right-user"])

    def make_message(self, body, subject="Test KCLS email"):
        message = EmailMessage()
        message["Subject"] = subject
        message.set_content(body)
        return message

    def test_expired_hold_is_created_for_the_correct_account(self):
        message = self.make_message(
            "Account:\n123\nTitle\nA Book\nAuthor\nAn Author\n"
            "Pickup Location\nLibrary\n",
            subject="Your Hold Has Expired at King County Library System",
        )
        mail = SimpleNamespace(store=lambda *args: None)

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.record_library_event") as record_event, \
                patch("kcls_parser.add_hold_to_task_manager") as add_hold:
            process_hold_emails(mail, [b"1"], {"123": "Sam"}, "google")

        add_hold.assert_called_once()
        self.assertTrue(add_hold.call_args.args[1]["expired"])
        record_event.assert_called_once_with(
            "hold_expired",
            {
                "title": "A Book",
                "author": "An Author",
                "account_number": "123",
                "user": "Sam",
                "pickup_location": "Library",
                "pickup_by": None,
            },
        )

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

    def test_due_date_email_updates_existing_task(self):
        message = self.make_message(
            "Account:\n123\nTitle\nA Book\nDue Date\n"
            "October 15, 2026\n")
        mail = SimpleNamespace(store=lambda *args: None)

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.update_due_book_in_task_manager") as update:
            process_due_date_emails(mail, [b"1"], {"123": "Sam"}, "google")

        update.assert_called_once()
        self.assertEqual(update.call_args.args[1]["title"], "A Book")
        self.assertEqual(update.call_args.args[1]["user"], "Sam")

    def test_checkin_email_completes_each_returned_book(self):
        message = self.make_message(
            "Checkin\n-------------------------\n"
            "The Mark of Athena / Rick Riordan.\n"
            "31000053765882\n-------------------------\n"
            "Forest Of Shadows\n31000000000000\n"
            "-------------------------\nNumber of checked in items 2"
        )
        message["To"] = "sam@example.com"
        mail = SimpleNamespace(store=lambda *args: None)

        with patch("kcls_parser.fetch_message", return_value=message), \
                patch("kcls_parser.complete_due_book_in_task_manager") as complete:
            process_checkin_emails(
                mail, [b"1"], {"sam@example.com": "Sam"}, "google")

        self.assertEqual(complete.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in complete.call_args_list],
            ["The Mark of Athena", "Forest Of Shadows"],
        )
        # The recipient email address tells us who returned the books.
        self.assertEqual(
            [call.args[3] for call in complete.call_args_list],
            ["Sam", "Sam"],
        )

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
