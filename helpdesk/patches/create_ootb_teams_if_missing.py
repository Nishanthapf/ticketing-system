from helpdesk.setup.team import create_ootb_teams


def execute():
    # hd_team.json fixture was removed because fixture import force-overwrites
    # the whole doc on every migrate, wiping admin-managed SLA escalation
    # config. This patch preserves create-if-missing seeding for sites that
    # already went through the old fixture-based install.
    create_ootb_teams()
