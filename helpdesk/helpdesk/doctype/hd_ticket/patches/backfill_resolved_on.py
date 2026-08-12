import frappe


def execute():
    """
    Seed `resolved_on` for tickets that are already sitting in a Resolved
    status when this patch runs, so they participate in auto-close instead of
    being skipped forever for lacking a timestamp. `modified` is the best
    available proxy for when the ticket last changed status (there is no
    historical record of the exact Resolved transition time).
    """
    Ticket = frappe.qb.DocType("HD Ticket")
    (
        frappe.qb.update(Ticket)
        .set(Ticket.resolved_on, Ticket.modified)
        .where(
            (Ticket.status_category == "Resolved") & (Ticket.resolved_on.isnull())
        )
    ).run()
