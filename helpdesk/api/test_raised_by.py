from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helpdesk.api.raised_by import (
    INTERNATIONAL_APPLICANT_LABEL,
    NLSAT_APPLICANT_LABEL,
    get_raised_by_context,
)
from helpdesk.test_utils import (
    create_user,
    make_raised_by_label,
    make_role,
    make_ticket,
    set_raised_by_role_map,
)

ROLE_A = "_Test Raised By Role A"
ROLE_B = "_Test Raised By Role B"
LABEL_A = "_Test Label A"
LABEL_B = "_Test Label B"
USER_EMAIL = "raised-by-test@example.com"


class TestRaisedBy(IntegrationTestCase):
    def setUp(self):
        self.original_role_map = [
            (r.role, r.label)
            for r in frappe.get_single("HD Settings").get("raised_by_role_map") or []
        ]
        make_role(ROLE_A)
        make_role(ROLE_B)
        make_raised_by_label(LABEL_A)
        make_raised_by_label(LABEL_B)
        set_raised_by_role_map([(ROLE_A, LABEL_A), (ROLE_B, LABEL_B)])
        self.user = create_user(USER_EMAIL)
        self.user.remove_roles(ROLE_A, ROLE_B)

    def tearDown(self):
        frappe.set_user("Administrator")
        set_raised_by_role_map(self.original_role_map)

    def test_first_matching_row_wins(self):
        self.user.add_roles(ROLE_B, ROLE_A)
        self.assertEqual(get_raised_by_context(USER_EMAIL)["raised_by_label"], LABEL_A)

        set_raised_by_role_map([(ROLE_B, LABEL_B), (ROLE_A, LABEL_A)])
        self.assertEqual(get_raised_by_context(USER_EMAIL)["raised_by_label"], LABEL_B)

    def test_disabled_label_is_skipped(self):
        self.user.add_roles(ROLE_A, ROLE_B)
        make_raised_by_label(LABEL_A, enabled=0)
        self.assertEqual(get_raised_by_context(USER_EMAIL)["raised_by_label"], LABEL_B)

    def test_unmapped_user_gets_no_label(self):
        self.assertEqual(get_raised_by_context(USER_EMAIL), {})
        self.assertEqual(get_raised_by_context("unknown-user@example.com"), {})

    def test_user_data_source_uses_full_name_and_email(self):
        self.user.add_roles(ROLE_A)
        ctx = get_raised_by_context(f"Someone <{USER_EMAIL}>")
        self.assertEqual(ctx["custom_student_name"], self.user.full_name)
        self.assertEqual(ctx["custom_student_id"], USER_EMAIL)

    def test_faculty_source_falls_back_to_user_without_record(self):
        self.user.add_roles(ROLE_A)
        make_raised_by_label(LABEL_A, data_source="Faculty")
        faculty_ctx = {"custom_student_name": "Dr. X", "custom_student_id": "FAC-001"}

        with patch(
            "helpdesk.api.raised_by.get_faculty_context", return_value=faculty_ctx
        ):
            ctx = get_raised_by_context(USER_EMAIL)
        self.assertEqual(ctx["custom_student_id"], "FAC-001")

        with patch("helpdesk.api.raised_by.get_faculty_context", return_value={}):
            ctx = get_raised_by_context(USER_EMAIL)
        self.assertEqual(ctx["custom_student_id"], USER_EMAIL)
        self.assertEqual(ctx["raised_by_label"], LABEL_A)

    def test_ticket_label_is_set_from_roles_not_client(self):
        self.user.add_roles(ROLE_A)
        ticket = make_ticket(raised_by=USER_EMAIL, custom_raised_by_label=LABEL_B)
        self.assertEqual(ticket.custom_raised_by_label, LABEL_A)
        self.assertEqual(ticket.custom_student_id, USER_EMAIL)

    def test_ticket_keeps_name_and_id_from_portal_form(self):
        self.user.add_roles(ROLE_A)
        ticket = make_ticket(
            raised_by=USER_EMAIL,
            custom_student_name="Form Name",
            custom_student_id="APP-0001",
        )
        self.assertEqual(ticket.custom_student_name, "Form Name")
        self.assertEqual(ticket.custom_student_id, "APP-0001")

    def test_duplicate_role_in_settings_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            set_raised_by_role_map([(ROLE_A, LABEL_A), (ROLE_A, LABEL_B)])

    def test_nlsat_applicant_label_follows_foreign_national(self):
        self.user.add_roles(ROLE_A)
        make_raised_by_label(NLSAT_APPLICANT_LABEL)
        make_raised_by_label(INTERNATIONAL_APPLICANT_LABEL)
        set_raised_by_role_map([(ROLE_A, NLSAT_APPLICANT_LABEL)])

        for foreign_national, expected in (
            ("Yes", INTERNATIONAL_APPLICANT_LABEL),
            ("No", NLSAT_APPLICANT_LABEL),
        ):
            application = frappe._dict(foriegn_national=foreign_national)
            with patch(
                "helpdesk.api.raised_by._get_nlsat_application", return_value=application
            ):
                # The application is the recorded fact, so it beats the ticket type
                ctx = get_raised_by_context(USER_EMAIL, "NLSAT International")
            self.assertEqual(ctx["raised_by_label"], expected)

    def test_nlsat_applicant_without_application_uses_ticket_type(self):
        self.user.add_roles(ROLE_A)
        make_raised_by_label(NLSAT_APPLICANT_LABEL)
        make_raised_by_label(INTERNATIONAL_APPLICANT_LABEL)
        set_raised_by_role_map([(ROLE_A, NLSAT_APPLICANT_LABEL)])

        with patch("helpdesk.api.raised_by._get_nlsat_application", return_value=None):
            international = get_raised_by_context(USER_EMAIL, "NLSAT International")
            domestic = get_raised_by_context(USER_EMAIL, "NLSAT")
        self.assertEqual(international["raised_by_label"], INTERNATIONAL_APPLICANT_LABEL)
        self.assertEqual(domestic["raised_by_label"], NLSAT_APPLICANT_LABEL)

    def test_disabled_international_label_keeps_nlsat_label(self):
        self.user.add_roles(ROLE_A)
        make_raised_by_label(NLSAT_APPLICANT_LABEL)
        make_raised_by_label(INTERNATIONAL_APPLICANT_LABEL, enabled=0)
        set_raised_by_role_map([(ROLE_A, NLSAT_APPLICANT_LABEL)])
        try:
            with patch("helpdesk.api.raised_by._get_nlsat_application", return_value=None):
                ctx = get_raised_by_context(USER_EMAIL, "NLSAT International")
            self.assertEqual(ctx["raised_by_label"], NLSAT_APPLICANT_LABEL)
        finally:
            make_raised_by_label(INTERNATIONAL_APPLICANT_LABEL)
