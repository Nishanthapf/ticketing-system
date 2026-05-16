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
	"""
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
	Returns student info + pre-filled field values for the current user.
	Looks up Student Master by user field first, then falls back to email match.
	Returns empty dict for non-student users.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {}

	# Try user field first, then fall back to email match (user field may be unset)
	student = frappe.db.get_value(
		"Student Master",
		{"user": user},
		[
			"name", "first_name", "middle_name", "last_name",
			"registration_id", "programme",
			"current_year", "current_term",
			"phone", "hostel", "hostel_room",
		],
		as_dict=True,
	)
	if not student:
		student = frappe.db.get_value(
			"Student Master",
			{"email": user},
			[
				"name", "first_name", "middle_name", "last_name",
				"registration_id", "programme",
				"current_year", "current_term",
				"phone", "hostel", "hostel_room",
			],
			as_dict=True,
		)
	if not student:
		return {}

	parts = [p for p in [student.first_name, student.middle_name, student.last_name] if p]

	room_no = ""
	if student.hostel_room:
		room_no = frappe.db.get_value("Hostel Room", student.hostel_room, "room_number") or student.hostel_room

	hostel_name = ""
	if student.hostel:
		hostel_name = frappe.db.get_value("Hostel", student.hostel, "hostel_name") or student.hostel

	# Use name (STUD-YYYY-NNNNN) as student ID if registration_id not set
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
