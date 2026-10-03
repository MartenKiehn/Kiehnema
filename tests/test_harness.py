"""Phase 1: harness build, runtime checks (positive + negative) and the golden example."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.checks.runtime import check_site, required_reading_time
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


async def test_ok_composition_passes(tmp_path: Path):
    report = await check_site(build_site(FIX / "ok", tmp_path / "ok"))
    assert report.ok, report.feedback()
    assert {t.text_id for t in report.texts} == {"a-title", "b-title"}


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


async def test_golden_example_passes_all_checks(tmp_path: Path):
    report = await check_site(build_site(ROOT / "examples" / "they", tmp_path / "they"))
    assert report.ok, report.feedback()
    assert len({t.text_id for t in report.texts}) == 28
    assert Manifest.model_validate_json((ROOT / "examples/they/manifest.json").read_text()).duration == 30
