"""
Claude-written drafts, with the SDK mocked.

No API key and no network: these pin the request we send and prove that every
failure mode falls back to the template merge rather than breaking the draft
flow. Sending a real request would cost money and be non-deterministic.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

import db
import outreach


BUSINESS = {
    "name": "Wisebuy Home Loans",
    "industry": "Home loans",
    "suburb": "Cooks Hill",
    "region": "Newcastle",
    "website": "https://wisebuygroup.com.au",
    "rating": 5.0,
    "review_count": 134,
    "size_band": "5-19",
    "description": "Newcastle mortgage brokers helping first home buyers.",
}


def _response(subject="A subject", body="A body", stop_reason="end_turn"):
    parsed = outreach.DraftedEmail(subject=subject, body=body)
    return MagicMock(parsed_output=parsed, stop_reason=stop_reason)


def test_the_brief_carries_only_facts_we_hold():
    brief = outreach._prospect_brief(BUSINESS)
    assert "Wisebuy Home Loans" in brief
    assert "Cooks Hill" in brief
    assert "5.0 stars from 134 Google reviews" in brief
    # A field we do not have must not appear as an empty promise.
    assert "None" not in brief
    assert "Staff: 5-19" in brief


def test_a_business_with_no_rating_omits_the_rating_line():
    brief = outreach._prospect_brief({"name": "Quiet Co", "suburb": "Hamilton"})
    assert "Google rating" not in brief
    assert "Quiet Co" in brief


@patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_draft_uses_the_sdk_with_the_configured_model():
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _response(
            "Wisebuy and Google's AI answers", "Hi there, ...")
        result = outreach._claude_draft(BUSINESS, {"sender_name": "ACM AEO Team"},
                                        {"subject": "{business_name}", "body": "hello"})

    assert result == ("Wisebuy and Google's AI answers", "Hi there, ...")
    kwargs = client_cls.return_value.messages.parse.call_args.kwargs
    assert kwargs["model"] == outreach.DRAFT_MODEL
    assert kwargs["output_format"] is outreach.DraftedEmail
    assert kwargs["output_config"] == {"effort": outreach.DRAFT_EFFORT}
    # Thinking tokens count against max_tokens, so it must not be tight.
    assert kwargs["max_tokens"] >= 2000
    prompt = kwargs["messages"][0]["content"]
    assert "Cooks Hill" in prompt
    assert "Never invent a fact" in prompt


def test_no_api_key_means_no_call_at_all():
    with patch.object(outreach, "ANTHROPIC_API_KEY", ""):
        with patch.object(anthropic, "Anthropic") as client_cls:
            assert outreach._claude_draft(BUSINESS, {}, {"subject": "s", "body": "b"}) is None
            client_cls.assert_not_called()


def _sdk_error(kind: str) -> Exception:
    """Build a real SDK exception — each class takes different constructor args."""
    import httpx2 as httpx_lib
    request = httpx_lib.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "network-down":
        return anthropic.APIConnectionError(message="connection refused", request=request)
    status = {"bad-key": 401, "rate-limited": 429, "api-error": 500}[kind]
    response = httpx_lib.Response(status, request=request)
    cls = {
        "bad-key": anthropic.AuthenticationError,
        "rate-limited": anthropic.RateLimitError,
        "api-error": anthropic.APIStatusError,
    }[kind]
    return cls(f"{kind} happened", response=response, body=None)


@pytest.mark.parametrize("kind", ["bad-key", "rate-limited", "api-error", "network-down"])
@patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_every_failure_falls_back_rather_than_raising(kind):
    """A drafting outage must degrade to the template, never break the flow."""
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.side_effect = _sdk_error(kind)
        try:
            result = outreach._claude_draft(BUSINESS, {}, {"subject": "s", "body": "b"})
        except Exception as e:              # noqa: BLE001 - the point of the test
            pytest.fail(f"{kind} escaped instead of falling back: {e}")
    assert result is None


@patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_a_refusal_falls_back():
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _response(
            stop_reason="refusal")
        assert outreach._claude_draft(BUSINESS, {}, {"subject": "s", "body": "b"}) is None


@patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_an_empty_draft_falls_back():
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _response(subject="  ", body="")
        assert outreach._claude_draft(BUSINESS, {}, {"subject": "s", "body": "b"}) is None


def test_the_stored_draft_uses_claude_when_it_answers(sample_run):
    """End to end: draft_message stores what Claude returned, not the template."""
    business = db.list_businesses(has_email=True)[0][0]
    with patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test"):
        with patch.object(anthropic, "Anthropic") as client_cls:
            client_cls.return_value.messages.parse.return_value = _response(
                "Written by Claude", "A personalised body.")
            message = outreach.draft_message(business["id"], use_ai=True)

    assert message["subject"] == "Written by Claude"
    assert message["body"] == "A personalised body."
    assert message["status"] == "draft"          # still needs a human


def test_the_stored_draft_falls_back_to_the_template(sample_run):
    """When Claude is unavailable the merge still produces a usable draft."""
    business = db.list_businesses(has_email=True)[0][0]
    with patch.object(outreach, "ANTHROPIC_API_KEY", ""):
        message = outreach.draft_message(business["id"], use_ai=True)

    assert business["name"] in message["subject"]
    assert "{business_name}" not in message["body"]


# ---------- Saying which part of Google ----------
# "Cited at the top of Google" describes AdWords and every SEO agency in the
# country equally well. The product is the answer Google's AI writes and the
# sources it names inside it, and a reader who files us under advertising or
# search rankings has already stopped reading.

def test_the_template_names_ai_mode_and_ai_overviews():
    import outreach
    body = outreach.DEFAULT_CAMPAIGN["body"].lower()
    assert "ai mode" in body
    assert "ai overview" in body


def test_the_template_says_it_is_neither_ads_nor_rankings():
    import outreach
    body = outreach.DEFAULT_CAMPAIGN["body"].lower()
    assert "not the ads" in body
    assert "blue links" in body


def test_the_subject_line_names_the_surface():
    import outreach
    assert "ai overview" in outreach.DEFAULT_CAMPAIGN["subject"].lower()


@patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_the_drafting_prompt_forbids_the_vague_version():
    """Claude rewrites the body, so the rule has to be in the brief."""
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = _response("s", "b")
        outreach._claude_draft(BUSINESS, {"sender_name": "ACM AEO Team"},
                               outreach.DEFAULT_CAMPAIGN)
    prompt = client_cls.return_value.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "AI Mode" in prompt and "AI Overviews" in prompt
    assert "not advertising" in prompt
    assert "search-engine optimisation" in prompt
    # The exact phrasing to avoid is named, not just implied.
    assert "top of Google" in prompt
    assert "AdWords" in prompt


def test_a_draft_that_never_names_the_surface_is_flagged():
    import outreach
    notes = outreach.warnings({"body": "We can get you found on Google. Call us."})
    assert any("AI Mode or AI Overviews" in n for n in notes), notes


def test_a_draft_that_sounds_like_seo_is_flagged():
    import outreach
    notes = outreach.warnings(
        {"body": "We get you to the top of Google, cited in AI Overviews."})
    assert any("search ranking" in n for n in notes), notes


def test_a_draft_that_names_the_surface_is_not_flagged():
    import outreach
    notes = outreach.warnings({
        "body": "Google's AI Mode writes the answer and names its sources. "
                "That list is not the ads. We get you quoted in it."})
    assert not any("AI Mode or AI Overviews" in n for n in notes), notes
