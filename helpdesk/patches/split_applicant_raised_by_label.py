import frappe

from helpdesk.api.nls_student import NLSAT_APPLICANT_ROLE, PACE_APPLICANT_ROLE
from helpdesk.api.raised_by import (
    NLSAT_APPLICANT_LABEL,
    PACE_APPLICANT_LABEL,
    get_raised_by_context,
)
from helpdesk.setup.raised_by_label import create_default_raised_by_labels

OLD_LABEL = "Applicant"
DT = "HD Raised By Label"


def execute():
    """
    The single "Applicant" label is split into NLSAT Applicant, International
    Applicant and PACE Applicant: repoint the role map rows, relabel existing
    tickets, then drop the old label.
    """
    create_default_raised_by_labels()
    update_role_map()
    relabel_tickets()
    remove_old_label()


def update_role_map():
    """Point role map rows still on "Applicant" at the role's new default label."""
    new_label_by_role = {
        NLSAT_APPLICANT_ROLE: NLSAT_APPLICANT_LABEL,
        PACE_APPLICANT_ROLE: PACE_APPLICANT_LABEL,
    }
    settings = frappe.get_single("HD Settings")
    changed = False
    for row in settings.get("raised_by_role_map") or []:
        if row.label == OLD_LABEL and row.role in new_label_by_role:
            row.label = new_label_by_role[row.role]
            changed = True

    if changed:
        settings.flags.ignore_mandatory = True
        settings.save(ignore_permissions=True)


def relabel_tickets():
    """Recompute the label (only) for tickets still labelled "Applicant"."""
    tickets = frappe.get_all(
        "HD Ticket",
        filters={"custom_raised_by_label": OLD_LABEL},
        fields=["name", "raised_by", "ticket_type"],
    )
    label_by_key = {}
    for ticket in tickets:
        key = (ticket.raised_by, ticket.ticket_type)
        if key not in label_by_key:
            label_by_key[key] = get_raised_by_context(*key).get("raised_by_label")
        label = label_by_key[key]
        if not label or label == OLD_LABEL:
            continue
        frappe.db.set_value(
            "HD Ticket", ticket.name, "custom_raised_by_label", label, update_modified=False
        )


def remove_old_label():
    """Delete "Applicant" once nothing uses it; disable it if something still does."""
    if not frappe.db.exists(DT, OLD_LABEL):
        return

    in_use = frappe.db.exists("HD Ticket", {"custom_raised_by_label": OLD_LABEL}) or frappe.db.exists(
        "HD Raised By Role Map", {"label": OLD_LABEL}
    )
    if in_use:
        frappe.db.set_value(DT, OLD_LABEL, "enabled", 0)
    else:
        frappe.delete_doc(DT, OLD_LABEL, ignore_permissions=True, force=True)
