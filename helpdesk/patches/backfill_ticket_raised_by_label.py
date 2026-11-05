import frappe
from frappe.utils.fixtures import sync_fixtures

from helpdesk.api.raised_by import get_raised_by_context


def execute():
    # custom_raised_by_label ships as a fixture, and fixtures sync after
    # patches — pull them in now so the column exists on the first migrate.
    if not frappe.db.has_column("HD Ticket", "custom_raised_by_label"):
        sync_fixtures("helpdesk")

    tickets = frappe.get_all(
        "HD Ticket",
        filters={"custom_raised_by_label": ["is", "not set"], "raised_by": ["is", "set"]},
        fields=["name", "raised_by", "custom_student_name", "custom_student_id"],
    )

    context_by_requester = {}
    for ticket in tickets:
        if ticket.raised_by not in context_by_requester:
            context_by_requester[ticket.raised_by] = get_raised_by_context(ticket.raised_by)
        ctx = context_by_requester[ticket.raised_by]
        if not ctx:
            continue

        values = {"custom_raised_by_label": ctx["raised_by_label"]}
        # Keep what was captured at creation; only fill gaps (e.g. Faculty
        # tickets, which never had Name / ID before).
        if not ticket.custom_student_name:
            values["custom_student_name"] = ctx.get("custom_student_name") or ""
        if not ticket.custom_student_id:
            values["custom_student_id"] = ctx.get("custom_student_id") or ""
        frappe.db.set_value("HD Ticket", ticket.name, values, update_modified=False)
