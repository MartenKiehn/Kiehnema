/* they consulting – 30-Sekunden-Motion-Graphic
 * Die komplette Animation ist eine einzige pausierte GSAP-Timeline. Der Renderer
 * (render.mjs) springt per window.__seek(t) Frame für Frame durch und macht Screenshots;
 * im Browser gibt es zusätzlich Play/Scrub-Steuerung. */

const FORMATS = { feed: [1080, 1350], story: [1080, 1920] };
const params = new URLSearchParams(location.search);
const FORMAT = FORMATS[params.get('format')] ? params.get('format') : 'feed';
const [W, H] = FORMATS[FORMAT];
const RENDER = params.has('render');
const DURATION = window.PLAN.duration;
const PLAN = window.PLAN.scenes; // [Start, Dauer] je Szene, siehe plan.js

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const stage = $('#stage');
stage.style.setProperty('--w', `${W}px`);
stage.style.setProperty('--h', `${H}px`);
stage.classList.add(`fmt-${FORMAT}`);

// ---------- Aufbau ----------

$$('.mark-slot').forEach((slot) => slot.appendChild($('#mark-tpl').content.cloneNode(true)));

// Jedes Wort in eine Maske packen, damit es von unten "hereingeschoben" werden kann.
function splitWords(el) {
  [...el.childNodes].forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const frag = document.createDocumentFragment();
      node.textContent.split(/(\s+)/).forEach((part) => {
        if (!part) return;
        if (/^\s+$/.test(part)) return frag.appendChild(document.createTextNode(' '));
        const w = document.createElement('span');
        const wi = document.createElement('span');
        w.className = 'w';
        wi.className = 'wi';
        wi.textContent = part;
        w.appendChild(wi);
        frag.appendChild(w);
      });
      node.replaceWith(frag);
    } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'BR' && !node.matches('.ul-line, .strike-line')) {
      splitWords(node);
    }
  });
}
$$('.split').forEach(splitWords);

// EU-Sternenkranz
(function buildStars() {
  const ring = $('.eu-ring');
  const star = Array.from({ length: 10 }, (_, i) => {
    const r = i % 2 ? 6 : 15;
    const a = (Math.PI / 5) * i - Math.PI / 2;
    return `${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`;
  });
  for (let i = 0; i < 12; i++) {
    const a = (Math.PI / 6) * i - Math.PI / 2;
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `translate(${(Math.cos(a) * 138).toFixed(2)} ${(Math.sin(a) * 138).toFixed(2)})`);
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', `M${star.join('L')}Z`);
    g.appendChild(p);
    ring.appendChild(g);
  }
})();

// Strich-Animationen vorbereiten
function prepStroke(path) {
  const len = path.getTotalLength();
  path.style.strokeDasharray = len;
  path.style.strokeDashoffset = len;
  return len;
}

// ---------- Timeline ----------
// Leitlinie für Lesbarkeit: Text ist nach spätestens ~1 s vollständig da und steht dann
// mindestens 2 s ruhig, bevor die Szene in 0,35 s abgeht.

gsap.defaults({ ease: 'power3.out', duration: 0.6 });
const tl = gsap.timeline({ paused: true });

const visible = (scene, start, end) => {
  tl.set(scene, { visibility: 'visible' }, start);
  tl.set(scene, { visibility: 'hidden' }, end);
};
const words = (sel, at, stagger = 0.045, duration = 0.6) =>
  tl.fromTo($$(`${sel} .wi`), { yPercent: 118 }, { yPercent: 0, duration, ease: 'power4.out', stagger }, at);
const fadeUp = (targets, at, extra = {}) =>
  tl.fromTo(targets, { autoAlpha: 0, y: 30 }, { autoAlpha: 1, y: 0, duration: 0.5, ...extra }, at);
const EXIT = 0.35;
const exitUp = (targets, at) =>
  tl.fromTo(targets, { filter: 'blur(0px)' },
    { y: '-=70', autoAlpha: 0, filter: 'blur(10px)', duration: EXIT, ease: 'power2.in', stagger: 0.02, immediateRender: false }, at);

// Bildmarke zusammensetzen: jede Form fliegt aus einer anderen Richtung ein.
const MARK_FROM = {
  '.m-orange': { x: -420, y: -520, rotation: -120 },
  '.m-yellow': { x: 520, y: -360, rotation: 140 },
  '.m-dark': { x: -560, y: 160, rotation: -160 },
  '.m-mint': { x: -260, y: 620, rotation: 110 },
  '.m-dot': { x: 520, y: 480, rotation: 0 },
};
function assembleMark(root, at, spread = 1) {
  Object.entries(MARK_FROM).forEach(([sel, from], i) => {
    tl.fromTo($(sel, root),
      { x: from.x * spread, y: from.y * spread, rotation: from.rotation, scale: 0.4, autoAlpha: 0, transformOrigin: '50% 50%' },
      { x: 0, y: 0, rotation: 0, scale: 1, autoAlpha: 1, duration: 0.8, ease: 'back.out(1.3)' },
      at + i * 0.125);
  });
}

// Hintergrund: langsames Driften über die gesamte Dauer
tl.fromTo('.orb-1', { x: 0, y: 0 }, { x: -220, y: 260, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
tl.fromTo('.orb-2', { x: 0, y: 0 }, { x: 300, y: -220, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
tl.fromTo('.orb-3', { x: 0, y: 0, scale: 1 }, { x: -260, y: -340, scale: 1.4, duration: DURATION / 3, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 0);
tl.fromTo('.bg .dots', { y: 0 }, { y: -72, duration: DURATION, ease: 'none' }, 0);

// 1 · Hook: Prototyp soll in die Schublade – wird gerettet und geht live ----------
{
  const [T, D] = PLAN.hook;
  const M = window.PLAN.hook; // Ton-Marken, siehe plan.js
  const s = '#s-hook';
  const card = `${s} .proto-card`;
  visible(s, T, T + D + 0.05);

  // Frage + Schublade mit Prototyp
  words(`${s} .question`, T + 0.15, 0.06);
  tl.fromTo(`${s} .cabinet, ${s} .drawer-front`, { autoAlpha: 0, y: 60 }, { autoAlpha: 1, y: 0, duration: 0.6 }, T + 0.1);
  tl.fromTo(card, { autoAlpha: 0, y: -280, rotation: 14, scale: 0.62, backgroundColor: '#d6e1e4' },
    { autoAlpha: 1, y: 0, rotation: -5, scale: 0.62, duration: 0.8, ease: 'back.out(1.4)' }, T + 0.3);
  tl.set(`${s} .pc-live`, { autoAlpha: 0 }, 0);
  tl.fromTo(`${s} .pc-bars i`, { scaleY: 0 }, { scaleY: 0, duration: 0.01 }, 0);
  tl.fromTo(`${s} .strike-line`, { scaleX: 0 }, { scaleX: 0, duration: 0.01 }, 0);
  tl.fromTo(`${s} .slot`, { scaleY: 0 }, { scaleY: 0, duration: 0.01 }, 0);

  // Schublade geht auf, Prototyp sinkt hinein
  tl.to(`${s} .drawer-front`, { y: 26, scale: 1.05, duration: 0.45, ease: 'power2.out' }, M.drawerOpen);
  tl.to(`${s} .slot`, { scaleY: 1, duration: 0.45, ease: 'power2.out' }, M.drawerOpen);
  tl.to(card, { y: 150, rotation: 0, duration: 0.65, ease: 'power2.in' }, M.drawerOpen + 0.3);

  // Stopp: Karte wird herausgezogen, Schublade knallt zu, "Schublade?" wird gestrichen
  tl.to(card, { y: -40, rotation: 4, duration: 0.45, ease: 'back.out(2)' }, M.slam - 0.08);
  tl.to(`${s} .drawer-front`, { y: 0, scale: 1, duration: 0.12, ease: 'power4.in' }, M.slam);
  tl.to(`${s} .slot`, { scaleY: 0, duration: 0.12, ease: 'power4.in' }, M.slam);
  tl.to(`${s} .cabinet, ${s} .drawer-front`, { y: 7, duration: 0.05, ease: 'none', yoyo: true, repeat: 3 }, M.slam + 0.12);
  tl.to(`${s} .strike-line`, { scaleX: 1, duration: 0.25, ease: 'power3.out' }, M.slam);

  // Frage geht, Schublade fällt weg
  tl.to($$(`${s} .question .wi`), { yPercent: -118, duration: EXIT, ease: 'power3.in', stagger: 0.015 }, M.answer - 0.3);
  tl.to(`${s} .strike-line`, { autoAlpha: 0, y: -40, duration: EXIT, ease: 'power3.in' }, M.answer - 0.25);
  tl.to(`${s} .cabinet, ${s} .drawer-front`, { y: 160, autoAlpha: 0, duration: 0.45, ease: 'power2.in' }, M.answer - 0.15);

  // Antwort: aus dem Prototyp wird eine Lösung im Einsatz
  words(`${s} .answer .h-xl`, M.answer, 0.06);
  words(`${s} .answer .h-m`, M.answer + 0.3, 0.04);
  tl.to(card, { y: 110, rotation: 0, scale: 1.12, backgroundColor: '#ffffff', duration: 0.7, ease: 'power3.inOut' }, M.answer - 0.05);
  tl.to(`${s} .pc-proto`, { autoAlpha: 0, duration: 0.25 }, M.answer);
  tl.to(`${s} .pc-live`, { autoAlpha: 1, duration: 0.3 }, M.answer + 0.2);
  tl.to(`${s} .pc-bars i`, { scaleY: 1, duration: 0.5, ease: 'back.out(1.8)', stagger: 0.1 }, M.answer + 0.35);
  tl.fromTo(`${s} .live-ring`, { scale: 1, autoAlpha: 1 }, { scale: 2.6, autoAlpha: 0, duration: 0.7, ease: 'power1.out', repeat: 3 }, M.answer + 0.4);
  tl.to(`${s} .pc-bars i:last-child`, { scaleY: 1.06, duration: 0.5, ease: 'sine.inOut', yoyo: true, repeat: 3 }, M.answer + 1.1);

  exitUp([`${s} .answer .h-xl`, `${s} .answer .h-m`, card], T + D - EXIT - 0.05);
}

// 2 · Claim auf hellem Grund: Logo in Originalfarben, "they make AI work." ----
{
  const [T, D] = PLAN.claim;
  const s = '#s-claim';
  const R = Math.ceil(Math.hypot(W, H)) + 100;
  tl.set('.paper', { visibility: 'visible' }, T - 0.3);
  tl.set('.paper', { visibility: 'hidden' }, T + D + 0.05);
  tl.fromTo('.paper', { clipPath: 'circle(0px at 50% 35%)' }, { clipPath: `circle(${R}px at 50% 35%)`, duration: 0.6, ease: 'power3.inOut' }, T - 0.3);
  visible(s, T - 0.1, T + D + 0.05);
  assembleMark($(s), T - 0.1);
  tl.fromTo(`${s} .mark`, { y: 0 }, { y: -12, duration: 1.5, ease: 'sine.inOut', yoyo: true, repeat: 1 }, T + 0.7);
  fadeUp(`${s} .chip`, T + 0.45);
  words(`${s} .h-xl`, T + 0.5, 0.06);
  tl.fromTo(`${s} .ul-line`, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power2.inOut' }, T + 0.9);
  words(`${s} .lead`, T + 0.9, 0.035);

  // Ausgang: Formen fliegen auseinander, heller Grund zieht sich zusammen
  Object.entries(MARK_FROM).forEach(([sel, to], i) => {
    tl.to(`${s} ${sel}`, { x: -to.x, y: -to.y * 0.6, rotation: -to.rotation, autoAlpha: 0, duration: 0.45, ease: 'power3.in' }, T + D - 0.45 + i * 0.02);
  });
  exitUp([`${s} .chip`, `${s} .h-xl`, `${s} .lead`], T + D - 0.45);
  tl.to('.paper', { clipPath: 'circle(0px at 50% 35%)', duration: 0.45, ease: 'power3.in' }, T + D - 0.4);
}

// 3 · Vorgehen: 5 Phasen --------------------------------------------------
{
  const [T, D] = PLAN.process;
  const s = '#s-process';
  visible(s, T, T + D + 0.05);
  fadeUp(`${s} .chip`, T + 0.05);
  words(`${s} .h-l`, T + 0.1, 0.05);
  tl.fromTo(`${s} .badge-12`, { scale: 0, rotation: -140 }, { scale: 1, rotation: -10, duration: 0.8, ease: 'back.out(2)' }, T + 0.5);
  tl.to(`${s} .badge-12`, { rotation: 6, duration: D - 1.8, ease: 'sine.inOut' }, T + 1.3);
  tl.fromTo(`${s} .tl-fill`, { scaleY: 0 }, { scaleY: 1, duration: 1.6, ease: 'power1.inOut' }, T + 0.7);
  $$(`${s} .step`).forEach((step, i) => {
    const at = T + 0.75 + i * 0.25;
    tl.fromTo($('.dot', step), { scale: 0 }, { scale: 1, duration: 0.5, ease: 'back.out(2.6)' }, at);
    tl.fromTo(step.lastElementChild, { autoAlpha: 0, x: -40 }, { autoAlpha: 1, x: 0, duration: 0.5 }, at + 0.05);
  });
  exitUp([`${s} .chip`, `${s} .h-l`, `${s} .badge-12`, ...$$(`${s} .step`), `${s} .tl-track`], T + D - EXIT);
}

// 4 · Budgetgarantie ------------------------------------------------------
{
  const [T, D] = PLAN.budget;
  const s = '#s-budget';
  visible(s, T, T + D + 0.05);
  tl.fromTo(`${s} .quote-mark`, { autoAlpha: 0, y: -120, rotation: -14 }, { autoAlpha: 1, y: 0, rotation: 0, duration: 0.9, ease: 'expo.out' }, T);
  fadeUp(`${s} .chip`, T + 0.05);
  $$(`${s} .q`).forEach((q, i) => {
    tl.fromTo($$('.wi', q), { yPercent: 118 }, { yPercent: 0, duration: 0.6, ease: 'power4.out', stagger: 0.04 }, T + 0.15 + i * 0.4);
  });
  tl.fromTo(`${s} .author`, { autoAlpha: 0, x: -40 }, { autoAlpha: 1, x: 0, duration: 0.5 }, T + 1.4);
  exitUp([`${s} .quote-mark`, `${s} .chip`, ...$$(`${s} .q`), `${s} .author`], T + D - EXIT);
}

// 5 · Digitale Souveränität ----------------------------------------------
{
  const [T, D] = PLAN.sovereign;
  const s = '#s-sovereign';
  visible(s, T, T + D + 0.05);
  tl.fromTo(`${s} .eu-ring`, { rotation: -90 }, { rotation: 30, duration: D, ease: 'power2.out' }, T);
  tl.fromTo($$(`${s} .eu-ring path`), { scale: 0, rotation: -180, transformOrigin: '50% 50%' },
    { scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)', stagger: 0.03 }, T + 0.05);
  tl.fromTo(`${s} .eu-shield`, { scale: 0 }, { scale: 1, duration: 0.7, ease: 'back.out(1.8)' }, T + 0.15);
  const tick = $(`${s} .tick`);
  prepStroke(tick);
  tl.to(tick, { strokeDashoffset: 0, duration: 0.4, ease: 'power2.inOut' }, T + 0.55);
  words(`${s} .h-l`, T + 0.25, 0.06);
  $$(`${s} .card`).forEach((card, i) => {
    const at = T + 0.7 + i * 0.1;
    tl.fromTo(card, { autoAlpha: 0, y: 40, scale: 0.94 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.6)' }, at);
    const p = $('path', card);
    prepStroke(p);
    tl.to(p, { strokeDashoffset: 0, duration: 0.35, ease: 'power2.out' }, at + 0.2);
  });
  exitUp([`${s} .eu`, `${s} .h-l`, ...$$(`${s} .card`)], T + D - EXIT);
}

// 6 · Kunden -------------------------------------------------------------
{
  const [T, D] = PLAN.clients;
  const s = '#s-clients';
  visible(s, T, T + D + 0.6);
  fadeUp(`${s} .chip`, T);
  words(`${s} .h-l`, T + 0.05, 0.05);
  tl.fromTo(`${s} .logos div`, { autoAlpha: 0, scale: 0.6, y: 40 },
    { autoAlpha: 1, scale: 1, y: 0, duration: 0.5, ease: 'back.out(1.6)', stagger: { each: 0.05, from: 'center', grid: [3, 3] } }, T + 0.35);
  tl.to(`${s} .logos`, { scale: 1.03, duration: D, ease: 'none' }, T + 0.35);
}

// 7 · Outro: Farb-Wipe in den Logo-Farben, Logo, Call-to-Action ------------
{
  const [T] = PLAN.outro;
  const s = '#s-outro';
  $$('.wipe-layer').forEach((layer, i) => {
    tl.fromTo(layer, { scale: 0 }, { scale: 1, duration: 0.7, ease: 'power3.inOut' }, T - 0.3 + i * 0.09);
  });
  tl.set('.bg', { visibility: 'hidden' }, T + 0.6);
  visible(s, T + 0.25, DURATION + 1);
  assembleMark($(s), T + 0.3, 0.6);
  tl.fromTo(`${s} .wordmark`, { clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0 0% 0 0)', duration: 0.7, ease: 'power3.inOut' }, T + 0.7);
  tl.fromTo(`${s} .wordmark img`, { x: -60 }, { x: 0, duration: 0.7, ease: 'power3.out' }, T + 0.7);
  words(`${s} .h-m`, T + 0.95, 0.06);
  fadeUp(`${s} .cta`, T + 1.2, { ease: 'back.out(1.8)' });
  fadeUp(`${s} .url`, T + 1.4);
  tl.to(`${s} .cta svg`, { x: 10, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 3 }, T + 2);
  tl.fromTo(`${s} .cta`, { scale: 1 }, { scale: 1.04, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 1 }, T + 2);
}

tl.set({}, {}, DURATION); // feste Gesamtlänge

// ---------- Schnittstellen ----------

window.__duration = DURATION;
window.__seek = (t) => { tl.seek(t, false); };

(async () => {
  await Promise.all([document.fonts.load('80px "Chivo Black"'), document.fonts.load('40px "Chivo"')]);
  await document.fonts.ready;
  await Promise.all($$('img').map((img) => img.decode().catch(() => {})));
  tl.seek(0);
  window.__ready = true;
})();

if (!RENDER) {
  const controls = $('#controls');
  const scrub = $('#scrub');
  const time = $('#time');
  const play = $('#play');
  controls.hidden = false;

  const fit = () => {
    const s = Math.min(innerWidth / W, (innerHeight - 56) / H);
    stage.style.transform = `translate(${(innerWidth - W * s) / 2}px, 0) scale(${s})`;
  };
  addEventListener('resize', fit);
  fit();

  let playing = false;
  let last = 0;
  const update = () => {
    scrub.value = tl.time() / DURATION;
    time.textContent = `${tl.time().toFixed(2)} s`;
  };
  const loop = (now) => {
    if (!playing) return;
    let t = tl.time() + (now - last) / 1000;
    last = now;
    if (t >= DURATION) t = 0;
    tl.seek(t);
    update();
    requestAnimationFrame(loop);
  };
  const toggle = () => {
    playing = !playing;
    play.textContent = playing ? '❚❚' : '▶︎';
    if (playing) { last = performance.now(); requestAnimationFrame(loop); }
  };
  play.onclick = toggle;
  addEventListener('keydown', (e) => { if (e.code === 'Space') { e.preventDefault(); toggle(); } });
  scrub.oninput = () => { tl.seek(scrub.value * DURATION); update(); };
}
