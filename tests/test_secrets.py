import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from keyring.errors import KeyringLocked, NoKeyringError, PasswordDeleteError

import kcls_secrets
from kcls_secrets import (
    SERVICE_NAME,
    delete_secret,
    get_secret,
    get_secret_storage,
    set_secret,
    validate_secret_storage,
    wait_for_keychain,
)


class FakeKeyring:
    """An in-memory stand-in for the OS keychain, so tests never touch it."""

    def __init__(self):
        self.saved = {}

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def set_password(self, service, name, value):
        self.saved[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.saved:
            raise PasswordDeleteError("not found")
        del self.saved[(service, name)]


class SecretStorageTests(unittest.TestCase):
    def setUp(self):
        # Swap the real keyring module for the fake one in every test.
        self.fake_keyring = FakeKeyring()
        keyring_patch = patch("kcls_secrets.keyring", self.fake_keyring)
        keyring_patch.start()
        self.addCleanup(keyring_patch.stop)

    def test_blank_setting_means_file(self):
        with patch.dict("os.environ", {"SECRET_STORAGE": " "}):
            self.assertEqual(get_secret_storage(), "file")
        with patch.dict("os.environ", {"SECRET_STORAGE": "KeyChain"}):
            self.assertEqual(get_secret_storage(), "keychain")

    def test_invalid_setting_is_rejected(self):
        validate_secret_storage("file")
        validate_secret_storage("keychain")
        with self.assertRaises(ValueError):
            validate_secret_storage("vault")

    def test_file_mode_reads_environment(self):
        self.fake_keyring.set_password(SERVICE_NAME, "EMAIL_PASS", "keychain")
        with patch.dict("os.environ", {
            "SECRET_STORAGE": "file",
            "EMAIL_PASS": "from env",
        }):
            self.assertEqual(get_secret("EMAIL_PASS"), "from env")

    def test_keychain_mode_reads_keychain_not_environment(self):
        self.fake_keyring.set_password(SERVICE_NAME, "EMAIL_PASS", "keychain")
        with patch.dict("os.environ", {
            "SECRET_STORAGE": "keychain",
            "EMAIL_PASS": "from env",
        }):
            self.assertEqual(get_secret("EMAIL_PASS"), "keychain")
            # A missing entry comes back as '' rather than None.
            self.assertEqual(get_secret("TODOIST_API_TOKEN"), "")

    def test_set_and_delete_secret(self):
        set_secret("TODOIST_API_TOKEN", "abc123")
        self.assertEqual(
            self.fake_keyring.get_password(SERVICE_NAME, "TODOIST_API_TOKEN"),
            "abc123",
        )
        self.assertTrue(delete_secret("TODOIST_API_TOKEN"))
        # Deleting again is not an error; it just reports nothing was there.
        self.assertFalse(delete_secret("TODOIST_API_TOKEN"))

    def test_locked_keychain_gives_helpful_error(self):
        self.fake_keyring.get_password = lambda service, name: (
            _ for _ in ()).throw(KeyringLocked("locked"))
        with (
            patch.dict("os.environ", {"SECRET_STORAGE": "keychain"}),
            self.assertRaisesRegex(RuntimeError, "SECRET_STORAGE=file"),
        ):
            get_secret("EMAIL_PASS")

    def test_status_never_prints_values(self):
        set_secret("TODOIST_API_TOKEN", "super-secret-value")
        output = io.StringIO()
        with (
            patch("kcls_secrets.load_dotenv"),
            patch.dict("os.environ", {"SECRET_STORAGE": "keychain"}),
            redirect_stdout(output),
        ):
            kcls_secrets.main(["status"])

        self.assertIn("todoist-api (TODOIST_API_TOKEN): saved", output.getvalue())
        self.assertIn("email-pass (EMAIL_PASS): not saved", output.getvalue())
        self.assertNotIn("super-secret-value", output.getvalue())

    def test_set_command_saves_hidden_input(self):
        with (
            patch("kcls_secrets.load_dotenv"),
            patch.dict("os.environ", {"SECRET_STORAGE": "keychain"}),
            patch("kcls_secrets.getpass.getpass", return_value=" abc123 "),
            redirect_stdout(io.StringIO()),
        ):
            kcls_secrets.main(["set", "todoist-api"])

        self.assertEqual(
            self.fake_keyring.get_password(SERVICE_NAME, "TODOIST_API_TOKEN"),
            "abc123",
        )


class WaitForKeychainTests(unittest.TestCase):
    def test_retries_until_keychain_appears(self):
        # First try: no keychain yet. Second try: it works.
        fake_keyring = Mock()
        fake_keyring.get_password.side_effect = [NoKeyringError("none"), None]

        with (
            patch("kcls_secrets.keyring", fake_keyring),
            patch("kcls_secrets.time.sleep") as sleep_mock,
        ):
            wait_for_keychain(max_attempts=3, wait_seconds=5)

        sleep_mock.assert_called_once_with(5)
        # keyring was asked to forget its old search and look again.
        fake_keyring.backend.get_all_keyring.reset.assert_called_once()
        fake_keyring.core.init_backend.assert_called_once()

    def test_gives_up_after_max_attempts(self):
        fake_keyring = Mock()
        fake_keyring.get_password.side_effect = NoKeyringError("none")

        with (
            patch("kcls_secrets.keyring", fake_keyring),
            patch("kcls_secrets.time.sleep") as sleep_mock,
            self.assertRaisesRegex(RuntimeError, "Could not reach"),
        ):
            wait_for_keychain(max_attempts=3, wait_seconds=5)

        # No pointless wait after the last attempt.
        self.assertEqual(sleep_mock.call_count, 2)

    def test_single_attempt_does_not_wait(self):
        fake_keyring = Mock()
        fake_keyring.get_password.side_effect = NoKeyringError("none")

        with (
            patch("kcls_secrets.keyring", fake_keyring),
            patch("kcls_secrets.time.sleep") as sleep_mock,
            self.assertRaises(RuntimeError),
        ):
            wait_for_keychain(max_attempts=1)

        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
