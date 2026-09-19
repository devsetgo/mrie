# -*- coding: utf-8 -*-
"""
Person names must never survive into a note's tags or summary, whatever
culture or script they come from - see ai.get_analysis() and its helpers.

get_analysis is driven with a fake OpenAI client so nothing here touches the
network; the model's reply is the only thing that varies between cases.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.functions import ai

# ---- helpers ----


def test_normalize_ignores_case_and_accents():
    assert ai._normalize("José") == ai._normalize("JOSE") == "jose"
    assert ai._normalize("Siobhán") == "siobhan"
    assert ai._normalize("Nguyễn") == "nguyen"


def test_name_tokens_splits_names_and_keeps_every_script():
    tokens = ai.name_tokens(["Wei Chen", "María de la Cruz", "李伟", "Wei-Ming"])
    assert {"wei", "chen", "maria", "cruz", "ming", "李伟"} <= tokens


def test_name_tokens_drops_initials_and_bad_input():
    # A one-letter token would block "a" from every summary.
    assert ai.name_tokens(["J."]) == set()
    assert ai.name_tokens(None) == set()
    assert ai.name_tokens("Wei") == set()


def test_name_check_matches_accented_forms():
    assert ai.name_check("José")
    assert ai.name_check("JOSÉ")


def test_tags_keep_accents_instead_of_mangling_them():
    # "[a-zA-Z]+" used to turn "José" into "Jos", which then evaded the
    # names database entirely.
    assert "".join(ai.re.findall(ai._LETTERS_ONLY_RE, "José")) == "José"
    assert "".join(ai.re.findall(ai._LETTERS_ONLY_RE, "李伟")) == "李伟"


def test_tag_check_drops_extra_names_only_when_given():
    tags = {"tags": ["lunch", "olumide"]}
    assert ai.tag_check(tags)["tags"] == ["lunch", "olumide"]
    assert ai.tag_check(tags, extra_names={"olumide"})["tags"] == ["lunch"]


@pytest.mark.parametrize(
    "summary,names_found,expected",
    [
        ("Dinner With Wei Chen", ["Wei Chen"], "Dinner"),
        ("Siobhan's Birthday Party", ["Siobhán"], "Birthday Party"),
        ("Olumide Visits Ireland", ["Olumide"], "Visits Ireland"),
        ("Lunch With 李伟", ["李伟"], "Lunch"),
        ("今天和李伟吃饭", ["李伟"], "今天和 吃饭"),
        ("Wei", ["Wei"], ""),
        ("Quiet Morning Walk", ["Wei"], "Quiet Morning Walk"),
    ],
)
def test_strip_names(summary, names_found, expected):
    assert ai.strip_names(summary, ai.name_tokens(names_found)) == expected


def test_is_blocked_matches_inflected_non_latin_names():
    # The model lists the case-inflected form from the entry; a tag or
    # summary may then use the nominative.
    blocked = ai.name_tokens(["Олексієм", "Наталією"])
    assert ai.is_blocked("Олексій", blocked)
    assert ai.is_blocked("Наталія", blocked)
    assert not ai.is_blocked("друг", blocked)
    assert not ai.is_blocked("день", blocked)


def test_is_blocked_stays_exact_for_latin_script():
    # Stem matching here would make "Grace" swallow "graceful".
    blocked = ai.name_tokens(["Grace"])
    assert ai.is_blocked("grace", blocked)
    assert not ai.is_blocked("graceful", blocked)


def test_capitalized_known_names_needs_a_mid_sentence_capital():
    # "Hope"/"Grace" are ordinary words that are also names, so position
    # decides: sentence-initial is a word, mid-sentence is a person.
    assert ai.capitalized_known_names("Hope is a good thing.") == set()
    found = ai.capitalized_known_names("I had lunch with Grace today.")
    assert found == {"grace"}


# ---- get_analysis end to end, with a fake client ----


def _fake_client(reply: dict):
    async def create(**kwargs):
        message = SimpleNamespace(content=json.dumps(reply))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _analyze(monkeypatch, content: str, reply: dict) -> dict:
    monkeypatch.setattr(ai, "_client", _fake_client(reply))
    return asyncio.run(ai.get_analysis(content))


def _reply(**overrides) -> dict:
    reply = {
        "person_names": [],
        "tags": ["work"],
        "summary": "Productive Day At Work",
        "mood_analysis": "content",
        "mood": "positive",
    }
    reply.update(overrides)
    return reply


def test_model_reported_international_names_are_removed(monkeypatch):
    # None of these are in the names database; only person_names catches them.
    result = _analyze(
        monkeypatch,
        "Had dinner with Siobhán and Nguyễn Văn An after meeting 李伟.",
        _reply(
            person_names=["Siobhán", "Nguyễn Văn An", "李伟"],
            tags=["dinner", "siobhan", "Nguyễn", "李伟", "friends"],
            summary="Dinner With Siobhán And Nguyễn",
        ),
    )
    assert result["tags"]["tags"] == ["dinner", "friends"]
    assert result["summary"] == "Dinner"
    assert "_ai_fix" not in result


def test_name_in_tags_is_caught_when_accents_differ(monkeypatch):
    # The model listed the accented name; the tag came back without accents.
    result = _analyze(
        monkeypatch,
        "Coffee with Zoë.",
        _reply(person_names=["Zoë"], tags=["coffee", "zoe"]),
    )
    assert result["tags"]["tags"] == ["coffee"]


def test_database_backstop_when_model_reports_no_names(monkeypatch):
    # Model forgot person_names entirely; "Grace" is capitalized mid-sentence.
    reply = _reply(tags=["lunch", "grace"], summary="Lunch With Grace")
    del reply["person_names"]
    result = _analyze(monkeypatch, "I had lunch with Grace today.", reply)
    assert result["tags"]["tags"] == ["lunch"]
    assert result["summary"] == "Lunch"


def test_ordinary_word_that_is_also_a_name_is_left_alone(monkeypatch):
    result = _analyze(
        monkeypatch,
        "Hope is a good thing to hold on to.",
        _reply(tags=["hope"], summary="Holding On To Hope"),
    )
    # Sentence-initial "Hope" is a word, not a person. The tag still goes
    # because the pre-existing names-database check on tags is unchanged.
    assert result["summary"] == "Holding On To Hope"


def test_summary_that_was_only_a_name_is_flagged_for_retry(monkeypatch):
    result = _analyze(
        monkeypatch,
        "Met Olumide.",
        _reply(person_names=["Olumide"], summary="Olumide"),
    )
    assert result["summary"] == ""
    assert result["_ai_fix"] is True


def test_malformed_person_names_does_not_break_analysis(monkeypatch):
    result = _analyze(monkeypatch, "A quiet day.", _reply(person_names="not a list"))
    assert result["summary"] == "Productive Day At Work"
    assert result["tags"]["tags"] == ["work"]
