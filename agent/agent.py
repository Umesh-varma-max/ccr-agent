from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from agent.prompts import DISCLAIMER, SYSTEM_PROMPT
    from agent.retriever import CCRRetriever, get_shared_retriever
except ModuleNotFoundError:
    from prompts import DISCLAIMER, SYSTEM_PROMPT
    from retriever import CCRRetriever, get_shared_retriever


FOLLOW_UP_HINTS = {
    "restaurant": "Is this a full-service restaurant, mobile food facility, bar, or commissary kitchen?",
    "farm": "What is being grown or handled, and are pesticides, labor housing, or food processing involved?",
    "theater": "Is this a movie theater, live performance venue, or temporary event space, and what is the occupancy?",
}

QUERY_EXPANSION_TERMS = {
    "restaurant": {"food", "meal", "meals", "eating", "kitchen", "sanitation", "sanitary", "foodhandler", "foodhandlers"},
    "farm": {"agriculture", "agricultural", "pesticide", "pesticides", "crop", "crops", "livestock", "housing", "processing"},
    "theater": {"theater", "theatre", "venue", "occupancy", "assembly", "audience", "performance", "egress", "exit"},
}

STOP_TERMS = {
    "what",
    "which",
    "apply",
    "applies",
    "california",
    "facility",
    "facilities",
    "section",
    "sections",
    "regulation",
    "regulations",
    "laws",
    "operator",
}


def infer_title_filter(question: str) -> int | None:
    match = re.search(r"title\s+(\d{1,2})", question, re.I)
    return int(match.group(1)) if match else None


def needs_follow_up(question: str) -> str | None:
    lowered = question.lower()
    for keyword, prompt in FOLLOW_UP_HINTS.items():
        if keyword in lowered and len(question.split()) < 12:
            return prompt
    if len(question.split()) < 5:
        return "What facility type, activity, location context, and compliance topic should I focus on?"
    return None


def build_context(hits: list[dict[str, Any]]) -> str:
    blocks = []
    for idx, hit in enumerate(hits, start=1):
        meta = hit["metadata"]
        blocks.append(
            f"[{idx}] {meta.get('citation')} | {meta.get('section_heading')}\n"
            f"Source: {meta.get('source_url')}\n"
            f"{hit['document']}"
        )
    return "\n\n---\n\n".join(blocks)


def build_llm_briefs(question: str, hits: list[dict[str, Any]]) -> str:
    blocks = []
    for idx, hit in enumerate(hits[:4], start=1):
        meta = hit["metadata"]
        overlap = ", ".join(hit_overlap_terms(question, hit)[:4]) or "closest CCR match from the indexed dataset"
        key_points = extract_key_points(hit.get("document", ""), limit=2)
        key_points_text = " | ".join(key_points) if key_points else summarize_hit(hit, length=180)
        blocks.append(
            f"{idx}. Citation: {meta.get('citation')}\n"
            f"Heading: {meta.get('section_heading')}\n"
            f"Why it may apply: {overlap}\n"
            f"Plain-English details: {key_points_text}"
        )
    return "\n\n".join(blocks)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_query_terms(question: str) -> set[str]:
    terms = {
        token
        for token in re.findall(r"[a-z0-9]+", question.lower())
        if len(token) > 3 and token not in STOP_TERMS
    }
    lowered = question.lower()
    for keyword, expansions in QUERY_EXPANSION_TERMS.items():
        if keyword in lowered:
            terms.update(expansions)
    return terms


def hit_overlap_terms(question: str, hit: dict[str, Any]) -> list[str]:
    meta = hit["metadata"]
    haystack = " ".join(
        [
            meta.get("section_heading") or "",
            meta.get("breadcrumb_path") or "",
            hit.get("document") or "",
        ]
    ).lower()
    return [term for term in sorted(extract_query_terms(question)) if term in haystack]


def prioritize_hits(question: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for hit in hits:
        overlap = hit_overlap_terms(question, hit)
        distance = float(hit.get("distance") or 0.0)
        scored.append((len(overlap), distance, hit))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    prioritized = [hit for _, _, hit in scored]
    overlapping_hits = [hit for hit in prioritized if hit_overlap_terms(question, hit)]
    return overlapping_hits or prioritized


def explain_hit_relevance(question: str, hit: dict[str, Any]) -> str:
    meta = hit["metadata"]
    heading = shorten_heading(meta.get("section_heading"))
    breadcrumb = normalize_space(meta.get("breadcrumb_path") or "")
    evidence = hit_overlap_terms(question, hit)
    if evidence:
        return f"This section overlaps with your question about {', '.join(evidence[:4])}."
    if heading:
        return f"This section appears relevant based on its heading: {heading}."
    if breadcrumb:
        return f"This section appears relevant based on its placement in the CCR hierarchy: {breadcrumb}."
    return "This section was one of the closest matches in the indexed CCR data."


def summarize_hit(hit: dict[str, Any], length: int = 260) -> str:
    snippet = normalize_space(hit.get("document", ""))
    return snippet[:length].rstrip() + ("..." if len(snippet) > length else "")


def shorten_heading(text: str | None, length: int = 110) -> str:
    cleaned = normalize_space(text or "") or "Untitled section"
    return cleaned[:length].rstrip() + ("..." if len(cleaned) > length else "")


def extract_key_points(document: str, limit: int = 3) -> list[str]:
    points: list[str] = []
    for raw_line in document.splitlines():
        line = normalize_space(raw_line.replace("##", ""))
        if not line or line.startswith("Note:") or line.startswith("History"):
            continue
        if line.startswith("§"):
            continue
        if re.match(r"^\([a-z0-9]+\)", line, re.I):
            points.append(line)
        elif not points and len(line) > 35:
            points.append(line)
        if len(points) >= limit:
            break
    return points


def build_advice_sentence(brief: dict[str, Any], hit: dict[str, Any]) -> str:
    heading = (brief.get("section_heading") or "").lower()
    points = extract_key_points(hit.get("document", ""))
    if "eligib" in heading and points:
        return "Check whether your site meets the eligibility requirements, including: " + "; ".join(points[:3]) + "."
    if "responsib" in heading and points:
        return "Plan to comply with the operating obligations in this section, especially: " + "; ".join(points[:3]) + "."
    if "revocation" in heading and points:
        return "Treat this section as an enforcement risk check, because it warns that noncompliance can affect designation status."
    if "definition" in heading and points:
        return "Use this section to confirm whether your facility or activity falls within the regulatory definition being discussed."
    if points:
        return "Use this section as a practical checklist, focusing on: " + "; ".join(points[:3]) + "."
    return brief.get("why_it_applies") or "Review this section because it was one of the closest matches in the indexed CCR data."


def dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique_hits: list[dict[str, Any]] = []
    for hit in hits:
        meta = hit["metadata"]
        key = (meta.get("source_url", ""), meta.get("citation", ""))
        if key in seen:
            continue
        seen.add(key)
        unique_hits.append(hit)
    return unique_hits


def build_section_briefs(question: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for hit in hits:
        meta = hit["metadata"]
        brief = {
            "citation": meta.get("citation"),
            "section_heading": shorten_heading(meta.get("section_heading")),
            "breadcrumb_path": meta.get("breadcrumb_path"),
            "source_url": meta.get("source_url"),
            "title_number": meta.get("title_number"),
            "chapter": meta.get("chapter"),
            "section_number": meta.get("section_number"),
            "snippet": summarize_hit(hit),
            "why_it_applies": explain_hit_relevance(question, hit),
        }
        brief["advice"] = build_advice_sentence(brief, hit)
        briefs.append(
            brief
        )
    return briefs


def format_assignment_response(
    question: str,
    section_briefs: list[dict[str, Any]],
    follow_up: str | None,
    has_strong_match: bool,
    no_data_message: str | None = None,
) -> str:
    lines: list[str] = []
    if follow_up:
        lines.append(f"Follow-up question: {follow_up}")

    if no_data_message:
        lines.append(no_data_message)
        lines.append(DISCLAIMER)
        return "\n\n".join(lines)

    if not has_strong_match:
        lines.append("I could not confirm a strong facility-specific match from the currently indexed CCR data, so treat this as preliminary guidance based on the closest sections available right now.")

    lines.append("Suggested compliance guidance:")
    for index, brief in enumerate(section_briefs[:3], start=1):
        citation = brief.get("citation") or "Unknown citation"
        heading = brief.get("section_heading") or "Untitled section"
        why = brief.get("why_it_applies") or "This was one of the closest matches in the indexed CCR data."
        advice = brief.get("advice") or why
        lines.append(
            f"{index}. {advice} ({citation}; {heading})"
        )

    if len(section_briefs) > 3:
        extra = len(section_briefs) - 3
        lines.append(f"I found {extra} additional supporting CCR sections, listed separately below the answer.")

    lines.append(DISCLAIMER)
    return "\n\n".join(lines)


def answer_with_llm(question: str, hits: list[dict[str, Any]], follow_up: str | None) -> str:
    from groq import Groq

    client = Groq()
    context = build_llm_briefs(question, hits)
    user_message = (
        f"Question: {question}\n\n"
        f"Retrieved CCR section briefs:\n{context}\n\n"
        "Write the answer in this exact structure:\n"
        "Compliance Advice:\n"
        "According to the provided context data, <one short introductory sentence>.\n"
        "Then 2 to 4 numbered items.\n"
        "Each numbered item must use this shape:\n"
        "<number>. <short topic label>: <citation> <two short sentences in plain English explaining why it matters and what the operator should do.>\n"
        "After the numbered items, add one short concluding sentence.\n"
        "Then add the exact disclaimer as the last line.\n\n"
        "Rules:\n"
        "- Keep the answer clean and easy to scan.\n"
        "- Sound like a human advisor, not like a statute database.\n"
        "- Do not include bullet points other than the numbered list.\n"
        "- Do not include source URLs.\n"
        "- Do not mention retrieval quality, confidence, internal systems, or missing embeddings.\n"
        "- Do not copy long legal passages.\n"
        "- Use the section heading to create the short topic label when possible."
    )
    if follow_up:
        user_message += f"\nIf a follow-up is truly necessary, ask this exact question before the numbered list: {follow_up}"
    response = client.chat.completions.create(
        model=os.getenv("GROQ_CHAT_MODEL", "llama-3.1-8b-instant"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )
    text = (response.choices[0].message.content or "").strip()
    return text if DISCLAIMER in text else f"{text}\n\n{DISCLAIMER}"


def answer_extractively(question: str, hits: list[dict[str, Any]], follow_up: str | None) -> str:
    if not hits:
        return format_assignment_response(
            question=question,
            section_briefs=[],
            follow_up=follow_up,
            has_strong_match=False,
            no_data_message="I could not find indexed CCR sections for that question in the current data.",
        )

    section_briefs = build_section_briefs(question, hits)
    strong_hits = [hit for hit in hits if hit_overlap_terms(question, hit)]
    return format_assignment_response(
        question=question,
        section_briefs=section_briefs,
        follow_up=follow_up,
        has_strong_match=bool(strong_hits),
    )


def build_agent_response(question: str, top_k: int = 5, retriever: CCRRetriever | None = None) -> dict[str, Any]:
    follow_up = needs_follow_up(question)
    title_filter = infer_title_filter(question)
    active_retriever = retriever or get_shared_retriever()
    hits = dedupe_hits(active_retriever.search(question, top_k=top_k, title_number=title_filter))
    hits = prioritize_hits(question, hits)
    used_llm = False

    if os.getenv("GROQ_API_KEY"):
        try:
            answer_text = answer_with_llm(question, hits, follow_up)
            used_llm = True
        except Exception:
            answer_text = answer_extractively(question, hits, follow_up)
    else:
        answer_text = answer_extractively(question, hits, follow_up)

    section_briefs = build_section_briefs(question, hits)
    strong_match = any(hit_overlap_terms(question, hit) for hit in hits)
    citations = list(
        dict.fromkeys(
            hit["metadata"].get("citation", "")
            for hit in hits
            if hit.get("metadata", {}).get("citation")
        )
    )
    return {
        "question": question,
        "answer": answer_text,
        "follow_up_question": follow_up,
        "needs_follow_up": follow_up is not None,
        "citations": citations,
        "hits": hits,
        "section_briefs": section_briefs,
        "title_filter": title_filter,
        "used_llm": used_llm,
        "has_strong_match": strong_match,
        "disclaimer": DISCLAIMER,
    }


def answer(question: str, top_k: int = 5) -> str:
    return build_agent_response(question, top_k=top_k)["answer"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the CCR compliance agent a question.")
    parser.add_argument("question", nargs="*", help="Question to answer. If omitted, starts interactive mode.")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if args.question:
        print(answer(" ".join(args.question), top_k=args.top_k))
        return

    print("CCR Compliance Agent. Type 'exit' to quit.")
    while True:
        question = input("\nQuestion> ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        print(answer(question, top_k=args.top_k))


if __name__ == "__main__":
    main()
