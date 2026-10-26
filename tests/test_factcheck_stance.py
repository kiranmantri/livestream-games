"""The checker's stance (decided with the user, 2026-10-26): CRITICAL. Pseudo-history and conspiracy claims are judged against the evidence; a
factual-sounding claim or a strong opinion WITHOUT strong evidence is "false", and the explanation says the evidence is missing. "unverifiable" is
left for taste, jokes, predictions about the future and personal faith phrased as such ("eu acredito em Deus"); "Deus existe" stated as a fact is
judged on the evidence. These tests pin the rules in the prompts; the model's real behaviour is checked with a real run (docs/validation/checker.md)."""
import asyncio

from app import factcheck as FC

R, A = FC.RESEARCH_INSTRUCTIONS, FC.ANSWER_INSTRUCTIONS


def test_the_answer_rules_turn_missing_evidence_into_false():
    assert "falta de evidência" in A and '"false"' in A
    assert "pseudo-história" in A.lower() and "conspiração" in A.lower()
    assert "opinião forte" in A


def test_unverifiable_is_only_for_taste_jokes_predictions_and_personal_faith():
    assert "eu acredito em Deus" in A
    assert '"Deus existe" afirmado como fato => false' in A          # the real model answered "unverifiable" to the softer wording
    assert '"Deus existe" afirmado como fato' in R and "pesquise" in R
    assert "por exemplo \"Deus existe\"): checkable=false" not in A        # the old rule is gone


def test_research_always_searches_unless_there_is_truly_nothing_to_check():
    assert "sem pesquisar" not in R.split("gosto pessoal")[0]          # beliefs/opinions are no longer skipped
    assert "pseudo-história" in R.lower() and "falta de evidência" in R.lower()


def test_the_checkable_field_tells_the_model_the_same_rule():
    desc = FC.Answer.model_fields["checkable"].description or ""
    assert "taste" in desc and "faith" in desc and "opinion" not in desc.split("False only for")[1].split(";")[0]


# ---------------------------------------------------------------- search: an empty answer is retried once (DuckDuckGo throttles parallel checks)
def _ddgs_script(monkeypatch, answers):
    import ddgs
    calls = []

    class Fake:
        def text(self, query, **k):
            calls.append(k.get("region"))
            a = answers[min(len(calls), len(answers)) - 1]
            if isinstance(a, Exception):
                raise a
            return a

    monkeypatch.setattr(ddgs, "DDGS", Fake)
    monkeypatch.setattr(FC, "SEARCH_RETRY_PAUSE_S", 0)
    return calls


HIT = [{"title": "t", "href": "https://example.org/a", "body": "b"}]


def test_an_empty_search_is_retried_once_worldwide(monkeypatch):
    from ddgs.exceptions import DDGSException
    calls = _ddgs_script(monkeypatch, [DDGSException("No results found."), HIT])
    assert asyncio.run(FC.default_search("q")) == HIT
    assert calls == ["br-pt", "wt-wt"]


def test_two_empty_answers_give_an_empty_list(monkeypatch):
    calls = _ddgs_script(monkeypatch, [[], []])
    assert asyncio.run(FC.default_search("q")) == []
    assert len(calls) == 2


def test_results_on_the_first_try_are_not_retried(monkeypatch):
    calls = _ddgs_script(monkeypatch, [HIT])
    assert asyncio.run(FC.default_search("q")) == HIT and calls == ["br-pt"]
