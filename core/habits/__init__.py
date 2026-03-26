"""
Aleph Framework — Operational Habits module
====================================================
Learn from human escalation resolutions.
Hybrid search RRF (tsvector + pgvector) on any Postgres with pgvector.
"""

from core.habits.database import HabitsDatabase
from core.habits.search import search_habits, search_and_format, HabitMatch
from core.habits.store import store_habit
from core.habits.embeddings import generate_embedding

__all__ = [
    "HabitsDatabase",
    "search_habits",
    "search_and_format",
    "store_habit",
    "generate_embedding",
    "HabitMatch",
]