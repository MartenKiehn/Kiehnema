// Erzeugt den Soundtrack (assets/audio/music.wav) komplett synthetisch – keine Samples, keine Lizenzen.
// 120 BPM, Am–F–C–G, Drop auf das Logo, Whooshes auf den Szenenwechseln, Auflösung nach C im Outro.
// Alle Zeitpunkte kommen aus plan.js, damit Bild und Ton synchron bleiben.
//   node music.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const planSrc = fs.readFileSync(path.join(ROOT, 'plan.js'), 'utf8');
const PLAN = JSON.parse(planSrc.slice(planSrc.indexOf('{'), planSrc.lastIndexOf('}') + 1));
const S = PLAN.scenes;
const HOOK = PLAN.hook; // Schublade: öffnen, zuknallen, Antwort

const SR = 48000;
const DUR = PLAN.duration;
const N = SR * DUR;
const BEAT = 60 / PLAN.bpm;
const BAR = 4 * BEAT;
// Taktraster so verschieben, dass der Drop (Logo) auf einer Eins liegt
const BAR0 = (S.claim[0] % BAR) - BAR;
const DROP = S.claim[0];
const OUTRO = S.outro[0];
const CUTS = [S.process[0], S.budget[0], S.sovereign[0], S.clients[0]];

// ---------- Grundbausteine ----------

let seed = 0x7e1c;
const rand = () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const noise = () => rand() * 2 - 1;
const mtof = (m) => 440 * 2 ** ((m - 69) / 12);
const idx = (t) => Math.round(t * SR);
const buf = () => new Float32Array(N);

// RBJ-Biquad
class Biquad {
  constructor(type, f, q = 0.707) { this.type = type; this.z1 = 0; this.z2 = 0; this.set(f, q); }
  set(f, q = this.q) {
    this.q = q;
    const w = (2 * Math.PI * Math.min(f, SR * 0.45)) / SR;
    const cos = Math.cos(w);
    const a = Math.sin(w) / (2 * q);
    let b0, b1, b2;
    if (this.type === 'lp') { b0 = (1 - cos) / 2; b1 = 1 - cos; b2 = b0; }
    else if (this.type === 'hp') { b0 = (1 + cos) / 2; b1 = -(1 + cos); b2 = b0; }
    else { b0 = a; b1 = 0; b2 = -a; } // Bandpass
    const a0 = 1 + a;
    this.b0 = b0 / a0; this.b1 = b1 / a0; this.b2 = b2 / a0;
    this.a1 = (-2 * cos) / a0; this.a2 = (1 - a) / a0;
  }
  run(x) {
    const y = this.b0 * x + this.z1;
    this.z1 = this.b1 * x - this.a1 * y + this.z2;
    this.z2 = this.b2 * x - this.a2 * y;
    return y;
  }
}

// bandbegrenzter Sägezahn (PolyBLEP)
function blep(p, dt) {
  if (p < dt) { p /= dt; return p + p - p * p - 1; }
  if (p > 1 - dt) { p = (p - 1) / dt; return p * p + p + p + 1; }
  return 0;
}
function saw(phase, dt) { return 2 * phase - 1 - blep(phase, dt); }

const add = (dst, i, v) => { if (i >= 0 && i < N) dst[i] += v; };

// ---------- Harmonie ----------

const CHORDS = {
  Am: { notes: [57, 60, 64, 67], bass: 45 },
  F: { notes: [53, 57, 60, 64], bass: 41 },
  C: { notes: [52, 55, 60, 64], bass: 48 },
  G: { notes: [55, 59, 62, 67], bass: 43 },
};
const PROG = ['Am', 'F', 'C', 'G'];
const BARS = Math.ceil((DUR - BAR0) / BAR);
const dropBar = Math.round((DROP - BAR0) / BAR);
const outroBar = Math.round((OUTRO - BAR0) / BAR);
const chordAt = (b) => {
  if (b === outroBar) return CHORDS.F;
  if (b > outroBar) return CHORDS.C; // Auflösung
  return CHORDS[PROG[(((b - dropBar) % 4) + 4) % 4]];
};
const barStart = (b) => BAR0 + b * BAR;

// ---------- Spuren ----------

const pad = [buf(), buf()];
const bass = buf();
const kick = buf();
const hats = [buf(), buf()];
const clap = buf();
const arp = [buf(), buf()];
const bells = [buf(), buf()];
const fx = [buf(), buf()];
const reverbSend = buf();
const delaySend = buf();

// Pad: je Seite drei verstimmte Sägezähne pro Akkordton
for (let b = 0; b < BARS; b++) {
  const t0 = Math.max(0, barStart(b));
  const t1 = Math.min(DUR, barStart(b + 1));
  const attack = b === 0 ? 1.4 : 0.25;
  const release = 0.7;
  chordAt(b).notes.forEach((m) => {
    [[-11, 0, 6], [-6, 0, 11]].forEach((detunes, side) => {
      detunes.forEach((cents) => {
        const f = mtof(m) * 2 ** (cents / 1200);
        const dt = f / SR;
        let ph = rand();
        const end = Math.min(N, idx(t1 + release));
        for (let i = idx(t0); i < end; i++) {
          const t = i / SR;
          let env = Math.min(1, (t - t0) / attack);
          if (t > t1) env *= Math.max(0, 1 - (t - t1) / release);
          pad[side][i] += saw(ph, dt) * env * 0.045;
          ph += dt; if (ph >= 1) ph -= 1;
        }
      });
    });
  });
}
// Filterfahrt: Intro öffnet sich, Claim hell, Outro schließt sanft
function padCutoff(t) {
  if (t < HOOK.slam) return 300 * (750 / 300) ** (t / HOOK.slam);
  if (t < HOOK.answer) return 750;
  if (t < DROP) return 900 * (2600 / 900) ** (((t - HOOK.answer) / (DROP - HOOK.answer)) ** 1.3);
  if (t < S.process[0]) return 2800;
  if (t < OUTRO) return 2100 + 300 * Math.sin(t * 0.9);
  return 2200 * (900 / 2200) ** Math.min(1, (t - OUTRO) / (DUR - OUTRO));
}
pad.forEach((ch) => {
  const f1 = new Biquad('lp', 1000, 0.8);
  const f2 = new Biquad('lp', 1000, 0.6);
  for (let i = 0; i < N; i++) {
    if (i % 32 === 0) { const c = padCutoff(i / SR); f1.set(c); f2.set(c); }
    ch[i] = f2.run(f1.run(ch[i]));
  }
});

// Bass: Offbeat-Achtel ab dem Drop, Filter-Hüllkurve pro Ton
for (let t = DROP; t < OUTRO - 1e-6; t += BEAT / 2) {
  const step = Math.round((t - BAR0) / (BEAT / 2));
  if (step % 2 === 0) continue; // nur Offbeats -> Pumpen gegen die Kick
  const b = Math.floor((t - BAR0) / BAR);
  const f = mtof(chordAt(b).bass);
  const dt = f / SR;
  const lp = new Biquad('lp', 400, 1.1);
  let ph = 0;
  let sub = 0;
  const len = 0.22;
  for (let i = idx(t), e = idx(t + len); i < e && i < N; i++) {
    const lt = i / SR - t;
    const env = Math.min(1, lt / 0.004) * Math.exp(-lt / 0.09);
    if ((i - idx(t)) % 16 === 0) lp.set(260 + 2200 * Math.exp(-lt / 0.05));
    const v = lp.run(saw(ph, dt)) * 0.8 + Math.sin(2 * Math.PI * sub) * 0.55;
    add(bass, i, v * env * 0.32);
    ph += dt; if (ph >= 1) ph -= 1;
    sub += f / 2 / SR;
  }
}

// Kick: Four-on-the-floor zwischen Drop und Outro, jeweils ein Beat Pause vor den Schnitten
const kickTimes = [];
for (let t = DROP; t < OUTRO - 1e-6; t += BEAT) {
  if (CUTS.some((c) => Math.abs(t - (c - BEAT)) < 1e-6)) continue;
  kickTimes.push(t);
}
kickTimes.forEach((t) => {
  let ph = 0;
  const hp = new Biquad('hp', 3000);
  for (let i = idx(t), e = idx(t + 0.45); i < e && i < N; i++) {
    const lt = i / SR - t;
    const f = 46 + 120 * Math.exp(-lt / 0.032);
    ph += f / SR;
    const body = Math.sin(2 * Math.PI * ph) * Math.exp(-lt / 0.26);
    const click = hp.run(noise()) * Math.exp(-lt / 0.003) * 0.5;
    add(kick, i, Math.tanh((body + click) * 1.6) * 0.42);
  }
});

// Sidechain-Kurve aus den Kicks
const duck = new Float32Array(N).fill(1);
kickTimes.forEach((t) => {
  for (let i = idx(t), e = idx(t + 0.4); i < e && i < N; i++) {
    const g = 1 - 0.45 * Math.exp(-(i / SR - t) / 0.12);
    if (g < duck[i]) duck[i] = g;
  }
});

// Hi-Hats
function hat(dst, t, decay, gain, cutoff = 7500) {
  const hp = new Biquad('hp', cutoff, 0.9);
  for (let i = idx(t), e = idx(t + decay * 6); i < e && i < N; i++) {
    add(dst, i, hp.run(noise()) * Math.exp(-(i / SR - t) / decay) * gain);
  }
}
for (let t = DROP; t < OUTRO + 2 * BAR - 1e-6; t += BEAT) {
  const fade = t < OUTRO ? 1 : 0.45 * (1 - (t - OUTRO) / (2 * BAR));
  hat(hats[1], t + BEAT / 2, 0.045, 0.16 * fade); // offener Offbeat
  if (t >= S.process[0] && t < OUTRO) {
    hat(hats[0], t + BEAT / 4, 0.012, 0.06);
    hat(hats[0], t + (3 * BEAT) / 4, 0.012, 0.075);
  }
}

// Clap auf 2 und 4 ab dem Vorgehen
for (let b = 0; b < BARS; b++) {
  [1, 3].forEach((beat) => {
    const t = barStart(b) + beat * BEAT;
    if (t < S.process[0] || t >= OUTRO) return;
    const bp = new Biquad('bp', 1400, 1.1);
    for (let i = idx(t), e = idx(t + 0.3); i < e && i < N; i++) {
      const lt = i / SR - t;
      const bursts = [0, 0.009, 0.018].reduce((s, o) => s + (lt >= o ? Math.exp(-(lt - o) / 0.0035) : 0), 0);
      const v = bp.run(noise()) * (bursts * 0.6 + Math.exp(-lt / 0.11) * 0.55);
      add(clap, i, v * 0.5);
      add(reverbSend, i, v * 0.22);
    }
  });
}

// Arpeggio: Pluck in Sechzehnteln, im Intro und Outro in Achteln
const ARP = [0, 1, 2, 3, 2, 1, 2, 3, 0, 1, 2, 3, 4, 3, 2, 1];
function pluck(dstL, dstR, t, m, gain, pan, decay = 0.16) {
  const f = mtof(m);
  const lp = new Biquad('lp', 3800, 0.7);
  let ph = 0;
  for (let i = idx(t), e = idx(t + decay * 5); i < e && i < N; i++) {
    const lt = i / SR - t;
    const env = Math.min(1, lt / 0.002) * Math.exp(-lt / decay);
    const x = Math.sin(2 * Math.PI * ph) + 0.35 * Math.sin(4 * Math.PI * ph) + 0.12 * saw(ph % 1, f / SR);
    const v = lp.run(x) * env * gain;
    add(dstL, i, v * (1 - pan));
    add(dstR, i, v * (1 + pan));
    add(delaySend, i, v * 0.5);
    ph += f / SR;
  }
}
for (let b = 0; b < BARS; b++) {
  const chord = chordAt(b);
  const tones = [...chord.notes.map((n) => n + 12), chord.notes[0] + 24];
  for (let s = 0; s < 16; s++) {
    const t = barStart(b) + s * (BEAT / 4);
    if (t < 0 || t >= DUR - 0.6) continue;
    if (t < HOOK.answer) continue; // vor der Antwort: nur das Ticken
    const sparse = t < DROP || t >= OUTRO; // Antwort/Outro: nur Achtel
    if (sparse && s % 2) continue;
    let gain = 0.11 * (s % 4 === 0 ? 1 : 0.75);
    if (t < DROP) gain *= 0.45 + 0.55 * ((t - HOOK.answer) / (DROP - HOOK.answer));
    if (t >= OUTRO) gain *= 0.8;
    pluck(arp[0], arp[1], t, tones[ARP[s]], gain, s % 2 ? 0.3 : -0.3);
  }
}

// Glockentöne, wenn sich das Logo zusammensetzt (passend zu assembleMark in main.js)
const BELL = [81, 84, 88, 91, 93];
function bell(t, m, gain) {
  const f = mtof(m);
  for (let i = idx(t), e = idx(t + 1.6); i < e && i < N; i++) {
    const lt = i / SR - t;
    const v = (Math.sin(2 * Math.PI * f * lt) * Math.exp(-lt / 0.5)
      + 0.4 * Math.sin(2 * Math.PI * f * 2.76 * lt) * Math.exp(-lt / 0.12))
      * Math.min(1, lt / 0.002) * gain;
    add(bells[0], i, v); add(bells[1], i, v);
    add(reverbSend, i, v * 0.6);
  }
}
[[DROP - 0.1, 0.07], [OUTRO + 0.3, 0.06]].forEach(([start, gain]) =>
  BELL.forEach((m, i) => bell(start + i * 0.125 + 0.4, m, gain)));
// Häkchen der Souveränität und Phasen-Punkte
bell(S.sovereign[0] + 0.6, 88, 0.05);
[0, 1, 2, 3, 4].forEach((i) => bell(S.process[0] + 0.75 + i * 0.25 + 0.15, [69, 72, 76, 79, 81][i], 0.035));

// Hook-Sounddesign: Ticken (Warten), Schublade auf, Karte rausziehen, Schublade knallt zu, Antwort
for (let t = 0.1, k = 0; t < HOOK.slam - 0.05; t += BEAT / 2, k++) {
  const f = k % 2 ? 1750 : 2350;
  for (let i = idx(t), e = idx(t + 0.05); i < e; i++) {
    const lt = i / SR - t;
    const v = Math.sin(2 * Math.PI * f * lt) * Math.exp(-lt / 0.007) * 0.07;
    add(fx[0], i, v * (k % 2 ? 0.7 : 1.3)); add(fx[1], i, v * (k % 2 ? 1.3 : 0.7));
    add(reverbSend, i, v * 0.3);
  }
}
{
  const bp = new Biquad('bp', 900, 0.8);
  const t0 = HOOK.drawerOpen;
  for (let i = idx(t0), e = idx(t0 + 0.45); i < e; i++) {
    const p = (i / SR - t0) / 0.45;
    const v = bp.run(noise()) * Math.sin(Math.PI * p) * 0.2;
    add(fx[0], i, v); add(fx[1], i, v);
  }
  const clack = new Biquad('bp', 380, 2);
  for (let i = idx(t0 + 0.42), e = idx(t0 + 0.55); i < e; i++) {
    const v = clack.run(noise()) * Math.exp(-(i / SR - t0 - 0.42) / 0.02) * 0.5;
    add(fx[0], i, v); add(fx[1], i, v);
  }
}
{
  const t0 = HOOK.slam - 0.12;
  const bp = new Biquad('bp', 600, 1.3);
  for (let i = idx(t0), e = idx(t0 + 0.35); i < e; i++) {
    const p = (i / SR - t0) / 0.35;
    if (i % 32 === 0) bp.set(600 * 5 ** p, 1.3);
    const v = bp.run(noise()) * Math.sin(Math.PI * p) ** 2 * 0.35;
    add(fx[0], i, v * (1.4 - p)); add(fx[1], i, v * (0.6 + p));
  }
  const tc = HOOK.slam + 0.12; // Front ist zu
  let ph = 0;
  const lp = new Biquad('lp', 1400);
  for (let i = idx(tc), e = idx(tc + 0.6); i < e; i++) {
    const lt = i / SR - tc;
    ph += (45 + 60 * Math.exp(-lt / 0.03)) / SR;
    const v = (Math.sin(2 * Math.PI * ph) * Math.exp(-lt / 0.16) + lp.run(noise()) * Math.exp(-lt / 0.035) * 0.8) * 0.75;
    add(fx[0], i, v); add(fx[1], i, v);
    add(reverbSend, i, v * 0.35);
  }
}
[76, 81, 88].forEach((m, i) => bell(HOOK.answer + i * 0.06, m, 0.055));
[69, 72, 74, 76, 79, 81].forEach((m, i) => bell(HOOK.answer + 0.4 + i * 0.1, m, 0.03));

// Riser vor Drop und Outro
function riser(tEnd, len, gain) {
  const t0 = tEnd - len;
  const bp = new Biquad('bp', 400, 2);
  let ph = 0;
  for (let i = idx(t0), e = idx(tEnd); i < e; i++) {
    const p = (i / SR - t0) / len;
    if (i % 32 === 0) bp.set(300 * 20 ** p, 2);
    const fade = Math.min(1, (tEnd - i / SR) / 0.01);
    ph += (180 * 4 ** p) / SR;
    const v = (bp.run(noise()) * 1.4 + Math.sin(2 * Math.PI * ph) * 0.12) * p * p * gain * fade;
    add(fx[0], i, v); add(fx[1], i, v);
    add(reverbSend, i, v * 0.4);
  }
}
riser(DROP, 1.5, 0.5);
riser(OUTRO, 1.5, 0.45);

// Whoosh auf jedem Szenenwechsel, mit Panorama-Fahrt
CUTS.forEach((c, k) => {
  const t0 = c - 0.35;
  const len = 0.65;
  const bp = new Biquad('bp', 3000, 1.4);
  for (let i = idx(t0), e = idx(t0 + len); i < e; i++) {
    const p = (i / SR - t0) / len;
    if (i % 32 === 0) bp.set(3200 * (500 / 3200) ** p, 1.4);
    const env = Math.sin(Math.PI * Math.min(1, p * 1.15)) ** 2;
    const v = bp.run(noise()) * env * 0.5;
    const pan = (k % 2 ? 1 : -1) * (0.8 - 1.6 * p);
    add(fx[0], i, v * (1 - pan)); add(fx[1], i, v * (1 + pan));
    add(reverbSend, i, v * 0.3);
  }
});

// Impacts auf Drop und Outro
function impact(t, gain) {
  let ph = 0;
  const lp = new Biquad('lp', 6000);
  const hp = new Biquad('hp', 2500);
  for (let i = idx(t), e = idx(t + 2); i < e && i < N; i++) {
    const lt = i / SR - t;
    ph += (38 + 40 * Math.exp(-lt / 0.08)) / SR;
    const sub = Math.sin(2 * Math.PI * ph) * Math.exp(-lt / 0.35);
    const crash = hp.run(lp.run(noise())) * Math.exp(-lt / 0.45) * 0.35;
    const v = (sub * 0.75 + crash) * gain;
    add(fx[0], i, v); add(fx[1], i, v);
    add(reverbSend, i, crash * gain * 0.8);
  }
}
impact(DROP, 0.7);
impact(OUTRO, 0.6);

// ---------- Effekte ----------

// Ping-Pong-Delay (punktierte Achtel)
function pingPong(input, time, feedback, wet) {
  const d = idx(time);
  const outL = buf();
  const outR = buf();
  const lpL = new Biquad('lp', 3000);
  const lpR = new Biquad('lp', 3000);
  for (let i = 0; i < N; i++) {
    const l = i >= d ? outR[i - d] * feedback + input[i - d] : 0;
    const r = i >= d ? outL[i - d] * feedback : 0;
    outL[i] = lpL.run(l);
    outR[i] = lpR.run(r);
  }
  return [outL.map((v) => v * wet), outR.map((v) => v * wet)];
}

// Freeverb (Jezar), auf 48 kHz skaliert
function freeverb(input, room = 0.84, damp = 0.3) {
  const scale = SR / 44100;
  const combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617];
  const allps = [556, 441, 341, 225];
  const channel = (spread) => {
    const cb = combs.map((l) => ({ b: new Float32Array(Math.round((l + spread) * scale)), i: 0, f: 0 }));
    const ab = allps.map((l) => ({ b: new Float32Array(Math.round((l + spread) * scale)), i: 0 }));
    const out = buf();
    for (let n = 0; n < N; n++) {
      const x = input[n] * 0.015;
      let y = 0;
      for (const c of cb) {
        const o = c.b[c.i];
        c.f = o * (1 - damp) + c.f * damp;
        c.b[c.i] = x + c.f * room;
        c.i = (c.i + 1) % c.b.length;
        y += o;
      }
      for (const a of ab) {
        const o = a.b[a.i];
        a.b[a.i] = y + o * 0.5;
        a.i = (a.i + 1) % a.b.length;
        y = o - y;
      }
      out[n] = y;
    }
    return out;
  };
  return [channel(0), channel(23)];
}

// Pads und Arp ebenfalls etwas in den Hall schicken
for (let i = 0; i < N; i++) {
  reverbSend[i] += (pad[0][i] + pad[1][i]) * 0.25 + (arp[0][i] + arp[1][i]) * 0.3;
}
const [dlyL, dlyR] = pingPong(delaySend, BEAT * 0.75, 0.38, 0.35);
const [revL, revR] = freeverb(reverbSend);

// ---------- Mix & Master ----------

const L = buf();
const R = buf();
// Pad kurz wegnehmen, wenn die Schublade zuknallt
const padGain = (t) => (t < HOOK.slam || t > HOOK.answer + 0.3 ? 1
  : t < HOOK.answer ? 0.25 + 0.75 * Math.exp(-(t - HOOK.slam) / 0.04)
  : 0.25 + 0.75 * ((t - HOOK.answer) / 0.3));
for (let i = 0; i < N; i++) {
  const d = duck[i] * padGain(i / SR);
  L[i] = pad[0][i] * d * 1.0 + bass[i] * (0.7 + 0.3 * d) + kick[i] + hats[0][i] + hats[1][i] * 0.6 + clap[i] * 0.9
    + (arp[0][i] + dlyL[i]) * (0.75 + 0.25 * d) + bells[0][i] + fx[0][i] + revL[i] * 1.6 * (0.7 + 0.3 * d);
  R[i] = pad[1][i] * d * 1.0 + bass[i] * (0.7 + 0.3 * d) + kick[i] + hats[0][i] * 0.6 + hats[1][i] + clap[i] * 0.9
    + (arp[1][i] + dlyR[i]) * (0.75 + 0.25 * d) + bells[1][i] + fx[1][i] + revR[i] * 1.6 * (0.7 + 0.3 * d);
}

const hpL = new Biquad('hp', 28);
const hpR = new Biquad('hp', 28);
let peak = 0;
for (let i = 0; i < N; i++) {
  L[i] = hpL.run(L[i]);
  R[i] = hpR.run(R[i]);
  peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
}
// sanfte Sättigung als Limiter, dann auf -1 dBFS normalisieren
const drive = 1.05 / peak;
let peak2 = 0;
let sumSq = 0;
for (let i = 0; i < N; i++) {
  const t = i / SR;
  const fade = Math.min(1, t / 0.01) * (t > DUR - 1.2 ? 0.5 + 0.5 * Math.cos((Math.PI * (t - (DUR - 1.2))) / 1.2) : 1);
  L[i] = Math.tanh(L[i] * drive) * fade;
  R[i] = Math.tanh(R[i] * drive) * fade;
  peak2 = Math.max(peak2, Math.abs(L[i]), Math.abs(R[i]));
}
const norm = 0.891 / peak2;
for (let i = 0; i < N; i++) { L[i] *= norm; R[i] *= norm; sumSq += L[i] * L[i] + R[i] * R[i]; }

// 16-Bit-WAV mit TPDF-Dither
const data = Buffer.alloc(N * 4);
for (let i = 0; i < N; i++) {
  const dl = (rand() - rand()) / 32768;
  const dr = (rand() - rand()) / 32768;
  data.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round((L[i] + dl) * 32767))), i * 4);
  data.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round((R[i] + dr) * 32767))), i * 4 + 2);
}
const header = Buffer.alloc(44);
header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8);
header.write('fmt ', 12); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(2, 22);
header.writeUInt32LE(SR, 24); header.writeUInt32LE(SR * 4, 28); header.writeUInt16LE(4, 32); header.writeUInt16LE(16, 34);
header.write('data', 36); header.writeUInt32LE(data.length, 40);
const out = path.join(ROOT, 'assets', 'audio', 'music.wav');
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, Buffer.concat([header, data]));
const rms = 10 * Math.log10(sumSq / (2 * N));
console.log(`${path.relative(ROOT, out)}: ${DUR}s, RMS ${rms.toFixed(1)} dBFS, Drop ${DROP}s, Outro ${OUTRO}s`);
