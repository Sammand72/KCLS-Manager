import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_secrets import FakeKeyring

from kcls_google_auth import (
    GMAIL_SCOPE,
    TASKS_SCOPE,
    build_scope_list,
    get_google_credentials,
    save_credentials,
)
from kcls_secrets import SERVICE_NAME


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
        project_dir_patch = patch(
            "kcls_google_auth.project_dir", self.directory.name)
        project_dir_patch.start()
        self.addCleanup(project_dir_patch.stop)
        # Default to file mode, even if the real .env says keychain.
        env_patch = patch.dict("os.environ", {"SECRET_STORAGE": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def use_fake_keychain(self):
        """Switch to keychain mode, backed by an in-memory fake keychain."""
        os.environ["SECRET_STORAGE"] = "keychain"
        fake_keyring = FakeKeyring()
        keyring_patch = patch("kcls_secrets.keyring", fake_keyring)
        keyring_patch.start()
        self.addCleanup(keyring_patch.stop)
        return fake_keyring

    def token_file_path(self):
        return os.path.join(self.directory.name, "token.json")

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

    def test_keychain_token_skips_browser(self):
        fake_keyring = self.use_fake_keychain()
        fake_keyring.set_password(
            SERVICE_NAME, "GOOGLE_TOKEN", '{"refresh_token": "r"}')
        saved = make_saved_credentials([GMAIL_SCOPE])

        with (
            patch(
                "kcls_google_auth.Credentials.from_authorized_user_info",
                return_value=saved,
            ) as load_mock,
            patch("kcls_google_auth.InstalledAppFlow") as flow_mock,
        ):
            creds = get_google_credentials(include_gmail=True)

        self.assertIs(creds, saved)
        load_mock.assert_called_once_with({"refresh_token": "r"})
        flow_mock.from_client_secrets_file.assert_not_called()

    def test_keychain_mode_saves_to_keychain_not_file(self):
        fake_keyring = self.use_fake_keychain()

        save_credentials(
            make_saved_credentials([GMAIL_SCOPE]), self.token_file_path())

        self.assertFalse(os.path.exists(self.token_file_path()))
        saved_text = fake_keyring.get_password(SERVICE_NAME, "GOOGLE_TOKEN")
        self.assertEqual(json.loads(saved_text)["refresh_token"],
                         "refresh-token")

    def test_old_token_file_moves_into_keychain(self):
        fake_keyring = self.use_fake_keychain()
        with open(self.token_file_path(), "w") as token_file:
            token_file.write('{"refresh_token": "r"}')
        saved = make_saved_credentials([GMAIL_SCOPE])

        with patch(
            "kcls_google_auth.Credentials.from_authorized_user_info",
            return_value=saved,
        ):
            creds = get_google_credentials(include_gmail=True)

        self.assertIs(creds, saved)
        self.assertFalse(os.path.exists(self.token_file_path()))
        self.assertEqual(
            fake_keyring.get_password(SERVICE_NAME, "GOOGLE_TOKEN"),
            '{"refresh_token": "r"}',
        )

    def test_token_file_is_kept_if_keychain_did_not_save_it(self):
        fake_keyring = self.use_fake_keychain()
        # A keychain that silently forgets everything it is given.
        fake_keyring.set_password = lambda service, name, value: None
        with open(self.token_file_path(), "w") as token_file:
            token_file.write('{"refresh_token": "r"}')

        with patch(
            "kcls_google_auth.Credentials.from_authorized_user_info",
            return_value=make_saved_credentials([GMAIL_SCOPE]),
        ):
            get_google_credentials(include_gmail=True)

        self.assertTrue(os.path.exists(self.token_file_path()))


if __name__ == "__main__":
    unittest.main()
