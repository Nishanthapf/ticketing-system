from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helpdesk.api.nls_student import (
	NLSAT_APPLICANT_ROLE,
	NLSAT_DOMESTIC_TICKET_TYPE,
	NLSAT_INTERNATIONAL_TICKET_TYPE,
)
from helpdesk.api.nlsat_portal import (
	DEFAULT_PORTAL_DOMAINS,
	get_nlsat_applicant_helpdesk_url,
)
from helpdesk.test_utils import create_user, make_role, make_ticket

USER_EMAIL = "nlsat-portal-test@example.com"
DOMESTIC_URL = "https://nlsatadmissions.nls.ac.in/nlsat/helpdesk"
INTERNATIONAL_URL = "https://internationaladmissions.nls.ac.in/nlsat/helpdesk"
MODULE = "helpdesk.api.nlsat_portal"


class TestNlsatPortal(IntegrationTestCase):
	def setUp(self):
		make_role(NLSAT_APPLICANT_ROLE)
		self.user = create_user(USER_EMAIL)
		self.user.add_roles(NLSAT_APPLICANT_ROLE)
		# Site Domain Settings varies per site; pin the portal domains
		self.patches = [
			patch(f"{MODULE}.get_portal_domain", side_effect=DEFAULT_PORTAL_DOMAINS.get),
			patch(f"{MODULE}.get_signup_portal_ticket_type", return_value=None),
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in self.patches:
			p.stop()
		self.user.remove_roles(NLSAT_APPLICANT_ROLE)
		frappe.db.rollback()

	def helpdesk_url(self, foreign_national=None, ticket_type=None):
		application = frappe._dict(foriegn_national=foreign_national) if foreign_national else None
		with patch(f"{MODULE}._get_nlsat_application", return_value=application):
			return get_nlsat_applicant_helpdesk_url(USER_EMAIL, ticket_type)

	def test_application_foreign_national_picks_portal(self):
		self.assertEqual(self.helpdesk_url("Yes"), INTERNATIONAL_URL)
		self.assertEqual(self.helpdesk_url("No"), DOMESTIC_URL)

	def test_application_wins_over_ticket_type(self):
		self.assertEqual(self.helpdesk_url("No", NLSAT_INTERNATIONAL_TICKET_TYPE), DOMESTIC_URL)

	def test_without_application_uses_ticket_type(self):
		self.assertEqual(
			self.helpdesk_url(ticket_type=NLSAT_INTERNATIONAL_TICKET_TYPE), INTERNATIONAL_URL
		)
		self.assertEqual(self.helpdesk_url(ticket_type=NLSAT_DOMESTIC_TICKET_TYPE), DOMESTIC_URL)

	def test_without_application_or_ticket_type_uses_signup_portal(self):
		with patch(
			f"{MODULE}.get_signup_portal_ticket_type",
			return_value=NLSAT_INTERNATIONAL_TICKET_TYPE,
		):
			self.assertEqual(self.helpdesk_url(), INTERNATIONAL_URL)

	def test_defaults_to_domestic(self):
		self.assertEqual(self.helpdesk_url(ticket_type="Technical Issue"), DOMESTIC_URL)

	def test_non_applicant_gets_no_portal_url(self):
		self.user.remove_roles(NLSAT_APPLICANT_ROLE)
		self.assertIsNone(self.helpdesk_url("Yes"))

	def test_ticket_portal_uri_for_applicant(self):
		ticket = make_ticket(save=False, raised_by=USER_EMAIL)
		ticket.name = "TEST-1"
		with patch(f"{MODULE}._get_nlsat_application", return_value=None):
			self.assertEqual(ticket.portal_uri, DOMESTIC_URL)

		self.user.remove_roles(NLSAT_APPLICANT_ROLE)
		self.assertEqual(
			ticket.portal_uri, frappe.utils.get_url("/helpdesk/my-tickets/TEST-1")
		)
