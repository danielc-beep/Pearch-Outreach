"""
The two fixed elements of every outreach email.

Every email opens by congratulating the business on its Google rating and
closes with the limited-places, no-pressure note. The rule that matters most
here is the exception: a business whose reviews do not support the praise must
not be congratulated on them anyway, because the recipient can check that in
one click.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic

import outreach


WELL_REVIEWED = {"name": "Wisebuy Home Loans", "industry": "Home loans",
                 "suburb": "Cooks Hill", "rating": 4.9, "review_count": 134}


def _capture_prompt(business):
    """Run a draft with the SDK mocked and hand back the prompt we sent."""
    with patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test"), \
         patch.object(anthropic, "Anthropic") as client_cls:
        parse = client_cls.return_value.messages.parse
        parse.return_value = MagicMock(
            parsed_output=outreach.DraftedEmail(subject="s", body="b"),
            stop_reason="end_turn")
        outreach._claude_draft(business, {"sender_name": "ACM AEO Team"},
                               {"subject": "{business_name}", "body": "hello"})
        return parse.call_args.kwargs["messages"][0]["content"]


# ---------- Who has earned the congratulation ----------

def test_a_strong_rating_with_real_volume_is_praiseworthy():
    standing = outreach.review_standing(WELL_REVIEWED)
    assert standing["praiseworthy"]
    assert standing["phrase"] == "your 4.9-star rating from 134 Google reviews"


def test_a_mediocre_rating_is_not_praised():
    standing = outreach.review_standing({"rating": 3.6, "review_count": 200})
    assert not standing["praiseworthy"]
    assert "3.6" not in standing["phrase"]


def test_a_perfect_rating_from_two_reviews_is_not_a_track_record():
    standing = outreach.review_standing({"rating": 5.0, "review_count": 2})
    assert not standing["praiseworthy"]


def test_no_rating_at_all_is_not_praised():
    standing = outreach.review_standing({"name": "Quiet Co"})
    assert not standing["praiseworthy"]
    assert standing["phrase"]  # still gives the template something true to say


# ---------- What Claude is told ----------

def test_claude_is_told_to_open_on_the_rating():
    prompt = _capture_prompt(WELL_REVIEWED)
    assert "OPEN by congratulating them on their Google review rating" in prompt
    assert "Name the star rating and the review count" in prompt
    assert "4.9 stars from 134 Google reviews" in prompt


def test_claude_is_forbidden_from_praising_a_rating_that_is_not_there():
    prompt = _capture_prompt({"name": "Quiet Co", "suburb": "Hamilton"})
    assert "Do NOT congratulate them on their" in prompt
    assert "no rating on file" in prompt


def test_a_weak_rating_is_flagged_in_the_brief_as_well_as_the_rules():
    business = {"name": "Battler Plumbing", "rating": 3.6, "review_count": 40}
    assert "do not praise it" in outreach._prospect_brief(business)
    prompt = _capture_prompt(business)
    assert "below 4.0" in prompt


def test_claude_is_told_how_to_close():
    # The rules are hard-wrapped, so compare on a single line.
    prompt = " ".join(_capture_prompt(WELL_REVIEWED).split())
    for point in ("places in our content are limited",
                  "isn't right for them that is completely fine",
                  "congratulate them on their hard work",
                  "communities connected and informed"):
        assert point in prompt
    # It is a courtesy, not a squeeze.
    assert "not a scarcity tactic" in prompt


# ---------- The template Claude falls back to ----------

def test_the_template_carries_both_elements_without_claude():
    body = outreach.DEFAULT_CAMPAIGN["body"]
    assert "Congratulations on {reviews}" in body
    assert "Places in our content are limited" in body
    assert "keeps our communities\nconnected and informed" in body


def test_the_merged_template_names_a_real_rating():
    merged = outreach.render_template(outreach.DEFAULT_CAMPAIGN["body"],
                                      outreach.merge_fields(WELL_REVIEWED))
    assert "Congratulations on your 4.9-star rating from 134 Google reviews" in merged


def test_the_merged_template_invents_nothing_when_there_is_no_rating():
    merged = outreach.render_template(outreach.DEFAULT_CAMPAIGN["body"],
                                      outreach.merge_fields({"name": "Quiet Co"}))
    assert "star" not in merged
    assert "Congratulations on the reputation you have built locally" in merged
    # The closing courtesy is unconditional.
    assert "Places in our content are limited" in merged


# ---------- What we can actually deliver ----------

OTHER_ENGINES = ("ChatGPT", "Perplexity", "Copilot", "Gemini", "chatbot",
                 "AI assistant", "LLM")


def test_the_template_promises_google_and_nothing_else():
    body = outreach.DEFAULT_CAMPAIGN["body"] + outreach.DEFAULT_CAMPAIGN["subject"]
    assert "Google" in body
    for engine in OTHER_ENGINES:
        assert engine.lower() not in body.lower(), engine


def test_claude_is_told_google_only():
    prompt = _capture_prompt(WELL_REVIEWED)
    assert "GOOGLE ONLY" in prompt
    # The named ones appear exactly once each, inside the prohibition itself.
    rule = prompt.split("GOOGLE ONLY", 1)[1]
    for engine in ("ChatGPT", "Perplexity", "Copilot", "Gemini"):
        assert prompt.count(engine) == 1, engine
        assert engine in rule, engine


def test_the_pitch_itself_names_only_google():
    # Everything before the rules — how ACM is described to the model.
    pitch = _capture_prompt(WELL_REVIEWED).split("Rules:", 1)[0]
    for engine in OTHER_ENGINES:
        assert engine.lower() not in pitch.lower(), engine
    assert "Google" in pitch
