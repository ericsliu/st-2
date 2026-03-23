"""Tests for event lookup: exact and fuzzy matching."""

import pytest

from uma_trainer.knowledge.event_lookup import EventLookup


class TestEventLookup:
    def test_exact_match(self, tmp_db):
        lookup = tmp_db.event_lookup
        event_text = "You're feeling fired up today!"
        lookup.insert(event_text, choice_index=0, effects=["Mood improves"])

        result = lookup.find_exact(event_text)
        assert result is not None
        assert result.best_choice_index == 0

    def test_exact_match_case_insensitive(self, tmp_db):
        lookup = tmp_db.event_lookup
        lookup.insert("Test Event Text", choice_index=1, effects=[])

        # Lookup with different case should still match (normalization)
        result = lookup.find_exact("test event text")
        assert result is not None
        assert result.best_choice_index == 1

    def test_exact_match_missing(self, tmp_db):
        lookup = tmp_db.event_lookup
        result = lookup.find_exact("Completely unknown event text here")
        assert result is None

    def test_fuzzy_match_high_similarity(self, tmp_db):
        """Very similar text should match at 85% threshold."""
        lookup = tmp_db.event_lookup
        lookup.insert(
            "A rival challenges you to a practice race.",
            choice_index=0,
            effects=["Speed +5"],
        )

        # Slightly different text
        result = lookup.find_fuzzy(
            "A rival challenges you to a practice race!",
            threshold=85,
        )
        assert result is not None
        assert result.best_choice_index == 0
        assert result.score >= 85

    def test_fuzzy_match_low_similarity_misses(self, tmp_db):
        """Completely different text should not match at 85% threshold."""
        lookup = tmp_db.event_lookup
        lookup.insert(
            "You're feeling fired up today!",
            choice_index=0,
            effects=[],
        )
        result = lookup.find_fuzzy(
            "The weather is nice for a walk in the park.",
            threshold=85,
        )
        assert result is None

    def test_upsert_updates_existing(self, tmp_db):
        """Inserting same text hash should update choice index."""
        lookup = tmp_db.event_lookup
        lookup.insert("Same event text", choice_index=0, effects=[])
        lookup.insert("Same event text", choice_index=1, effects=["Updated"])

        result = lookup.find_exact("Same event text")
        assert result is not None
        assert result.best_choice_index == 1

    def test_corpus_cache_invalidated_on_insert(self, tmp_db):
        """After inserting a new event, fuzzy corpus should include it."""
        lookup = tmp_db.event_lookup

        # Pre-populate corpus
        _ = lookup._get_corpus()

        # Insert new event
        lookup.insert("Newly inserted event text", choice_index=0, effects=[])

        # Corpus should be invalidated and rebuilt
        assert lookup._corpus is None
        corpus = lookup._get_corpus()
        texts = [t for t, _ in corpus]
        assert any("newly inserted" in t for t in texts)
