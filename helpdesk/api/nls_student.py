import frappe


AUTO_CLOSE_TYPES = {"OOR Intimation", "Electric Appliance Declaration"}

# Type of Issue options per ticket type
ISSUE_OPTIONS = {
	"Academics": ["Attendance", "Certificates", "Examination", "Letters", "Learning Material", "Projects", "Viva", "Others"],
	"Facilities": ["Carpentry", "Electrical", "House Keeping", "Plumbing", "Security", "Others"],
	"Finance": ["Fees", "Others"],
	"IT": ["Admin Portal", "Gsuite", "Group Creation", "ID Card", "Internet Connectivity", "Learning Platform", "Microsoft Office", "Online Library Access", "Others"],
	"PACE": ["Admission", "Academics", "Degree and Certificate", "Examination/Result", "Fee-related", "Grievance", "Technical Issues", "Transcripts"],
}


@frappe.whitelist()
def get_issue_options_academics() -> list:
	return [{"label": o, "value": o} for o in ISSUE_OPTIONS["Academics"]]


@frappe.whitelist()
def get_issue_options_facilities() -> list:
	return [{"label": o, "value": o} for o in ISSUE_OPTIONS["Facilities"]]


@frappe.whitelist()
def get_issue_options_finance() -> list:
	return [{"label": o, "value": o} for o in ISSUE_OPTIONS["Finance"]]


@frappe.whitelist()
def get_issue_options_it() -> list:
	return [{"label": o, "value": o} for o in ISSUE_OPTIONS["IT"]]


@frappe.whitelist()
def get_issue_options_pace() -> list:
	return [{"label": o, "value": o} for o in ISSUE_OPTIONS["PACE"]]


@frappe.whitelist()
def get_ticket_types() -> list:
	"""
	Returns enabled ticket types as Autocomplete options.
	Used by url_method in HD Ticket Template fields — avoids the
	search_link + disabled-field permission error for portal users.

	If the current user has the PACE Applicant role, only the PACE ticket
	type is returned so applicants cannot raise tickets of any other category.
	"""
	user_roles = frappe.get_roles(frappe.session.user)
	if "PACE Applicant" in user_roles:
		# Restrict PACE Applicants to only the PACE ticket type
		pace_type = frappe.db.get_value(
			"HD Ticket Type", {"name": "PACE", "disabled": 0}, "name"
		)
		if pace_type:
			return [{"label": pace_type, "value": pace_type}]
		return []

	types = frappe.get_all(
		"HD Ticket Type",
		filters={"disabled": 0},
		fields=["name"],
		order_by="name asc",
		ignore_permissions=True,
	)
	return [{"label": t.name, "value": t.name} for t in types]


@frappe.whitelist()
def get_student_context() -> dict:
	"""
	Returns pre-filled ticket field values for the current portal user.

	Logic:
	  1. Guest users → empty dict (no context).
	  2. PACE Applicant role → fetch details from PACE Application doctype.
	  3. All other users (Students, staff) → fetch from Student Master.

	Source selection for PACE Applicants:
	  - Looks up PACE Application by owner (= frappe.session.user) first.
	  - Falls back to email_address field match in case owner differs.
	  - Returns the most recently modified application if multiple exist.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {}

	user_roles = frappe.get_roles(user)

	# ── PACE Applicant: pull details from PACE Application ──────────────────
	if "PACE Applicant" in user_roles:
		return _get_pace_applicant_context(user)

	# ── Regular student / staff: pull from Student Master ───────────────────
	return _get_student_master_context(user)


def _get_pace_applicant_context(user: str) -> dict:
	"""
	Fetch ticket pre-fill data from PACE Application for a PACE Applicant user.
	Tries owner match first, then email_address match as fallback.
	Returns empty dict if no application is found.
	"""
	PACE_FIELDS = [
		"name", "first_name", "middle_name", "last_name",
		"applicant_name", "mobile_number", "programme",
	]

	# Primary: records owned by this user
	app = frappe.db.get_value(
		"PACE Application",
		{"owner": user},
		PACE_FIELDS,
		as_dict=True,
		order_by="modified desc",
		ignore_permissions=True,
	)

	# Fallback: email_address field matches login email
	if not app:
		app = frappe.db.get_value(
			"PACE Application",
			{"email_address": user},
			PACE_FIELDS,
			as_dict=True,
			order_by="modified desc",
			ignore_permissions=True,
		)

	if not app:
		return {}

	# Build full name: prefer computed applicant_name, else join parts
	if app.applicant_name:
		full_name = app.applicant_name
	else:
		parts = [p for p in [app.first_name, app.middle_name, app.last_name] if p]
		full_name = " ".join(parts)

	# Resolve programme label (PACE Programme stores name as-is)
	programme = app.programme or ""

	return {
		"custom_student_name":   full_name,
		"custom_student_id":     app.name or "",        # e.g. PACE-2025-00001
		"custom_programme":      programme,
		"custom_current_year":   "",                    # Not applicable for applicants
		"custom_current_term":   "",                    # Not applicable for applicants
		"custom_contact_number": app.mobile_number or "",
		"custom_hostel":         "",                    # Not applicable for applicants
		"custom_room_no":        "",                    # Not applicable for applicants
	}


def _get_student_master_context(user: str) -> dict:
	"""
	Fetch ticket pre-fill data from Student Master for enrolled students.
	Tries user field first, then email match as fallback.
	Returns empty dict if no student record is found.
	"""
	STUDENT_FIELDS = [
		"name", "first_name", "middle_name", "last_name",
		"registration_id", "programme",
		"current_year", "current_term",
		"phone", "hostel", "hostel_room",
	]

	# Primary: user field (set during admission → student conversion)
	student = frappe.db.get_value(
		"Student Master",
		{"user": user},
		STUDENT_FIELDS,
		as_dict=True,
	)

	# Fallback: email field match
	if not student:
		student = frappe.db.get_value(
			"Student Master",
			{"email": user},
			STUDENT_FIELDS,
			as_dict=True,
		)

	if not student:
		return {}

	# Build full name
	parts = [p for p in [student.first_name, student.middle_name, student.last_name] if p]

	# Resolve hostel room number (stored as a Link to Hostel Room doctype)
	room_no = ""
	if student.hostel_room:
		room_no = (
			frappe.db.get_value("Hostel Room", student.hostel_room, "room_number")
			or student.hostel_room
		)

	# Resolve hostel display name
	hostel_name = ""
	if student.hostel:
		hostel_name = (
			frappe.db.get_value("Hostel", student.hostel, "hostel_name")
			or student.hostel
		)

	# Use registration_id if available, otherwise fall back to the doc name
	student_id = student.registration_id or student.name or ""

	return {
		"custom_student_name":   " ".join(parts),
		"custom_student_id":     student_id,
		"custom_programme":      student.programme or "",
		"custom_current_year":   student.current_year or "",
		"custom_current_term":   student.current_term or "",
		"custom_contact_number": student.phone or "",
		"custom_hostel":         hostel_name,
		"custom_room_no":        room_no,
	}


def auto_close_intimation_ticket(doc, method=None):
	"""
	doc_event: HD Ticket after_insert.
	Immediately sets status to Closed for intimation-only ticket types.
	"""
	if doc.ticket_type not in AUTO_CLOSE_TYPES:
		return

	closed_status = frappe.db.get_value(
		"HD Ticket Status", {"category": "Closed"}, "name"
	)
	if not closed_status:
		return

	frappe.db.set_value("HD Ticket", doc.name, "status", closed_status)
	doc.reload()
