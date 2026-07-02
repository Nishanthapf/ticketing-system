from helpdesk.setup.ticket_type import create_nls_ticket_types


def execute():
    # hd_ticket_type.json fixture was removed because fixture import force-
    # overwrites the whole doc on every migrate, wiping admin-managed child
    # tables (year_wise_assignment_rules, pace_year_wise_assignment_rules).
    # This patch preserves create-if-missing seeding for sites that already
    # went through the old fixture-based install.
    create_nls_ticket_types()
