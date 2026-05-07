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


def test_restaurant_query_filters_out_welcome_center_hits(monkeypatch):
    class FakeRetriever:
        def search(self, query, top_k=5, title_number=None, section_number=None):
            return [
                {
                    "document": "A California Welcome Center applicant shall submit an application to the Office.",
                    "metadata": {
                        "citation": "10 CCR § 5372",
                        "section_heading": "Application Process",
                        "source_url": "https://example.test/welcome-center",
                        "breadcrumb_path": "Title 10 > Chapter 7.67 > California Welcome Center",
                    },
                    "distance": 0.99,
                },
                {
                    "document": "(a) All food handlers shall wash hands before preparing food.",
                    "metadata": {
                        "citation": "17 CCR § 1234",
                        "section_heading": "Sanitation Requirements for Food Handlers",
                        "source_url": "https://example.test/food",
                        "breadcrumb_path": "Title 17 > Food Safety",
                    },
                    "distance": 0.7,
                },
            ]

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    response = build_agent_response("What CCR sections apply to a restaurant in California?", top_k=3, retriever=FakeRetriever())
    assert "17 CCR § 1234" in response["citations"]
    assert "10 CCR § 5372" not in response["citations"]
