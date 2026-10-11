import re

UNKNOWN_USER = "unknown user"

# Every task title starts with one of these, so both task managers can tell
# pickup tasks, expired pickups, and due-date tasks apart.
TASK_TITLE_PREFIX = "KCLS book pickup: "
EXPIRED_TASK_TITLE_PREFIX = "EXPIRED KCLS book pickup: "
DUE_TASK_TITLE_PREFIX = "KCLS book due: "


def strip_task_prefix(task_title, prefix):
    """Return the book title after a task prefix, or None if it is missing.

    The prefix check ignores case, so a task the user retyped as
    "kcls book pickup: ..." still counts.
    """
    stripped_title = task_title.strip()
    if not stripped_title.lower().startswith(prefix.lower()):
        return None

    return stripped_title[len(prefix):]


def build_hold_notes(author, location, address, account_user):
    """Build the notes text stored on a pickup task.

    The "Account:" line must stay on its own line: extract_task_user reads
    it back later to match checkouts to the right family member.
    """
    notes = f"Author: {author}\nLocation: {location}"
    if address:
        notes += f"\nAddress: {address}"
    notes += f"\nAccount: {account_user}"
    return notes


def build_due_notes(author, account_user):
    """Build the notes text stored on a due-date task."""
    return f"Author: {author}\nAccount: {account_user}"


def normalize_title(title):
    """Lowercase a title and remove punctuation without dropping Unicode letters."""
    normalized_title = re.sub(r"[^\w ]", " ", title.lower(), flags=re.UNICODE)
    return " ".join(normalized_title.split())


def normalize_user(user):
    """Normalize a task user name for a case-insensitive comparison."""
    return " ".join(user.lower().split())


def extract_task_user(task_text):
    """Read the family member stored after the task's Account label."""
    match = re.search(r"(?:^|\n)Account:\s*(.+)", task_text, re.IGNORECASE)
    if not match:
        return ""

    return match.group(1).strip()


def users_match(checkout_user, task_text):
    """Require a known checkout user to match the task's account user."""
    normalized_checkout_user = normalize_user(checkout_user)
    if not normalized_checkout_user or normalized_checkout_user == UNKNOWN_USER:
        return False

    normalized_task_user = normalize_user(extract_task_user(task_text))
    return bool(normalized_task_user) and normalized_task_user != UNKNOWN_USER and (
        normalized_task_user == normalized_checkout_user
    )


def is_same_task(task_title, task_notes, book_title, account_user):
    """Check whether an open task was already created for this book and user.

    This is stricter than titles_match on purpose: it is used to avoid creating
    a duplicate task when an email is retried, so the title must match exactly
    (after normalizing) and the Account line must name the same user.
    """
    if normalize_title(task_title) != normalize_title(book_title):
        return False

    # Plain equality here, so "Unknown user" tasks can also be recognized.
    task_user = normalize_user(extract_task_user(task_notes))
    return task_user == normalize_user(account_user)


def titles_match(checkout_title, hold_title):
    """Match an exact title or a sufficiently long truncated hold title."""
    normalized_checkout_title = normalize_title(checkout_title)
    normalized_hold_title = normalize_title(hold_title)

    if not normalized_checkout_title or not normalized_hold_title:
        return False

    if normalized_checkout_title == normalized_hold_title:
        return True

    return (
        len(normalized_hold_title) >= 10
        and normalized_checkout_title.startswith(normalized_hold_title)
    )
