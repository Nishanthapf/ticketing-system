# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

"""
Multi-level SLA ticket escalation engine for HD Ticket.

Design crux: escalation eligibility per level is based on "is the ticket
currently awaiting an agent reply at its CURRENT level", not a one-time
first-response flag.

- Agent replies at Level N -> stop that level's timer (escalation_active=0),
  do not advance level.
- Customer replies again while still at Level N (including via reopen from
  Resolved) -> timer restarts from the moment of that reply, at the SAME
  level.
- Only if the agent then fails to reply within that level's configured
  duration, measured from the restart point, does it escalate to Level N+1.

See `apps/helpdesk/helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.py` for the
lifecycle hooks (`on_communication_update`, `set_resolved_on`-adjacent logic)
that call into this module, and `hooks.py` for the hourly scheduled job
registration.
"""

from datetime import timedelta

import frappe
from frappe.desk.form.assign_to import add as assign_to_add
from frappe.desk.form.assign_to import remove as assign_to_remove
from frappe.utils import flt, now_datetime

from helpdesk.helpdesk.doctype.hd_ticket_activity.hd_ticket_activity import (
    log_ticket_activity,
)


def start_escalation_if_enabled(ticket_doc):
    """
    Called from HD Ticket's `validate()` (see hd_ticket.py) on every save.
    Sets Level 1 escalation fields in-memory, exactly once — guarded by
    `current_escalation_level == 0` so it never re-triggers on subsequent
    saves. Safe to call before the ticket has a DB row (e.g. during insert's
    validate phase): only mutates fields on `ticket_doc` itself, no writes to
    other doctypes (assignment / activity log) — those happen afterwards, once
    the ticket is guaranteed to exist in the DB, via
    `apply_escalation_side_effects_if_pending`.
    """
    if ticket_doc.get("current_escalation_level"):
        return
    if not ticket_doc.agent_group:
        return

    team = frappe.get_cached_doc("HD Team", ticket_doc.agent_group)
    if not team.enable_ticket_escalation or not team.escalation_levels:
        return

    level1 = min(team.escalation_levels, key=lambda r: r.idx)

    ticket_doc.current_escalation_level = 1
    ticket_doc.escalation_level_started_on = now_datetime()
    ticket_doc.escalation_active = 1
    ticket_doc.escalation_access_level = level1.access_level
    ticket_doc.flags.pending_escalation_level1_side_effects = True


def apply_escalation_side_effects_if_pending(ticket_doc):
    """
    Called from `after_insert` / `on_update` once the ticket definitely has a
    DB row. Performs the Level 1 assignment + activity log that
    `start_escalation_if_enabled` deferred, exactly once (flag is cleared
    immediately so a later save on the same in-memory doc can't repeat it).
    """
    if not ticket_doc.flags.pop("pending_escalation_level1_side_effects", False):
        return

    team = frappe.get_cached_doc("HD Team", ticket_doc.agent_group)
    level1 = next(
        (r for r in team.escalation_levels if r.level == 1),
        min(team.escalation_levels, key=lambda r: r.idx),
    )

    try:
        assign_to_add(
            {
                "assign_to": [level1.assigned_to],
                "doctype": "HD Ticket",
                "name": ticket_doc.name,
            }
        )
    except Exception:
        frappe.log_error(
            title="Ticket Escalation — Level 1 assignment failed",
            message=frappe.get_traceback(),
        )

    log_ticket_activity(
        ticket_doc.name,
        f"Escalation started — assigned to Level 1 ({level1.assigned_to}), "
        f"will escalate after {flt(level1.escalate_after_hours)} hours if no reply.",
    )


def on_agent_reply(ticket_doc):
    """Stop the current level's timer — the agent has responded."""
    if not ticket_doc.get("current_escalation_level"):
        return
    ticket_doc.db_set("escalation_active", 0)


def on_customer_reply(ticket_doc):
    """Re-arm the current level's timer from now — same level, no advance."""
    if not ticket_doc.get("current_escalation_level"):
        return
    now = now_datetime()
    ticket_doc.db_set("escalation_active", 1)
    ticket_doc.db_set("escalation_level_started_on", now)


def on_reopen(ticket_doc):
    """
    Ticket moved from a Resolved-equivalent status_category back to Open.
    Re-arm the SAME level's timer from the reopen moment — never resurrect a
    stale pre-resolution timestamp.
    """
    if not ticket_doc.get("current_escalation_level"):
        return
    now = now_datetime()
    ticket_doc.db_set("escalation_active", 1)
    ticket_doc.db_set("escalation_level_started_on", now)


def on_resolved(ticket_doc):
    """
    Ticket transitioned to Resolved — pause the timer but preserve the level
    so a later reopen resumes at the same level.
    """
    if not ticket_doc.get("current_escalation_level"):
        return
    ticket_doc.db_set("escalation_active", 0)


def process_ticket_escalations():
    """
    Scheduled task (runs hourly) — escalates tickets whose current level's
    reply-wait period has elapsed with no agent reply.

    Race-safety: each candidate is re-fetched fresh and re-validated
    (`escalation_active` + `current_escalation_level > 0`) before any mutation,
    and every state transition is written via `db_set` exactly once per pass,
    so concurrent/repeated runs cannot double-escalate the same ticket.
    """
    candidates = frappe.get_all(
        "HD Ticket",
        filters={"escalation_active": 1, "current_escalation_level": [">", 0]},
        fields=["name"],
    )

    for candidate in candidates:
        name = candidate.name
        try:
            _process_single_ticket_escalation(name)
        except Exception as e:
            frappe.log_error(
                message=f"Failed to process escalation for ticket {name}. Error: {e}",
                title="Ticket Escalation Failed",
            )
            continue


def _process_single_ticket_escalation(name):
    ticket = frappe.get_doc("HD Ticket", name)

    # Re-check race-safety: state may have changed since the candidate query.
    if not ticket.escalation_active or not ticket.current_escalation_level:
        return

    if not ticket.agent_group:
        return

    team = frappe.get_cached_doc("HD Team", ticket.agent_group)
    current_row = next(
        (r for r in team.escalation_levels if r.level == ticket.current_escalation_level),
        None,
    )
    if not current_row:
        # Team was reconfigured (levels removed/renumbered) out from under this
        # ticket — nothing sane to do; skip gracefully.
        return

    if not ticket.escalation_level_started_on:
        return

    cutoff = ticket.escalation_level_started_on + timedelta(
        hours=flt(current_row.escalate_after_hours)
    )
    if now_datetime() < cutoff:
        return  # Not yet due.

    max_level = len(team.escalation_levels)

    if ticket.current_escalation_level < max_level:
        _escalate_to_next_level(ticket, team, current_row)
    else:
        # Already at the final level and still overdue — stop escalating,
        # fire exactly once on this active -> inactive transition.
        ticket.db_set("escalation_active", 0)
        log_ticket_activity(
            ticket.name,
            f"Ticket reached final escalation level (Level {ticket.current_escalation_level}) "
            f"with no reply; no further automatic escalation will occur.",
        )

    frappe.db.commit()  # nosemgrep


def _escalate_to_next_level(ticket, team, current_row):
    old_level = ticket.current_escalation_level
    old_assignee = current_row.assigned_to
    new_level = old_level + 1
    next_row = next(
        (r for r in team.escalation_levels if r.level == new_level), None
    )
    if not next_row:
        # Shouldn't happen given the max_level check above, but guard anyway.
        return

    # Unassign current assignee(s), assign to the next level's assignee.
    try:
        assignees = frappe.desk.form.assign_to.get(
            {"doctype": "HD Ticket", "name": ticket.name}
        )
        for assignee in assignees:
            assign_to_remove("HD Ticket", ticket.name, assignee.owner)
    except Exception:
        frappe.log_error(
            title="Ticket Escalation — unassign failed",
            message=frappe.get_traceback(),
        )

    try:
        assign_to_add(
            {
                "assign_to": [next_row.assigned_to],
                "doctype": "HD Ticket",
                "name": ticket.name,
            }
        )
    except Exception:
        frappe.log_error(
            title="Ticket Escalation — next level assignment failed",
            message=frappe.get_traceback(),
        )

    now = now_datetime()
    ticket.db_set("current_escalation_level", new_level)
    ticket.db_set("escalation_level_started_on", now)
    ticket.db_set("escalation_active", 1)
    ticket.db_set("escalation_access_level", next_row.access_level)

    hours = flt(current_row.escalate_after_hours)
    log_ticket_activity(
        ticket.name,
        f"Escalated from Level {old_level} ({old_assignee}) to Level {new_level} "
        f"({next_row.assigned_to}) after {hours} hours with no reply.",
    )

    if next_row.notify_assignee:
        _send_escalation_email(ticket, next_row, new_level, hours)


def _build_escalation_email_context(ticket, level_row, new_level, hours):
    """
    Context passed to the level's Email Template (or the default message).
    Keep keys stable — admins reference these as {{ field }} in custom
    templates, so treat this dict as a small public contract.
    """
    assignee_name = frappe.db.get_value("User", level_row.assigned_to, "full_name") or level_row.assigned_to
    try:
        ticket_url = frappe.utils.get_url(ticket.portal_uri())
    except Exception:
        ticket_url = frappe.utils.get_url(f"/helpdesk/tickets/{ticket.name}")

    return {
        "ticket_name": ticket.name,
        "subject": ticket.subject or "(No Subject)",
        "level": new_level,
        "hours": hours,
        "team_name": ticket.agent_group or "",
        "assignee_name": assignee_name,
        "priority": ticket.priority or "",
        "ticket_url": ticket_url,
    }


def _default_escalation_email_message(context):
    return (
        f"Ticket #{context['ticket_name']} has been escalated to you "
        f"(Level {context['level']}) because there was no reply within "
        f"{context['hours']} hours.<br><br>"
        f"Subject: {context['subject']}"
    )


def _send_escalation_email(ticket, level_row, new_level, hours):
    context = _build_escalation_email_context(ticket, level_row, new_level, hours)
    default_subject = f"Ticket #{ticket.name} escalated to you (Level {new_level})"
    default_message = _default_escalation_email_message(context)

    try:
        if level_row.email_template and frappe.db.exists(
            "Email Template", level_row.email_template
        ):
            template = frappe.get_doc("Email Template", level_row.email_template)
            formatted = template.get_formatted_email(context)
            email_subject = formatted["subject"]
            email_message = formatted["message"]
        else:
            email_subject = default_subject
            email_message = default_message

        frappe.sendmail(
            recipients=[level_row.assigned_to],
            subject=email_subject,
            message=email_message,
            reference_doctype="HD Ticket",
            reference_name=ticket.name,
            now=True,
        )
    except Exception as e:
        frappe.log_error(
            message=f"Failed to send escalation email for ticket {ticket.name} "
            f"to {level_row.assigned_to}. Error: {e}",
            title="Ticket Escalation Email Failed",
        )
