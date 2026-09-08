"""
The dashboard coach: three things to do next, and a sounding board.

The SDK is mocked throughout — no key, no network. What these pin down is
the part that matters on a screen someone makes decisions from: the advice
is built from real figures, it degrades to arithmetic when Claude cannot be
reached, and it can never point at a link the app does not have.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import anthropic
import pytest

import coach
import crm
import db


def _seed(rows):
    """A handful of businesses at chosen stages, with values on some."""
    ids = []
    for i, (status, value) in enumerate(rows):
        business_id = db.insert_business({
            "name": f"Business {i}", "suburb": "Newcastle", "region": "Newcastle",
            "industry": "plumber", "status": status, "rating": 4.7,
            "review_count": 60, "email": f"b{i}@example.com",
            "masthead": "newcastleherald.com.au", "deal_value": value,
        })
        ids.append(business_id)
    return ids


def _advice(*suggestions):
    parsed = coach.Advice(suggestions=[coach.Suggestion(**s) for s in suggestions])
    return MagicMock(parsed_output=parsed, stop_reason="end_turn")


# ---------- The brief ----------

def test_the_brief_carries_the_live_pipeline(client):
    _seed([("replied", 5000), ("contacted", None), ("won", 9000)])
    data = coach.brief()
    text = coach.brief_text(data)
    assert "$9,000" in text                      # closed won
    assert "Replied: 1" in text
    assert "SCREENS A SUGGESTION CAN POINT AT" in text


def test_the_brief_names_the_screens_the_app_actually_has(client):
    text = coach.brief_text(coach.brief())
    for key in coach.FOCUS:
        assert f"- {key}:" in text


def test_the_fingerprint_moves_only_when_the_pipeline_does(client):
    ids = _seed([("new", None), ("new", None)])
    first = coach.fingerprint(coach.brief())
    assert coach.fingerprint(coach.brief()) == first, "the same pipeline, twice"
    crm.move(ids[0], "researching")
    assert coach.fingerprint(coach.brief()) != first


# ---------- Advice without Claude ----------

def test_it_advises_with_no_api_key_at_all(client):
    _seed([("replied", 5000)])
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        got = coach.suggestions()
    assert got["source"] == "rules"
    assert len(got["suggestions"]) == 3


def test_the_reply_comes_first_because_it_is_the_closest_money(client):
    _seed([("replied", 5000), ("new", None), ("new", None)])
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        first = coach.suggestions()["suggestions"][0]
    assert "replied" in first["headline"].lower()
    assert first["urgency"] == "now"
    assert first["url"] == "/businesses?status=replied"


def test_every_suggestion_quotes_a_number(client):
    _seed([("replied", 5000), ("contacted", None), ("qualified", None)])
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        got = coach.suggestions()["suggestions"]
    for suggestion in got:
        assert any(ch.isdigit() for ch in suggestion["why"]), suggestion


def test_an_empty_database_still_gets_advice(client):
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        got = coach.suggestions()
    assert got["suggestions"], "the panel is never blank"
    assert all(s["url"] for s in got["suggestions"])


# ---------- Advice from Claude ----------

@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_claude_writes_the_three_when_it_can_be_asked(client):
    _seed([("replied", 5000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _advice(
            {"headline": "Answer the one who replied", "why": "1 reply is waiting.",
             "focus": "replied", "urgency": "now"},
            {"headline": "Send the approved", "why": "0 sitting.",
             "focus": "outbox", "urgency": "soon"},
            {"headline": "Prospect more", "why": "3 businesses is thin.",
             "focus": "prospect", "urgency": "steady"},
        )
        got = coach.suggestions()
    assert got["source"] == "claude"
    assert got["suggestions"][0]["headline"] == "Answer the one who replied"
    assert got["suggestions"][0]["url"] == "/businesses?status=replied"


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_the_prompt_carries_the_figures_and_nothing_invented(client):
    _seed([("replied", 5000), ("won", 9000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _advice(
            {"headline": "A", "why": "1 thing.", "focus": "replied", "urgency": "now"})
        coach.suggestions()
        sent = client_cls.return_value.messages.parse.call_args.kwargs
    assert "$9,000" in sent["messages"][0]["content"]
    assert "Never invent" in sent["system"]


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_a_made_up_link_falls_back_to_a_real_one(client):
    """The model picks a key; the app owns the URL. A bad key cannot escape."""
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _advice(
            {"headline": "Ring them", "why": "5 waiting.",
             "focus": "/made/up/path", "urgency": "urgent-now-immediately"})
        got = coach.suggestions()["suggestions"][0]
    assert got["url"] == coach.FOCUS[coach.DEFAULT_FOCUS]["url"]
    assert got["urgency"] == "steady"


@pytest.mark.parametrize("boom", [
    anthropic.AuthenticationError("no", response=MagicMock(status_code=401), body=None),
    anthropic.APIConnectionError(request=MagicMock()),
])
@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_an_outage_degrades_to_arithmetic(client, boom):
    _seed([("replied", 5000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.side_effect = boom
        got = coach.suggestions()
    assert got["source"] == "rules"
    assert len(got["suggestions"]) == 3


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_a_refusal_degrades_too(client):
    _seed([("replied", 5000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = MagicMock(
            parsed_output=None, stop_reason="refusal")
        assert coach.suggestions()["source"] == "rules"


# ---------- The cache ----------

@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_the_same_pipeline_is_not_asked_about_twice(client):
    _seed([("replied", 5000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _advice(
            {"headline": "A", "why": "1 thing.", "focus": "replied", "urgency": "now"})
        coach.suggestions()
        coach.suggestions()
        assert client_cls.return_value.messages.parse.call_count == 1


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_moving_a_deal_asks_again(client):
    ids = _seed([("replied", 5000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _advice(
            {"headline": "A", "why": "1 thing.", "focus": "replied", "urgency": "now"})
        coach.suggestions()
        crm.move(ids[0], "won")
        coach.suggestions()
        assert client_cls.return_value.messages.parse.call_count == 2


def test_the_fallback_is_never_cached(client):
    """A key arriving should fix the panel, not wait for the pipeline to move."""
    _seed([("replied", 5000)])
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        coach.suggestions()
    assert db.get_setting(coach.CACHE_KEY) == ""


def test_preview_never_calls_the_model(client):
    """It runs while someone waits for the page to draw."""
    _seed([("replied", 5000)])
    with patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test"):
        with patch.object(anthropic, "Anthropic") as client_cls:
            got = coach.preview()
            assert client_cls.return_value.messages.parse.call_count == 0
    assert len(got["suggestions"]) == 3


# ---------- The sounding board ----------

def test_the_chat_says_so_when_it_has_no_key(client):
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        answer = coach.ask("What should I do about Contacted?")
    assert answer["source"] == "unconfigured"
    assert "ANTHROPIC_API_KEY" in answer["reply"]


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_the_chat_answers_against_the_live_figures(client):
    _seed([("won", 9000)])
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.create.return_value = MagicMock(
            stop_reason="end_turn",
            content=[MagicMock(type="text", text="Chase the replies first.")])
        answer = coach.ask("Where should I spend today?")
        sent = client_cls.return_value.messages.create.call_args.kwargs
    assert answer["reply"] == "Chase the replies first."
    assert "$9,000" in sent["system"]
    assert sent["messages"][-1]["content"] == "Where should I spend today?"


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_the_conversation_is_carried_and_starts_with_a_person(client):
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.create.return_value = MagicMock(
            stop_reason="end_turn", content=[MagicMock(type="text", text="Yes.")])
        coach.ask("And after that?", history=[
            {"role": "assistant", "text": "An orphan reply, first in the list."},
            {"role": "user", "text": "What about Qualified?"},
            {"role": "assistant", "text": "Work it Tuesday."},
        ])
        turns = client_cls.return_value.messages.create.call_args.kwargs["messages"]
    assert turns[0]["role"] == "user", "the API rejects a conversation Claude starts"
    assert [t["content"] for t in turns] == [
        "What about Qualified?", "Work it Tuesday.", "And after that?"]


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_an_empty_question_never_reaches_the_model(client):
    with patch.object(anthropic, "Anthropic") as client_cls:
        coach.ask("   ")
        assert client_cls.return_value.messages.create.call_count == 0


@patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_a_chat_outage_is_a_sentence_not_a_stack_trace(client):
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.create.side_effect = \
            anthropic.APIConnectionError(request=MagicMock())
        answer = coach.ask("Anything there?")
    assert answer["source"] == "error"
    assert "Anthropic" in answer["reply"]


# ---------- On the page ----------

def _rendered_items(body):
    """The list as the server drew it, not the one the script would draw."""
    block = re.search(r'<ol class="coach-list" id="coach-list">(.*?)</ol>', body, re.S)
    return re.findall(r'<li class="coach-item', block.group(1)) if block else []


def test_the_dashboard_renders_three_suggestions(client):
    _seed([("replied", 5000), ("contacted", None)])
    body = client.get("/").text
    assert 'id="coach"' in body
    assert len(_rendered_items(body)) == 3
    assert "undefined" not in body


def test_coverage_is_gone_from_the_dashboard(client):
    body = client.get("/").text
    assert "cover-group" not in body


def test_the_suggestions_endpoint_answers(client):
    _seed([("replied", 5000)])
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        got = client.get("/api/coach/suggestions").json()
    assert len(got["suggestions"]) == 3
    assert got["source"] == "rules"


def test_the_ask_endpoint_answers(client):
    with patch.object(coach, "ANTHROPIC_API_KEY", ""):
        got = client.post("/api/coach/ask", json={"question": "What now?"}).json()
    assert got["source"] == "unconfigured"


def test_a_long_question_is_cut_rather_than_refused(client):
    with patch.object(coach, "ANTHROPIC_API_KEY", "sk-ant-test"):
        with patch.object(anthropic, "Anthropic") as client_cls:
            client_cls.return_value.messages.create.return_value = MagicMock(
                stop_reason="end_turn", content=[MagicMock(type="text", text="Fine.")])
            coach.ask("x" * 9000)
            sent = client_cls.return_value.messages.create.call_args.kwargs
    assert len(sent["messages"][-1]["content"]) == coach.MAX_QUESTION


# ---------- Where it sits on the page ----------
# Laid out flat, the coach was half again as tall as the two columns beside
# it and the page ended in a wedge of empty blue. These pin the arrangement
# that fixed it, because it is one CSS edit away from coming back.

CSS = (__import__("pathlib").Path(__file__).resolve().parent.parent / "static" / "app.css").read_text()


def test_the_coach_runs_down_the_full_height_of_the_grid(client):
    body = client.get("/").text
    assert 'class="dash-col dash-tall coach"' in body
    assert ".dash-tall { grid-column: 3; grid-row: 1 / span 2;" in CSS


def test_the_trades_table_fills_the_space_the_coach_casts(client):
    """It used to span every column, which left the shortfall under it."""
    assert ".dash-wide { grid-column: 1 / 3; }" in CSS


def test_the_composer_sits_on_the_floor_of_the_column(client):
    assert ".coach-ask { display: flex; gap: 6px; margin-top: auto; }" in CSS


def test_the_transcript_can_actually_be_hidden(client):
    """The class sets display, which beats the browser's own rule for [hidden]."""
    assert ".coach-chat[hidden] { display: none; }" in CSS


def test_the_starters_fill_the_room_the_three_leave_over(client):
    body = client.get("/").text
    assert body.count('class="coach-starter"') == 3
    assert "What should I do first today?" in body
