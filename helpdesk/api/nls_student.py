import frappe
from frappe import _


AUTO_CLOSE_TYPES = {"OOR Intimation", "Electric Appliance Declaration"}

# Ticket type exclusively available to faculty/staff
FACULTY_ONLY_TYPE = "Travel & Transportation"

def _get_issue_options(ticket_type: str) -> list:
	rows = frappe.get_all(
		"HD Ticket Type Of Issue",
		filters={"ticket_type": ticket_type, "enabled": 1},
		fields=["name", "issue_name"],
		order_by="issue_name asc",
	)
	return [{"label": r.issue_name, "value": r.name} for r in rows]


@frappe.whitelist()
def get_issue_options_academics() -> list:
	return _get_issue_options("Academics")


@frappe.whitelist()
def get_issue_options_facilities() -> list:
	return _get_issue_options("Facilities")


@frappe.whitelist()
def get_issue_options_finance() -> list:
	return _get_issue_options("Finance")


@frappe.whitelist()
def get_issue_options_it() -> list:
	return _get_issue_options("IT")


@frappe.whitelist()
def get_issue_options_pace() -> list:
	return _get_issue_options("PACE")


@frappe.whitelist()
def get_issue_names_for_ticket_type(ticket_type: str) -> list:
	"""
	Returns HD Ticket Type Of Issue names (docnames) enabled for the given
	ticket_type. Used by the form script to scope the "Type of Issue" Link
	field via applyFilters, since link_filters on the Custom Field can't
	reference the current form's ticket_type at query time.
	"""
	if not ticket_type:
		return []
	return frappe.get_all(
		"HD Ticket Type Of Issue",
		filters={"ticket_type": ticket_type, "enabled": 1},
		pluck="name",
	)


@frappe.whitelist()
def get_ticket_types() -> list:
	"""
	Returns enabled ticket types as Autocomplete options.
	Used by url_method in HD Ticket Template fields — avoids the
	search_link + disabled-field permission error for portal users.

	Role-based filtering:
	  - PACE Applicant  → PACE and Technical Issue ticket types only.
	  - slcm_Student    → All ticket types EXCEPT "Travel & Transportation"
	                       (that type is reserved for faculty / staff).
	  - Everyone else   → All enabled ticket types.
	"""
	user_roles = frappe.get_roles(frappe.session.user)

	# PACE Applicants can only raise PACE or Technical Issue tickets
	if "PACE Applicant" in user_roles:
		allowed = frappe.get_all(
			"HD Ticket Type",
			filters={"name": ["in", ["PACE", "Technical Issue"]], "disabled": 0},
			fields=["name"],
			order_by="name asc",
			ignore_permissions=True,
		)
		return [{"label": t.name, "value": t.name} for t in allowed]

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

	  is_enrolled_student → True for slcm_Student only.
	              The form script uses this to show fields that only
	              apply to enrolled students (e.g. Year, Section/Term),
	              which PACE Applicants don't have yet.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return {"is_faculty": False, "is_student": False, "is_enrolled_student": False}

	user_roles = frappe.get_roles(user)
	is_faculty = "slcm_Faculty" in user_roles
	is_enrolled_student = "slcm_Student" in user_roles
	is_student = is_enrolled_student or "PACE Applicant" in user_roles

	return {
		"is_faculty": is_faculty,
		"is_student": is_student,
		"is_enrolled_student": is_enrolled_student,
	}


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


def resolve_programme_context(identifier: str) -> dict:
	"""
	Server-side fallback to resolve custom_programme/custom_current_year for a
	ticket when the form script hasn't already set them (e.g. tickets created
	via inbound email, where there is no browser session to run client JS).

	Tries Student Master first (enrolled students have a current_year),
	falling back to PACE Application (applicants, no current_year).
	`identifier` is typically the ticket's raised_by email, but a Frappe user
	name works too since Student Master/PACE Application lookups match by
	user or email fields either way.
	"""
	if not identifier:
		return {}

	ctx = _get_student_master_context(identifier)
	if ctx:
		return ctx

	return _get_pace_applicant_context(identifier)


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
def get_student_current_term_courses() -> list:
	"""
	Returns the logged-in student's enrolled courses for their current term,
	as Autocomplete options. Used by the Attendance Condonation ticket form
	to restrict the Course field to subjects the student is actually taking.

	The "current term" is taken as the student's most recently updated
	Student Enrollment record with status = Enrolled (term_name on Student
	Master is free text and doesn't reliably match Student Enrollment's
	term_name, so we don't try to match on it).

	Returns an empty list for Guest users, non-students, or students with
	no Enrolled Student Enrollment record.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return []

	student = frappe.db.get_value("Student Master", {"user": user}, "name") \
		or frappe.db.get_value("Student Master", {"email": user}, "name") \
		or frappe.db.get_value("Student Master", {"official_email_id": user}, "name")

	if not student:
		return []

	enrollment = frappe.db.get_value(
		"Student Enrollment",
		{"student": student, "status": "Enrolled"},
		"name",
		order_by="modified desc",
	)
	if not enrollment:
		return []

	rows = frappe.get_all(
		"Student Enrollment Course",
		filters={"parent": enrollment, "parenttype": "Student Enrollment"},
		fields=["course", "course_offering"],
	)

	seen = set()
	options = []
	for r in rows:
		course = r.course or r.course_offering
		if not course or course in seen:
			continue
		seen.add(course)
		options.append({"label": course, "value": course})

	return options


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


TRANSCRIPT_TICKET_TYPE = "Transcript Request"

# The Helpdesk ticket form only offers "Final Transcript" today, but shows it
# to students as "Provisional Transcript" — the stored value has to stay
# "Final Transcript" to match Transcript Request / Transcript Fee Settings /
# the student portal page, all of which already use that value in live data.
TRANSCRIPT_TYPE_DISPLAY_LABELS = {
	"Final Transcript": "Provisional Transcript",
}


def _transcript_type_label(transcript_type):
	return TRANSCRIPT_TYPE_DISPLAY_LABELS.get(transcript_type, transcript_type)


@frappe.whitelist()
def get_transcript_types() -> list:
	"""
	url_method for the Transcript Type field on the Helpdesk ticket form.
	Returns {label, value} pairs so the field renders as an Autocomplete with
	a friendlier label ("Provisional Transcript") while still submitting the
	underlying value ("Final Transcript") that Transcript Request / Transcript
	Fee Settings / the student portal already store and key off of.
	"""
	return [
		{"label": _transcript_type_label(v), "value": v}
		for v in TRANSCRIPT_TYPE_DISPLAY_LABELS
	]


def create_transcript_request_on_ticket(doc, method=None):
	"""
	doc_event: HD Ticket after_insert.

	Two paths, both ending with the ticket linked to a Transcript Request via
	custom_transcript_request (same doc that /student-portal/transcript-request
	creates — fee calculation, Razorpay payment, auto-approval, and PDF
	generation all reuse that existing, already-tested implementation):

	1. Pay-first (normal path): the New Ticket form already walked the student
	   through create_request() + initiate_payment() + confirm_payment() for
	   this exact Transcript Request before Submit was even enabled, so
	   doc.custom_transcript_request arrives pre-set and already Paid. This
	   function just verifies that server-side (ownership + payment status —
	   never trusts the client) and links it. No second charge, no new
	   request created.
	2. Fallback (ticket created without going through that flow — API, email,
	   or a client that hasn't loaded the payment step): behaves like before,
	   creating an unpaid Transcript Request and posting a pay-link comment.
	"""
	if doc.ticket_type != TRANSCRIPT_TICKET_TYPE:
		return

	try:
		import slcm.api.transcript_request as tr_api
	except ImportError:
		frappe.log_error(
			title="Transcript Request ticket: slcm app not available",
			message=frappe.get_traceback(),
		)
		return

	prepaid_request = doc.get("custom_transcript_request")
	if prepaid_request:
		_link_prepaid_transcript_request(doc, prepaid_request)
		return

	transcript_type = doc.get("custom_transcript_type")
	if not transcript_type:
		# Template marks this field required, so this should not happen via
		# the portal form; guard anyway for tickets created via API/email.
		# Agent-only note — the student can't act on a missing-field
		# diagnostic; give them a generic message instead.
		_post_ticket_comment(
			doc.name,
			_(
				"This ticket is missing the Transcript Type field, so a "
				"Transcript Request record could not be created automatically. "
				"Please ask the student to resubmit via the portal, or set "
				"custom_transcript_type and rerun the request manually."
			),
		)
		_post_student_update(
			doc.name,
			_("We couldn't process your transcript request automatically. The Academic Office has been notified."),
		)
		return

	try:
		result = tr_api._create_request(
			transcript_type,
			num_copies=doc.get("custom_transcript_num_copies") or 1,
			purpose=doc.get("custom_transcript_purpose") or "",
			delivery_mode=doc.get("custom_transcript_delivery_mode") or "Soft Copy (PDF)",
			helpdesk_ticket=doc.name,
		)
	except frappe.ValidationError as e:
		# Business-rule rejection (duplicate open request, not yet a graduate,
		# transcript type disabled, etc.) — surface it on the ticket instead
		# of failing ticket creation, so the student still has a place to see
		# why nothing happened next.
		_post_student_update(doc.name, _("Could not create the transcript request: {0}").format(str(e)))
		return
	except Exception:
		frappe.log_error(title="Transcript Request auto-create failed", message=frappe.get_traceback())
		_post_student_update(
			doc.name,
			_(
				"Something went wrong while creating your transcript request. "
				"Please contact the Academic Office."
			),
		)
		return

	frappe.db.set_value("HD Ticket", doc.name, "custom_transcript_request", result["name"])
	doc.custom_transcript_request = result["name"]

	type_label = _transcript_type_label(transcript_type)

	if result.get("payment_required"):
		pay_url = frappe.utils.get_url(
			f"/student-portal/transcript-request?request={frappe.utils.quote(result['name'])}"
		)
		_post_student_update(
			doc.name,
			_(
				"Fee for {0}: ₹{1}. Please complete payment to proceed — "
				"<a href=\"{2}\" target=\"_blank\" rel=\"noopener noreferrer\">Pay Now</a>. "
				"This request will be processed only after payment is confirmed."
			).format(type_label, frappe.utils.fmt_money(result.get("fee_amount") or 0), pay_url),
		)
	else:
		_post_student_update(
			doc.name,
			_("Your {0} request ({1}) has been submitted and does not require payment.").format(
				type_label, result["name"]
			),
		)


def _link_prepaid_transcript_request(doc, request_name):
	"""
	Verify (server-side, never trusting the client) that request_name is a
	real Transcript Request belonging to the ticket's own raiser and is
	either already Paid or doesn't require payment, then link it and set
	helpdesk_ticket back-reference. If verification fails for any reason,
	the ticket is NOT silently marked paid — it falls back to creating a
	fresh unpaid request instead, same as if no prepaid id had been sent.
	"""
	row = frappe.db.get_value(
		"Transcript Request",
		request_name,
		["name", "student", "payment_required", "payment_status", "status", "helpdesk_ticket",
		 "transcript_type", "fee_amount"],
		as_dict=True,
	)

	# Same resolution _require_student() uses in transcript_request.py — the
	# ticket's raiser is who owns the Transcript Request created moments
	# earlier by the same session during the pre-payment step.
	student_name = None
	for field in ("user", "email", "official_email_id"):
		student_name = frappe.db.get_value("Student Master", {field: doc.raised_by}, "name")
		if student_name:
			break

	verified = (
		row
		and student_name
		and row.student == student_name
		and row.status != "Cancelled"
		and (not row.payment_required or row.payment_status == "Paid")
		and not row.helpdesk_ticket  # not already claimed by another ticket
	)

	if not verified:
		frappe.log_error(
			title="Transcript Request prepaid link rejected",
			message=(
				f"Transcript Request ticket {doc.name}: prepaid request {request_name} "
				f"failed verification (row={row}, student={student_name}) — falling back "
				f"to creating a fresh unpaid request."
			),
		)
		frappe.db.set_value("HD Ticket", doc.name, "custom_transcript_request", None)
		doc.custom_transcript_request = None
		create_transcript_request_on_ticket(doc)
		return

	frappe.db.set_value("Transcript Request", request_name, "helpdesk_ticket", doc.name)
	type_label = _transcript_type_label(row.transcript_type)
	if row.payment_required:
		_post_student_update(
			doc.name,
			_("Payment of ₹{0} for {1} has been received. Your request has been submitted.").format(
				frappe.utils.fmt_money(row.fee_amount or 0), type_label
			),
		)
	else:
		_post_student_update(
			doc.name,
			_("Your {0} request ({1}) has been submitted and does not require payment.").format(
				type_label, request_name
			),
		)


TRANSCRIPT_RESOLVED_STATUSES = {"Generated", "Delivered"}
TRANSCRIPT_CLOSED_STATUSES = {"Rejected", "Cancelled"}


def _transcript_status_message(status):
	# Built at call time (not module import time) so _() translates using the
	# current request's locale rather than whatever was active on import.
	return {
		"Payment Pending": _("Waiting for payment."),
		"Submitted": _("Payment received. Your request has been submitted for review."),
		"Under Review": _("Your request is under review by the Academic Office."),
		"Approved": _("Your request has been approved and is being processed."),
		"Generated": _("Your transcript has been generated. You can download it from the Documents page on the student portal."),
		"Delivered": _("Your transcript has been delivered."),
		"Rejected": _("Your request was rejected. Reason: {0}"),
		"Cancelled": _("This request was cancelled."),
	}.get(status)


def sync_transcript_status_to_ticket(transcript_request_doc):
	"""
	Called from Transcript Request.on_update() (slcm side) whenever a
	Transcript Request linked to a Helpdesk ticket changes status or payment
	status. Posts a status comment and, for terminal outcomes, moves the
	ticket into the corresponding HD Ticket Status category so it leaves the
	agent's open queue automatically.
	"""
	ticket_name = transcript_request_doc.helpdesk_ticket
	status = transcript_request_doc.status
	payment_status = transcript_request_doc.payment_status

	if payment_status == "Paid" and status == "Payment Pending":
		# Fee captured but the status field hasn't rolled forward yet
		# (rare race between webhook and status field save) — still worth
		# telling the agent payment came in.
		_post_student_update(ticket_name, _("Payment received (₹{0}).").format(
			frappe.utils.fmt_money(transcript_request_doc.fee_amount or 0)
		))

	message = _transcript_status_message(status)
	if message:
		if status == "Rejected":
			message = message.format(transcript_request_doc.rejection_reason or _("Not specified"))
		_post_student_update(ticket_name, message)

	target_category = None
	if status in TRANSCRIPT_RESOLVED_STATUSES:
		target_category = "Resolved"
	elif status in TRANSCRIPT_CLOSED_STATUSES:
		target_category = "Closed"

	if not target_category:
		return

	target_status = frappe.db.get_value(
		"HD Ticket Status", {"category": target_category}, "name"
	)
	if not target_status:
		return

	current_status = frappe.db.get_value("HD Ticket", ticket_name, "status")
	if current_status == target_status:
		return

	frappe.db.set_value(
		"HD Ticket", ticket_name,
		{"status": target_status, "status_category": target_category},
	)


def _post_ticket_comment(ticket_name, content):
	"""
	Post an internal agent-only note (HD Ticket Comment). NOT visible to the
	student — that doctype has no read permission for HD Customer/portal
	roles, so anything posted here only ever shows up in the agent desk's
	internal Comments tab. Use _post_student_update for anything the
	student needs to see.
	"""
	try:
		frappe.get_doc({
			"doctype": "HD Ticket Comment",
			"reference_ticket": ticket_name,
			"commented_by": "Administrator",
			"content": content,
			"is_pinned": 1,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Transcript Request: could not post ticket comment", message=frappe.get_traceback())


def _post_student_update(ticket_name, content):
	"""
	Post a student-visible system notice on the ticket (fee due, payment
	received, status changes). Unlike _post_ticket_comment (HD Ticket
	Comment — agent-only), this creates a Communication the same way an
	agent's reply would, so it renders in the student's own "Activity"
	thread on the portal.

	communication_type "Automated Message" keeps this out of
	HDTicket.has_agent_replied's count, so it can never be mistaken for a
	real agent reply and doesn't let a ticket get closed without one.
	"""
	try:
		ticket = frappe.db.get_value("HD Ticket", ticket_name, ["subject", "raised_by"], as_dict=True)
		if not ticket:
			return
		frappe.get_doc({
			"doctype": "Communication",
			"communication_type": "Automated Message",
			"communication_medium": "",
			"sent_or_received": "Sent",
			"content": content,
			"subject": f"Re: {ticket.subject}",
			"sender": "Administrator",
			"user": "Administrator",
			"recipients": ticket.raised_by,
			"status": "Linked",
			"reference_doctype": "HD Ticket",
			"reference_name": ticket_name,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Transcript Request: could not post student update", message=frappe.get_traceback())
