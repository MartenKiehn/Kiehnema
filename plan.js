// Szenenplan [Start, Dauer] in Sekunden – wird von main.js (Animation) und music.mjs (Musik) gelesen.
// Alle Schnitte liegen auf Beats (120 BPM = 0,5 s pro Beat). Muss reines JSON nach dem "=" bleiben.
window.PLAN = {
  "duration": 30,
  "bpm": 120,
  "scenes": {
    "hook":      [0,    5.5],
    "claim":     [5.5,  3.5],
    "process":   [9,    5.5],
    "budget":    [14.5, 4.5],
    "sovereign": [19,   3.5],
    "clients":   [22.5, 3.0],
    "outro":     [25.5, 4.5]
  },
  "hook": {
    "drawerOpen": 0.9,
    "slam": 2.0,
    "answer": 2.5
  }
};
