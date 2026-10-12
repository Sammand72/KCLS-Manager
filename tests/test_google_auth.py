import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kcls_google_auth import (
    GMAIL_SCOPE,
    TASKS_SCOPE,
    build_scope_list,
    get_google_credentials,
)


def make_saved_credentials(scopes, valid=True):
    """Build a stand-in for credentials loaded from token.json."""
    return SimpleNamespace(
        valid=valid,
        expired=not valid,
        refresh_token="refresh-token",
        token="token",
        token_uri="token-uri",
        client_id="client-id",
        client_secret="client-secret",
        scopes=scopes,
    )


class GoogleAuthTests(unittest.TestCase):
    def setUp(self):
        # Every test gets its own empty folder, so no real token.json is used.
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        script_dir_patch = patch(
            "kcls_google_auth.script_dir", self.directory.name)
        script_dir_patch.start()
        self.addCleanup(script_dir_patch.stop)

    def write_token_file(self):
        """Create an empty token.json so the code tries to load it."""
        token_path = os.path.join(self.directory.name, "token.json")
        with open(token_path, "w") as token_file:
            token_file.write("{}")

    def test_scope_list_with_and_without_tasks(self):
        self.assertEqual(build_scope_list(True, False), [GMAIL_SCOPE])
        self.assertEqual(
            build_scope_list(True, True), [GMAIL_SCOPE, TASKS_SCOPE])
        self.assertEqual(build_scope_list(False, True), [TASKS_SCOPE])

    def test_saved_token_with_all_scopes_skips_browser(self):
        self.write_token_file()
        saved = make_saved_credentials([GMAIL_SCOPE, TASKS_SCOPE])

        with (
            patch(
                "kcls_google_auth.Credentials.from_authorized_user_file",
                return_value=saved,
            ),
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
        ):
            creds = get_google_credentials(
                include_gmail=True, include_tasks=True)

        self.assertIs(creds, saved)
        flow_mock.from_client_secrets_file.assert_not_called()

    def test_expired_token_refreshes_without_browser(self):
        self.write_token_file()
        saved = make_saved_credentials([GMAIL_SCOPE], valid=False)

        def refresh(_request):
            saved.valid = True

        saved.refresh = refresh

        with (
            patch(
                "kcls_google_auth.Credentials.from_authorized_user_file",
                return_value=saved,
            ),
            patch("kcls_google_auth.is_interactive", return_value=False),
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
        ):
            creds = get_google_credentials(include_gmail=True)

        self.assertTrue(creds.valid)
        flow_mock.from_client_secrets_file.assert_not_called()

    def test_old_tasks_only_token_asks_to_sign_in_again(self):
        self.write_token_file()
        saved = make_saved_credentials([TASKS_SCOPE])
        new_creds = make_saved_credentials([GMAIL_SCOPE, TASKS_SCOPE])

        with (
            patch(
                "kcls_google_auth.Credentials.from_authorized_user_file",
                return_value=saved,
            ),
            patch("kcls_google_auth.is_interactive", return_value=True),
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
        ):
            flow_mock.from_client_secrets_file.return_value \
                .run_local_server.return_value = new_creds
            creds = get_google_credentials(include_gmail=True)

        self.assertIs(creds, new_creds)
        # The old Tasks permission is kept alongside the new Gmail one.
        requested_scopes = flow_mock.from_client_secrets_file.call_args[0][1]
        self.assertEqual(requested_scopes, [GMAIL_SCOPE, TASKS_SCOPE])

    def test_missing_token_in_noninteractive_run_is_actionable(self):
        with (
            patch("kcls_google_auth.is_interactive", return_value=False),
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
            self.assertRaisesRegex(RuntimeError, "interactive browser login"),
        ):
            get_google_credentials(include_gmail=True)

        flow_mock.from_client_secrets_file.assert_not_called()

    def test_refresh_failure_in_noninteractive_run_is_actionable(self):
        self.write_token_file()
        saved = make_saved_credentials([GMAIL_SCOPE], valid=False)
        saved.refresh = lambda _request: (_ for _ in ()).throw(
            ConnectionError("network unavailable")
        )

        with (
            patch(
                "kcls_google_auth.Credentials.from_authorized_user_file",
                return_value=saved,
            ),
            patch("kcls_google_auth.is_interactive", return_value=False),
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
            self.assertRaisesRegex(RuntimeError, "interactive browser login"),
        ):
            get_google_credentials(include_gmail=True)

        flow_mock.from_client_secrets_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
