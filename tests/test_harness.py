"""Phase 1: harness build, runtime checks (positive + negative) and the golden example."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.checks.runtime import _copy_key, check_site, required_reading_time
from app.harness.build import Manifest, Theme, build_site

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "compositions"


def test_theme_rejects_css_injection():
    with pytest.raises(ValueError):
        Theme(colors={"bg": "red; } body { display:none"})
    with pytest.raises(ValueError):
        Theme(colors={"Bad Name": "#fff"})
    with pytest.raises(ValueError):
        Theme.model_validate({"colors": {}, "fonts": [{"family": "X", "src": "../../etc/passwd"}]})
    css = Theme(colors={"petrol": "#164656"}, fonts=[{"family": "Chivo Black", "src": "assets/f.ttf", "role": "display"}]).css()
    assert "--brand-petrol: #164656;" in css and "--font-display: 'Chivo Black'" in css


def test_manifest_json_cannot_break_out_of_script_tag(tmp_path: Path):
    src = tmp_path / "c"
    src.mkdir()
    for f in ("theme.json", "composition.html", "composition.css", "composition.js"):
        (src / f).write_text((FIX / "ok" / f).read_text())
    (src / "manifest.json").write_text(json.dumps({"title": "x</script><script>alert(1)</script>", "duration": 4}))
    html = (build_site(src, tmp_path / "site") / "index.html").read_text()
    assert "</script><script>alert(1)" not in html


def test_reading_time_rule():
    assert required_reading_time(6) == pytest.approx(2.0)


def test_copy_comparison_preserves_meaning_when_layout_changes():
    assert _copy_key("nowhere") != _copy_key("now here")
    assert _copy_key("Go\n  now", "uppercase") == _copy_key("GO NOW")
    assert _copy_key("Studio.Example/Start?ID=5") == _copy_key("studio.example/Start?ID=5")
    assert _copy_key("studio.example/Start", "uppercase") != _copy_key("STUDIO.EXAMPLE/START")
    assert _copy_key("5 m", "uppercase") != _copy_key("5 M")
    assert _copy_key("10 mW", "uppercase") != _copy_key("10 MW")


async def test_ok_composition_passes(tmp_path: Path):
    report = await check_site(build_site(FIX / "ok", tmp_path / "ok"))
    assert report.ok, report.feedback()
    assert {t.text_id for t in report.texts} == {"a-title", "b-title"}


async def test_copy_matches_the_settled_text_in_each_format(tmp_path: Path):
    src = tmp_path / "source"
    shutil.copytree(FIX / "ok", src)
    (src / "composition.html").write_text("""
    <section id="s-a"><h1 data-text-id="proof"><span id="count">0</span>+ Projekte</h1></section>
    <section id="s-b"><h1 class="split" data-text-id="cta">Let’s <em>talk</em><br>today</h1>
    <p data-text-id="url">studio.example/net</p></section>
    """)
    (src / "composition.js").write_text("""
    MG.composition(({ scene, tl, isStory }) => {
      scene('s-a', 0, 2);
      scene('s-b', 2, 2);
      tl.set('#count', {textContent: isStory ? '8' : '80'}, 0.4);
      tl.call(() => document.querySelector('#s-b').appendChild(document.querySelector('[data-text-id="cta"]')), [], 2);
    });
    """)
    report = await check_site(build_site(src, tmp_path / "site"), determinism=False, expected_texts={
        "proof": "80+ Projekte", "cta": "Let's talk today", "url": "studio.example/new",
    })
    errors = {(i.format, i.text_id): i.message for i in report.errors if i.code == "text-mismatch"}
    assert set(errors) == {("feed", "url"), ("story", "url"), ("story", "proof")}
    assert "80+ Projekte" in errors["story", "proof"] and "8+ Projekte" in errors["story", "proof"]
    assert next(t.text for t in report.texts if t.format == "feed" and t.text_id == "proof") == "80+ Projekte"


async def test_late_reveal_selects_readable_frame_and_flicker_fails_hold(tmp_path: Path):
    src = tmp_path / "source"
    shutil.copytree(FIX / "ok", src)
    (src / "composition.js").write_text("""
    MG.composition(({ tl, scene, isStory }) => {
      scene('s-a', 0, 2);
      scene('s-b', 2, 2);
      tl.set('#s-a h1', {opacity: 0}, 0);
      tl.set('#s-a h1', {opacity: 1}, isStory ? 1.5 : 0.2);
      for (const t of [2.45, 3.05, 3.65]) {
        tl.set('#s-b h1', {filter: 'blur(6px)'}, t);
        tl.set('#s-b h1', {filter: 'blur(0px)'}, t + 0.2);
      }
    });
    """)
    report = await check_site(build_site(src, tmp_path / "site"), determinism=False)
    assert report.keyframe_times["feed"]["s-a"] < 1.5
    assert 1.5 <= report.keyframe_times["story"]["s-a"] < 2
    flickers = [t for t in report.texts if t.text_id == "b-title"]
    assert len(flickers) == 2 and all(0 < t.full_hold < 0.8 for t in flickers)
    assert any(i.code == "text-too-short" and i.text_id == "b-title" for i in report.errors)


async def test_bad_composition_is_caught(tmp_path: Path):
    report = await check_site(build_site(FIX / "bad", tmp_path / "bad"))
    codes = {i.code for i in report.issues}
    assert {"scene-gap", "external-request", "nondeterministic", "cue"} <= codes, report.feedback()
    assert codes & {"text-too-short", "text-never-visible"}
    assert not report.ok


async def test_build_failure_is_reported(tmp_path: Path):
    src = tmp_path / "c"
    src.mkdir()
    for f in ("manifest.json", "theme.json", "composition.html", "composition.css"):
        (src / f).write_text((FIX / "ok" / f).read_text())
    (src / "composition.js").write_text("MG.composition(({ scene }) => { scene('does-not-exist', 0, 4); });")
    report = await check_site(build_site(src, tmp_path / "site"))
    assert [i.code for i in report.errors] == ["build-failed", "build-failed"]
    assert "does-not-exist" in report.errors[0].message


async def test_corrupt_photo_returns_specific_feedback(tmp_path: Path):
    src = tmp_path / "source"
    shutil.copytree(FIX / "ok", src)
    (src / "assets").mkdir(exist_ok=True)
    (src / "assets" / "photo.jpg").write_bytes(b"not a JPEG")
    html = src / "composition.html"
    html.write_text(html.read_text().replace('<h1', '<img src="assets/photo.jpg" alt=""><h1', 1))
    report = await check_site(build_site(src, tmp_path / "site"), formats=("story",), determinism=False)
    assert any("asset failed to load:" in i.message and "assets/photo.jpg" in i.message for i in report.errors)


async def test_shared_soundtrack_catches_story_only_timing_changes(tmp_path: Path):
    src = tmp_path / "source"
    shutil.copytree(FIX / "ok", src)
    (src / "composition.js").write_text("""MG.composition(({ scene, cue, isStory }) => {
      scene('s-a', 0, 2);
      scene('s-b', 2, 2, {whoosh: !isStory});
      cue(2, 'stop', {until: isStory ? 3.5 : 3});
    });""")
    report = await check_site(build_site(src, tmp_path / "site"), determinism=False)
    differences = [i.message.split()[0] for i in report.errors if i.code == "format-divergence"]
    assert differences == ["scenes", "cues"]


async def test_golden_example_passes_all_checks(tmp_path: Path):
    report = await check_site(build_site(ROOT / "examples" / "they", tmp_path / "they"))
    assert report.ok, report.feedback()
    assert len({t.text_id for t in report.texts}) == 28
    assert Manifest.model_validate_json((ROOT / "examples/they/manifest.json").read_text()).duration == 30
