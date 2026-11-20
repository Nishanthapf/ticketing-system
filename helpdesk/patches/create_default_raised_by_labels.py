from helpdesk.setup.raised_by_label import (
    create_default_raised_by_labels,
    create_default_raised_by_role_map,
)


def execute():
    create_default_raised_by_labels()
    create_default_raised_by_role_map()
