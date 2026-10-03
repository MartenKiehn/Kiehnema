"""Phase 1: music synthesiser driven by the composition registry."""

from __future__ import annotations

import numpy as np
import pytest

from app.harness.build import MusicSpec
from app.music.synth import SR, MusicPlan, parse_chord, synthesize, write_wav


@pytest.mark.parametrize(("name", "bass", "pcs"), [
    ("Am", 45, {9, 0, 4}), ("F", 41, {5, 9, 0}), ("C", 48, {0, 4, 7}), ("G", 43, {7, 11, 2}), ("Cmaj7", 48, {0, 4, 7, 11}),
    ("F#m", 42, {6, 9, 1}), ("Bbsus2", 46, {10, 0, 5}),
])
def test_parse_chord(name, bass, pcs):
    chord = parse_chord(name)
    assert chord.bass == bass
    assert {n % 12 for n in chord.notes} == pcs
    assert len(chord.notes) == 4 and all(52 <= n < 80 for n in chord.notes)


def test_parse_chord_rejects_garbage():
    with pytest.raises(ValueError):
        parse_chord("H7#11")


SCENES = [{"start": 0, "duration": 2, "whoosh": True}, {"start": 2, "duration": 2, "whoosh": True},
          {"start": 4, "duration": 2, "whoosh": True}, {"start": 6, "duration": 2, "whoosh": False}]


def test_plan_cuts_skip_anchors_and_disabled_whooshes():
    cues = [{"t": 2, "type": "drop"}, {"t": 3, "type": "laser"}, {"t": 99, "type": "impact"}]
    plan = MusicPlan.from_registry(8, MusicSpec(), SCENES, cues)
    assert plan.cuts == [4.0]  # 2.0 is the drop, 6.0 has whoosh disabled
    assert [c.type for c in plan.cues] == ["drop"]
    assert len(plan.warnings) == 2


def _energy(x: np.ndarray, t0: float, t1: float) -> float:
    seg = x[int(t0 * SR):int(t1 * SR)]
    return float(np.sqrt((seg ** 2).mean()))


def test_synth_structure_and_determinism(tmp_path):
    cues = [{"t": 2, "type": "riser"}, {"t": 2, "type": "drop"}, {"t": 2, "type": "impact"}, {"t": 6, "type": "outro"}]
    plan = MusicPlan.from_registry(8, MusicSpec(bpm=120), SCENES, cues)
    a = synthesize(plan)
    b = synthesize(plan)
    assert a.shape == (8 * SR, 2)
    assert np.array_equal(a, b)
    assert not np.isnan(a).any()
    assert np.abs(a).max() == pytest.approx(10 ** (-1.5 / 20), abs=1e-3)
    assert _energy(a, 2.1, 3.4) > 2 * _energy(a, 0.2, 1.5)  # drums + bass after the drop
    write_wav(a, tmp_path / "m.wav")
    assert (tmp_path / "m.wav").stat().st_size == 8 * SR * 4 + 44


def test_calm_energy_has_no_kick():
    cues = [{"t": 2, "type": "drop"}]
    calm = synthesize(MusicPlan.from_registry(6, MusicSpec(energy="calm"), SCENES[:3], cues))
    medium = synthesize(MusicPlan.from_registry(6, MusicSpec(energy="medium"), SCENES[:3], cues))
    low = lambda x: np.abs(np.fft.rfft(x[int(2.5 * SR):int(3.5 * SR)].mean(1)))[:120].sum()  # < 60 Hz  # noqa: E731
    assert low(medium) > 2 * low(calm)
