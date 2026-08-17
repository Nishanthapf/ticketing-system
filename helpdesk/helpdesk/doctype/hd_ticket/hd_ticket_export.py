"""Ticket conversation export (PDF), individually or in bulk.

Only the customer<->agent conversation (Communication records) is exported.
Internal-only records (HD Ticket Comment, HD Ticket Activity, View Log,
call logs) are never included.
"""

import base64
import io
import json
import mimetypes
import os
import zipfile

import frappe
from frappe import _
from frappe.utils import get_files_path, get_url

from helpdesk.helpdesk.doctype.hd_ticket.api import get_attachments, get_communications
from helpdesk.utils import is_agent

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

TICKET_SUMMARY_FIELDS = [
    "name",
    "subject",
    "status",
    "priority",
    "agent_group",
    "_assign",
    "creation",
    "resolution_date",
    "resolved_on",
]


def check_export_permission(ticket: str):
    if not is_agent():
        frappe.throw(_("Only agents can export tickets"), frappe.PermissionError)
    frappe.has_permission("HD Ticket", "read", ticket, throw=True)


def _file_url_to_disk_path(file_url: str) -> str | None:
    if not file_url:
        return None
    if file_url.startswith("/private/files/"):
        return get_files_path(file_url[len("/private/files/"):], is_private=True)
    if file_url.startswith("/files/"):
        return get_files_path(file_url[len("/files/"):])
    return None


def _embed_image_as_data_uri(file_url: str) -> str | None:
    path = _file_url_to_disk_path(file_url)
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        return None
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _classify_attachments(attachments: list[dict]):
    images, files = [], []
    for a in attachments:
        ext = os.path.splitext(a.get("file_name") or "")[1].lower()
        if ext in IMAGE_EXTENSIONS:
            data_uri = _embed_image_as_data_uri(a.get("file_url"))
            if data_uri:
                images.append({**a, "data_uri": data_uri})
                continue
        files.append(a)
    return images, files


def get_ticket_summary(ticket_doc) -> dict:
    assignees = []
    if ticket_doc._assign:
        try:
            assignees = json.loads(ticket_doc._assign)
        except (TypeError, ValueError):
            assignees = []
    return {
        "ticket_id": ticket_doc.name,
        "subject": ticket_doc.subject,
        "status": ticket_doc.status,
        "priority": ticket_doc.priority,
        "team": ticket_doc.agent_group,
        "assigned_to": ", ".join(assignees) if assignees else "-",
        "created": ticket_doc.creation,
        "resolved_on": ticket_doc.resolved_on,
        "closed_on": ticket_doc.resolution_date
        if ticket_doc.status == "Closed"
        else None,
    }


def get_conversation_for_export(ticket: str) -> list[dict]:
    """Customer<->agent messages only, chronological, with attachments split
    into embeddable images and separate files."""
    communications = get_communications(ticket)
    conversation = []
    for c in communications:
        if c.get("communication_type") not in (None, "Communication"):
            continue
        images, files = _classify_attachments(c.get("attachments") or [])
        sender_name = None
        if c.get("user"):
            sender_name = c["user"].get("full_name") or c["user"].get("name")
        conversation.append(
            {
                "role": "Agent" if c.get("sent_or_received") == "Sent" else "Student/Customer",
                "sender": sender_name or c.get("sender"),
                "datetime": c.get("communication_date") or c.get("creation"),
                "content": c.get("content"),
                "images": images,
                "files": files,
            }
        )
    return conversation


def render_ticket_html(ticket_doc, conversation: list[dict]) -> str:
    return frappe.render_template(
        # path is "<app>/<path relative to the app's python package root>"
        "helpdesk/doctype/hd_ticket/templates/ticket_export.html",
        {
            "summary": get_ticket_summary(ticket_doc),
            "conversation": conversation,
        },
    )


def build_ticket_pdf(ticket: str) -> tuple[bytes, str, list[dict]]:
    """Returns (pdf_bytes, filename, non_embeddable_attachments)."""
    from frappe.utils.pdf import get_pdf

    ticket_doc = frappe.get_doc("HD Ticket", ticket)
    conversation = get_conversation_for_export(ticket)
    html = render_ticket_html(ticket_doc, conversation)
    pdf_content = get_pdf(html)
    filename = f"{ticket_doc.name}_Ticket-Conversation.pdf"

    other_files = []
    for msg in conversation:
        other_files.extend(msg["files"])
    return pdf_content, filename, other_files


@frappe.whitelist()
def export_ticket(ticket: str):
    check_export_permission(ticket)
    pdf_content, filename, attachments = build_ticket_pdf(ticket)

    if not attachments:
        frappe.local.response.filename = filename
        frappe.local.response.filecontent = pdf_content
        frappe.local.response.type = "download"
        return

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, pdf_content)
        for a in attachments:
            path = _file_url_to_disk_path(a.get("file_url"))
            if path and os.path.isfile(path):
                zf.writestr(f"Attachments/{a.get('file_name')}", open(path, "rb").read())

    frappe.local.response.filename = f"{ticket}_Ticket-Export.zip"
    frappe.local.response.filecontent = buffer.getvalue()
    frappe.local.response.type = "download"


@frappe.whitelist()
def bulk_export_tickets(tickets: str | list | None = None, filters: str | dict | None = None):
    """Export multiple tickets as a single ZIP: one PDF per ticket, plus a
    shared Attachments/<ticket>/ folder for non-embeddable files."""
    if not is_agent():
        frappe.throw(_("Only agents can export tickets"), frappe.PermissionError)

    if isinstance(tickets, str):
        tickets = frappe.parse_json(tickets)
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    if tickets:
        ticket_names = list(tickets)
    else:
        ticket_names = frappe.get_list(
            "HD Ticket", filters=filters or {}, pluck="name", limit_page_length=0
        )

    allowed = []
    for name in ticket_names:
        if frappe.has_permission("HD Ticket", "read", name):
            allowed.append(name)

    if not allowed:
        frappe.throw(_("No tickets available to export"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in allowed:
            pdf_content, filename, attachments = build_ticket_pdf(name)
            zf.writestr(filename, pdf_content)
            for a in attachments:
                path = _file_url_to_disk_path(a.get("file_url"))
                if path and os.path.isfile(path):
                    zf.writestr(
                        f"Attachments/{name}/{a.get('file_name')}", open(path, "rb").read()
                    )

    today = frappe.utils.nowdate()
    frappe.local.response.filename = f"Helpdesk_Ticket_Export_{today}.zip"
    frappe.local.response.filecontent = buffer.getvalue()
    frappe.local.response.type = "download"
