# Raised By Label

Shows who raised a ticket (Student, NLSAT Applicant, International Applicant,
PACE Applicant, Faculty, Staff, AAD, …) and labels the requester's Name / ID
to match: "NLSAT Applicant Name / NLSAT Applicant ID",
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
| Applicant | NLSAT Applicant (or International Applicant, see below) | Applicant (NLSAT application docname) |
| PACE Applicant | PACE Applicant | Applicant (PACE Application docname) |
| slcm_Faculty, Guest Faculty | Faculty | Faculty (`faculty_id`) |
| AAD, AAD Staff | AAD | Faculty |
| Staff | Staff | Faculty |

Seeded by `helpdesk/setup/raised_by_label.py` from `after_install` and the
`create_default_raised_by_labels` patch. The role map is only filled when it
is empty, and roles missing on the site (e.g. no slcm) are skipped.

### NLSAT Applicant vs International Applicant

Domestic and international NLSAT applicants hold the same `Applicant` role,
so the role map can only pick `NLSAT Applicant`.
`raised_by.INTERNATIONAL_LABELS` maps it to `International Applicant`, which
is used instead when the applicant is international and that label is
enabled. Domestic or international is decided, first match wins, by:

1. the NLSAT application's `foriegn_national` (Yes = international);
2. the ticket's own type (`NLSAT` / `NLSAT International`);
3. the `nlsat_portal` cookie of the portal the applicant logged in through.

If none of these decide, the label stays `NLSAT Applicant`.

### Migration from the single "Applicant" label

The `split_applicant_raised_by_label` patch:

- creates the new labels;
- repoints role map rows still on `Applicant`: role `Applicant` to
  `NLSAT Applicant`, role `PACE Applicant` to `PACE Applicant`;
- relabels existing `Applicant` tickets (label only, Name / ID kept);
- deletes the `Applicant` label, or disables it if something still links to it.

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
  filter, available as a list column. The quick filter is hidden from NLSAT
  applicants (`NLSAT_APPLICANT_HIDDEN_QUICK_FILTERS` in
  `helpdesk/api/nls_student.py`), since they only see their own tickets.
- `custom_student_name` / `custom_student_id`: fieldnames kept for existing
  data and templates. Static labels are now the generic "Name" / "ID", shown
  when `custom_raised_by_label` is set (or for legacy student tickets).
- Section renamed "Requester Information".
- The Travel & Transportation template (Faculty form) now includes Name / ID.

## Backend

`helpdesk/api/raised_by.py`

- `get_my_raised_by_context()`: whitelisted; context for the session user,
  used by the portal form script.
- `get_raised_by_context(identifier, ticket_type=None)`: `{raised_by_label,
  custom_student_name, custom_student_id, …}` for a user name, email, or
  `Name <email>`; `{}` when no mapped role matches. `ticket_type` is used for
  the NLSAT / International choice above.
- `get_international_label(label, user, ticket_type)` /
  `get_nlsat_ticket_type(user, ticket_type)`: the International swap.
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

## Ticket link in reply emails to NLSAT applicants

`HDTicket.portal_uri` (the `ticket_url` in the agent-reply email) points
NLSAT applicants (`Applicant` role without `slcm_Student`) to the helpdesk
inside their admission portal, students (a Student Master record by `user`,
`email` or `official_email_id`) to `/student-portal/support`, everyone else to
`/helpdesk/my-tickets/<name>`.

The applicant check runs before the Student Master check: an applicant can
already have a Student Master record (e.g. test data, or one created before the
role swap to `slcm_Student`) and was wrongly sent to `app.nls.ac.in/student-portal`.
Once the user has `slcm_Student` (set by slcm's applicant→student conversion),
they are treated as a student.

`helpdesk/api/nlsat_portal.py` → `get_nlsat_applicant_helpdesk_url(raised_by,
ticket_type)` returns `https://<portal domain>/nlsat/helpdesk`:

- Domestic: `nlsatadmissions.nls.ac.in`; International:
  `internationaladmissions.nls.ac.in`. The domain is read from slcm's Site
  Domain Settings (`slcm.admission.utils.nlsat_portal.get_portal_domain`);
  these two are the fallback when none is set. `frappe.utils.get_url()` can't
  be used: it always gives the site's host_name.
- Domestic vs International: the application's `foriegn_national`, then the
  ticket's NLSAT type, then the portal the user signed up on
  (`User.nlsat_portal`), else Domestic. Unlike the Raised By label, the
  `nlsat_portal` cookie is not used: the email is sent from the agent's
  request, so it would be the agent's cookie.
- `/nlsat/helpdesk` opens the helpdesk list, not the ticket itself (slcm's page
  always loads `/helpdesk` in its iframe).

Tests: `helpdesk/api/test_nlsat_portal.py`.

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
