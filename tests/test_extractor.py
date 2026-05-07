from crawler.extract import extract_section


SAMPLE_HTML = """
<html>
  <head><title>Title 17 - Public Health</title></head>
  <body>
    <nav>Search Previous Next</nav>
    <main>
      <div>Title 17. Public Health</div>
      <div>Division 1. State Department of Health Services</div>
      <div>Chapter 1. Food and Drug</div>
      <h1>§ 1234. Sanitation Requirements for Food Handlers.</h1>
      <p>(a) All food handlers shall wash hands before preparing food.</p>
      <p>(b) Equipment shall be kept in sanitary condition.</p>
    </main>
  </body>
</html>
"""


def test_extract_section_matches_canonical_schema():
    section = extract_section(SAMPLE_HTML, "https://govt.westlaw.com/calregs/Document/example")

    assert section["title_number"] == 17
    assert section["title_name"] == "Public Health"
    assert section["chapter"] == "1"
    assert section["section_number"] == "1234"
    assert section["citation"] == "17 CCR § 1234"
    assert section["has_subsections"] is True
    assert "(a) All food handlers" in section["content_markdown"]
    assert "Search Previous Next" not in section["content_markdown"]
