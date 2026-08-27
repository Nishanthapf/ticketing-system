from helpdesk.setup.ticket_type import create_nls_ticket_types


def execute():
    # create_nls_ticket_types_if_missing already ran (and is a one-shot patch,
    # skipped on repeat migrate) before "Transcript Request" was added to
    # NLS_TICKET_TYPES, so sites that migrated past it never got the new
    # type seeded. This patch re-runs the same create-if-missing seeding to
    # backfill it (and any other type added the same way in the future)
    # without touching ticket types that already exist.
    create_nls_ticket_types()
