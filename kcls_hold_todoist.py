# Python3 code to take the hold details from kcls_parser.py into Todoist

import os
import logging
from todoist_api_python.api import TodoistAPI

# Every task has this in front of the book title, so it has to be removed before comparing with checkout email
TASK_TITLE_PREFIX = "KCLS book pickup: "
DUE_TASK_TITLE_PREFIX = "KCLS book due: "
TRACER_LOGGER = logging.getLogger("kcls.tracer")


def authenticate_todoist():
    token = os.getenv('TODOIST_API_TOKEN')
    return TodoistAPI(token)


def get_project_id(api, target_name):
    # get_projects() returns pages of projects, so we have to loop through each page
    for project_page in api.get_projects():
        for project in project_page:
            if project.name == target_name:
                return project.id  # We found the matching project!

    TRACER_LOGGER.warning(
        f"Warning: Could not find a project named '{target_name}'. Using Todoist's default Inbox.")
    return None


def add_hold_to_todoist(book_title, author, location, account_user, deadline_datetime):
    # Connect to Todoist
    api = authenticate_todoist()

    target_project_id = get_project_id(api, "KCLS Stuff")

    # .date() chops the time part off the datetime
    pickup_date = None
    if deadline_datetime is not None:
        pickup_date = deadline_datetime.date()

    # Push the task to the KCLS Stuff project (or Inbox if not found)
    TRACER_LOGGER.info("Pushing '%s' to Todoist", book_title)
    api.add_task(
        content=f"{TASK_TITLE_PREFIX}{book_title}",
        description=f"Author: {author}\nLocation: {location}\nAccount: {account_user}",
        project_id=target_project_id,
        due_date=pickup_date
    )
    TRACER_LOGGER.info("Todoist hold created successfully")


def add_due_book_to_todoist(book_title, author, account_user, due_datetime):
    # Connect to Todoist
    api = authenticate_todoist()

    target_project_id = get_project_id(api, "KCLS Stuff")

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


def mark_hold_complete_todoist(book_title):
    # Connect to Todoist
    api = authenticate_todoist()

    target_project_id = get_project_id(api, "KCLS Stuff")

    # Lowercase + strip so small formatting differences don't break the match
    normalized_checkout_title = book_title.lower().strip()

    # get_tasks() returns pages of tasks, so we have to loop through each page
    for task_page in api.get_tasks(project_id=target_project_id):
        for task in task_page:
            if not task.content.lower().strip().startswith(
                    TASK_TITLE_PREFIX.lower()):
                continue

            # removeprefix drops "KCLS book pickup: " off the front
            normalized_task_content = task.content.lower().strip().removeprefix(
                TASK_TITLE_PREFIX.lower())

            # Bidirectional substring check: checkout email may contain a subtitle that is cut-off
            if normalized_checkout_title in normalized_task_content or normalized_task_content in normalized_checkout_title:
                api.complete_task(task.id)
                TRACER_LOGGER.info(
                    "Marked '%s' complete in Todoist", task.content)
                return

    TRACER_LOGGER.info(
        f"No matching Todoist hold task found for '{book_title}' (probably wasn't on hold).")
