# Python3 code to take the hold details from kcls_parser.py into Todoist

import logging
import os

from todoist_api_python.api import TodoistAPI

from kcls_task_matching import (
    DUE_TASK_TITLE_PREFIX,
    EXPIRED_TASK_TITLE_PREFIX,
    TASK_TITLE_PREFIX,
    build_due_notes,
    build_hold_notes,
    is_same_task,
    strip_task_prefix,
    titles_match,
    users_match,
)

TRACER_LOGGER = logging.getLogger("kcls.tracer")


def get_project_name():
    """Get the Todoist project name from the environment."""
    return os.getenv('TODOIST_PROJECT_NAME', '').strip()


def authenticate_todoist():
    """Create a Todoist API client using the token from the environment."""
    token = os.getenv('TODOIST_API_TOKEN')
    return TodoistAPI(token)


def get_project_id(api, target_name):
    """Find a Todoist project by name, or use Todoist's Inbox."""
    if not target_name:
        return None

    # get_projects() returns pages of projects, so we have to loop through each page
    for project_page in api.get_projects():
        for project in project_page:
            if project.name == target_name:
                return project.id  # We found the matching project!

    TRACER_LOGGER.warning(
        "Warning: Could not find a project named '%s'. "
        "Using Todoist's default Inbox.",
        target_name,
    )
    return None


def resolve_todoist_target(api, target_project_id):
    """Fill in the Todoist client and project ID when a caller didn't pass them.

    kcls_parser.py normally passes both (so we only look up the project once
    per run), but each task function can still be called on its own.
    """
    if api is None:
        api = authenticate_todoist()

    if target_project_id is None:
        target_project_id = get_project_id(api, get_project_name())

    return api, target_project_id


def iter_open_todoist_tasks(api, target_project_id):
    """Go through every open task in the project, one task at a time.

    get_tasks() hands back pages (lists) of tasks. Because this function uses
    `yield`, it is a generator: a `for` loop over it sees single tasks and
    never has to deal with the pages itself.
    """
    for task_page in api.get_tasks(project_id=target_project_id):
        # `yield from` hands out each task in this page, one at a time.
        yield from task_page


def find_open_task(
        api, target_project_id, title_prefix, book_title, account_user):
    """Return an open task already created for this book and user, or None.

    Used before adding, so an email that is retried after a failure
    doesn't create a second copy of a task that was already added.
    """
    for task in iter_open_todoist_tasks(api, target_project_id):
        task_title = strip_task_prefix(task.content, title_prefix)
        if task_title is None:
            continue

        if is_same_task(
                task_title=task_title,
                task_notes=getattr(task, 'description', ''),
                book_title=book_title,
                account_user=account_user):
            return task

    return None


def add_hold_to_todoist(
    book_title, author, location, account_user, deadline_datetime,
        api=None, target_project_id=None, address=""):
    """Create a Todoist task for a library hold."""
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    # Skip the add if this hold already has an open pickup task.
    existing_task = find_open_task(
        api=api,
        target_project_id=target_project_id,
        title_prefix=TASK_TITLE_PREFIX,
        book_title=book_title,
        account_user=account_user,
    )
    if existing_task is not None:
        TRACER_LOGGER.info(
            "Todoist hold for '%s' already exists; not adding it again",
            book_title,
        )
        return

    # .date() chops the time part off the datetime
    pickup_date = None
    if deadline_datetime is not None:
        pickup_date = deadline_datetime.date()

    # Push the task to the selected project, or Todoist's Inbox if no project was selected.
    TRACER_LOGGER.info("Pushing '%s' to Todoist", book_title)
    api.add_task(
        content=f"{TASK_TITLE_PREFIX}{book_title}",
        description=build_hold_notes(
            author=author,
            location=location,
            address=address,
            account_user=account_user,
        ),
        project_id=target_project_id,
        due_date=pickup_date
    )
    TRACER_LOGGER.info("Todoist hold created successfully")


def expire_hold_todoist(
        book_title, account_user, api=None, target_project_id=None):
    """Rename the matching normal pickup task instead of creating a task."""
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    for task in iter_open_todoist_tasks(api, target_project_id):
        task_title = strip_task_prefix(task.content, TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=book_title,
                hold_title=task_title) and users_match(
                account_user, getattr(task, 'description', '')):
            expired_title = f"{EXPIRED_TASK_TITLE_PREFIX}{task_title}"
            api.update_task(task.id, content=expired_title)
            TRACER_LOGGER.info(
                "Renamed '%s' to '%s' in Todoist", task.content,
                expired_title)
            return


def add_due_book_to_todoist(
        book_title, author, account_user, due_datetime, api=None,
        target_project_id=None):
    """Create a Todoist task for a book's checkout due date."""
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    due_date = None
    if due_datetime is not None:
        due_date = due_datetime.date()

    # If this book already has an open due task (a retried email, or the
    # same book checked out again), just refresh its due date instead.
    existing_task = find_open_task(
        api=api,
        target_project_id=target_project_id,
        title_prefix=DUE_TASK_TITLE_PREFIX,
        book_title=book_title,
        account_user=account_user,
    )
    if existing_task is not None:
        if due_date is not None:
            api.update_task(existing_task.id, due_date=due_date)
        TRACER_LOGGER.info(
            "Todoist due task for '%s' already exists; "
            "updated its due date instead of adding it again",
            book_title,
        )
        return

    TRACER_LOGGER.info("Pushing '%s' due date to Todoist", book_title)
    api.add_task(
        content=f"{DUE_TASK_TITLE_PREFIX}{book_title}",
        description=build_due_notes(author=author, account_user=account_user),
        project_id=target_project_id,
        due_date=due_date
    )
    TRACER_LOGGER.info("Todoist due-date task created successfully")


def update_due_book_todoist(
        book_title, account_user, due_datetime, api=None,
        target_project_id=None):
    """Update the matching open Todoist due-date task."""
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    for task in iter_open_todoist_tasks(api, target_project_id):
        task_title = strip_task_prefix(task.content, DUE_TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if not titles_match(
                checkout_title=task_title,
                hold_title=book_title):
            continue
        if account_user != "Unknown user" and not users_match(
                account_user, getattr(task, 'description', '')):
            continue

        api.update_task(
            task.id,
            due_date=due_datetime.date(),
        )
        TRACER_LOGGER.info(
            "Updated due date for '%s' in Todoist", task.content)
        return

    TRACER_LOGGER.info(
        "No matching Todoist due task found to renew for '%s'", book_title)


def mark_hold_complete_todoist(
        book_title, checkout_user="Unknown user", api=None,
        target_project_id=None):
    """Find a matching open pickup task and mark it complete."""
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    for task in iter_open_todoist_tasks(api, target_project_id):
        # Remove the pickup prefix before comparing titles.
        task_title = strip_task_prefix(task.content, TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=book_title,
                hold_title=task_title) and users_match(
                checkout_user, getattr(task, 'description', '')):
            api.complete_task(task.id)
            TRACER_LOGGER.info(
                "Marked '%s' complete in Todoist", task.content)
            return

    TRACER_LOGGER.info(
        "No matching Todoist hold task found for '%s' "
        "(probably wasn't on hold).",
        book_title,
    )


def complete_due_book_todoist(
        book_title, account_user="Unknown user", api=None,
        target_project_id=None):
    """Find a matching open due-date task and mark it complete.

    A task for the same account user is preferred, but any task with a
    matching title is used as a fallback, because a shared email address
    can't always tell family members apart.
    """
    api, target_project_id = resolve_todoist_target(api, target_project_id)

    title_matches = []
    for task in iter_open_todoist_tasks(api, target_project_id):
        task_title = strip_task_prefix(task.content, DUE_TASK_TITLE_PREFIX)
        if task_title is None:
            continue

        if titles_match(
                checkout_title=task_title,
                hold_title=book_title):
            title_matches.append(task)

    # next() returns the first task for this user, or None if there isn't one.
    task_to_complete = next(
        (
            task for task in title_matches
            if users_match(account_user, getattr(task, 'description', ''))
        ),
        None,
    )
    if task_to_complete is None and title_matches:
        task_to_complete = title_matches[0]

    if task_to_complete is not None:
        api.complete_task(task_to_complete.id)
        TRACER_LOGGER.info(
            "Marked returned book '%s' complete in Todoist",
            task_to_complete.content)
        return

    TRACER_LOGGER.info(
        "No matching Todoist due task found for returned book '%s'",
        book_title,
    )
