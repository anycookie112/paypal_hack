"""Everything that decides or moves money, as plain functions over the database (docs/SPEC.md §14).

The web API, and later the chat bot and the AI's tools, only call into here. Nothing in app/core knows about
HTTP, React or any chat platform.

    settings.py    the group's settings: a closed set with defaults
    decisions.py   the decision engine: confirmed / needs approval / declined / already on it
    requests.py    telling the group "I'm getting X"; approving and rejecting
    payouts.py     the 3-day hold, reports, and releasing payouts through PayPal; the ledger
    memory.py      the group's memory: who's on what, purchases, budgets, patterns, conversations, the agent log
    defaults.py    a starter list of categories and common items for a new group
"""
