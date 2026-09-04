"""
The masthead a prospect is pitched from.

A business in Newcastle knows the Newcastle Herald and has never dealt with
"Australian Community Media", so the local title is what the email is written
from. These cover the four alignments that were specified by name, the
matching that picks the rest, and the wiring that carries a masthead from the
search bar to the sentence a business reads.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic

import db
import mastheads
import outreach
import prospect


# ---------- The four that were specified ----------

def test_the_named_alignments():
    for location, expected in (
        ("Newcastle NSW", "the Newcastle Herald"),
        ("Illawarra", "the Illawarra Mercury"),
        ("Sutherland NSW", "The St George and Sutherland Leader"),
        ("Launceston TAS", "The Examiner"),
    ):
        assert mastheads.name_for(mastheads.match(location)) == expected, location


def test_every_title_on_the_rate_card_is_here():
    assert len(mastheads.TITLES) == 79
    assert len({t["site"] for t in mastheads.TITLES}) == 79
    for title in mastheads.TITLES:
        assert title["name"].strip()
        assert title["site"].endswith((".com.au", ".net.au"))


# ---------- Matching ----------

def test_a_suburb_beats_the_region_it_sits_in():
    # "cooks hill" is Newcastle Herald country; the word "hill" alone is not.
    assert mastheads.name_for(mastheads.match("Cooks Hill")) == "the Newcastle Herald"


def test_the_longest_keyword_wins():
    # Both mastheads carry the word "port"; only one carries "port macquarie".
    assert mastheads.name_for(mastheads.match("Port Macquarie")) == "the Port Macquarie News"
    assert mastheads.name_for(mastheads.match("Port Stephens")) == "the Port Stephens Examiner"


def test_an_unmatched_town_falls_back_to_the_network_rather_than_guessing():
    assert mastheads.match("Perth WA") == ""
    assert mastheads.name_for("") == "Australian Community Media"
    assert mastheads.name_for(None) == "Australian Community Media"
    assert mastheads.name_for("nosuchpaper.com.au") == "Australian Community Media"


def test_matching_is_case_and_order_insensitive():
    assert mastheads.match("WOLLONGONG") == mastheads.match("wollongong")
    assert mastheads.match("NSW, Wollongong") == "illawarramercury.com.au"


def test_the_picker_groups_by_state():
    groups = mastheads.options()
    assert [g["state"] for g in groups][:2] == ["NSW", "ACT"]
    assert sum(len(g["titles"]) for g in groups) == 79


# ---------- Prospecting stamps it ----------

def test_a_run_stamps_the_chosen_masthead(client):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC",
                            "limit": 4, "masthead": "newcastleherald.com.au"}, enrich=False)
    rows, _ = db.list_businesses(limit=99)
    assert rows
    # The sample records sit in Bendigo, so the suburb match wins over the
    # masthead that was typed — that is the point of matching per record.
    assert all(b["masthead"] == "bendigoadvertiser.com.au" for b in rows)


def test_a_run_without_a_masthead_falls_back_to_the_location(client):
    result = prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC",
                                     "limit": 3}, enrich=False)
    assert result["masthead"] == "bendigoadvertiser.com.au"
    assert result["masthead_name"] == "the Bendigo Advertiser"


def test_the_list_can_be_filtered_by_masthead(client):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": 4},
                 enrich=False)
    assert db.list_businesses(masthead="bendigoadvertiser.com.au", limit=99)[1] == 4
    assert db.list_businesses(masthead="newcastleherald.com.au", limit=99)[1] == 0


# ---------- What the business reads ----------

def test_the_stored_masthead_is_what_the_email_uses():
    business = {"name": "Wisebuy", "suburb": "Cooks Hill", "masthead": "examiner.com.au"}
    assert outreach.masthead_for(business)["name"] == "The Examiner"


def test_a_record_with_no_masthead_is_matched_on_its_suburb():
    """CSV imports and anything found before the column existed."""
    business = {"name": "Wisebuy", "suburb": "Cooks Hill", "region": "Newcastle"}
    assert outreach.masthead_for(business)["name"] == "the Newcastle Herald"


def test_the_template_pitches_the_masthead_not_the_network():
    fields = outreach.merge_fields({"name": "Wisebuy", "suburb": "Cooks Hill",
                                    "region": "Newcastle", "rating": 4.8, "review_count": 90})
    merged = outreach.render_template(outreach.DEFAULT_CAMPAIGN["body"], fields)
    assert "the Newcastle Herald" in merged
    assert "Australian Community Media" not in merged


def test_an_unmatched_business_still_gets_a_sentence_that_reads():
    fields = outreach.merge_fields({"name": "Perth Co", "suburb": "Perth", "state": "WA"})
    merged = outreach.render_template(outreach.DEFAULT_CAMPAIGN["body"], fields)
    assert "Australian Community Media" in merged
    assert "{masthead}" not in merged


def _prompt_for(business):
    with patch.object(outreach, "ANTHROPIC_API_KEY", "sk-ant-test"), \
         patch.object(anthropic, "Anthropic") as client_cls:
        parse = client_cls.return_value.messages.parse
        parse.return_value = MagicMock(
            parsed_output=outreach.DraftedEmail(subject="s", body="b"), stop_reason="end_turn")
        outreach._claude_draft(business, {"sender_name": "ACM AEO Team"},
                               {"subject": "{business_name}", "body": "hello"})
        return parse.call_args.kwargs["messages"][0]["content"]


def test_claude_is_told_to_write_as_the_masthead():
    prompt = _prompt_for({"name": "Wisebuy", "suburb": "Cooks Hill",
                          "rating": 4.8, "review_count": 90})
    assert "the Newcastle Herald" in prompt
    assert "never pitch as \"Australian Community Media\" instead of the paper" in prompt


def test_claude_is_told_to_use_the_network_when_no_paper_covers_the_town():
    prompt = _prompt_for({"name": "Perth Co", "suburb": "Perth", "state": "WA"})
    assert "do not name a specific paper" in prompt


def test_the_spam_act_footer_names_the_masthead_and_still_names_acm():
    footer = outreach._footer("a@b.com.au", "https://example.com",
                              mastheads.get("newcastleherald.com.au"))
    assert "The Newcastle Herald" in footer          # capitalised, leading the line
    assert "Australian Community Media" in footer    # the entity that actually sent it
    assert "Unsubscribe" in footer


def test_the_footer_without_a_masthead_is_unchanged():
    footer = outreach._footer("a@b.com.au", "https://example.com", None)
    assert "Australian Community Media" in footer


# ---------- The picker and the endpoint ----------

def test_the_search_bar_offers_the_picker(client):
    html = client.get("/").text
    assert 'id="hs-masthead"' in html
    assert "The Newcastle Herald" in html
    assert "<optgroup" in html


def test_the_match_endpoint_drives_the_picker(client):
    body = client.get("/api/masthead/match?location=Wollongong").json()
    assert body == {"site": "illawarramercury.com.au",
                    "name": "the Illawarra Mercury", "matched": True}
    miss = client.get("/api/masthead/match?location=Perth").json()
    assert miss["matched"] is False
    assert miss["name"] == "Australian Community Media"


def test_a_masthead_can_be_corrected_on_the_record(client):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": 2},
                 enrich=False)
    business_id = db.list_businesses(limit=1)[0][0]["id"]
    ok = client.post(f"/api/businesses/{business_id}",
                     json={"masthead": "examiner.com.au"})
    assert ok.status_code == 200
    assert db.get_business(business_id)["masthead"] == "examiner.com.au"


def test_an_invented_masthead_is_refused(client):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": 2},
                 enrich=False)
    business_id = db.list_businesses(limit=1)[0][0]["id"]
    bad = client.post(f"/api/businesses/{business_id}",
                      json={"masthead": "thedailyplanet.com.au"})
    assert bad.status_code == 400
