import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowUp, LoaderCircle } from "lucide-react";

const API_BASE = "";

type HealthResponse = {
  status: string;
  collection: string;
  indexed_records: number;
};

type RetrievedSection = {
  citation: string | null;
  section_heading: string | null;
  breadcrumb_path: string | null;
  source_url: string | null;
  title_number: number | null;
  chapter: string | null;
  section_number: string | null;
  snippet: string;
  why_it_applies: string | null;
  advice: string | null;
};

type AskDetailedResponse = {
  answer: string;
  citations: string[];
  needs_follow_up: boolean;
  follow_up_question: string | null;
  retrieved_sections: number;
  used_llm: boolean;
  has_strong_match: boolean;
  disclaimer: string;
  sections: RetrievedSection[];
};

const EXAMPLES = [
  "What CCR sections apply to a restaurant in California?",
  "What regulations should a movie theater operator be aware of?",
  "What laws apply to farms or agricultural facilities?"
];

function renderAnswer(answer: string) {
  const paragraphs = answer
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter((part) => {
      if (!part) return false;
      if (/^Follow-up question:/i.test(part)) return false;
      if (/^This is not legal advice\./i.test(part)) return false;
      return true;
    });
  const intro: string[] = [];
  const numbered: string[] = [];
  const outro: string[] = [];

  for (const part of paragraphs) {
    if (/^\d+\.\s/.test(part)) {
      numbered.push(part.replace(/^\d+\.\s*/, ""));
      continue;
    }
    if (/^Compliance Advice:/i.test(part)) {
      intro.push(part.replace(/^Compliance Advice:\s*/i, ""));
      continue;
    }
    if (numbered.length === 0) {
      intro.push(part);
    } else {
      outro.push(part);
    }
  }

  return (
    <div className="answer-copy">
      {intro.map((part, index) => (
        <p key={`intro-${index}`}>{part}</p>
      ))}
      {numbered.length ? (
        <ol className="answer-list">
          {numbered.map((item, index) => (
            <li key={`item-${index}`}>{item}</li>
          ))}
        </ol>
      ) : null}
      {outro.map((part, index) => (
        <p key={`outro-${index}`}>{part}</p>
      ))}
    </div>
  );
}

function LogoMark() {
  return (
    <div className="logo-mark" aria-hidden="true">
      <svg viewBox="0 0 84 84" role="img">
        <defs>
          <linearGradient id="logoGradient" x1="0%" x2="100%" y1="0%" y2="100%">
            <stop offset="0%" stopColor="#0f766e" />
            <stop offset="100%" stopColor="#1d4ed8" />
          </linearGradient>
        </defs>
        <rect x="8" y="8" width="68" height="68" rx="22" fill="url(#logoGradient)" />
        <path d="M42 21v11" stroke="#f8fafc" strokeWidth="4.5" strokeLinecap="round" />
        <path d="M27 36h30" stroke="#f8fafc" strokeWidth="4.5" strokeLinecap="round" />
        <path d="M32 36c0 7-4 12-8 14 4 2 8 1 11-2 2-2 3-5 3-8" fill="none" stroke="#f8fafc" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M52 36c0 7 4 12 8 14-4 2-8 1-11-2-2-2-3-5-3-8" fill="none" stroke="#f8fafc" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M31 59h22" stroke="#dbeafe" strokeWidth="4.5" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState("");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<AskDetailedResponse | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Health check failed with ${res.status}`);
        }
        return (await res.json()) as HealthResponse;
      })
      .then(setHealth)
      .catch((err: Error) => setHealthError(err.message));
  }, []);

  const helperText = useMemo(() => {
    if (health) {
      if (health.status !== "ok") {
        return "The indexed CCR dataset is not available right now.";
      }
      return `${health.indexed_records.toLocaleString()} indexed records available in the current CCR dataset.`;
    }
    return healthError || "Connecting to the indexed CCR dataset...";
  }, [health, healthError]);

  async function submitQuery(nextQuestion?: string) {
    const finalQuestion = (nextQuestion ?? question).trim();
    if (!finalQuestion) {
      setError("Ask a facility-specific CCR question to continue.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/ask-detailed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: finalQuestion, top_k: 5 })
      });
      if (!res.ok) {
        throw new Error(`Agent request failed with ${res.status}`);
      }
      const data = (await res.json()) as AskDetailedResponse;
      setQuestion(finalQuestion);
      setResponse(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown frontend error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-shell">
      <main className="page-main">
        <section className={`hero-shell ${response ? "hero-shell--answered" : ""}`}>
          <div className="brand-lockup">
            <LogoMark />
            <div className="brand-copy">
              <p className="brand-kicker">California Code of Regulations</p>
              <h1>CalReg Compass</h1>
              <p className="brand-subtitle">
                Ask which CCR sections apply to a facility, then review citations, reasons, and source pages.
              </p>
            </div>
          </div>

          <div className="composer-card">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="How can CalReg Compass help today?"
              rows={response ? 4 : 3}
            />
            <div className="composer-footer">
              <span className="composer-hint">{helperText}</span>
              <button className="send-button" onClick={() => submitQuery()} disabled={loading}>
                {loading ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={18} />}
              </button>
            </div>
          </div>

          <div className="example-chips">
            {EXAMPLES.map((example) => (
              <button key={example} className="chip-button" onClick={() => submitQuery(example)}>
                {example}
              </button>
            ))}
          </div>

          {error ? <p className="error-text">{error}</p> : null}
        </section>

        {response ? (
          <section className="response-shell">
            {response.needs_follow_up && response.follow_up_question ? (
              <div className="notice-card">
                <AlertCircle size={18} />
                <div>
                  <strong>Follow-up needed</strong>
                  <p>{response.follow_up_question}</p>
                </div>
              </div>
            ) : null}

            {!response.has_strong_match ? (
              <div className="soft-warning">
                Current indexed data does not yet show a strong facility-specific match, so the sections below are the closest supported citations available right now.
              </div>
            ) : null}

            <section className="answer-card">
              <h2>Compliance Advice</h2>
              {renderAnswer(response.answer)}
            </section>

            <section className="sources-card">
              <div className="sources-header">
                <h2>Referenced CCR Sections (Context)</h2>
                <span>{response.retrieved_sections} linked</span>
              </div>
              <details className="sources-dropdown">
                <summary className="sources-dropdown-summary">Open referenced CCR sections</summary>
                <div className="sources-list">
                  {response.sections.map((section, index) => (
                    <article className="source-row" key={`${section.source_url}-${index}`}>
                      <div className="source-title-row">
                        <div>
                          <h3>{section.citation || "Unknown citation"}</h3>
                          <p>{section.section_heading || "Untitled section"}</p>
                        </div>
                        {section.source_url ? (
                          <a
                            className="source-link"
                            href={section.source_url}
                            target="_self"
                            rel="noreferrer"
                            title={section.source_url}
                          >
                            Open source
                          </a>
                        ) : (
                          <span className="source-link source-link-disabled">No source URL</span>
                        )}
                      </div>
                      <p className="source-reason">{section.why_it_applies || "No applicability note available."}</p>
                      {section.advice ? <p className="source-advice">{section.advice}</p> : null}
                    </article>
                  ))}
                </div>
              </details>
            </section>

            <p className="disclaimer-bar">{response.disclaimer}</p>
          </section>
        ) : null}
      </main>
    </div>
  );
}
