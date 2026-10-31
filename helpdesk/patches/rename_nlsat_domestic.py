import frappe

OLD_TICKET_TYPE = "NLSAT Domestic"
NEW_TICKET_TYPE = "NLSAT"
OLD_TEAM = "NLSAT Team - Domestic"
NEW_TEAM = "NLSAT Team"


def execute():
    # The domestic NLSAT track is just "NLSAT" — only International is
    # qualified. rename_doc updates every Link to the renamed docs (HD Ticket
    # ticket_type / agent_group / custom_type_of_issue, HD Ticket Type.team, ...).
    rename_ticket_type()
    rename_team()
    frappe.db.commit()


def rename_ticket_type():
    if not _can_rename("HD Ticket Type", OLD_TICKET_TYPE, NEW_TICKET_TYPE):
        return

    frappe.rename_doc(
        "HD Ticket Type", OLD_TICKET_TYPE, NEW_TICKET_TYPE, force=True, show_alert=False
    )

    # Type Of Issue docnames are autonamed "{ticket_type}-{issue_name}", so they
    # don't follow the ticket type rename on their own — and the "NLSAT-Other"
    # checks in the form script and custom field depends_on rely on them.
    if not frappe.db.table_exists("HD Ticket Type Of Issue"):
        return

    issues = frappe.get_all(
        "HD Ticket Type Of Issue",
        filters={"ticket_type": NEW_TICKET_TYPE},
        fields=["name", "issue_name"],
    )
    for issue in issues:
        new_name = f"{NEW_TICKET_TYPE}-{issue.issue_name}"
        if _can_rename("HD Ticket Type Of Issue", issue.name, new_name):
            frappe.rename_doc(
                "HD Ticket Type Of Issue",
                issue.name,
                new_name,
                force=True,
                show_alert=False,
            )


def rename_team():
    if not _can_rename("HD Team", OLD_TEAM, NEW_TEAM):
        return

    # HDTeam.after_rename rewrites the assignment rule's conditions to the new
    # team name; the rule's own name is renamed here so it doesn't keep
    # saying "Domestic".
    frappe.rename_doc("HD Team", OLD_TEAM, NEW_TEAM, force=True, show_alert=False)

    assignment_rule = frappe.db.get_value("HD Team", NEW_TEAM, "assignment_rule")
    new_rule = f"{NEW_TEAM} - Support Rotation"
    if assignment_rule and _can_rename("Assignment Rule", assignment_rule, new_rule):
        frappe.rename_doc(
            "Assignment Rule", assignment_rule, new_rule, force=True, show_alert=False
        )


def _can_rename(doctype: str, old: str, new: str) -> bool:
    return (
        old != new
        and bool(frappe.db.exists(doctype, old))
        and not frappe.db.exists(doctype, new)
    )
