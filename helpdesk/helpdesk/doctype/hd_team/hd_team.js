// Copyright (c) 2022, Frappe Technologies and contributors
// For license information, please see license.txt

const MAX_ESCALATION_LEVELS = 6;

function renumber_escalation_levels(frm) {
  (frm.doc.escalation_levels || []).forEach((row, i) => {
    row.level = i + 1;
  });
  frm.refresh_field("escalation_levels");
}

// Keep the "Number of Escalation Levels" select in sync with the actual row
// count, without re-triggering the count-change handler (which would try to
// resize the table again).
function sync_level_count_field(frm) {
  const count = String((frm.doc.escalation_levels || []).length || 1);
  if (frm.doc.no_of_escalation_levels !== count) {
    frm.set_value("no_of_escalation_levels", count);
  }
}

function add_blank_escalation_level(frm) {
  frm.add_child("escalation_levels", {
    escalate_after_hours: 0,
    access_level: "Read & Reply",
    notify_assignee: 1,
  });
}

frappe.ui.form.on("HD Team", {
  refresh: function (frm) {
    renumber_escalation_levels(frm);
  },

  enable_ticket_escalation: function (frm) {
    if (frm.doc.enable_ticket_escalation && !(frm.doc.escalation_levels || []).length) {
      add_blank_escalation_level(frm);
      frm.set_value("no_of_escalation_levels", "1");
      renumber_escalation_levels(frm);
    }
  },

  no_of_escalation_levels: function (frm) {
    const target = parseInt(frm.doc.no_of_escalation_levels, 10) || 1;
    const rows = frm.doc.escalation_levels || [];
    const current = rows.length;

    if (target === current) {
      return;
    }

    if (target > current) {
      for (let i = current; i < target; i++) {
        add_blank_escalation_level(frm);
      }
      renumber_escalation_levels(frm);
      return;
    }

    // Shrinking — warn if any of the rows about to be removed have real
    // configuration in them, so an admin can't silently lose a level.
    const rows_to_remove = rows.slice(target);
    const has_data = rows_to_remove.some((r) => r.assigned_to || r.escalate_after_hours);

    const do_shrink = () => {
      frm.doc.escalation_levels = rows.slice(0, target);
      renumber_escalation_levels(frm);
    };

    if (!has_data) {
      do_shrink();
      return;
    }

    frappe.confirm(
      __(
        "This will remove {0} configured escalation level(s) (Level {1} onward). Continue?",
        [rows_to_remove.length, target + 1]
      ),
      do_shrink,
      () => {
        // Cancelled — restore the count field to match the unchanged row count.
        sync_level_count_field(frm);
      }
    );
  },
});

frappe.ui.form.on("HD Escalation Level", {
  escalation_levels_add: function (frm) {
    if (frm.doc.escalation_levels.length > MAX_ESCALATION_LEVELS) {
      frm.doc.escalation_levels.pop();
      frm.refresh_field("escalation_levels");
      frappe.msgprint(
        __("A team can have a maximum of {0} escalation levels.", [
          MAX_ESCALATION_LEVELS,
        ])
      );
      return;
    }
    renumber_escalation_levels(frm);
    sync_level_count_field(frm);
  },
  escalation_levels_remove: function (frm) {
    renumber_escalation_levels(frm);
    sync_level_count_field(frm);
  },
  escalation_levels_move: function (frm) {
    renumber_escalation_levels(frm);
  },
});
