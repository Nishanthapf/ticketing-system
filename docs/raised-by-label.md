# Raised By Label

Shows who raised a ticket (Student, Applicant, Faculty, Staff, AAD, …) and
labels the requester's Name / ID to match: "Applicant Name / Applicant ID",
"Faculty Name / Faculty ID", and so on.

## Configuration

**HD Raised By Label** (DocType, named by `label_name`)

| Field | Purpose |
|---|---|
| `label_name` | Text shown before "Name" / "ID" |
| `data_source` | Where Name / ID are read from: `Student Master`, `Applicant`, `Faculty`, `User` |
| `enabled` | Disabled labels are skipped during matching |

**HD Settings > Ticket Settings > Raised By Label** (`raised_by_role_map`,
child DocType **HD Raised By Role Map**: `role`, `label`)

The first row whose role the user holds wins, so row order is precedence.
A role can appear only once (`HDSettings.validate_raised_by_role_map`).

### Defaults (seeded create-if-missing)

| Role | Label | Data source |
|---|---|---|
| slcm_Student, Student | Student | Student Master (`registration_id`) |
| Applicant, PACE Applicant | Applicant | Applicant (PACE Application / NLSAT Applicant docname) |
| slcm_Faculty, Guest Faculty | Faculty | Faculty (`faculty_id`) |
| AAD, AAD Staff | AAD | Faculty |
| Staff | Staff | Faculty |

Seeded by `helpdesk/setup/raised_by_label.py` from `after_install` and the
`create_default_raised_by_labels` patch. The role map is only filled when it
is empty, and roles missing on the site (e.g. no slcm) are skipped.

**Why not fixtures:** fixture import force-overwrites the whole doc on every
migrate, which would undo admin edits to labels and wipe the HD Settings role
map.

## Data sources

| Source | Lookup | Name | ID |
|---|---|---|---|
| Student Master | `user`, `email`, `official_email_id` | first/middle/last | `registration_id` or docname |
| Applicant | PACE Application, else NLSAT Applicant | applicant name | application docname |
| Faculty | `user_id`, `email`, `official_email_id` | first + last | `faculty_id` or docname |
| User | User record | `full_name` | email |

If the source has no record for the user, the **User** fallback is used. A
new label defaults to `User`, so it works without a code change.

Identity note: an applicant applies with a personal email (Applicant role) and
gets a separate official-email user once enrolled (slcm_Student). The label
follows whichever account raised the ticket.

## Ticket fields (HD Ticket custom fields)

- `custom_raised_by_label`: Link to HD Raised By Label, read-only, standard
  filter, available as a list column.
- `custom_student_name` / `custom_student_id`: fieldnames kept for existing
  data and templates. Static labels are now the generic "Name" / "ID", shown
  when `custom_raised_by_label` is set (or for legacy student tickets).
- Section renamed "Requester Information".
- The Travel & Transportation template (Faculty form) now includes Name / ID.

## Backend

`helpdesk/api/raised_by.py`

- `get_my_raised_by_context()`: whitelisted; context for the session user,
  used by the portal form script.
- `get_raised_by_context(identifier)`: `{raised_by_label, custom_student_name,
  custom_student_id, …}` for a user name, email, or `Name <email>`; `{}` when
  no mapped role matches.
- `get_raised_by_label(roles)`: first enabled label in the role map.

`HDTicket.set_raised_by_details()` (in `before_validate`):

- On insert, the label is always recomputed from the requester's roles. A
  value posted by the client is ignored.
- On update, it only fills the label when it is empty.
- Name / ID are filled only when empty, so the values captured by the portal
  form are kept. Tickets from inbound email get them here.

Patch `backfill_ticket_raised_by_label` sets the label on existing tickets
and fills empty Name / ID. It syncs fixtures first, because fixtures normally
sync after patches and the column would not exist yet on the first migrate.

## Frontend

`raisedByFieldLabel(fieldname, label, doc)` in `desk/src/utils.ts` turns the
Name / ID labels into `<Label> Name` / `<Label> ID` from
`doc.custom_raised_by_label`. Used in:

- `pages/ticket/TicketNew.vue` (new ticket form)
- `components/ticket-agent/TicketDetailsTab.vue` (agent sidebar)
- `pages/ticket/MobileTicketAgent.vue` (mobile agent)
- `components/ticket/TicketCustomerSidebar.vue`,
  `pages/ticket/TicketCustomerTemplateFields.vue` (customer portal)

The form script "NLS Student Ticket Auto-populate" calls
`get_my_raised_by_context` once and sets `custom_raised_by_label`, then fills
Name / ID where the student/applicant lookups left them empty (Faculty, Staff,
AAD, custom labels).

The list view column header can't change per row, so the ID column reads "ID".
Add the "Raised By" column next to it to see the requester type.

## Tests

`helpdesk/api/test_raised_by.py`; helpers `make_role`, `make_raised_by_label`,
`set_raised_by_role_map` in `helpdesk/test_utils.py`.
