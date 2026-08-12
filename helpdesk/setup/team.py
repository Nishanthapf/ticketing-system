import frappe

DT = "HD Team"

# Department/support teams, seeded once on fresh install only (create-if-missing).
# Deliberately NOT a fixture: HD Team has admin-managed fields (ticket escalation
# config: enable_ticket_escalation, escalation_levels) that fixture import would
# wipe on every `bench migrate` / Frappe Cloud build, since fixture sync force-
# overwrites the whole doc regardless of the app's hooks.py fixtures list.
OOTB_TEAMS = [
    "PACE Team - Hellen",
    "PACE Team - Pratibha",
    "IT Team",
    "Academics Team",
    "Facilities Team",
    "Finance Team",
    "Food and Beverage Team",
    "Library Team",
    "Stores Team",
    "PACE Team",
    "Grade Team",
    "Internship Team",
    "Electives Team",
    "PACE Team - Group A",
    "PACE Team - Group B",
    "PACE Team - PGDAL",
    "PACE Team - PGDTXL",
    "Travel & Transportation Team",
    "AER Condonation - BA LLB (1-3 Year)",
    "AER Condonation - BA LLB (4-5 Year)",
    "AER Condonation - MPP and LLM",
    "AER Condonation - LLB Hons",
    "AER Condonation - PhD",
]


def create_ootb_teams():
    for team_name in OOTB_TEAMS:
        if frappe.db.exists(DT, team_name):
            continue

        doc = frappe.new_doc(DT)
        doc.team_name = team_name
        doc.insert(ignore_mandatory=True)
