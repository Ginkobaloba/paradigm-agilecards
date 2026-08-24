from . import audit_log, cards, misc, sprints, sse, stories, triage, views

ALL_ROUTERS = (
    misc.router,
    cards.router,
    views.router,
    sprints.router,
    triage.router,
    stories.router,
    audit_log.router,
    sse.router,
)

__all__ = ["ALL_ROUTERS"]
