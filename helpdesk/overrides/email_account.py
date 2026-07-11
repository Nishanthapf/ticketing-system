import hashlib
import re
from email import message_from_string

import frappe
from frappe import _
from frappe.core.doctype.communication.communication import Communication
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.doctype.email_queue.email_queue import EmailQueue
from frappe.email.receive import InboundMail

# Senders used by mail servers for bounce/delivery-failure notifications
# (Postfix, Exim, Exchange, Gmail, etc. all use one of these).
BOUNCE_SENDER_PATTERN = re.compile(
    r"(mailer-daemon|mail delivery subsystem|postmaster)", re.IGNORECASE
)


def is_bounce_notification(msg) -> bool:
    """
    Detect delivery-failure / NDR emails (e.g. "Delivery Status Notification
    (Failure)" from Mailer Daemon) so they never turn into HD Tickets.

    These clutter the agent queue: they aren't from real customers and no
    agent can act on them. We check, in order of reliability:
      1. Content-Type: multipart/report; report-type=delivery-status
         - the RFC 3464 standard signature for bounce messages.
      2. Auto-Submitted header set to anything other than "no"
         - RFC 3834 standard for any automated response, incl. bounces.
      3. From address matching known bounce senders (mailer-daemon, postmaster, etc.)
         - fallback for older/nonstandard mail servers that skip the above.
    """
    content_type = (msg.get("Content-Type") or "").lower()
    if "report-type=delivery-status" in content_type:
        return True

    auto_submitted = (msg.get("Auto-Submitted") or "").lower()
    if auto_submitted and auto_submitted != "no":
        return True

    from_header = msg.get("From") or ""
    if BOUNCE_SENDER_PATTERN.search(from_header):
        return True

    return False


class CustomInboundMail(InboundMail):
    """
    Extend InboundMail with robust thread stitching for forwarded emails.
       1. Run the standard Frappe parent_communication lookups first (In-Reply-To → Communication, EmailQueue, communication-name fallback)
       2. If still no parent, use the References header from emails, which may contain multiple message IDs in a thread

    Also guards against duplicate ticket creation: Helpdesk email accounts use
    email_sync_option="ALL" (see helpdesk/api/settings/email.py), so every mail is
    re-fetched on each poll. Frappe's own duplicate check (is_exist_in_system) only
    works when the mail has a Message-ID header; mails without one (or with a
    malformed one) were being re-processed as brand-new mail on every sync,
    creating a new HD Ticket each time. We derive a stable fallback message_id from
    the mail's own content so repeated fetches of the same physical email are
    recognized as duplicates.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.message_id:
            self.message_id = self._generate_fallback_message_id()

    def _generate_fallback_message_id(self):
        """Build a stable synthetic id for mails missing a Message-ID header.

        Hashing From + Date + Subject + a snippet of the body means the same
        physical email always yields the same id across repeated IMAP syncs,
        while two distinct emails (even with identical subjects) won't collide.
        """
        parts = [
            self.mail.get("From") or "",
            self.mail.get("Date") or "",
            self.mail.get("Subject") or "",
            (self.text_content or self.html_content or "")[:500],
        ]
        digest = hashlib.sha256("|".join(parts).encode("utf-8", errors="replace")).hexdigest()
        return f"generated-{digest}@{frappe.local.site}"

    def is_exist_in_system(self):
        """Same as core, but relies on our always-present message_id fallback."""
        if not self.message_id:
            return None

        return Communication.find_one_by_filters(
            message_id=self.message_id, sent_or_received="Received", order_by="creation DESC"
        )

    def _find_communication_by_message_id(self, msg_id: str):
        """Return a Communication for msg_id, checking both Communication and EmailQueue."""
        # Direct hit: incoming email stored its message_id on Communication
        comm = Communication.find_one_by_filters(
            message_id=msg_id, order_by="creation DESC"
        )
        if comm:
            return comm

        # Outgoing email: message_id lives in EmailQueue, not on Communication
        eq = EmailQueue.find_one_by_filters(message_id=msg_id)
        if eq and eq.communication:
            return Communication.find(eq.communication, ignore_error=True) or None

        return None

    def parent_communication(self):
        # Respect cached result from any prior call on this instance
        if self._parent_communication is not None:
            return self._parent_communication

        # Run the standard Frappe lookup method first. Checks for finding in reply to in Communication then if not found it looks in EmailQueue
        result = super().parent_communication()
        if result:
            return result

        # fallback: use the References header from emails
        references_raw = self.mail.get("References") or ""
        ref_ids = re.findall(r"<([^>]+)>", references_raw)

        for ref_id in reversed(ref_ids):
            communication = self._find_communication_by_message_id(ref_id)
            if communication:
                self._parent_communication = communication
                return self._parent_communication

        self._parent_communication = ""
        return self._parent_communication


class CustomEmailAccount(EmailAccount):
    def get_inbound_mails(self) -> list[InboundMail]:
        """retrive and return inbound mails."""
        mails = []

        def process_mail(messages, append_to=None):
            for index, message in enumerate(messages.get("latest_messages", [])):
                try:
                    _msg = message_from_string(
                        message.decode("utf-8", errors="replace")
                    )

                    # Important: If the email is auto-generated, we do not create a ticket
                    if _msg.get("X-Auto-Generated"):
                        continue

                    # Skip bounce/NDR emails (e.g. "Delivery Status Notification
                    # (Failure)" from Mailer Daemon) so they don't clutter the
                    # agent queue with tickets nobody can action.
                    if is_bounce_notification(_msg):
                        continue

                    uid = (
                        messages["uid_list"][index]
                        if messages.get("uid_list")
                        else None
                    )
                    seen_status = messages.get("seen_status", {}).get(uid)
                    if self.email_sync_option != "UNSEEN" or seen_status != "SEEN":
                        _inbound_mail = CustomInboundMail(
                            message,
                            self,
                            frappe.safe_decode(uid),
                            seen_status,
                            append_to,
                        )
                        mails.append(_inbound_mail)
                except Exception as e:
                    # Log the error but continue processing other emails
                    frappe.log_error(
                        title=_(
                            "Error processing email at index {0}, message: {1}"
                        ).format(index, e),
                        message=frappe.get_traceback(),
                    )
                    self.handle_bad_emails(index, message, frappe.get_traceback())
                    continue

        if not self.enable_incoming:
            return []

        try:
            if self.service == "Frappe Mail":
                frappe_mail_client = self.get_frappe_mail_client()
                messages = frappe_mail_client.pull_raw(
                    last_received_at=self.last_synced_at
                )
                process_mail(messages)
                self.db_set(
                    "last_synced_at",
                    messages["last_received_at"],
                    update_modified=False,
                )
            else:
                email_sync_rule = self.build_email_sync_rule()
                email_server = self.get_incoming_server(
                    in_receive=True, email_sync_rule=email_sync_rule
                )
                if self.use_imap:
                    # process all given imap folder
                    for folder in self.imap_folder:
                        if email_server.select_imap_folder(folder.folder_name):
                            email_server.settings["uid_validity"] = folder.uidvalidity
                            messages = (
                                email_server.get_messages(
                                    folder=f'"{folder.folder_name}"'
                                )
                                or {}
                            )
                            process_mail(messages, folder.append_to)
                else:
                    # process the pop3 account
                    messages = email_server.get_messages() or {}
                    process_mail(messages)

                # close connection to mailserver
                email_server.logout()
        except Exception:
            self.log_error(
                title=_("Error while connecting to email account {0}").format(self.name)
            )
            return []

        return mails
