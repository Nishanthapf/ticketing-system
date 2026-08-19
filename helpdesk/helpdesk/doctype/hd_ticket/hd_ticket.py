import json
import uuid
from datetime import timedelta
from email.utils import parseaddr

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.core.page.permission_manager.permission_manager import remove
from frappe.desk.form.assign_to import add as assign
from frappe.desk.form.assign_to import clear as clear_all_assignments
from frappe.desk.form.assign_to import get as get_assignees
from frappe.model.document import Document
from frappe.permissions import add_permission, update_permission_property
from frappe.query_builder import DocType, Order
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime
from pypika.functions import Count
from pypika.queries import Query
from pypika.terms import Criterion

from helpdesk.helpdesk.doctype.hd_settings.helpers import (
    get_default_email_content,
    is_email_content_empty,
)
from helpdesk.helpdesk.doctype.hd_ticket.escalation import (
    apply_escalation_side_effects_if_pending,
    on_agent_reply,
    on_customer_reply,
    on_reopen,
    on_resolved,
    start_escalation_if_enabled,
)
from helpdesk.helpdesk.doctype.hd_ticket_activity.hd_ticket_activity import (
    log_ticket_activity,
)
from helpdesk.helpdesk.utils.email import (
    default_outgoing_email_account,
    default_ticket_outgoing_email_account,
)
from helpdesk.utils import (
    agent_only,
    capture_event,
    get_agents_team,
    get_customers,
    get_doc_room,
    is_admin,
    is_agent,
    publish_event,
)

from ..hd_notification.utils import clear as clear_notifications
from ..hd_service_level_agreement.utils import get_sla


class HDTicket(Document):
    @property
    def default_open_status(self):
        return frappe.db.get_value(
            "HD Service Level Agreement",
            self.sla,
            "default_ticket_status",
        ) or frappe.db.get_single_value("HD Settings", "default_ticket_status")

    @property
    def ticket_reopen_status(self):
        return frappe.db.get_value(
            "HD Service Level Agreement",
            self.sla,
            "ticket_reopen_status",
        ) or frappe.db.get_single_value("HD Settings", "ticket_reopen_status")

    def publish_update(self):
        room = get_doc_room("HD Ticket", self.name)
        publish_event(
            "helpdesk:ticket-update", room=room, data={"ticket_id": self.name}
        )

    def autoname(self):
        return self.name

    def before_insert(self):
        self.generate_key()

    def before_validate(self):
        self.check_update_perms()
        self.set_ticket_type()
        self.set_raised_by()
        self.set_team_from_ticket_type()
        self.set_priority()
        self.set_first_responded_on()
        self.set_feedback_values()
        self.set_default_status()
        self.set_status_category()
        self.set_sla()

        self.set_contact()
        self.set_customer()

    def validate(self):
        self.validate_feedback()
        self.validate_reply_on_close()
        if self.agent_group:
            start_escalation_if_enabled(self)

    def is_closing_status(self):
        if not self.status:
            return False
        if self.status in ("Closed", "Resolved"):
            return True
        if self.status_category in ("Resolved", "Closed"):
            return True
        category = frappe.db.get_value("HD Ticket Status", self.status, "category")
        return category in ("Resolved", "Closed")

    def validate_reply_on_close(self):
        if self.flags.ignore_validate or self.get("is_merged"):
            return

        try:
            from helpdesk.helpdesk.api.nls_student import AUTO_CLOSE_TYPES
            if self.get("ticket_type") in AUTO_CLOSE_TYPES:
                return
        except ImportError:
            pass

        if self.is_closing_status():
            if not self.has_agent_replied:
                frappe.throw(
                    _("Reply is mandatory before closing or resolving the ticket. Please reply to the ticket first."),
                    frappe.ValidationError,
                )


    def before_save(self):
        self.apply_sla()
        if not self.is_new():
            self.handle_ticket_activity_update()

        self.set_resolved_on()
        self.handle_email_feedback()

        if self.is_new():
            self.raised_outside_working_hours = (
                self.is_currently_outside_working_hours()
            )

    def _get_rendered_template(
        self, content: str, default_content: str, args: dict[str, str] | None = None
    ):
        if args is None:
            args = dict()
        template_args = {
            "doc": self.as_dict(),
        }
        for key, value in args.items():
            template_args[key] = value
        return frappe.render_template(
            default_content if is_email_content_empty(content) else content,
            template_args,
        )

    def handle_email_feedback(self):
        if (
            self.is_new()
            or self.via_customer_portal
            or self.feedback_rating
            or not self.has_value_changed("status")
            or not self.key
        ):
            return

        [is_email_feedback_enabled, email_feedback_status] = frappe.get_cached_value(
            "HD Settings",
            "HD Settings",
            ["enable_email_ticket_feedback", "send_email_feedback_on_status"],
        )

        send_feedback_email = int(is_email_feedback_enabled) and (
            email_feedback_status == self.status
            or email_feedback_status == ""
            and self.status == "Closed"
        )

        if not send_feedback_email:
            return

        last_communication = self.get_last_communication()

        url = f"{frappe.utils.get_url()}/ticket-feedback/new?key={self.key}"
        feedback_email_content = frappe.db.get_single_value(
            "HD Settings", "feedback_email_content"
        )
        default_feedback_email_content = get_default_email_content("share_feedback")
        try:
            frappe.sendmail(
                recipients=[self.raised_by],
                subject=f"Re: {self.subject}",
                message=self._get_rendered_template(
                    feedback_email_content,
                    default_feedback_email_content,
                    {"url": url},
                ),
                reference_doctype="HD Ticket",
                reference_name=self.name,
                now=True,
                in_reply_to=last_communication.name if last_communication else None,
                email_headers={"X-Auto-Generated": "hd-email-feedback"},
            )
            frappe.msgprint(_("Feedback email has been sent to the customer"))
        except Exception as e:
            frappe.throw(_("Could not send feedback email,due to: {0}").format(e))

    def after_insert(self):
        apply_escalation_side_effects_if_pending(self)

        # Telemetry Event
        self.capture_ticket_created_telemetry_events()
        publish_event("helpdesk:new-ticket")

        if self.get("description"):
            self.create_communication_via_contact(self.description, new_ticket=True)
            self.handle_inline_media_new_ticket()

        send_ack_email = frappe.db.get_single_value(
            "HD Settings", "send_acknowledgement_email"
        )
        if (
            not self.via_customer_portal
            and not frappe.flags.initial_sync
            and send_ack_email
        ):
            self.send_acknowledgement_email()

    def capture_ticket_created_telemetry_events(self):
        if self.subject == "Welcome to Helpdesk":
            return

        capture_event("ticket_created")
        if not self.via_customer_portal:
            capture_event("ticket_created_via_email")
        if self.via_customer_portal and not is_agent():
            capture_event("ticket_created_via_customer")

        if self.ticket_split_from:
            log_ticket_activity(
                self.name,
                "split the ticket from #{0}".format(self.ticket_split_from),
            )
            capture_event("ticket_split")

    def on_update(self):
        # flake8: noqa
        apply_escalation_side_effects_if_pending(self)
        if self.status_category == "Open":
            if (
                self.get_doc_before_save()
                and self.get_doc_before_save().status_category != "Open"
            ):
                agents = self.get_assigned_agents()
                if agents:
                    for agent in agents:
                        if agent.name == frappe.session.user:
                            continue
                        self.notify_agent(agent.name, "Reaction")

        self.remove_assignment_if_not_in_team()
        self.publish_update()
        self.capture_update_telemetry_events()

    def notify_agent(self, agent, notification_type="Assignment"):
        frappe.get_doc(
            frappe._dict(
                doctype="HD Notification",
                user_from=frappe.session.user,
                reference_ticket=self.name,
                user_to=agent,
                notification_type=notification_type,
            )
        ).insert(ignore_permissions=True)

    def capture_update_telemetry_events(self):
        capture_event("ticket_updated")

        if self.has_value_changed("status"):
            capture_event("ticket_status_updated")
        if (
            self.has_value_changed("status_category")
            and self.status_category == "Resolved"
        ):
            capture_event("ticket_resolved")

    def set_ticket_type(self):
        if self.ticket_type:
            return
        self.ticket_type = (
            frappe.db.get_single_value("HD Settings", "default_ticket_type") or ""
        )

    def set_team_from_ticket_type(self):
        if self.agent_group or not self.ticket_type:
            return

        # Check Type of Issue's own team first — e.g. PACE + "Technical Issues"
        # always routes to a fixed team regardless of the student's programme/year.
        type_of_issue = self.get("custom_type_of_issue") or ""
        if type_of_issue:
            team = frappe.db.get_value("HD Ticket Type Of Issue", type_of_issue, "team")
            if team:
                self.agent_group = team
                return

        # Check programme/year-wise rules first (e.g. PACE)
        programme = self.get("custom_programme") or ""
        current_year = self.get("custom_current_year") or ""

        # Tickets with no client-side form script (e.g. created from inbound
        # email) never get custom_programme/custom_current_year set. Resolve
        # them server-side from Student Master / PACE Application so such
        # tickets still route through the Programme/Year-wise rules instead
        # of falling through to the ticket type's flat team.
        if not programme and self.raised_by:
            from helpdesk.api.nls_student import resolve_programme_context

            ctx = resolve_programme_context(self.raised_by)
            programme = ctx.get("custom_programme") or ""
            current_year = ctx.get("custom_current_year") or current_year
            if programme:
                self.custom_programme = programme
            if current_year:
                self.custom_current_year = current_year

        if programme:
            for assignment_doctype in (
                "PACE Ticket Type Assignment Rule",
                "HD Ticket Type Assignment Rule",
            ):
                rule = frappe.db.get_value(
                    assignment_doctype,
                    {
                        "parent": self.ticket_type,
                        "parenttype": "HD Ticket Type",
                        "programme": programme,
                    },
                    ["team", "current_year"],
                    as_dict=True,
                    order_by="idx asc",
                )
                if rule and rule.team:
                    # If rule has current_year, it must match; blank means all years
                    if not rule.current_year or rule.current_year == current_year:
                        self.agent_group = rule.team
                        return

        # Fall back to the flat team on the ticket type
        team = frappe.db.get_value("HD Ticket Type", self.ticket_type, "team")
        if team:
            self.agent_group = team

    def set_raised_by(self):
        if self.raised_by:
            return
        self.raised_by = frappe.session.user

    def set_contact(self):
        email_id = parseaddr(self.raised_by)[1]
        # flake8: noqa
        if email_id:
            if not self.contact:
                contact = frappe.db.get_value("Contact", {"email_id": email_id})
                if contact:
                    self.contact = contact

    def set_customer(self):
        if not frappe.db.get_single_value(
            "HD Settings", "auto_set_customer_from_contact"
        ):
            return

        # For existing tickets, only validate if customer value has changed
        if not self.is_new() and not self.has_value_changed("customer"):
            return

        contact_customers = get_customers(contact=self.contact) if self.contact else []

        if self.customer:
            if self.customer not in contact_customers and not is_agent():
                frappe.throw(
                    _(
                        "The selected customer {0} is not linked to the contact {1}."
                        "Please select a valid customer or update the contact's linked customers."
                    ).format(self.customer, self.contact),
                    frappe.ValidationError,
                )
            return

        # Auto-set customer only for new tickets
        if self.is_new() and self.contact:
            if len(contact_customers) == 1:
                self.customer = contact_customers[0]
            elif (
                len(contact_customers) > 1
                and not is_agent()
                and self.via_customer_portal
            ):
                frappe.throw(
                    _(
                        "The contact {0} is linked to multiple customers. Please select the customer manually."
                    ).format(self.contact),
                    frappe.ValidationError,
                )

    def set_priority(self):
        if self.priority:
            return
        self.priority = frappe.get_cached_value(
            "HD Ticket Type", self.ticket_type, "priority"
        ) or frappe.get_cached_value("HD Settings", "HD Settings", "default_priority")

    def set_first_responded_on(self):
        if self.is_new():
            return
        if self.first_responded_on:
            return

        old_status_category = (
            self.get_doc_before_save().status_category
            if self.get_doc_before_save()
            else None
        )
        is_closed_or_resolved = (
            old_status_category == "Open" and self.status_category == "Resolved"
        )

        if self.status_category == "Paused" or is_closed_or_resolved:
            self.first_responded_on = frappe.utils.now_datetime()

    def set_feedback_values(self):
        if not self.feedback:
            return
        feedback_option = frappe.get_doc("HD Ticket Feedback Option", self.feedback)
        self.feedback_rating = feedback_option.rating

    @property
    def has_agent_replied(self):
        return bool(
            frappe.db.exists(
                "Communication",
                {
                    "reference_doctype": "HD Ticket",
                    "reference_name": self.name,
                    "sent_or_received": "Sent",
                    "communication_type": ["!=", "Automated Message"],
                },
            )
        )




    def validate_feedback(self):
        is_feedback_mandatory = frappe.get_cached_value(
            "HD Settings", "HD Settings", "is_feedback_mandatory"
        )
        if (
            self.feedback_rating
            or self.status_category != "Resolved"
            or is_agent()
            or not self.has_agent_replied
            or not is_feedback_mandatory
        ):
            return

        frappe.throw(
            _("Ticket must be resolved with a feedback"), frappe.ValidationError
        )

    def check_update_perms(self):
        if (
            self.is_new()
            or is_agent()
            or not self.via_customer_portal
            or self.flags.allow_customer_reopen
        ):
            return
        old_doc = self.get_doc_before_save()
        is_closed = old_doc.status == "Closed"
        is_rated = bool(old_doc.feedback)
        if is_closed or is_rated:
            text = _("Closed or rated tickets cannot be updated by non-agents")
            frappe.throw(text, frappe.PermissionError)

    def handle_ticket_activity_update(self):
        """
        Handles the ticket activity update.
        Should be called inside on_update
        """
        field_maps = {
            "status": "status",
            "priority": "priority",
            "agent_group": "team",
            "ticket_type": "type",
            "contact": "contact",
            "sla": "SLA",
        }
        for field in [
            "status",
            "priority",
            "agent_group",
            "contact",
            "ticket_type",
            "sla",
        ]:
            if self.has_value_changed(field):
                value = self.as_dict()[field]
                if not value:
                    msg = f"cleared {field_maps[field]}"
                else:
                    msg = f"set {field_maps[field]} to {value}"

                log_ticket_activity(self.name, msg)

    def generate_key(self):
        self.key = uuid.uuid4()

    def remove_assignment_if_not_in_team(self):
        """
        Removes the assignment if the agent is not in the team.
        Should be called inside on_update
        """
        if self.is_new():
            return
        if not self.agent_group or (hasattr(self, "_assign") and not self._assign):
            return
        if self.has_value_changed("agent_group") and self.status_category == "Open":
            current_assigned_agent = self.get_assigned_agent()
            if not current_assigned_agent:
                return
            is_agent_in_assigned_team = self.agent_in_assigned_team(
                current_assigned_agent, self.agent_group
            )

            if (
                not is_agent_in_assigned_team
            ) and self.users_present_in_team_assignment_rule():
                clear_all_assignments("HD Ticket", self.name)

    def agent_in_assigned_team(self, agent, team):
        return frappe.db.exists(
            "HD Team Member",
            {
                "parent": team,
                "user": agent,
            },
        )

    def users_present_in_team_assignment_rule(self):
        if not self.agent_group:
            return False

        assignment_rule = frappe.db.get_value(
            "HD Team", self.agent_group, "assignment_rule"
        )
        if not assignment_rule:
            return False

        is_disabled = frappe.db.get_value(
            "Assignment Rule", assignment_rule, "disabled"
        )
        if is_disabled:
            return False

        users = frappe.get_all(
            "Assignment Rule User", filters={"parent": assignment_rule}
        )
        if not users:
            return False

        return True

    @frappe.whitelist()
    @agent_only
    def assign_agent(self, agent: str):
        assign({"assign_to": [agent], "doctype": "HD Ticket", "name": self.name})

        if frappe.session.user != agent:
            self.notify_agent(agent, "Assignment")

    def get_assigned_agents(self):
        assignees = get_assignees({"doctype": "HD Ticket", "name": self.name})
        if len(assignees) > 0:
            names = [assignee.owner for assignee in assignees]
            return frappe.get_all("HD Agent", filters={"name": ["in", names]})

    def get_assigned_agent(self):
        # TODO: deprecate this
        # for some reason _assign is not set, maybe a framework bug?
        if hasattr(self, "_assign") and self._assign:
            assignees = json.loads(self._assign)
            if len(assignees) > 0:
                # TODO: temporary fix, remove this when only agents can be assigned to ticket
                exists = frappe.db.exists("HD Agent", assignees[0])
                if exists:
                    return assignees[0]

        assignees = get_assignees({"doctype": "HD Ticket", "name": self.name})
        if len(assignees) > 0:
            # TODO: temporary fix, remove this when only agents can be assigned to ticket
            return frappe.db.exists("HD Agent", assignees[0].owner)

        return None

    def on_trash(self):
        activities = frappe.db.get_all("HD Ticket Activity", {"ticket": self.name})
        for activity in activities:
            frappe.db.delete("HD Ticket Activity", activity)

        comments = frappe.db.get_all(
            "HD Ticket Comment", {"reference_ticket": self.name}
        )
        for comment in comments:
            frappe.db.delete("HD Ticket Comment", comment)

    def skip_email_workflow(self):
        skip: str = frappe.get_value("HD Settings", None, "skip_email_workflow") or "0"

        return bool(int(skip))

    def _resolve_sender_email(self, email_account_name, from_email_id):
        if not email_account_name:
            sender_email = self.sender_email()
            return sender_email, (sender_email.name if sender_email else None)

        if not frappe.db.exists("Email Account", email_account_name):
            frappe.throw(_("No Email Account found for {0}").format(from_email_id))

        sender_email = frappe._dict(name=email_account_name, email_id=from_email_id)
        return sender_email, email_account_name

    def instantly_send_email(self):
        check: str = (
            frappe.get_value("HD Settings", None, "instantly_send_email") or "0"
        )

        return bool(int(check))

    @frappe.whitelist()
    def reopen_ticket(self, reason: str):
        """
        Customer-initiated reopen of a Closed ticket, allowed only within a 24
        hour window of resolution and only with a mandatory reason. Distinct
        from the implicit reopen-on-email-reply path in `on_communication_update`
        — this is the explicit "Reopen Ticket" action on the customer portal.
        """
        reason = (reason or "").strip()
        if not reason:
            frappe.throw(_("Please provide a reason for reopening this ticket."))

        if frappe.session.user not in (self.contact, self.raised_by, self.owner):
            frappe.throw(_("Not permitted"), frappe.PermissionError)

        if self.status != "Closed":
            frappe.throw(_("Only closed tickets can be reopened."))

        if not self.resolution_date:
            frappe.throw(_("This ticket has no resolution date and cannot be reopened."))

        if now_datetime() - get_datetime(self.resolution_date) > timedelta(hours=24):
            frappe.throw(
                _("This ticket can no longer be reopened (the 24 hour window has passed).")
            )

        self.reopen_reason = reason
        self.status = self.ticket_reopen_status or self.default_open_status
        self.flags.allow_customer_reopen = True
        self.save(ignore_permissions=True)

        log_ticket_activity(self.name, _("reopened the ticket: {0}").format(reason))
        on_reopen(self)

    @frappe.whitelist()
    def get_last_communication(self):
        filters = {
            "reference_doctype": "HD Ticket",
            "reference_name": ["=", str(self.name)],
        }

        try:
            communication = frappe.get_last_doc(
                "Communication",
                filters=filters,
            )

            return communication
        except Exception:
            return None

    def last_communication_email(self):
        if not (communication := self.get_last_communication()):
            return

        if not communication.email_account:
            return

        email_account = frappe.get_doc("Email Account", communication.email_account)

        if not email_account.enable_outgoing:
            return

        return email_account

    def sender_email(self):
        """
        Find an email to use as sender. Fall back through multiple choices

        :return: `Email Account`
        """
        if email_account := self.last_communication_email():
            return email_account

        if email_account := default_ticket_outgoing_email_account():
            return email_account

        if email_account := default_outgoing_email_account():
            return email_account

    @property
    def portal_uri(self):
        root_uri = frappe.utils.get_url()
        # Check if the ticket was raised by a student; if so, link to the student portal
        is_student = frappe.db.exists("Student Master", {"user": self.raised_by}) or \
            frappe.db.exists("Student Master", {"email": self.raised_by}) or \
            frappe.db.exists("Student Master", {"official_email_id": self.raised_by})
        if is_student:
            return f"{root_uri}/student-portal/support?ticket={self.name}"
        return f"{root_uri}/helpdesk/my-tickets/{self.name}"

    def check_escalation_read_only(self):
        """
        Guard for reply/comment-creation methods: if the acting user is the
        ticket's current escalation-level assignee and that level's access is
        "Read Only", block the mutating action. Reply Only / Read & Reply /
        Full Access tiers are unaffected — this only ever blocks, never grants.
        """
        if self.escalation_access_level != "Read Only":
            return
        if not self.current_escalation_level or not self.agent_group:
            return
        user = frappe.session.user
        team = frappe.get_cached_doc("HD Team", self.agent_group)
        current_row = next(
            (r for r in team.escalation_levels if r.level == self.current_escalation_level),
            None,
        )
        if current_row and current_row.assigned_to == user:
            frappe.throw(
                _(
                    "You have read-only access to this ticket at your current escalation level."
                ),
                frappe.PermissionError,
            )

    @frappe.whitelist()
    def new_comment(self, content: str, attachments: list[str] = []):
        if not is_agent():
            frappe.throw(
                _("You are not permitted to add a comment"), frappe.PermissionError
            )
        self.check_escalation_read_only()
        c = frappe.new_doc("HD Ticket Comment")
        c.commented_by = frappe.session.user
        c.content = content
        c.is_pinned = False
        c.reference_ticket = self.name
        c.save()
        for attachment in attachments:
            self.attach_file_with_doc(
                "HD Ticket Comment", c.name, attachment.get("file_url")
            )

    @frappe.whitelist()
    @agent_only
    def reply_via_agent(
        self,
        message: str,
        from_email: dict | None = None,
        to: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        attachments: list[str] = [],
    ):
        if not is_agent():
            frappe.throw(
                _("You are not permitted to reply as an agent"), frappe.PermissionError
            )
        self.check_escalation_read_only()
        skip_email_workflow = self.skip_email_workflow()
        medium = "" if skip_email_workflow else "Email"
        subject = f"Re: {self.subject}"
        from_email_id = from_email.get("email_id") if from_email else None
        email_account_name = from_email.get("email_account") if from_email else None
        sender = from_email_id or frappe.session.user
        recipients = to

        sender_email = None
        if not skip_email_workflow:
            sender_email, email_account_name = self._resolve_sender_email(
                email_account_name, from_email_id
            )

        if recipients == "Administrator":
            recipients = frappe.get_value("User", "Administrator", "email")

        communication = frappe.get_doc(
            {
                "bcc": bcc,
                "cc": cc,
                "communication_medium": medium,
                "communication_type": "Communication",
                "content": message,
                "doctype": "Communication",
                "email_account": email_account_name,
                "email_status": "Open",
                "recipients": recipients,
                "reference_doctype": "HD Ticket",
                "reference_name": self.name,
                "sender": sender,
                "sent_or_received": "Sent",
                "status": "Linked",
                "subject": subject,
            }
        )

        last_communication = self.get_last_communication()
        if last_communication and last_communication.message_id:
            communication.in_reply_to = last_communication.name

        communication.insert(ignore_permissions=True)
        capture_event("agent_replied")

        _attachments = []

        for attachment in attachments:
            file_url = frappe.db.get_value("File", attachment, "file_url")
            self.attach_file_with_doc("Communication", communication.name, file_url)
            self.attach_file_with_doc("HD Ticket", self.name, file_url)
            _attachments.append({"file_url": file_url})

        if skip_email_workflow or not frappe.db.get_single_value(
            "HD Settings", "enable_reply_email_via_agent"
        ):
            return

        if not sender_email:
            frappe.throw(
                _("Unable to send email. Please setup default outgoing email account.")
            )

        message = self.parse_content(message)

        reply_to_email = sender_email.email_id
        rendered_template: str | None = None
        if self.via_customer_portal:
            email_content = frappe.db.get_single_value(
                "HD Settings", "reply_via_agent_email_content"
            )
            default_email_content = get_default_email_content("reply_via_agent")
            try:
                rendered_template = self._get_rendered_template(
                    email_content,
                    default_email_content,
                    {"message": message, "ticket_url": self.portal_uri},
                )
            except Exception as e:
                frappe.throw(_("Could not an email due to: {0}").format(e))

        send_delayed = True
        send_now = False

        if self.instantly_send_email():
            send_delayed = False
            send_now = True

        try:
            frappe.sendmail(
                attachments=_attachments,
                bcc=bcc,
                cc=cc,
                communication=communication.name,
                delayed=send_delayed,
                expose_recipients="header",
                message=rendered_template if rendered_template is not None else message,
                as_markdown=True,
                now=send_now,
                recipients=recipients,
                reference_doctype="HD Ticket",
                reference_name=self.name,
                reply_to=reply_to_email,
                sender=reply_to_email,
                subject=subject,
                with_container=False,
                in_reply_to=(
                    last_communication.name if last_communication.name else None
                ),
            )
        except Exception as e:
            frappe.throw(_(e))

    @frappe.whitelist()
    # flake8: noqa
    def create_communication_via_contact(
        self, message: str, attachments: list[dict] = [], new_ticket: bool = False
    ):
        if not new_ticket and frappe.db.get_single_value(
            "HD Settings", "enable_reply_email_to_agent"
        ):
            # send email to assigned agents
            self.send_reply_email_to_agent(message)

        # if self.status_category == "Paused" and not new_ticket:
        if not new_ticket:
            self.status = self.ticket_reopen_status
            self.save(ignore_permissions=True)

        c = frappe.new_doc("Communication")
        c.communication_type = "Communication"
        c.communication_medium = "Email"
        c.sent_or_received = "Received"
        c.email_status = "Open"
        c.subject = f"Re: {self.subject}"
        c.sender = frappe.session.user
        c.content = message
        c.status = "Linked"
        c.reference_doctype = "HD Ticket"
        c.reference_name = self.name
        c.ignore_permissions = True
        c.ignore_mandatory = True
        c.save(ignore_permissions=True)

        _attachments = self.get("attachments") or attachments or []
        if not len(_attachments):
            return
        QBFile = frappe.qb.DocType("File")
        condition_name = [QBFile.name == i["name"] for i in _attachments]
        frappe.qb.update(QBFile).set(QBFile.attached_to_name, c.name).set(
            QBFile.attached_to_doctype, "Communication"
        ).where(Criterion.any(condition_name)).run()

        # attach files to ticket
        file_urls = frappe.get_all(
            "File", filters={"attached_to_name": c.name}, pluck="file_url"
        )
        for url in file_urls:
            self.attach_file_with_doc("HD Ticket", self.name, url)

    def handle_inline_media_new_ticket(self):
        soup = BeautifulSoup(self.description, "html.parser")
        files = []  # List of file URLs
        for tag in soup.find_all(["img", "video"]):
            if tag.has_attr("src"):
                src = tag["src"]
                files.append(src)
        for f in files:
            file = frappe.db.exists(
                "File",
                {
                    "file_url": f,
                    "attached_to_doctype": ["is", "Not Set"],
                    "owner": frappe.session.user,
                },
            )
            if file:
                doc = frappe.get_doc("File", file)
                doc.attached_to_doctype = "HD Ticket"
                doc.attached_to_name = self.name
                doc.save()

    def send_reply_email_to_agent(
        self, message: str = "Please check the latest update on the portal."
    ):
        assigned_agents = self.get_assigned_agents()
        if not assigned_agents:
            return

        recipients = [a.get("name") for a in self.get_assigned_agents()]

        email_content = frappe.db.get_single_value(
            "HD Settings", "reply_email_to_agent_content"
        )
        default_email_content = get_default_email_content("reply_to_agents")
        try:
            frappe.sendmail(
                recipients=recipients,
                subject=f"Re: {self.subject} - #{self.name}",
                message=self._get_rendered_template(
                    email_content,
                    default_email_content,
                    {
                        "ticket_url": frappe.utils.get_url(
                            "/helpdesk/tickets/" + str(self.name)
                        ),
                        "message": message,
                    },
                ),
                reference_doctype="HD Ticket",
                reference_name=self.name,
                now=True,
            )
        except Exception as e:
            frappe.throw(_(e))

    def send_acknowledgement_email(self):
        acknowledgement_email_content = frappe.db.get_single_value(
            "HD Settings", "acknowledgement_email_content"
        )
        default_acknowledgement_email_content = get_default_email_content(
            "acknowledgement"
        )

        try:
            frappe.sendmail(
                recipients=[self.raised_by],
                subject=_("Ticket #{0}: We've received your request").format(self.name),
                message=self._get_rendered_template(
                    acknowledgement_email_content,
                    default_acknowledgement_email_content,
                ),
                reference_doctype="HD Ticket",
                reference_name=self.name,
                now=True,
                expose_recipients="header",
                email_headers={"X-Auto-Generated": "hd-acknowledgement"},
            )
        except Exception as e:
            frappe.throw(
                _("Could not send an acknowledgement email due to: {0}").format(e)
            )

    @frappe.whitelist()
    def mark_seen(self):
        self.add_viewed(
            unique_views=True, force=True
        )  # Document class method, no way to add unique_views via document settings, hence used force and unique_views=True
        self.add_seen()
        clear_notifications(ticket=str(self.name))

    def set_sla(self):
        """
        Find an SLA to apply to this ticket.
        """
        if sla := get_sla(self):
            self.sla = sla.name

    def apply_sla(self):
        """
        Apply SLA if set.
        """
        if sla := frappe.get_last_doc("HD Service Level Agreement", {"name": self.sla}):
            sla.apply(self)

    def get_sla(self):
        return frappe.get_doc("HD Service Level Agreement", {"name": self.sla})

    def is_currently_outside_working_hours(self):
        """Return True if current time is outside this SLA's working hours."""

        sla = self.get_sla()
        current_date = getdate()
        now = now_datetime()

        current_td = timedelta(
            hours=now.hour,
            minutes=now.minute,
            seconds=now.second,
            microseconds=now.microsecond,
        )

        day_name = current_date.strftime("%A")
        Holiday = DocType("HD Holiday")

        # Check holidays for this SLA
        holidays = (
            frappe.qb.from_(Holiday)
            .select(Holiday.holiday_date)
            .where(Holiday.parent == sla.name)
            .run(pluck=True)
        )

        if current_date in holidays:
            return True

        working_hours = sla.get_working_hours()
        # No working hours today
        if day_name not in working_hours:
            return True

        start_time, end_time = working_hours[day_name]

        # Outside working hours
        if not (start_time <= current_td < end_time):
            return True
        return False

    def set_default_status(self):
        if self.is_new():
            self.status = self.default_open_status

    def set_status_category(self):
        self.status_category = self.status_category or frappe.get_value(
            "HD Ticket Status",
            self.status,
            "category",
        )

    def set_resolved_on(self):
        """
        Stamps `resolved_on` whenever the ticket enters the Resolved category, and
        clears it as soon as it leaves. This powers auto-close of resolved tickets
        (see `auto_close_resolved_tickets`) independent of whether an SLA is set,
        and naturally restarts/cancels the countdown on Resolved -> Open -> Resolved.
        """
        if self.is_new() or not self.has_value_changed("status_category"):
            return
        self.resolved_on = (
            frappe.utils.now_datetime() if self.status_category == "Resolved" else None
        )
        if self.status_category == "Resolved":
            on_resolved(self)

    def get_merge_target(self):
        # Follow the chain of merged tickets to the final, non-merged ticket. Return None
        # if the chain dead-ends on a missing ticket or loops back on itself (a corrupt
        # cycle), so a reply is never redirected onto another merged ticket.
        current_ticket_name = self.merged_with
        visited_ticket_names = {self.name}
        while current_ticket_name and current_ticket_name not in visited_ticket_names:
            ticket = frappe.db.get_value(
                "HD Ticket",
                current_ticket_name,
                ["is_merged", "merged_with"],
                as_dict=True,
            )
            if not ticket:
                return None
            visited_ticket_names.add(current_ticket_name)
            if not ticket.is_merged:
                return current_ticket_name
            if not ticket.merged_with:
                return None
            current_ticket_name = ticket.merged_with
        return None

    def redirect_communication_to_merge_target(self, communication):
        merge_target_name = self.get_merge_target()
        if not merge_target_name:
            return False
        communication.db_set("reference_name", merge_target_name)
        merge_target = frappe.get_doc("HD Ticket", merge_target_name)
        merge_target.on_communication_update(communication)
        return True

    # `on_communication_update` is a special method exposed from `Communication` doctype.
    # It is called when a communication is updated. Beware of changes as this effectively
    # is an external dependency. Refer `communication.py` of Frappe framework for more.
    # Since this is called from communication itself, `c` is the communication doc.
    def on_communication_update(self, c):
        # A reply to a merged ticket belongs to its merge target; redirect it there. If no
        # safe target resolves (cycle/dead-end), fall through and handle it here so the
        # reply isn't dropped.
        if c.sent_or_received == "Received" and self.is_merged and self.merged_with:
            if self.redirect_communication_to_merge_target(c):
                return

        # If communication is incoming, then it is a reply from customer, and ticket must
        # be reopened.
        # handle re opening tickets for email
        if c.sent_or_received == "Received":
            # check if agent has replied
            was_resolved_category = self.status_category == "Resolved"

            if self.has_agent_replied:
                self.status = self.ticket_reopen_status
            else:
                self.status = self.default_open_status
            # if received that means customer has replied
            self.last_customer_response = frappe.utils.now_datetime()

            # Escalation: a customer reply always re-arms the current level's
            # timer at the same level. If this reply is what reopens a
            # Resolved-equivalent ticket, on_reopen() covers the same
            # state transition — both are idempotent (same end state), so
            # calling both here is safe and avoids relying on save-time
            # status_category diffing for the reopen case.
            if was_resolved_category:
                on_reopen(self)
            else:
                on_customer_reply(self)
        # If communication is outgoing, it must be a reply from agent
        if c.sent_or_received == "Sent":
            # Ignore system notifications
            if c.communication_type and c.communication_type == "Automated Message":
                return
            # Set first response date if not set already
            self.first_responded_on = (
                self.first_responded_on or frappe.utils.now_datetime()
            )
            self.last_agent_response = frappe.utils.now_datetime()
            on_agent_reply(self)

            # TODO: remove this feature once we add automation feature
            if frappe.db.get_single_value("HD Settings", "auto_update_status"):
                self.status = frappe.db.get_single_value(
                    "HD Settings", "update_status_to"
                )

        # Fetch description from communication if not set already. This might not be needed
        # anymore as a communication is created when a ticket is created.
        self.description = self.description or c.content
        # Save the ticket, allowing for hooks to run.
        self.save(
            ignore_permissions=c.get("ignore_permissions")
            or c.flags.ignore_permissions
            or self.flags.ignore_permissions
        )


    def attach_file_with_doc(self, doctype, docname, file_url):
        if frappe.db.exists(
            "File",
            {
                "file_url": file_url,
                "attached_to_doctype": doctype,
                "attached_to_name": docname,
            },
        ):
            return
        file_doc = frappe.new_doc("File")
        file_doc.attached_to_doctype = doctype
        file_doc.attached_to_name = docname
        file_doc.file_url = file_url
        file_doc.save(ignore_permissions=True)

    @staticmethod
    def default_list_data(show_customer_portal_fields=False):
        columns = [
            {
                "label": "ID",
                "type": "Int",
                "key": "name",
                "width": "auto",
            },
            {
                "label": "Subject",
                "type": "Data",
                "key": "subject",
                "width": "25rem",
            },
            {
                "label": "Status",
                "type": "Select",
                "key": "status",
                "width": "8rem",
            },
            {
                "label": "First Response",
                "type": "Datetime",
                "key": "response_by",
                "width": "8rem",
            },
            {
                "label": "Resolution",
                "type": "Datetime",
                "key": "resolution_by",
                "width": "8rem",
            },
            {
                "label": "Assigned To",
                "type": "MultipleAvatar",
                "key": "_assign",
                "width": "8rem",
            },
            {
                "label": "Customer",
                "type": "Link",
                "key": "customer",
                "options": "HD Customer",
                "width": "8rem",
            },
            {
                "label": "Priority",
                "type": "Link",
                "options": "HD Ticket Priority",
                "key": "priority",
                "width": "10rem",
            },
            {
                "label": "Type",
                "type": "Link",
                "options": "HD Ticket Type",
                "key": "ticket_type",
                "width": "11rem",
            },
            {
                "label": "Team",
                "type": "Link",
                "options": "HD Team",
                "key": "agent_group",
                "width": "10rem",
            },
            {
                "label": "Contact",
                "type": "Link",
                "key": "contact",
                "options": "Contact",
                "width": "8rem",
            },
            {
                "label": "Rating",
                "type": "Rating",
                "key": "feedback_rating",
                "width": "10rem",
            },
            {
                "label": "Created",
                "type": "Datetime",
                "key": "creation",
                "options": "Contact",
                "width": "8rem",
            },
        ]
        customer_portal_columns = [
            {
                "label": "ID",
                "type": "Int",
                "key": "name",
                "width": "5rem",
            },
            {
                "label": "Subject",
                "type": "Data",
                "key": "subject",
                "width": "22rem",
            },
            {
                "label": "Status",
                "type": "Select",
                "key": "status",
                "width": "11rem",
            },
            {
                "label": "Priority",
                "type": "Link",
                "options": "HD Ticket Priority",
                "key": "priority",
                "width": "10rem",
            },
            {
                "label": "First response",
                "type": "Datetime",
                "key": "response_by",
                "width": "8rem",
            },
            {
                "label": "Resolution",
                "type": "Datetime",
                "key": "resolution_by",
                "width": "8rem",
            },
            {
                "label": "Team",
                "type": "Link",
                "options": "HD Team",
                "key": "agent_group",
                "width": "10rem",
            },
            {
                "label": "Created",
                "type": "Datetime",
                "key": "creation",
                "options": "Contact",
                "width": "8rem",
            },
        ]
        rows = [
            "name",
            "subject",
            "status",
            "priority",
            "ticket_type",
            "agent_group",
            "contact",
            "agreement_status",
            "response_by",
            "resolution_by",
            "customer",
            "first_responded_on",
            "modified",
            "creation",
            "_assign",
            "resolution_date",
        ]
        return {
            "columns": (
                customer_portal_columns if show_customer_portal_fields else columns
            ),
            "rows": rows,
        }

    def parse_content(self, content):
        """
        Finds 'src' attribute of img/video and replaces it  with 'embed' attribute
        embed tag is important because framework replaces it with <img src="cid:content_id">
        this in turn is displayed as an image in the mail sent to the customer
        """
        if not content:
            return ""

        soup = BeautifulSoup(content, "html.parser")

        for tag in soup.find_all(["img", "video"]):
            if tag.name == "img":
                tag["embed"] = tag.get("src")
            elif tag.name == "video":
                tag["embed"] = tag.get("src")

        return str(soup)

    @staticmethod
    def filter_standard_fields(fields):
        for f in fields:
            if f["name"] in customer_not_allowed_fields:
                fields.remove(f)
        return fields


# Check if `user` has access to this specific ticket (`doc`). This implements extra
# permission checks which is not possible with standard permission system. This function
# is being called from hooks. `doc` is the ticket to check against
def _role_has_doctype_permission(user: str, ptype: str) -> bool:
    """
    Return True if any role assigned to user has the given ptype on HD Ticket.
    Mirrors Frappe's own logic: if Custom DocPerm rows exist for this doctype,
    they completely replace DocPerm rows (Role Permissions Manager takes over).
    """
    roles = frappe.get_roles(user)
    has_custom = frappe.db.exists("Custom DocPerm", {"parent": "HD Ticket", "permlevel": 0})
    table = "Custom DocPerm" if has_custom else "DocPerm"
    return bool(
        frappe.db.exists(
            table,
            {"parent": "HD Ticket", "role": ["in", roles], ptype: 1, "permlevel": 0},
        )
    )


MUTATING_PTYPES = {"write", "delete", "submit", "cancel", "create"}


def has_permission(doc, ptype=None, user=None, **kwargs):
    user = user or frappe.session.user
    if is_admin(user):
        return True
    if (
        user in (doc.contact, doc.raised_by, doc.owner)
        or _is_customer_manager(doc.customer, user)
    ):
        # Non-admin, non-agent users must also hold role-level read permission.
        if not is_admin(user) and not is_agent(user):
            if not _role_has_doctype_permission(user, "read"):
                return False
        return True
    if not is_agent(user):
        return False
    if not _agent_has_permission(doc, user):
        return False
    # Additive floor/ceiling restriction layered on top for the specific
    # escalation assignee only, not a full override of the permission model —
    # broader grants above (admin, owner/contact, agent-team access) already
    # won and returned before this point.
    if _is_read_only_escalation_assignee(doc, user) and ptype in MUTATING_PTYPES:
        return False
    return True


def _is_read_only_escalation_assignee(doc, user: str) -> bool:
    """
    True if `user` is the ticket's CURRENT escalation-level assignee (per the
    team's escalation_levels config, not merely someone who happens to be
    assigned) AND that level's access is "Read Only".
    """
    if doc.get("escalation_access_level") != "Read Only":
        return False
    if not doc.get("current_escalation_level") or not doc.get("agent_group"):
        return False

    # Must actually be an assignee on the ticket (ToDo owner).
    assignees = doc.get("_assign")
    if assignees:
        try:
            if user not in json.loads(assignees):
                return False
        except (ValueError, TypeError):
            return False
    else:
        return False

    team = frappe.get_cached_doc("HD Team", doc.agent_group)
    current_row = next(
        (r for r in team.escalation_levels if r.level == doc.current_escalation_level),
        None,
    )
    return bool(current_row and current_row.assigned_to == user)


def _is_customer_manager(customer: str, user: str) -> bool:
    return any(
        c.get("name") == customer and c.get("is_manager")
        for c in get_customers(user, get_roles=True)
    )


def _agent_has_permission(doc, user: str) -> bool:
    if not frappe.db.get_single_value("HD Settings", "restrict_tickets_by_agent_group"):
        return True
    show_tickets_without_team = frappe.db.get_single_value(
        "HD Settings", "do_not_restrict_tickets_without_an_agent_group"
    )
    if show_tickets_without_team and not doc.get("agent_group"):
        return True

    if doc.get("_assign"):
        try:
            if user in json.loads(doc._assign):
                return True
        except (ValueError, TypeError):
            return False

    teams = get_agents_team()
    if any(team.get("ignore_restrictions") for team in teams):
        return True

    team_names = [t.team_name for t in teams]
    is_team_member = frappe.db.exists(
        "HD Team Member", {"parent": ["in", team_names], "user": frappe.session.user}
    )
    return bool(is_team_member) and doc.get("agent_group") in team_names


# Custom perms for list query. Only the `WHERE` part
# https://frappeframework.com/docs/user/en/python-api/hooks#modify-list-query
def permission_query(user: str | None = None):
    user = user or frappe.session.user
    if is_admin(user):
        return
    if not is_agent(user):
        return _customer_query(user)
    return _agent_query(user)


def _customer_query(user: str) -> str:
    """Non-agents see their own tickets, plus all tickets of customers they manage."""
    query = _get_base_visibility(user)
    managed_customers = _get_managed_customers(user)
    if managed_customers:
        query += " OR " + _build_in_clause("customer", managed_customers)
    return query


def _agent_query(user: str) -> str | None:
    query = _get_base_visibility(user)

    if not frappe.db.get_single_value("HD Settings", "restrict_tickets_by_agent_group"):
        return  # Restrictions disabled, return all tickets

    show_tickets_without_team = frappe.db.get_single_value(
        "HD Settings", "do_not_restrict_tickets_without_an_agent_group"
    )
    if show_tickets_without_team:
        query += " OR (`tabHD Ticket`.agent_group is null OR `tabHD Ticket`.agent_group = '')"

    # An agent on a team with `ignore_restrictions` set can see every team's tickets.
    teams = get_agents_team()
    if any(team.get("ignore_restrictions") for team in teams):
        all_teams = frappe.get_all("HD Team", pluck="name")
        if not all_teams:
            return query
        query += " OR (" + _build_in_clause("agent_group", all_teams) + ")"
        if not show_tickets_without_team:
            query += " OR (`tabHD Ticket`.agent_group is null)"
        return query

    query += " OR (JSON_SEARCH(`tabHD Ticket`._assign, 'all', {u}) IS NOT NULL)".format(
        u=frappe.db.escape(user)
    )
    team_names = [t.get("team_name") for t in teams]
    if team_names:
        query += " OR (" + _build_in_clause("agent_group", team_names) + ")"
    return query


def _get_base_visibility(user: str) -> str:
    """WHERE fragment for tickets a user is directly tied to: owner, contact, or raiser."""

    return "(`tabHD Ticket`.owner = {u} OR `tabHD Ticket`.contact = {u} OR `tabHD Ticket`.raised_by = {u})".format(
        u=frappe.db.escape(user)
    )


def _get_managed_customers(user: str) -> list[str]:
    return [
        str(c.get("name"))
        for c in get_customers(user, get_roles=True)
        if c.get("is_manager")
    ]


def _build_in_clause(field: str, values: list[str]) -> str:
    _values = ", ".join(frappe.db.escape(v) for v in values)
    return f"`tabHD Ticket`.{field} in ({_values})"


def set_guest_ticket_creation_permission():
    doctype = "HD Ticket"
    add_permission(doctype, "Guest", 0)

    role = "Guest"
    permlevel = 0
    ptype = ["read", "write", "create", "if_owner"]

    for p in ptype:
        # update permissions
        update_permission_property(doctype, role, permlevel, p, 1)


def remove_guest_ticket_creation_permission():
    doctype = "HD Ticket"
    role = "Guest"
    permlevel = 0
    remove(doctype, role, permlevel, 1)


customer_not_allowed_fields = ["customer"]


def close_tickets_after_n_days():
    if frappe.db.get_single_value("HD Settings", "auto_close_tickets") == 0:
        return

    status, days_threshold = frappe.db.get_value(
        "HD Settings", "HD Settings", ["auto_close_status", "auto_close_after_days"]
    )
    days_threshold = cint(days_threshold)

    # Compute the cutoff in the system timezone to match how communication_date is
    # stored. Using the database's NOW() instead would select the wrong tickets when
    # the DB server runs in a different timezone (e.g. UTC) than the Frappe system.
    inactivity_cutoff = add_to_date(now_datetime(), days=-days_threshold)

    tickets_to_close = (
        frappe.db.sql(
            """
                SELECT t.name
                FROM `tabHD Ticket` t
                INNER JOIN (
                    SELECT reference_name, MAX(communication_date) as last_communication_date
                    FROM `tabCommunication`
                    WHERE reference_doctype = 'HD Ticket'
                    GROUP BY reference_name
                ) latest_comm ON t.name = latest_comm.reference_name
                WHERE t.status = %(status)s
                AND latest_comm.last_communication_date < %(inactivity_cutoff)s
            """,
            {"inactivity_cutoff": inactivity_cutoff, "status": status},
            pluck="name",
        )
        or []
    )
    tickets_to_close = list(set(tickets_to_close))

    # cant do set_value because SLA will not be applied as setting directly to db and doc is not running.
    for ticket in tickets_to_close:
        doc = frappe.get_doc("HD Ticket", ticket)
        doc.status = "Closed"
        doc.flags.ignore_validate = True
        try:
            doc.save(ignore_permissions=True)
            # activity log for auto closing the ticket
            log_ticket_activity(
                doc.name,
                f"automatically closed the ticket after {days_threshold} day{'s' if days_threshold > 1 else ''} of inactivity",
            )
        except Exception as e:
            frappe.log_error(
                message=f"Failed to auto close ticket {doc.name} after {days_threshold} days. Error: {e}",
                title="Auto Close Ticket Failed",
            )
            continue

        frappe.db.commit()  # nosemgrep


def auto_close_resolved_tickets():
    """
    Scheduled task (runs hourly) — closes tickets that have been sitting in a
    Resolved status for longer than the configured `auto_close_resolved_after`
    duration (HD Settings -> Ticket Settings). The timer is `resolved_on`, which
    is stamped/cleared purely by status-category transitions (see
    `HDTicket.set_resolved_on`), so a ticket reopened before the deadline is
    naturally excluded, and a fresh Resolved transition restarts the countdown.
    """
    settings = frappe.get_cached_doc("HD Settings")
    if not settings.enable_auto_close_resolved_tickets:
        return

    duration_seconds = cint(settings.auto_close_resolved_after) or 86400
    cutoff = add_to_date(now_datetime(), seconds=-duration_seconds)

    closed_status = frappe.db.get_value(
        "HD Ticket Status", {"category": "Closed"}, "name"
    )
    if not closed_status:
        frappe.log_error(
            title="Auto Close Resolved Tickets",
            message="No HD Ticket Status with category 'Closed' found. Skipping run.",
        )
        return

    candidate_tickets = frappe.get_all(
        "HD Ticket",
        filters={
            "status_category": "Resolved",
            "resolved_on": ["<=", cutoff],
        },
        pluck="name",
    )

    for name in candidate_tickets:
        doc = frappe.get_doc("HD Ticket", name)

        # Re-check against current state: the ticket may have been reopened (or
        # already closed) between the query above and this fetch.
        if doc.status_category != "Resolved" or not doc.resolved_on:
            continue
        if get_datetime(doc.resolved_on) > cutoff:
            continue

        doc.status = closed_status
        doc.flags.ignore_validate = True
        try:
            doc.save(ignore_permissions=True)
            log_ticket_activity(
                doc.name,
                f"automatically closed the ticket after being resolved for "
                f"{duration_seconds // 3600} hour(s)",
            )
        except Exception as e:
            frappe.log_error(
                message=f"Failed to auto-close resolved ticket {doc.name}. Error: {e}",
                title="Auto Close Resolved Ticket Failed",
            )
            continue

        frappe.db.commit()  # nosemgrep


def update_sla_status_in_ticket():
    stale_tickets = frappe.get_all(
        "HD Ticket",
        filters={
            "status_category": ["=", "Open"],
            "sla": ["is", "set"],
        },
        pluck="name",
    )
    for ticket in stale_tickets:
        doc = frappe.get_doc("HD Ticket", ticket)
        sla = frappe.get_doc("HD Service Level Agreement", doc.sla)
        sla.handle_agreement_status(doc)
        try:
            frappe.db.set_value(
                "HD Ticket",
                doc.name,
                "agreement_status",
                doc.agreement_status,
                update_modified=False,
            )

        except Exception as e:
            frappe.log_error(
                message=f"Failed to update agreement status for ticket {doc.name}. Error: {e}",
                title="Update SLA Status Failed",
            )
            continue
        frappe.db.commit()  # nosemgrep


def send_sla_breach_reminder():
    """
    Scheduled task (runs hourly) — sends a reminder email to the assigned agent
    for every open ticket that:
      • has an SLA attached,
      • has NOT yet received a first agent reply (first_responded_on is NULL), AND
      • whose SLA agreement_status is "Failed" (already breached) or "At Risk"
        (approaching breach within the next hour).

    A cache key prevents the same ticket from triggering more than one email
    per 12-hour window, so agents aren't flooded.

    Gated by HD Settings -> Ticket Settings -> Enable SLA Breach Reminder.
    """
    if not frappe.db.get_single_value("HD Settings", "enable_sla_breach_reminder"):
        return

    import os
    from frappe.utils import now_datetime, get_datetime, format_datetime

    # Find open tickets with an SLA that haven't been responded to yet
    tickets = frappe.get_all(
        "HD Ticket",
        filters={
            "status_category": "Open",
            "sla": ["is", "set"],
            "first_responded_on": ["is", "not set"],
            "agreement_status": ["in", ["Failed", "At Risk"]],
        },
        fields=[
            "name", "subject", "raised_by", "ticket_type", "priority",
            "agreement_status", "response_by", "creation",
            "_assign", "agent_group",
        ],
    )

    if not tickets:
        return

    # Build a clean, browser-accessible site URL.
    #
    # • Frappe Cloud / Production:
    #     frappe.utils.get_url() already returns the correct public HTTPS URL
    #     (e.g. https://yoursite.frappe.cloud) — use it as-is.
    #
    # • Local dev (developer_mode = 1):
    #     get_url() returns http://slcm.local:8001 (gunicorn port) which browsers
    #     cannot open directly. We reconstruct the URL using frappe.local.site
    #     (e.g. "slcm.local") so the link opens on the correct nginx port (80).
    import re as _re
    _raw_url       = frappe.utils.get_url().rstrip("/")
    _developer_mode = frappe.conf.get("developer_mode", 0)

    if _developer_mode:
        # Strip the internal :PORT — nginx/haproxy handles routing on port 80
        _site   = getattr(frappe.local, "site", None) or ""
        _scheme = _raw_url.split("://")[0]            # keep http or https
        site_url = f"{_scheme}://{_site}" if _site else _re.sub(r":\d+$", "", _raw_url)
    else:
        # Production / Frappe Cloud — get_url() is already correct
        site_url = _raw_url

    # Resolve the configured Email Template once per run (not per ticket) — if
    # none is set, or the configured one was deleted/renamed since, fall back
    # to the built-in static template so the job never breaks silently.
    email_template_name = frappe.db.get_single_value(
        "HD Settings", "sla_breach_reminder_template"
    )
    email_template = None
    if email_template_name:
        if frappe.db.exists("Email Template", email_template_name):
            candidate = frappe.get_doc("Email Template", email_template_name)
            # `enabled` may be a site-specific Custom Field (not part of stock
            # Frappe's Email Template) — only honor it if present, and only
            # treat it as a block when explicitly disabled (falsy default of
            # "field absent" must not be mistaken for "disabled").
            if candidate.get("enabled") == 0:
                frappe.log_error(
                    message=(
                        f"HD Settings -> SLA Breach Reminder Email Template "
                        f"'{email_template_name}' is disabled. Falling back to "
                        f"the default template."
                    ),
                    title="SLA Reminder — Disabled Email Template",
                )
            else:
                email_template = candidate
        else:
            frappe.log_error(
                message=(
                    f"HD Settings -> SLA Breach Reminder Email Template "
                    f"'{email_template_name}' no longer exists. Falling back to "
                    f"the default template."
                ),
                title="SLA Reminder — Missing Email Template",
            )

    default_template_str = None
    if not email_template:
        template_path = os.path.join(
            frappe.get_app_path("helpdesk"),
            "templates", "emails", "sla_breach_reminder.html",
        )
        with open(template_path, "r") as f:
            default_template_str = f.read()

    for ticket in tickets:
        # ── Throttle: skip if we already sent a reminder for this ticket
        #    in the last 12 hours ──────────────────────────────────────
        cache_key = f"sla_reminder_sent:{ticket.name}"
        if frappe.cache().get_value(cache_key):
            continue

        # ── Resolve the assigned agent's email ───────────────────────
        agent_email = None
        agent_name  = "Agent"

        if ticket._assign:
            try:
                import json as _json
                assignees = _json.loads(ticket._assign)
                if assignees:
                    agent_email = assignees[0]
                    agent_name  = frappe.db.get_value(
                        "User", agent_email, "full_name"
                    ) or agent_email
            except Exception:
                pass

        # Fall back to team members if no direct assignee
        if not agent_email and ticket.agent_group:
            members = frappe.get_all(
                "HD Team Member",
                filters={"parent": ticket.agent_group},
                pluck="user",
            )
            if members:
                agent_email = members[0]
                agent_name  = frappe.db.get_value(
                    "User", agent_email, "full_name"
                ) or agent_email

        if not agent_email:
            frappe.log_error(
                message=f"SLA Reminder: No agent found for ticket {ticket.name}. Skipping.",
                title="SLA Reminder — No Agent",
            )
            continue

        # ── Build template context ────────────────────────────────────
        is_breached = ticket.agreement_status == "Failed"

        response_deadline = (
            format_datetime(ticket.response_by, "dd-MM-yyyy hh:mm a")
            if ticket.response_by
            else "Not set"
        )
        creation_str = (
            format_datetime(ticket.creation, "dd-MM-yyyy hh:mm a")
            if ticket.creation
            else ""
        )

        ticket_url = f"{site_url}/helpdesk/tickets/{ticket.name}"

        context = {
            "agent_name":        agent_name,
            "ticket_name":       ticket.name,
            "subject":           ticket.subject or "(No Subject)",
            "raised_by":         ticket.raised_by or "Unknown",
            "ticket_type":       ticket.ticket_type or "—",
            "priority":          ticket.priority or "—",
            "is_breached":       is_breached,
            "response_deadline": response_deadline,
            "creation":          creation_str,
            "ticket_url":        ticket_url,
        }

        if email_template:
            formatted = email_template.get_formatted_email(context)
            email_subject = formatted["subject"]
            rendered_body = formatted["message"]
        else:
            rendered_body = frappe.render_template(default_template_str, context)
            subject_prefix = "🔴 SLA Breached" if is_breached else "🟡 SLA At Risk"
            email_subject = (
                f"{subject_prefix} — Ticket {ticket.name}: "
                f"{ticket.subject or '(No Subject)'}"
            )

        try:
            frappe.sendmail(
                recipients=[agent_email],
                subject=email_subject,
                message=rendered_body,
                now=True,
            )

            # Mark as sent — expires after 12 hours (43 200 seconds)
            frappe.cache().set_value(cache_key, 1, expires_in_sec=43200)

            frappe.logger().info(
                f"SLA reminder sent to {agent_email} for ticket {ticket.name} "
                f"(status: {ticket.agreement_status})"
            )

        except Exception as e:
            frappe.log_error(
                message=f"Failed to send SLA reminder for ticket {ticket.name} "
                        f"to {agent_email}. Error: {e}",
                title="SLA Reminder Email Failed",
            )
