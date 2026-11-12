import frappe


def execute():
    # Field defaults aren't written to existing Singles on migrate; seed it so
    # applicants keep the Knowledge Base hidden as before this setting existed.
    frappe.db.set_single_value("HD Settings", "hide_knowledge_base_for_applicant", 1)
