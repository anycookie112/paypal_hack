"""Database tables. The group fund's tables (docs/SPEC.md §14) are added in week 1 of the build plan."""

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)
