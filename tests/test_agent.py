from agent.agent import answer_extractively, build_agent_response, needs_follow_up
from agent.prompts import DISCLAIMER


def test_agent_identifies_vague_facility_question():
    assert "full-service" in needs_follow_up("restaurant")


def test_disclaimer_text_is_fixed():
    assert DISCLAIMER.startswith("This is not legal advice.")


def test_extract_answer_keeps_disclaimer_and_relevance_text():
    hits = [
        {
            "document": "Proposal plans must include the physical address of the proposed center.",
            "metadata": {
                "citation": "10 CCR Section 5374",
                "section_heading": "Proposal Plans and Scoring Criteria",
                "source_url": "https://example.test/5374",
                "breadcrumb_path": "Title 10 > Chapter 7.67",
            },
        }
    ]
    response = answer_extractively("What CCR sections apply to a California welcome center?", hits, None)
    assert "Suggested compliance guidance:" in response
    assert "10 CCR Section 5374" in response
    assert "Use this section as a practical checklist" in response
    assert "10 CCR Section 5374" in response
    assert DISCLAIMER in response


def test_build_agent_response_includes_follow_up_metadata(monkeypatch):
    class FakeRetriever:
        def search(self, query, top_k=5, title_number=None, section_number=None):
            return []

    monkeypatch.setattr("agent.agent.CCRRetriever", FakeRetriever)
    response = build_agent_response("restaurant", top_k=3)
    assert response["needs_follow_up"] is True
    assert "full-service" in response["follow_up_question"]
