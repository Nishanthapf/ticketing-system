from helpdesk.setup.escalation_email_templates import (
    create_escalation_email_templates_if_missing,
)


def execute():
    # Create-if-missing, same reasoning as create_ootb_teams_if_missing: this
    # provisions the 6 default per-level escalation Email Templates for sites
    # that installed Helpdesk before Ticket Escalation existed. Not a fixture
    # so an admin's later edits to these templates are never overwritten.
    create_escalation_email_templates_if_missing()
