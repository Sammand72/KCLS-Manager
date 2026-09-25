import re


UNKNOWN_USER = "unknown user"


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
    """Require equal known users, while allowing an unknown checkout user."""
    normalized_checkout_user = normalize_user(checkout_user)
    if not normalized_checkout_user or normalized_checkout_user == UNKNOWN_USER:
        return True

    normalized_task_user = normalize_user(extract_task_user(task_text))
    return bool(normalized_task_user) and normalized_task_user != UNKNOWN_USER and (
        normalized_task_user == normalized_checkout_user
    )


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
