import frappe

from helpdesk.consts import DEFAULT_TICKET_TYPE

DT = "HD Ticket Type"
TICKET_TYPES = ["Question", "Incident"]

# NLS ticket types, seeded once on fresh install only (create-if-missing).
# Deliberately NOT a fixture: HD Ticket Type has admin-managed child tables
# (year_wise_assignment_rules, pace_year_wise_assignment_rules) that fixture
# import would wipe on every `bench migrate`, since fixture sync force-
# overwrites the whole doc regardless of the app's hooks.py fixtures list.
NLS_TICKET_TYPES = {
    "Academics": "Issues related to attendance, certificates, examination, letters, learning material, projects, viva, and other academic matters.",
    "Facilities": "Issues related to hostel facilities including carpentry, electrical, housekeeping, plumbing, security, and others.",
    "Finance": "Issues related to fees and other finance matters.",
    "Food and Beverage": "Issues related to food and beverage services on campus.",
    "IT": "Issues related to admin portal, G-Suite, group creation, ID card, internet connectivity, learning platform, Microsoft Office, online library access, and others.",
    "Library": "Issues related to library services, including book requests by accession number, title, and author.",
    "Stores Request": "Material requisition from stores for canteen, central kitchen, training center, or boys mess.",
    "PACE": "Issues related to admission, academics, degree and certificate, examination/result, fee-related, grievance, technical issues, and transcripts for PACE students.",
    "OOR Intimation": "Out of Residence intimation — arriving late, night out, or out of station. No approval required; ticket auto-closes on submission.",
    "Electric Appliance Declaration": "Declaration of electric appliance in room. No approval required; ticket auto-closes on submission.",
    "Transcript Request": "Request for an academic transcript, consolidated marksheet, or duplicate/digital copy. A fee applies and payment is required before the request is processed.",
    "Travel & Transportation": "Request for campus vehicle / driver for official or personal travel — includes trip details, driver and car assignment, and trip log (KM, time, charges).",
    "Technical Issue": "Technical issues for PACE and NLSAT applicants — e.g. login, portal access, or application form problems.",
    "NLSAT Domestic": "Queries from domestic NLSAT applicants — curriculum, eligibility, reservation, the NLSAT exam, test center / admit card, and others.",
    "NLSAT International": "Queries from international (foreign national) NLSAT applicants — curriculum, eligibility, reservation, the NLSAT exam, test center / admit card, and others.",
}

# Default "Type of Issue" options for each NLSAT ticket type, seeded create-if-
# missing. Admins can add more from Desk (HD Ticket Type Of Issue); the portal
# picks them up automatically via get_issue_names_for_ticket_type.
NLSAT_TICKET_TYPES = ["NLSAT Domestic", "NLSAT International"]
NLSAT_TYPE_OF_ISSUES = [
    "Curriculum",
    "Eligibility",
    "Reservation",
    "NLSAT Exam",
    "Test Center / Admit Card",
    "Other",
]


def create_fallback_ticket_type():
    if frappe.db.exists(DT, DEFAULT_TICKET_TYPE):
        return

    d = frappe.new_doc(DT)
    d.name = DEFAULT_TICKET_TYPE
    d.is_system = True
    d.save()


def create_ootb_ticket_types():
    for ticket_type in TICKET_TYPES:
        if frappe.db.exists(DT, ticket_type):
            return

        d = frappe.new_doc(DT)
        d.name = ticket_type
        d.is_system = False
        d.save()


def create_nls_ticket_types():
    for ticket_type, description in NLS_TICKET_TYPES.items():
        if frappe.db.exists(DT, ticket_type):
            continue

        d = frappe.new_doc(DT)
        d.name = ticket_type
        d.description = description
        d.is_system = False
        d.save()


def create_nlsat_type_of_issues():
    # HD Ticket Type Of Issue is defined by the slcm app
    if not frappe.db.table_exists("HD Ticket Type Of Issue"):
        return

    for ticket_type in NLSAT_TICKET_TYPES:
        if not frappe.db.exists(DT, ticket_type):
            continue

        for issue_name in NLSAT_TYPE_OF_ISSUES:
            if frappe.db.exists(
                "HD Ticket Type Of Issue",
                {"ticket_type": ticket_type, "issue_name": issue_name},
            ):
                continue

            d = frappe.new_doc("HD Ticket Type Of Issue")
            d.ticket_type = ticket_type
            d.issue_name = issue_name
            d.enabled = 1
            d.insert(ignore_permissions=True)
