import frappe


AUTO_CLOSE_TYPES = {"OOR Intimation", "Electric Appliance Declaration"}

# Ticket type exclusively available to faculty/staff
FACULTY_ONLY_TYPE = "Travel & Transportation"

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

	Role-based filtering:
	  - PACE Applicant  → PACE ticket type only.
	  - slcm_Student    → All ticket types EXCEPT "Travel & Transportation"
	                       (that type is reserved for faculty / staff).
	  - Everyone else   → All enabled ticket types.
	"""
	user_roles = frappe.get_roles(frappe.session.user)

	# PACE Applicants can only raise PACE tickets
	if "PACE Applicant" in user_roles:
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

	# Students cannot raise Travel & Transportation tickets
	STUDENT_HIDDEN_TYPES = {FACULTY_ONLY_TYPE}
	if "slcm_Student" in user_roles:
		types = [t for t in types if t.name not in STUDENT_HIDDEN_TYPES]

	# Faculty can ONLY raise Travel & Transportation tickets
	if "slcm_Faculty" in user_roles:
		types = [t for t in types if t.name == FACULTY_ONLY_TYPE]

	return [{"label": t.name, "value": t.name} for t in types]


@frappe.whitelist()
def get_user_role_context() -> dict:
	"""
	Returns role-level context flags used by the form script to drive
	template switching and field visibility.

	  is_faculty → True for slcm_Faculty users.
	              The form script uses this to redirect to the
	              'Travel & Transportation' template and pre-fill
	              the ticket type.

	  is_student → True for slcm_Student and PACE Applicant users.
	              The form script uses this to show the student
	              information section.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {"is_faculty": False, "is_student": False}

	user_roles = frappe.get_roles(user)
	is_faculty = "slcm_Faculty" in user_roles
	is_student = "slcm_Student" in user_roles or "PACE Applicant" in user_roles

	return {"is_faculty": is_faculty, "is_student": is_student}


@frappe.whitelist()
def get_student_context() -> dict:
	"""
	Returns pre-filled ticket field values for the current portal user,
	plus an `is_student` boolean the form script uses to show/hide the
	student information section.

	Logic:
	  1. Guest users              → {is_student: False} (no context).
	  2. PACE Applicant role      → PACE Application data, is_student = True.
	  3. slcm_Student role        → Student Master data,  is_student = True.
	  4. Faculty / staff / others → empty field values,   is_student = False
	                                (student section is hidden for them).
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {"is_student": False}

	user_roles = frappe.get_roles(user)

	# ── PACE Applicant: pull details from PACE Application ──────────────────
	if "PACE Applicant" in user_roles:
		ctx = _get_pace_applicant_context(user)
		ctx["is_student"] = True
		return ctx

	# ── Enrolled student: pull from Student Master ───────────────────────────
	if "slcm_Student" in user_roles:
		ctx = _get_student_master_context(user)
		ctx["is_student"] = True
		return ctx

	# ── Faculty / staff / other roles: no student data ──────────────────────
	return {
		"is_student": False,
		"custom_student_name":   "",
		"custom_student_id":     "",
		"custom_programme":      "",
		"custom_current_year":   "",
		"custom_current_term":   "",
		"custom_contact_number": "",
		"custom_hostel":         "",
		"custom_room_no":        "",
	}


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
	)

	# Fallback: email_address field matches login email
	if not app:
		app = frappe.db.get_value(
			"PACE Application",
			{"email_address": user},
			PACE_FIELDS,
			as_dict=True,
			order_by="modified desc",
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

	# Fallback 1: email field match
	if not student:
		student = frappe.db.get_value(
			"Student Master",
			{"email": user},
			STUDENT_FIELDS,
			as_dict=True,
		)

	# Fallback 2: official_email_id field (used when student logs in with personal email)
	if not student:
		student = frappe.db.get_value(
			"Student Master",
			{"official_email_id": user},
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


@frappe.whitelist()
def get_in_campus_students() -> list:
	"""
	Returns all active in-campus (hosteller) students as Autocomplete options.
	Used by url_method in the Roommate Intimation template for Roommate 1 and Roommate 2 fields.
	Excludes the currently logged-in student from the list (you cannot pick yourself as a roommate).

	In-campus students are identified by is_hosteller = 1 on Student Master.
	Option format: "Full Name (Registration ID)"
	"""
	current_user = frappe.session.user

	# Resolve current student's registration_id to exclude from list
	current_reg_id = frappe.db.get_value(
		"Student Master", {"user": current_user}, "registration_id"
	) or frappe.db.get_value(
		"Student Master", {"email": current_user}, "registration_id"
	)

	# Fetch all in-campus students (is_hosteller = 1)
	students = frappe.get_all(
		"Student Master",
		filters={"is_hosteller": 1},
		fields=["first_name", "middle_name", "last_name", "registration_id", "name"],
		order_by="first_name asc",
		ignore_permissions=True,
	)

	options = []
	for s in students:
		reg_id = s.registration_id or s.name
		# Exclude the requesting student
		if current_reg_id and reg_id == current_reg_id:
			continue
		parts = [p for p in [s.first_name, s.middle_name, s.last_name] if p]
		full_name = " ".join(parts)
		label = f"{full_name} ({reg_id})" if reg_id else full_name
		options.append({"label": label, "value": label})

	return options


@frappe.whitelist()
def get_transport_context() -> dict:
	"""
	Returns pre-filled Requestor name and email for the Travel & Transportation
	ticket form, sourced from the Faculty doctype for slcm_Faculty users.

	Lookup order:
	  1. Faculty doctype  — match user_id == frappe.session.user.
	     Full name is built from first_name + last_name.
	     Email comes from the Faculty.email field.
	  2. Fallback to Frappe User record (full_name + email) — covers cases
	     where a Faculty row has not yet been created.

	Returns empty dict for Guest users.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {}

	# ── Primary: look up Faculty record linked to this user account ──────────
	faculty = frappe.db.get_value(
		"Faculty",
		{"user_id": user},
		["first_name", "last_name", "email"],
		as_dict=True,
		ignore_permissions=True,
	)

	# ── Secondary: match by Faculty.email (covers cases where user_id unset) ─
	if not faculty:
		faculty = frappe.db.get_value(
			"Faculty",
			{"email": user},
			["first_name", "last_name", "email"],
			as_dict=True,
			ignore_permissions=True,
		)

	if faculty:
		parts = [p for p in [faculty.first_name, faculty.last_name] if p]
		full_name = " ".join(parts)
		return {
			"custom_transport_requestor":       full_name,
			"custom_transport_requestor_email": faculty.email or "",
		}

	# ── Fallback: use the Frappe User record directly ─────────────────────────
	user_doc = frappe.db.get_value(
		"User",
		user,
		["full_name", "email"],
		as_dict=True,
	)
	if not user_doc:
		return {}

	return {
		"custom_transport_requestor":       user_doc.full_name or "",
		"custom_transport_requestor_email": user_doc.email or "",
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
