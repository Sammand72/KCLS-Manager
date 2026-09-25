# Python3 code to take the hold details from kcls_parser.py into Todoist

import os
import logging
from todoist_api_python.api import TodoistAPI
from kcls_task_matching import titles_match, users_match

# Every task has this in front of the book title, so it has to be removed before comparing with checkout email
TASK_TITLE_PREFIX = "KCLS book pickup: "
DUE_TASK_TITLE_PREFIX = "KCLS book due: "
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
        f"Warning: Could not find a project named '{target_name}'. Using Todoist's default Inbox.")
    return None


def add_hold_to_todoist(
    book_title, author, location, account_user, deadline_datetime,
        api=None, target_project_id=None, address=""):
    """Create a Todoist task for a library hold."""
    if api is None:
        api = authenticate_todoist()

    if target_project_id is None:
        target_project_id = get_project_id(api, get_project_name())

    # .date() chops the time part off the datetime
    pickup_date = None
    if deadline_datetime is not None:
        pickup_date = deadline_datetime.date()

    description = f"Author: {author}\nLocation: {location}"
    if address:
        description += f"\nAddress: {address}"
    description += f"\nAccount: {account_user}"

    # Push the task to the selected project, or Todoist's Inbox if no project was selected.
    TRACER_LOGGER.info("Pushing '%s' to Todoist", book_title)
    api.add_task(
        content=f"{TASK_TITLE_PREFIX}{book_title}",
        description=description,
        project_id=target_project_id,
        due_date=pickup_date
    )
    TRACER_LOGGER.info("Todoist hold created successfully")


def add_due_book_to_todoist(
        book_title, author, account_user, due_datetime, api=None,
        target_project_id=None):
    """Create a Todoist task for a book's checkout due date."""
    if api is None:
        api = authenticate_todoist()

    if target_project_id is None:
        target_project_id = get_project_id(api, get_project_name())

    due_date = None
    if due_datetime is not None:
        due_date = due_datetime.date()

    TRACER_LOGGER.info("Pushing '%s' due date to Todoist", book_title)
    api.add_task(
        content=f"{DUE_TASK_TITLE_PREFIX}{book_title}",
        description=f"Author: {author}\nAccount: {account_user}",
        project_id=target_project_id,
        due_date=due_date
    )
    TRACER_LOGGER.info("Todoist due-date task created successfully")


def mark_hold_complete_todoist(
        book_title, checkout_user="Unknown user", api=None,
        target_project_id=None):
    """Find a matching open pickup task and mark it complete."""
    if api is None:
        api = authenticate_todoist()

    if target_project_id is None:
        target_project_id = get_project_id(api, get_project_name())

    # get_tasks() returns pages of tasks, so we have to loop through each page
    for task_page in api.get_tasks(project_id=target_project_id):
        for task in task_page:
            if not task.content.lower().strip().startswith(
                    TASK_TITLE_PREFIX.lower()):
                continue

            # Remove the pickup prefix before comparing titles.
            task_title = task.content.lower().strip().removeprefix(
                TASK_TITLE_PREFIX.lower())

            if titles_match(book_title, task_title) and users_match(
                    checkout_user, getattr(task, 'description', '')):
                api.complete_task(task.id)
                TRACER_LOGGER.info(
                    "Marked '%s' complete in Todoist", task.content)
                return

    TRACER_LOGGER.info(
        f"No matching Todoist hold task found for '{book_title}' (probably wasn't on hold).")
