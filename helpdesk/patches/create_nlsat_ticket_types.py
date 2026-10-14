import frappe

from helpdesk.setup.team import create_ootb_teams
from helpdesk.setup.ticket_type import (
    create_nls_ticket_types,
    create_nlsat_type_of_issues,
)


def execute():
    # Backfills the NLSAT Domestic / International ticket types, their default
    # Type of Issue options and the two NLSAT teams on sites that already ran
    # the one-shot create_*_if_missing patches. All three are create-if-missing,
    # so existing types, teams and admin-added issues are left untouched. The
    # ticket type → team link is set by the after_migrate sync_ticket_type_teams.
    create_ootb_teams()
    create_nls_ticket_types()
    create_nlsat_type_of_issues()
    frappe.db.commit()
