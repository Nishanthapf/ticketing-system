"""
Links to the NLSAT admission portals' helpdesk page, for emails sent to
applicants.

NLSAT applicants use the helpdesk embedded in slcm's /nlsat/helpdesk page on
their admission portal's own domain (domestic and international portals have
separate domains), not the agent desk on the site's host_name.
"""

import frappe

from helpdesk.api.nls_student import (
	NLSAT_APPLICANT_ROLE,
	NLSAT_DOMESTIC_TICKET_TYPE,
	NLSAT_INTERNATIONAL_TICKET_TYPE,
	NLSAT_TICKET_TYPES,
	_get_nlsat_application,
	_get_nlsat_ticket_type,
)
from helpdesk.api.raised_by import resolve_user

NLSAT_HELPDESK_PATH = "/nlsat/helpdesk"

# slcm's portal types (slcm.admission.utils.nlsat_portal)
SLCM_PORTAL_TYPES = {
	NLSAT_DOMESTIC_TICKET_TYPE: "Domestic",
	NLSAT_INTERNATIONAL_TICKET_TYPE: "International",
}

# Used when Site Domain Settings has no domain for the portal
DEFAULT_PORTAL_DOMAINS = {
	NLSAT_DOMESTIC_TICKET_TYPE: "nlsatadmissions.nls.ac.in",
	NLSAT_INTERNATIONAL_TICKET_TYPE: "internationaladmissions.nls.ac.in",
}


def get_nlsat_applicant_helpdesk_url(raised_by: str, ticket_type: str | None = None) -> str | None:
	"""
	https://<portal domain>/nlsat/helpdesk for a ticket raised by an NLSAT
	applicant, on the domestic or international portal's domain. None when
	`raised_by` is not an NLSAT applicant.
	"""
	user = resolve_user(raised_by)
	if not user or NLSAT_APPLICANT_ROLE not in frappe.get_roles(user):
		return None

	ticket_type = get_applicant_ticket_type(user, ticket_type)
	return f"https://{get_portal_domain(ticket_type)}{NLSAT_HELPDESK_PATH}"


def get_applicant_ticket_type(user: str, ticket_type: str | None) -> str:
	"""
	NLSAT / NLSAT International for an applicant: their application's
	foriegn_national, then the ticket's own NLSAT type, then the portal they
	signed up on, else domestic. The nlsat_portal cookie is not consulted —
	these emails go out from an agent's request, so it would be the agent's.
	"""
	from_application = _get_nlsat_ticket_type(_get_nlsat_application(user))
	if from_application:
		return from_application
	if ticket_type in NLSAT_TICKET_TYPES:
		return ticket_type
	return get_signup_portal_ticket_type(user) or NLSAT_DOMESTIC_TICKET_TYPE


def get_signup_portal_ticket_type(user: str) -> str | None:
	"""Ticket type for the portal the user signed up on (slcm User.nlsat_portal), else None."""
	try:
		from slcm.admission.utils.nlsat_portal import get_user_signup_portal
	except ImportError:
		return None
	portal_type = get_user_signup_portal(user)
	if not portal_type:
		return None
	if portal_type == SLCM_PORTAL_TYPES[NLSAT_INTERNATIONAL_TICKET_TYPE]:
		return NLSAT_INTERNATIONAL_TICKET_TYPE
	return NLSAT_DOMESTIC_TICKET_TYPE


def get_portal_domain(ticket_type: str) -> str:
	"""The portal's domain from slcm's Site Domain Settings, else its default domain."""
	try:
		from slcm.admission.utils.nlsat_portal import get_portal_domain as get_slcm_portal_domain
	except ImportError:
		get_slcm_portal_domain = None
	domain = get_slcm_portal_domain(SLCM_PORTAL_TYPES[ticket_type]) if get_slcm_portal_domain else None
	return domain or DEFAULT_PORTAL_DOMAINS[ticket_type]
