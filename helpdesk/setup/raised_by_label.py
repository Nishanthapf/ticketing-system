import frappe

from helpdesk.api.raised_by import DEFAULT_LABELS, DEFAULT_ROLE_MAP

DT = "HD Raised By Label"


# Seeded in code (create-if-missing), not as a fixture: fixture import force-
# overwrites the whole doc on every migrate, which would undo admin edits to
# labels and, for HD Settings, wipe the whole role map.
def create_default_raised_by_labels():
    for label_name, data_source in DEFAULT_LABELS.items():
        if frappe.db.exists(DT, label_name):
            continue
        doc = frappe.new_doc(DT)
        doc.label_name = label_name
        doc.data_source = data_source
        doc.insert(ignore_permissions=True)


def create_default_raised_by_role_map():
    """Fill HD Settings' role map with the defaults, only if admins haven't set one up."""
    settings = frappe.get_single("HD Settings")
    if settings.get("raised_by_role_map"):
        return

    for role, label in DEFAULT_ROLE_MAP:
        # slcm roles don't exist on sites without slcm installed
        if frappe.db.exists("Role", role) and frappe.db.exists(DT, label):
            settings.append("raised_by_role_map", {"role": role, "label": label})

    if settings.get("raised_by_role_map"):
        settings.flags.ignore_mandatory = True
        settings.save(ignore_permissions=True)
