"""
Who raised a ticket — Student, Applicant, Faculty, Staff, AAD, ... — and the
requester's Name / ID for that label.

The label comes from HD Settings > Ticket Settings > Raised By Label
(raised_by_role_map): the first row whose role the user holds wins. Each
HD Raised By Label names a data_source that decides where Name / ID are read
from, so admins can add labels without code changes.
"""

from email.utils import parseaddr

import frappe

from helpdesk.api.nls_student import (
	_get_applicant_context,
	_get_student_master_context,
)

DATA_SOURCE_STUDENT_MASTER = "Student Master"
DATA_SOURCE_APPLICANT = "Applicant"
DATA_SOURCE_FACULTY = "Faculty"
DATA_SOURCE_USER = "User"

# Default labels and role mapping seeded on install (create-if-missing). Row
# order is precedence: an enrolled student who still holds an applicant role
# is shown as a Student.
DEFAULT_LABELS = {
	"Student": DATA_SOURCE_STUDENT_MASTER,
	"Applicant": DATA_SOURCE_APPLICANT,
	"Faculty": DATA_SOURCE_FACULTY,
	"Staff": DATA_SOURCE_FACULTY,
	"AAD": DATA_SOURCE_FACULTY,
}
DEFAULT_ROLE_MAP = [
	("slcm_Student", "Student"),
	("Student", "Student"),
	("Applicant", "Applicant"),
	("PACE Applicant", "Applicant"),
	("slcm_Faculty", "Faculty"),
	("Guest Faculty", "Faculty"),
	("AAD", "AAD"),
	("AAD Staff", "AAD"),
	("Staff", "Staff"),
]

FACULTY_FIELDS = ["faculty_id", "name", "first_name", "last_name", "phone"]


@frappe.whitelist()
def get_my_raised_by_context() -> dict:
	"""Raised By label and requester details for the session user (portal form pre-fill)."""
	user = frappe.session.user
	if not user or user == "Guest":
		return {}
	return get_raised_by_context(user)


def get_raised_by_context(identifier: str) -> dict:
	"""
	{"raised_by_label", "custom_student_name", "custom_student_id", ...} for a
	user name or email. Empty dict when no mapped role matches. Any extra
	custom_* fields the data source provides (programme, contact number, ...)
	are passed through so callers can pre-fill them too.
	"""
	user = resolve_user(identifier)
	if not user:
		return {}

	roles = frappe.get_roles(user)
	label = get_raised_by_label(roles)
	if not label:
		return {}

	data_source = frappe.get_cached_value("HD Raised By Label", label, "data_source")
	ctx = get_source_context(data_source, user, roles) or get_user_context(user)
	ctx["raised_by_label"] = label
	return ctx


def get_raised_by_label(roles: list) -> str | None:
	"""First enabled label in the HD Settings role map whose role is in `roles`."""
	role_map = frappe.get_cached_doc("HD Settings").get("raised_by_role_map") or []
	for row in role_map:
		if row.role not in roles:
			continue
		if frappe.get_cached_value("HD Raised By Label", row.label, "enabled"):
			return row.label
	return None


def resolve_user(identifier: str) -> str | None:
	"""User name for a user name or email (tickets store raised_by as an email)."""
	# Inbound email can set raised_by as "Name <email>"
	identifier = parseaddr(identifier or "")[1] or identifier
	if not identifier or identifier == "Guest":
		return None
	if frappe.db.exists("User", identifier):
		return identifier
	return frappe.db.get_value("User", {"email": identifier}, "name")


def get_source_context(data_source: str, user: str, roles: list) -> dict:
	"""Requester details from the label's data source; {} when no record is found."""
	if data_source == DATA_SOURCE_STUDENT_MASTER:
		return _get_student_master_context(user)
	if data_source == DATA_SOURCE_APPLICANT:
		ctx = _get_applicant_context(user, roles)
		# nlsat_ticket_type alone means no application record was found
		return ctx if ctx.get("custom_student_id") else {}
	if data_source == DATA_SOURCE_FACULTY:
		return get_faculty_context(user)
	return {}


def get_faculty_context(user: str) -> dict:
	"""Name / Faculty ID from the Faculty doctype (also holds Staff and AAD users)."""
	faculty = None
	for field in ("user_id", "email", "official_email_id"):
		faculty = frappe.db.get_value("Faculty", {field: user}, FACULTY_FIELDS, as_dict=True)
		if faculty:
			break
	if not faculty:
		return {}

	return {
		"custom_student_name": " ".join(p for p in (faculty.first_name, faculty.last_name) if p),
		"custom_student_id": faculty.faculty_id or faculty.name,
		"custom_contact_number": faculty.phone or "",
	}


def get_user_context(user: str) -> dict:
	"""Fallback for labels with no source record: the User's full name and email."""
	user_doc = frappe.db.get_value("User", user, ["full_name", "email"], as_dict=True)
	if not user_doc:
		return {}
	return {
		"custom_student_name": user_doc.full_name or "",
		"custom_student_id": user_doc.email or user,
	}
