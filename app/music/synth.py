"""Procedural soundtrack synthesiser (numpy/scipy port of the hand-built music.mjs).

Everything is driven by the composition's runtime registry:
- scenes  -> cut times (automatic whoosh + one-beat kick gap before each cut)
- cues    -> arrangement and sound design (see CUE_TYPES)
- manifest.music -> bpm, chord progression, outro progression, energy

No samples are used, so the output has no licensing constraints. Output: 48 kHz stereo 16-bit WAV.
"""

from __future__ import annotations

import logging
import math
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from app.harness.build import MusicSpec

log = logging.getLogger(__name__)

SR = 48_000
PEAK = 10 ** (-1.5 / 20)  # 0.841

CUE_TYPES = {
    # arrangement markers
    "drop": "drums + bass start here (bar one); usually together with riser + impact",
    "groove": "claps and ghost hats join from here (default: first cut after the drop)",
    "arp": "arpeggio starts here (default: two bars before the drop)",
    "outro": "drums stop, harmony resolves (outro_progression)",
    "stop": "music ducks from t until opts.until (dramatic pause)",
    # sound design
    "riser": "noise riser ending at t (opts.length s, default 1.5)",
    "impact": "sub boom + crash at t",
    "whoosh": "transition whoosh centred on t (added automatically at scene cuts)",
    "swoosh": "short upward swoosh starting at t (something is pulled/thrown)",
    "thud": "heavy thud at t (something slams)",
    "slide": "sliding noise + clack at t (a drawer/panel moves)",
    "tick": "clock ticking from t until opts.until (waiting/tension)",
    "chime": "bright three-note chime at t (positive reveal)",
    "bell": "single bell at t (opts.note MIDI, default 88)",
    "bells": "ascending bells from t (opts.notes MIDI list, opts.step s)",
}


# ---------------------------------------------------------------- plan

@dataclass
class Cue:
    t: float
    type: str
    opts: dict = field(default_factory=dict)


@dataclass
class MusicPlan:
    duration: float
    spec: MusicSpec
    cuts: list[float]
    cues: list[Cue]
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_registry(cls, duration: float, spec: MusicSpec, scenes: list[dict], cues: list[dict]) -> "MusicPlan":
        warnings: list[str] = []
        parsed: list[Cue] = []
        for raw in cues:
            kind = str(raw.get("type", ""))
            t = float(raw.get("t", -1))
            if kind not in CUE_TYPES:
                warnings.append(f"unknown cue type {kind!r} at {t:.2f}s ignored")
                continue
            if not 0 <= t <= duration:
                warnings.append(f"cue {kind!r} at {t:.2f}s outside 0..{duration}s ignored")
                continue
            parsed.append(Cue(t, kind, {k: v for k, v in raw.items() if k not in ("t", "type")}))
        anchors = [c.t for c in parsed if c.type in ("drop", "outro")]
        cuts = sorted(
            float(s["start"]) for s in scenes
            if float(s["start"]) > 0.05 and s.get("whoosh", True) and not any(abs(float(s["start"]) - a) < 0.05 for a in anchors)
        )
        return cls(duration, spec, cuts, sorted(parsed, key=lambda c: c.t), warnings)

    def first(self, kind: str) -> Cue | None:
        return next((c for c in self.cues if c.type == kind), None)

    def all(self, kind: str) -> list[Cue]:
        return [c for c in self.cues if c.type == kind]


# ---------------------------------------------------------------- dsp helpers

def _biquad(kind: str, f: float, q: float = 0.707) -> tuple[np.ndarray, np.ndarray]:
    w = 2 * math.pi * min(f, SR * 0.45) / SR
    cos, alpha = math.cos(w), math.sin(w) / (2 * q)
    if kind == "lp":
        b = [(1 - cos) / 2, 1 - cos, (1 - cos) / 2]
    elif kind == "hp":
        b = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2]
    else:  # band-pass (constant skirt gain)
        b = [alpha, 0.0, -alpha]
    a0 = 1 + alpha
    return np.array(b) / a0, np.array([1.0, -2 * cos / a0, (1 - alpha) / a0])


def _filt(kind: str, f: float, x: np.ndarray, q: float = 0.707) -> np.ndarray:
    b, a = _biquad(kind, f, q)
    return lfilter(b, a, x)


def _filt_tv(kind: str, x: np.ndarray, cutoff, q: float = 0.707, block: int = 32) -> np.ndarray:
    """Time-varying biquad: ``cutoff(i)`` gives the cutoff at sample offset i (updated per block)."""
    y = np.empty_like(x)
    zi = np.zeros(2)
    for s in range(0, len(x), block):
        b, a = _biquad(kind, cutoff(s), q)
        y[s:s + block], zi = lfilter(b, a, x[s:s + block], zi=zi)
    return y


def _saw(phase: np.ndarray, dt: float) -> np.ndarray:
    """Band-limited saw (PolyBLEP); phase in [0, 1)."""
    out = 2 * phase - 1
    lo = phase < dt
    p = phase[lo] / dt
    out[lo] -= p + p - p * p - 1
    hi = phase > 1 - dt
    p = (phase[hi] - 1) / dt
    out[hi] -= p * p + p + p + 1
    return out


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_QUALITY = {
    "": [0, 4, 7], "maj": [0, 4, 7], "m": [0, 3, 7], "min": [0, 3, 7], "7": [0, 4, 7, 10], "m7": [0, 3, 7, 10],
    "maj7": [0, 4, 7, 11], "sus2": [0, 2, 7], "sus4": [0, 5, 7], "add9": [0, 4, 7, 14], "madd9": [0, 3, 7, 14],
    "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "dim": [0, 3, 6], "aug": [0, 4, 8],
}
_CHORD = re.compile(r"^([A-G])([#b]?)(.*)$")


@dataclass(frozen=True)
class Chord:
    notes: tuple[int, ...]  # 4-voice pad voicing (MIDI)
    bass: int


def parse_chord(name: str) -> Chord:
    m = _CHORD.match(name.strip())
    if not m or m.group(3) not in _QUALITY:
        raise ValueError(f"unsupported chord {name!r}")
    root = (_NOTE[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    pcs = sorted({(root + i) % 12 for i in _QUALITY[m.group(3)]}, key=lambda pc: (pc - root) % 12)
    # close voicing upwards from the lowest chord tone >= E3 (52), doubled to four voices
    pitches = [p for p in range(52, 80) if p % 12 in pcs]
    notes = tuple(pitches[:4])
    bass = next(p for p in range(40, 52) if p % 12 == root)
    return Chord(notes, bass)


# ---------------------------------------------------------------- synth

class _Synth:
    def __init__(self, plan: MusicPlan, seed: int = 0x7E1C):
        self.p = plan
        self.rng = np.random.default_rng(seed)
        self.dur = plan.duration
        self.n = int(round(SR * plan.duration))
        self.beat = 60.0 / plan.spec.bpm
        self.bar = 4 * self.beat
        drop = plan.first("drop")
        self.drop = drop.t if drop else None
        outro = plan.first("outro")
        self.outro = outro.t if outro else self.dur
        self.bar0 = (self.drop % self.bar) - self.bar if self.drop is not None else 0.0
        groove = plan.first("groove")
        after_drop = [c for c in plan.cuts if self.drop is not None and c > self.drop]
        self.groove = groove.t if groove else (after_drop[0] if after_drop else self.drop)
        arp = plan.first("arp")
        self.arp_start = arp.t if arp else (max(0.0, self.drop - 2 * self.bar) if self.drop is not None else 0.0)
        self.stops = [(c.t, float(c.opts.get("until", c.t + self.beat))) for c in plan.all("stop")]
        self.energy = plan.spec.energy
        self.drums = self.drop is not None and self.energy != "calm"

        self.prog = [parse_chord(c) for c in plan.spec.progression]
        self.outro_prog = [parse_chord(c) for c in plan.spec.outro_progression] or self.prog[:1]
        self.bars = math.ceil((self.dur - self.bar0) / self.bar)
        self.drop_bar = round((self.drop - self.bar0) / self.bar) if self.drop is not None else 0
        self.outro_bar = round((self.outro - self.bar0) / self.bar) if outro else self.bars + 1

        z = lambda: np.zeros(self.n)  # noqa: E731
        self.pad = [z(), z()]
        self.bass = z()
        self.kick = z()
        self.hats = [z(), z()]
        self.clap = z()
        self.arp = [z(), z()]
        self.bells = [z(), z()]
        self.fx = [z(), z()]
        self.reverb_send = z()
        self.delay_send = z()
        self.kick_times: list[float] = []

    # -- helpers
    def idx(self, t: float) -> int:
        return int(round(t * SR))

    def seg(self, t: float, length: float) -> tuple[int, int, np.ndarray]:
        i0 = max(0, self.idx(t))
        i1 = min(self.n, self.idx(t + length))
        lt = np.arange(i0, i1) / SR - t
        return i0, i1, lt

    def noise(self, k: int) -> np.ndarray:
        return self.rng.uniform(-1, 1, k)

    def chord_at(self, b: int) -> Chord:
        if b >= self.outro_bar:
            return self.outro_prog[min(b - self.outro_bar, len(self.outro_prog) - 1)]
        return self.prog[(b - self.drop_bar) % len(self.prog)]

    def bar_start(self, b: int) -> float:
        return self.bar0 + b * self.bar

    def add_stereo(self, dst, i0, i1, v, left=1.0, right=1.0):
        dst[0][i0:i1] += v * left
        dst[1][i0:i1] += v * right

    # -- arrangement
    def pad_cutoff(self, t: float) -> float:
        drop, outro = self.drop, self.outro
        if drop is None:
            return 300 * (2200 / 300) ** min(1.0, t / 4) if t < 4 else 2100 + 300 * math.sin(t * 0.9)
        if t < drop:
            stop = next(((a, b) for a, b in self.stops if a < drop), None)
            if stop is None:
                return 350 * (2600 / 350) ** ((t / drop) ** 1.6)
            a, b = stop
            if t < a:
                return 300 * (750 / 300) ** (t / a)
            if t < b:
                return 750
            return 900 * (2600 / 900) ** (((t - b) / max(1e-6, drop - b)) ** 1.3)
        if t < self.groove:
            return 2800
        if t < outro:
            return 2100 + 300 * math.sin(t * 0.9)
        return 2200 * (900 / 2200) ** min(1.0, (t - outro) / max(1e-6, self.dur - outro))

    def build_pad(self):
        release = 0.7
        for b in range(self.bars):
            t0 = max(0.0, self.bar_start(b))
            t1 = min(self.dur, self.bar_start(b + 1))
            if t1 <= t0:
                continue
            attack = 1.4 if b == 0 else 0.25
            i0, i1, lt = self.seg(t0, t1 + release - t0)
            t = lt + t0
            env = np.minimum(1, lt / attack) * np.where(t > t1, np.maximum(0, 1 - (t - t1) / release), 1)
            for m in self.chord_at(b).notes:
                for side, detunes in enumerate(((-11, 0, 6), (-6, 0, 11))):
                    for cents in detunes:
                        f = mtof(m) * 2 ** (cents / 1200)
                        dt = f / SR
                        phase = (self.rng.random() + dt * np.arange(i1 - i0)) % 1.0
                        self.pad[side][i0:i1] += _saw(phase, dt) * env * 0.045
        for side in range(2):
            cut = lambda i: self.pad_cutoff(i / SR)  # noqa: E731
            self.pad[side] = _filt_tv("lp", _filt_tv("lp", self.pad[side], cut, 0.8, 64), cut, 0.6, 64)

    def build_bass(self):
        if self.drop is None:
            return
        step = self.beat / 2
        t = self.drop
        while t < self.outro - 1e-6:
            if round((t - self.bar0) / step) % 2 == 1:  # off-beats only -> pumping against the kick
                b = math.floor((t - self.bar0) / self.bar + 1e-9)
                f = mtof(self.chord_at(b).bass)
                i0, i1, lt = self.seg(t, 0.22)
                k = np.arange(i1 - i0)
                saw = _saw((f / SR * k) % 1.0, f / SR)
                lp = _filt_tv("lp", saw, lambda i: 260 + 2200 * math.exp(-(i / SR) / 0.05), 1.1, 32)
                sub = np.sin(2 * np.pi * (f / 2) / SR * k)
                env = np.minimum(1, lt / 0.004) * np.exp(-lt / 0.09)
                gain = 0.32 if self.energy != "calm" else 0.2
                self.bass[i0:i1] += (lp * 0.8 + sub * 0.55) * env * gain
            t += step

    def build_drums(self):
        if not self.drums:
            return
        t = self.drop
        while t < self.outro - 1e-6:
            if not any(abs(t - (c - self.beat)) < 1e-6 for c in self.p.cuts):
                self.kick_times.append(t)
            t += self.beat
        for tk in self.kick_times:
            i0, i1, lt = self.seg(tk, 0.45)
            f = 46 + 120 * np.exp(-lt / 0.032)
            ph = np.cumsum(f) / SR
            body = np.sin(2 * np.pi * ph) * np.exp(-lt / 0.26)
            click = _filt("hp", 3000, self.noise(len(lt))) * np.exp(-lt / 0.003) * 0.5
            self.kick[i0:i1] += np.tanh((body + click) * 1.6) * 0.42

        def hat(dst, th, decay, gain, cutoff=7500.0):
            i0, i1, lt = self.seg(th, decay * 6)
            dst[i0:i1] += _filt("hp", cutoff, self.noise(len(lt)), 0.9) * np.exp(-lt / decay) * gain

        t = self.drop
        end = self.outro + 2 * self.bar if self.outro < self.dur else self.dur
        while t < end - 1e-6:
            fade = 1.0 if t < self.outro else 0.45 * (1 - (t - self.outro) / (2 * self.bar))
            hat(self.hats[1], t + self.beat / 2, 0.045, 0.16 * fade)
            if self.groove is not None and self.groove <= t < self.outro:
                hat(self.hats[0], t + self.beat / 4, 0.012, 0.06)
                hat(self.hats[0], t + 3 * self.beat / 4, 0.012, 0.075)
            t += self.beat

        for b in range(self.bars):
            for beat in (1, 3):
                tc = self.bar_start(b) + beat * self.beat
                if self.groove is None or tc < self.groove or tc >= self.outro:
                    continue
                i0, i1, lt = self.seg(tc, 0.3)
                bursts = sum(np.where(lt >= o, np.exp(-(lt - o) / 0.0035), 0) for o in (0, 0.009, 0.018))
                v = _filt("bp", 1400, self.noise(len(lt)), 1.1) * (bursts * 0.6 + np.exp(-lt / 0.11) * 0.55)
                self.clap[i0:i1] += v * 0.5
                self.reverb_send[i0:i1] += v * 0.22

    def duck(self) -> np.ndarray:
        d = np.ones(self.n)
        for tk in self.kick_times:
            i0, i1, lt = self.seg(tk, 0.4)
            d[i0:i1] = np.minimum(d[i0:i1], 1 - 0.45 * np.exp(-lt / 0.12))
        return d

    def pad_gain(self) -> np.ndarray:
        g = np.ones(self.n)
        t = np.arange(self.n) / SR
        for a, b in self.stops:
            inside = (t >= a) & (t <= b)
            g[inside] = 0.25 + 0.75 * np.exp(-(t[inside] - a) / 0.04)
            rec = (t > b) & (t <= b + 0.3)
            g[rec] = 0.25 + 0.75 * (t[rec] - b) / 0.3
        return g

    def pluck(self, t, m, gain, pan, decay=0.16):
        f = mtof(m)
        i0, i1, lt = self.seg(t, decay * 5)
        ph = f * lt
        x = np.sin(2 * np.pi * ph) + 0.35 * np.sin(4 * np.pi * ph) + 0.12 * _saw(ph % 1.0, f / SR)
        env = np.minimum(1, lt / 0.002) * np.exp(-lt / decay)
        v = _filt("lp", 3800, x, 0.7) * env * gain
        self.add_stereo(self.arp, i0, i1, v, 1 - pan, 1 + pan)
        self.delay_send[i0:i1] += v * 0.5

    def build_arp(self):
        pattern = [0, 1, 2, 3, 2, 1, 2, 3, 0, 1, 2, 3, 4, 3, 2, 1]
        for b in range(self.bars):
            chord = self.chord_at(b)
            tones = [n + 12 for n in chord.notes] + [chord.notes[0] + 24]
            for s in range(16):
                t = self.bar_start(b) + s * self.beat / 4
                if t < 0 or t >= self.dur - 0.6 or t < self.arp_start - 1e-6:
                    continue
                sparse = self.drop is None or t < self.drop or t >= self.outro
                if sparse and s % 2:
                    continue
                gain = 0.11 * (1 if s % 4 == 0 else 0.75)
                if self.drop is not None and t < self.drop:
                    gain *= 0.45 + 0.55 * ((t - self.arp_start) / max(1e-6, self.drop - self.arp_start))
                if t >= self.outro:
                    gain *= 0.8
                self.pluck(t, tones[pattern[s]], gain, 0.3 if s % 2 else -0.3)

    def bell(self, t, m, gain):
        f = mtof(m)
        i0, i1, lt = self.seg(t, 1.6)
        v = (np.sin(2 * np.pi * f * lt) * np.exp(-lt / 0.5)
             + 0.4 * np.sin(2 * np.pi * f * 2.76 * lt) * np.exp(-lt / 0.12)) * np.minimum(1, lt / 0.002) * gain
        self.add_stereo(self.bells, i0, i1, v)
        self.reverb_send[i0:i1] += v * 0.6

    # -- sound design cues
    def cue_tick(self, c: Cue):
        until = float(c.opts.get("until", c.t + 2))
        t, k = c.t, 0
        while t < until:
            f = 1750 if k % 2 else 2350
            i0, i1, lt = self.seg(t, 0.05)
            v = np.sin(2 * np.pi * f * lt) * np.exp(-lt / 0.007) * 0.07
            self.add_stereo(self.fx, i0, i1, v, 0.7 if k % 2 else 1.3, 1.3 if k % 2 else 0.7)
            self.reverb_send[i0:i1] += v * 0.3
            t += self.beat / 2
            k += 1

    def cue_slide(self, c: Cue):
        i0, i1, lt = self.seg(c.t, 0.45)
        v = _filt("bp", 900, self.noise(len(lt)), 0.8) * np.sin(np.pi * lt / 0.45) * 0.2
        self.add_stereo(self.fx, i0, i1, v)
        i0, i1, lt = self.seg(c.t + 0.42, 0.13)
        v = _filt("bp", 380, self.noise(len(lt)), 2) * np.exp(-lt / 0.02) * 0.5
        self.add_stereo(self.fx, i0, i1, v)

    def cue_swoosh(self, c: Cue):
        length = 0.35
        i0, i1, lt = self.seg(c.t, length)
        p = lt / length
        v = _filt_tv("bp", self.noise(len(lt)), lambda i: 600 * 5 ** (i / SR / length), 1.3) * np.sin(np.pi * p) ** 2 * 0.35
        self.add_stereo(self.fx, i0, i1, v, 1.4 - p, 0.6 + p)

    def cue_thud(self, c: Cue):
        i0, i1, lt = self.seg(c.t, 0.6)
        ph = np.cumsum(45 + 60 * np.exp(-lt / 0.03)) / SR
        v = (np.sin(2 * np.pi * ph) * np.exp(-lt / 0.16) + _filt("lp", 1400, self.noise(len(lt))) * np.exp(-lt / 0.035) * 0.8) * 0.75
        self.add_stereo(self.fx, i0, i1, v)
        self.reverb_send[i0:i1] += v * 0.35

    def cue_riser(self, c: Cue):
        length = float(c.opts.get("length", 1.5))
        gain = float(c.opts.get("gain", 0.5))
        i0, i1, lt = self.seg(c.t - length, length)
        p = lt / length
        bp = _filt_tv("bp", self.noise(len(lt)), lambda i: 300 * 20 ** (i / SR / length), 2.0)
        tone = np.sin(2 * np.pi * np.cumsum(180 * 4 ** p) / SR)
        fade = np.minimum(1, (length - lt) / 0.01)
        v = (bp * 1.4 + tone * 0.12) * p * p * gain * fade
        self.add_stereo(self.fx, i0, i1, v)
        self.reverb_send[i0:i1] += v * 0.4

    def whoosh(self, center: float, k: int):
        length = 0.65
        i0, i1, lt = self.seg(center - 0.35, length)
        p = lt / length
        v = _filt_tv("bp", self.noise(len(lt)), lambda i: 3200 * (500 / 3200) ** (i / SR / length), 1.4)
        v = v * np.sin(np.pi * np.minimum(1, p * 1.15)) ** 2 * 0.5
        pan = (1 if k % 2 else -1) * (0.8 - 1.6 * p)
        self.add_stereo(self.fx, i0, i1, v, 1 - pan, 1 + pan)
        self.reverb_send[i0:i1] += v * 0.3

    def cue_impact(self, c: Cue):
        gain = float(c.opts.get("gain", 0.6))
        i0, i1, lt = self.seg(c.t, 2.0)
        ph = np.cumsum(38 + 40 * np.exp(-lt / 0.08)) / SR
        sub = np.sin(2 * np.pi * ph) * np.exp(-lt / 0.35)
        crash = _filt("hp", 2500, _filt("lp", 6000, self.noise(len(lt)))) * np.exp(-lt / 0.45) * 0.35
        self.add_stereo(self.fx, i0, i1, (sub * 0.75 + crash) * gain)
        self.reverb_send[i0:i1] += crash * gain * 0.8

    def sound_design(self):
        for c in self.p.cues:
            kind = c.type
            if kind == "tick":
                self.cue_tick(c)
            elif kind == "slide":
                self.cue_slide(c)
            elif kind == "swoosh":
                self.cue_swoosh(c)
            elif kind == "thud":
                self.cue_thud(c)
            elif kind == "riser":
                self.cue_riser(c)
            elif kind == "impact":
                self.cue_impact(c)
            elif kind == "whoosh":
                self.whoosh(c.t, 0)
            elif kind == "chime":
                for i, m in enumerate(c.opts.get("notes", [76, 81, 88])):
                    self.bell(c.t + i * 0.06, int(m), float(c.opts.get("gain", 0.055)))
            elif kind == "bell":
                self.bell(c.t, int(c.opts.get("note", 88)), float(c.opts.get("gain", 0.05)))
            elif kind == "bells":
                step = float(c.opts.get("step", 0.125))
                for i, m in enumerate(c.opts.get("notes", [81, 84, 88, 91, 93])):
                    self.bell(c.t + i * step, int(m), float(c.opts.get("gain", 0.05)))
        for k, cut in enumerate(self.p.cuts):
            self.whoosh(cut, k)

    # -- effects
    def ping_pong(self, x: np.ndarray, delay: float, feedback: float, wet: float) -> tuple[np.ndarray, np.ndarray]:
        d = self.idx(delay)
        out_l, out_r = np.zeros(self.n), np.zeros(self.n)
        b, a = _biquad("lp", 3000)
        zl, zr = np.zeros(2), np.zeros(2)
        for s in range(d, self.n, d):
            e = min(self.n, s + d)
            k = e - s
            src = slice(s - d, s - d + k)
            out_l[s:e], zl = lfilter(b, a, out_r[src] * feedback + x[src], zi=zl)
            out_r[s:e], zr = lfilter(b, a, out_l[src] * feedback, zi=zr)
        return out_l * wet, out_r * wet

    def freeverb(self, x: np.ndarray, room: float = 0.84, damp: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
        scale = SR / 44100
        x = x * 0.015

        def comb(inp: np.ndarray, length: int) -> np.ndarray:
            c = np.zeros(self.n)
            out = np.zeros(self.n)
            zi = np.zeros(1)
            for s in range(0, self.n, length):
                e = min(self.n, s + length)
                o = c[s - length:e - length] if s >= length else np.zeros(e - s)
                out[s:e] = o
                f, zi = lfilter([1 - damp], [1, -damp], o, zi=zi)
                c[s:e] = inp[s:e] + room * f
            return out

        def allpass(inp: np.ndarray, length: int) -> np.ndarray:
            bbuf = np.zeros(self.n)
            out = np.zeros(self.n)
            for s in range(0, self.n, length):
                e = min(self.n, s + length)
                o = bbuf[s - length:e - length] if s >= length else np.zeros(e - s)
                bbuf[s:e] = inp[s:e] + 0.5 * o
                out[s:e] = o - inp[s:e]
            return out

        chans = []
        for spread in (0, 23):
            y = sum(comb(x, round((l + spread) * scale)) for l in (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617))
            for l in (556, 441, 341, 225):
                y = allpass(y, round((l + spread) * scale))
            chans.append(y)
        return chans[0], chans[1]

    # -- render
    def render(self) -> np.ndarray:
        self.build_pad()
        self.build_bass()
        self.build_drums()
        self.build_arp()
        self.sound_design()
        self.reverb_send += (self.pad[0] + self.pad[1]) * 0.25 + (self.arp[0] + self.arp[1]) * 0.3
        dly_l, dly_r = self.ping_pong(self.delay_send, self.beat * 0.75, 0.38, 0.35)
        rev_l, rev_r = self.freeverb(self.reverb_send)

        d = self.duck()
        dp = d * self.pad_gain()
        common = self.bass * (0.7 + 0.3 * d) + self.kick + self.clap * 0.9
        rev_gain = 1.6 * (0.7 + 0.3 * d)
        arp_gain = 0.75 + 0.25 * d
        left = (self.pad[0] * dp + common + self.hats[0] + self.hats[1] * 0.6
                + (self.arp[0] + dly_l) * arp_gain + self.bells[0] + self.fx[0] + rev_l * rev_gain)
        right = (self.pad[1] * dp + common + self.hats[0] * 0.6 + self.hats[1]
                 + (self.arp[1] + dly_r) * arp_gain + self.bells[1] + self.fx[1] + rev_r * rev_gain)

        stereo = np.stack([_filt("hp", 28, left), _filt("hp", 28, right)], axis=1)
        drive = 1.05 / max(1e-9, np.abs(stereo).max())
        t = np.arange(self.n) / SR
        fade = np.minimum(1, t / 0.01)
        tail = t > self.dur - 1.2
        fade[tail] *= 0.5 + 0.5 * np.cos(np.pi * (t[tail] - (self.dur - 1.2)) / 1.2)
        stereo = np.tanh(stereo * drive) * fade[:, None]
        # -1.5 dBFS: AAC encoding overshoots peaks by ~0.5 dB; platforms ask for ≤ -1 dBTP
        return stereo * (PEAK / max(1e-9, np.abs(stereo).max()))


def synthesize(plan: MusicPlan, seed: int = 0x7E1C) -> np.ndarray:
    """Render the plan to float stereo samples, shape (n, 2), peak -1.5 dBFS."""
    return _Synth(plan, seed).render()


def write_wav(samples: np.ndarray, path: Path, seed: int = 0xD17E) -> None:
    rng = np.random.default_rng(seed)
    dither = (rng.random(samples.shape) - rng.random(samples.shape)) / 32768
    pcm = np.clip(np.round((samples + dither) * 32767), -32768, 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())
