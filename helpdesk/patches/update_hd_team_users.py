import frappe


def execute():
    teams = frappe.get_all("HD Team", pluck="name")
    for team in teams:
        existing_agents = frappe.get_all(
            "HD Team Item", filters={"team": team}, pluck="parent"
        )  # agents in HD Agent doctype
        team_users = frappe.get_all(
            "HD Team Member", filters={"parent": team}, pluck="user"
        )  # agents in HD Team doctype

        agents_to_add = [
            a for a in existing_agents
            if frappe.get_value("HD Agent", a, "is_active") and a not in team_users
        ]
        if agents_to_add:
            team_doc = frappe.get_doc("HD Team", team)
            for agent in agents_to_add:
                team_doc.append("users", {"user": agent})
            team_doc.save()
            print("Agent Added")
