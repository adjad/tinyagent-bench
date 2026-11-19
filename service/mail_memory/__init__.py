"""Mail memory: long-term reminders and profile facts from months-old mail.

Design and measurements: an internal design note.
It runs in the background and shows a suggestion on the Brief only when it is
important (importance.py); the rest is stored with the reason it was held back.
Profile facts (profile_facts.py, an internal design note) are
saved to Harbor's fact memory (facts.db) and used without review, local only; Memory
lists them, and each can be deleted or all forgotten at once.
"""
