# Python3 code to take the hold details from kcls_hold_parser.py into Todoist

import os
from todoist_api_python.api import TodoistAPI


def authenticate_todoist():
    token = os.getenv('TODOIST_API_TOKEN')
    return TodoistAPI(token)


def get_project_id(api, target_name):
    # get_projects() returns pages of projects, so we have to loop through each page
    for project_page in api.get_projects():
        for project in project_page:
            if project.name == target_name:
                return project.id  # We found the matching project!

    print(
        f"Warning: Could not find a project named '{target_name}'. Using Todoist's default Inbox.")
    return None


def add_hold_to_todoist(book_title, author, location, account_user, deadline_datetime):
    # Connect to Todoist
    api = authenticate_todoist()

    target_project_id = get_project_id(api, "KCLS Stuff")

    # Push the task to the KCLS Stuff project (or Inbox if not found)
    print(f"\nPushing '{book_title}' to Todoist...")
    api.add_task(
        content=f"KCLS book pickup: {book_title}",
        description=f"Author: {author}\nLocation: {location}\nAccount: {account_user}",
        project_id=target_project_id,
        due_datetime=deadline_datetime
    )
    print("Success!")


def mark_hold_complete_todoist(book_title):
    # Connect to Todoist
    api = authenticate_todoist()

    target_project_id = get_project_id(api, "KCLS Stuff")

    # Lowercase + strip so small formatting differences don't break the match
    normalized_checkout_title = book_title.lower().strip()

    # get_tasks() returns pages of tasks, so we have to loop through each page
    for task_page in api.get_tasks(project_id=target_project_id):
        for task in task_page:
            normalized_task_content = task.content.lower().strip()

            # Bidirectional substring check: the checkout receipt's title may
            # include a subtitle (after a colon) that the hold task doesn't, or vice versa
            if normalized_checkout_title in normalized_task_content or normalized_task_content in normalized_checkout_title:
                api.complete_task(task.id)
                print(f"Marked '{task.content}' complete in Todoist\n")
                return

    print(
        f"No matching Todoist hold task found for '{book_title}' (probably wasn't on hold).")
