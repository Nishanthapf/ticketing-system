import frappe

TICKET_TYPE_TEAM_MAP = {
    "Academics": "Academics Team",
    "Electives": "Electives Team",
    "Facilities": "Facilities Team",
    "Finance": "Finance Team",
    "Food and Beverage": "Food and Beverage Team",
    "Grade": "Grade Team",
    "Internship": "Internship Team",
    "IT": "IT Team",
    "Library": "Library Team",
    "Library Book Request": "Library Team",
    "PACE": "PACE Team",
    "Stores Request": "Stores Team",
    "Technical Issue": "PACE Team",
}


def sync_ticket_type_teams():
    for ticket_type, team in TICKET_TYPE_TEAM_MAP.items():
        if frappe.db.exists("HD Ticket Type", ticket_type) and frappe.db.exists(
            "HD Team", team
        ):
            frappe.db.set_value(
                "HD Ticket Type", ticket_type, "team", team, update_modified=False
            )
    frappe.db.commit()
