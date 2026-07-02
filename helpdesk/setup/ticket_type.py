import frappe

from helpdesk.consts import DEFAULT_TICKET_TYPE

DT = "HD Ticket Type"
TICKET_TYPES = ["Question", "Bug", "Incident"]

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
    "Library": "Issues related to library services.",
    "Stores Request": "Material requisition from stores for canteen, central kitchen, training center, or boys mess.",
    "PACE": "Issues related to admission, academics, degree and certificate, examination/result, fee-related, grievance, technical issues, and transcripts for PACE students.",
    "Library Book Request": "Request for a specific library book by accession number, title, and author.",
    "OOR Intimation": "Out of Residence intimation — arriving late, night out, or out of station. No approval required; ticket auto-closes on submission.",
    "Electric Appliance Declaration": "Declaration of electric appliance in room. No approval required; ticket auto-closes on submission.",
    "Nominations": "Self-nomination for a student body post with statement of purpose.",
    "SBA Committee Application": "Application to join an SBA committee — includes three questions and comments.",
    "Attendance Condonation Under AER": "Application for attendance condonation under AER for medical, bereavement, critical illness, or menstrual leave reasons.",
    "Grade": "Issues related to grades for a specific subject.",
    "Internship": "Issues related to internship matters.",
    "Electives": "Issues related to elective course selection or matters.",
    "Roommate Intimation": "Roommate preference intimation for hostel room allocation. Does not constitute confirmation of room-mate or room allotment.",
    "Travel & Transportation": "Request for campus vehicle / driver for official or personal travel — includes trip details, driver and car assignment, and trip log (KM, time, charges).",
}


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
