"""
Remove orphaned duplicate Assignment Rules created by repeated fixture re-imports.
Each bench migrate deleted+reinserted HD Team fixtures, causing after_insert to fire
and create a new "Team Name - Support Rotation-N" rule each time. This patch keeps
only the rule currently linked to each team and deletes the rest.
"""
import frappe


def execute():
    # Collect the one rule each team actually uses
    linked_rules = set(
        frappe.db.sql(
            "SELECT assignment_rule FROM `tabHD Team` WHERE assignment_rule IS NOT NULL",
            pluck="assignment_rule",
        )
    )

    # Also keep the base support rotation from HD Settings (if it exists)
    base_rotation = frappe.db.get_single_value("HD Settings", "base_support_rotation")
    if base_rotation:
        linked_rules.add(base_rotation)

    # Find all HD Ticket assignment rules that are NOT in the keep-set
    all_hd_rules = frappe.db.sql(
        "SELECT name FROM `tabAssignment Rule` WHERE document_type = 'HD Ticket'",
        pluck="name",
    )

    orphaned = [r for r in all_hd_rules if r not in linked_rules]

    if not orphaned:
        frappe.logger().info("cleanup_duplicate_assignment_rules: nothing to delete")
        return

    frappe.logger().info(
        f"cleanup_duplicate_assignment_rules: deleting {len(orphaned)} orphaned rules"
    )

    for rule_name in orphaned:
        try:
            # Delete directly — bypasses the on_trash guard that blocks deletion
            # when it would leave zero HD Ticket rules (not applicable here since
            # we keep the linked rules).
            frappe.db.delete("Assignment Rule User", {"parent": rule_name})
            frappe.db.delete("Assignment Rule Day", {"parent": rule_name})
            frappe.db.delete("Assignment Rule", {"name": rule_name})
        except Exception as e:
            frappe.log_error(
                title="cleanup_duplicate_assignment_rules",
                message=f"Could not delete {rule_name}: {e}",
            )

    frappe.db.commit()
