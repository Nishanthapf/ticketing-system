import frappe

# NLSIU brand palette (Student Portal Settings defaults on this site):
# primary navy #2b2e4a, accent red #ed0505, success #16a34a, warning #d97706,
# danger #dc2626, info #0369a1. Header color escalates in urgency by level.
NLSIU_NAVY = "#2b2e4a"
NLSIU_ACCENT_RED = "#ed0505"
NLSIU_SUCCESS = "#16a34a"
NLSIU_WARNING = "#d97706"
NLSIU_DANGER = "#dc2626"
NLSIU_INFO = "#0369a1"

LEVEL_HEADER_COLOR = {
    1: NLSIU_NAVY,
    2: NLSIU_NAVY,
    3: NLSIU_INFO,
    4: NLSIU_WARNING,
    5: NLSIU_DANGER,
    6: NLSIU_ACCENT_RED,
}

LEVEL_BADGE_TEXT = {
    1: "Escalation · Level 1",
    2: "Escalation · Level 2",
    3: "Escalation · Level 3",
    4: "Escalation · Level 4 — Please Review",
    5: "Escalation · Level 5 — Urgent",
    6: "Escalation · Level 6 — Final Level",
}

TEMPLATE_NAME_PREFIX = "Ticket Escalation - Level"


def escalation_template_name(level: int) -> str:
    return f"{TEMPLATE_NAME_PREFIX} {level}"


def _build_html(level: int) -> str:
    header_color = LEVEL_HEADER_COLOR[level]
    badge_text = LEVEL_BADGE_TEXT[level]

    return f"""<div style="max-width:560px;margin:0 auto;font-family:-apple-system, Segoe UI, Helvetica, Arial, sans-serif">
  <div style="border-radius:10px 10px 0 0;background:{header_color};padding:18px 24px">
    <span style="display:inline-block;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,0.2);color:#ffffff;font-size:11px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:8px">{badge_text}</span>
    <div style="color:#ffffff;font-size:18px;font-weight:700;line-height:1.35">Ticket escalated to you</div>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;border-radius:0 0 10px 10px;padding:24px;background:#ffffff">
    <p style="font-size:15px;color:#111827;margin:0 0 14px">Hi {{{{ assignee_name }}}},</p>
    <p style="font-size:14px;color:#374151;line-height:1.6;margin:0 0 18px">
      Ticket <strong>#{{{{ ticket_name }}}} — {{{{ subject }}}}</strong> received no agent reply within
      <strong>{{{{ hours }}}} hour(s)</strong> and has been escalated to <strong>Level {{{{ level }}}}</strong>,
      which is now assigned to you.
    </p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #f1f1f1;border-radius:8px;overflow:hidden;margin:4px 0 4px">
      <tbody>
      <tr style="border-bottom:1px solid #f1f1f1">
        <td style="padding:10px 14px;color:#6b7280;font-size:13px;width:42%">Team</td>
        <td style="padding:10px 14px;color:#111827;font-size:13px;font-weight:600">{{{{ team_name }}}}</td>
      </tr>
      <tr style="background:#f9fafb;border-bottom:1px solid #f1f1f1">
        <td style="padding:10px 14px;color:#6b7280;font-size:13px;width:42%">Priority</td>
        <td style="padding:10px 14px;color:#111827;font-size:13px;font-weight:600">{{{{ priority }}}}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;color:#6b7280;font-size:13px;width:42%">Escalation Level</td>
        <td style="padding:10px 14px;color:#111827;font-size:13px;font-weight:600">Level {{{{ level }}}}</td>
      </tr>
      </tbody>
    </table>
    <p style="font-size:14px;color:#374151;margin:18px 0 0">Please respond as soon as possible to avoid further escalation.</p>
    <div style="text-align:center;margin-top:28px">
      <a href="{{{{ ticket_url }}}}" style="display:inline-block;background:{header_color};color:#ffffff;font-weight:600;font-size:14px;padding:11px 28px;border-radius:6px;text-decoration:none" rel="noopener noreferrer">Open Ticket</a>
    </div>
  </div>
  <p style="text-align:center;color:#9ca3af;font-size:12px;margin:16px 0 0">
    Automated notification from the NLSIU Helpdesk ticket escalation system.
  </p>
</div>"""


def _build_subject(level: int) -> str:
    if level >= 6:
        return "Ticket #{{ ticket_name }} escalated to you — Final Level ({{ level }})"
    if level >= 4:
        return "Ticket #{{ ticket_name }} escalated to you — Level {{ level }} (Urgent)"
    return "Ticket #{{ ticket_name }} escalated to you (Level {{ level }})"


def create_escalation_email_templates_if_missing():
    """
    Create-if-missing, matches the pattern in setup/team.py — deliberately
    NOT a fixture, so re-running (e.g. via a migrate patch) never overwrites
    an admin's customisations to these templates once they exist.
    """
    meta = frappe.get_meta("Email Template")
    has_enabled_field = meta.has_field("enabled")

    for level in range(1, 7):
        name = escalation_template_name(level)

        if frappe.db.exists("Email Template", name):
            # Backfill for templates created before this field was accounted
            # for (see note below) — only ever touches our own named rows,
            # never a blanket update to unrelated Email Templates.
            if has_enabled_field and not frappe.db.get_value("Email Template", name, "enabled"):
                frappe.db.set_value("Email Template", name, "enabled", 1, update_modified=False)
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Email Template",
                "name": name,
                "subject": _build_subject(level),
                "response_html": _build_html(level),
                "use_html": 1,
            }
        )
        # CRM installs a custom "enabled" Check field (default 0) on Email
        # Template. frappe.desk.search.search_widget filters every Link-field
        # dropdown on enabled=1 when that field exists, so an unset value here
        # makes the template exist in the DB but be invisible in every picker
        # across the whole site (Email Template, HD Escalation Level, etc.).
        if has_enabled_field:
            doc.enabled = 1
        doc.insert(ignore_permissions=True)
