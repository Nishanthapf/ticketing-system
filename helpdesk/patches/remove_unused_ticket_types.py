import frappe

REMOVED_TICKET_TYPES = [
	"Electives",
	"Grade",
	"Internship",
	"Nominations",
	"Roommate Intimation",
	"SBA Committee Application",
]

# Folded into "Library" as a Type of Issue instead of a standalone Ticket Type.
LIBRARY_BOOK_REQUEST_TICKET_TYPE = "Library Book Request"


def execute():
	for ticket_type in REMOVED_TICKET_TYPES + [LIBRARY_BOOK_REQUEST_TICKET_TYPE]:
		if frappe.db.exists("HD Ticket", {"ticket_type": ticket_type}):
			continue
		if frappe.db.exists("HD Ticket Template", ticket_type):
			frappe.delete_doc("HD Ticket Template", ticket_type, ignore_missing=True, force=True)
		if frappe.db.exists("HD Ticket Type", ticket_type):
			frappe.delete_doc("HD Ticket Type", ticket_type, ignore_missing=True, force=True)

	if frappe.db.exists("HD Ticket Type", "Library") and not frappe.db.exists(
		"HD Ticket Type Of Issue", "Library-Book Request"
	):
		doc = frappe.new_doc("HD Ticket Type Of Issue")
		doc.issue_name = "Book Request"
		doc.ticket_type = "Library"
		doc.team = "Library Team" if frappe.db.exists("HD Team", "Library Team") else None
		doc.enabled = 1
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
