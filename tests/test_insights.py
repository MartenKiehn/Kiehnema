import json

from app.web.insights import job_insights
from app.web.main import templates


def save(path, name, value):
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value))


def test_discoveries_grow_from_excerpts_to_facts_colours_and_story(tmp_path):
    assert job_insights(tmp_path) == []
    excerpt = "We design, build and run robots for small factories."
    save(tmp_path, "crawl/crawl.json", {"pages": [
        {"url": "https://example.com/", "data": {"blocks": [
            {"area": "header", "md": "Cookie settings and navigation are not interesting discoveries."},
            {"area": "main", "md": excerpt}]}},
        {"url": "https://example.com/about", "data": {"blocks": [{"area": "main", "md": excerpt}]}},
    ]})
    cards = job_insights(tmp_path)
    assert cards[0]["items"] == [{"text": excerpt, "url": "https://example.com/"}]
    excerpt_id = cards[0]["id"]
    save(tmp_path, "brand/brand.json", {"colors": [{"hex": "#ffffff"}, {"hex": "#19285a"}, {"hex": "#ffffff"}]})
    cards = job_insights(tmp_path)
    assert cards[0]["id"] == excerpt_id
    assert [colour["name"] for colour in cards[1]["colours"]] == ["White", "Dark blue"]
    save(tmp_path, "profile.json", {"facts": [{"text": "Founded in 2019.", "source_url": "https://example.com/about"}]})
    save(tmp_path, "concept.json", {"rationale": "Technical directions must not appear here.", "scenes": [
        {"visual": "Use CSS and #ffffff", "texts": [{"role": "headline", "text": "Make room for better work."}]},
        {"texts": []},
    ]})
    cards = job_insights(tmp_path)
    assert len(cards) == 3 and cards[0]["id"] != excerpt_id
    assert cards[0]["items"][0]["text"] == "Founded in 2019."
    assert cards[2]["items"] == [{"label": "Scene 1", "text": "Make room for better work."}]
    assert "Technical directions" not in str(cards) and "CSS" not in str(cards)


def test_partially_written_results_do_not_break_discoveries(tmp_path):
    save(tmp_path, "profile.json", {"facts": [{"text": "A saved fact.", "source_url": "https://example.com/"}]})
    (tmp_path / "concept.json").write_text('{"scenes": [')
    assert len(job_insights(tmp_path)) == 1
    save(tmp_path, "concept.json", {"scenes": [{"texts": [{"role": "headline", "text": "A finished idea."}]}]})
    assert len(job_insights(tmp_path)) == 2


def test_website_content_is_escaped_and_colours_cannot_load_external_resources(tmp_path):
    save(tmp_path, "profile.json", {"facts": [{"text": '<script>alert("x")</script>', "source_url": "javascript:alert(1)"}]})
    save(tmp_path, "brand/brand.json", {"colors": [
        {"hex": "url(https://evil.example/image.svg)"}, {"hex": '#ffffff" onload="alert(1)'}, {"hex": "#ffffff"}]})
    html = templates.env.get_template("partials/insights.html").render(insights=job_insights(tmp_path))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html and "evil.example" not in html and "onload=" not in html
    assert 'fill="#ffffff"' in html and "White" in html
