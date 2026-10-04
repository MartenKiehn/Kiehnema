/* Motion-graphics harness – everything that is not creative.
 *
 * A composition is three files (composition.html/.css/.js). composition.js calls
 *   MG.composition((ctx) => { ...build the paused GSAP timeline ctx.tl... })
 * The harness provides format/stage sizing, theme, deterministic randomness, word masks,
 * scene visibility, sound cues, readiness and the renderer contract:
 *   window.__ready, window.__duration, window.__seek(t), window.__mg (registry), window.__mgProbe()
 */
(() => {
  'use strict';

  const FORMATS = { feed: [1080, 1350], story: [1080, 1920] };
  const params = new URLSearchParams(location.search);
  const FORMAT = FORMATS[params.get('format')] ? params.get('format') : 'feed';
  const [W, H] = FORMATS[FORMAT];
  const RENDER = params.has('render');

  const readJson = (id) => JSON.parse(document.getElementById(id)?.textContent || '{}');
  const manifest = readJson('mg-manifest');
  const theme = readJson('mg-theme');
  const DURATION = Number(manifest.duration);

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const stage = document.getElementById('stage');
  stage.style.setProperty('--w', `${W}px`);
  stage.style.setProperty('--h', `${H}px`);
  stage.classList.add(`fmt-${FORMAT}`);
  if (RENDER) document.documentElement.classList.add('mg-render');

  // Deterministic randomness: identical frames on every render and every seek order.
  let seed = 0x7e1c5eed;
  Math.random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  // ---------- word masks ----------
  const NO_SPLIT = '.no-split, .deco, svg, img';
  function split(el) {
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
      } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'BR' && !node.matches(NO_SPLIT)) {
        split(node);
      }
    });
    return el;
  }

  // ---------- timeline + helpers ----------
  gsap.defaults({ ease: 'power3.out', duration: 0.6 });
  const tl = gsap.timeline({ paused: true });
  const EXIT = 0.35;
  const scenes = [];
  const cues = [];

  const targets = (sel) => (typeof sel === 'string' ? $$(sel) : sel);

  /** Register a scene: element #id is visible in [start, start+duration] (override with opts). */
  function scene(id, start, duration, opts = {}) {
    const el = document.getElementById(id);
    if (!el) throw new Error(`scene element #${id} not found`);
    el.classList.add('mg-scene');
    const from = opts.visibleFrom ?? start;
    const until = opts.visibleUntil ?? start + duration + 0.05;
    tl.set(el, { visibility: 'visible' }, from);
    tl.set(el, { visibility: 'hidden' }, until);
    scenes.push({ id, start, duration, whoosh: opts.whoosh !== false });
    return { T: start, D: duration, end: start + duration, el, sel: `#${id}` };
  }

  /** Word-by-word reveal of every .wi inside sel (elements must carry class "split"). */
  const words = (sel, at, stagger = 0.045, duration = 0.6) => {
    const els = typeof sel === 'string' ? $$(`${sel} .wi`) : targets(sel).flatMap((e) => $$('.wi', e));
    return tl.fromTo(els, { yPercent: 118 }, { yPercent: 0, duration, ease: 'power4.out', stagger }, at);
  };
  const fadeUp = (sel, at, extra = {}) =>
    tl.fromTo(sel, { autoAlpha: 0, y: 30 }, { autoAlpha: 1, y: 0, duration: 0.5, ...extra }, at);
  const exitUp = (sel, at) =>
    tl.fromTo(sel, { filter: 'blur(0px)' },
      { y: '-=70', autoAlpha: 0, filter: 'blur(10px)', duration: EXIT, ease: 'power2.in', stagger: 0.02, immediateRender: false }, at);

  /** Prepare an SVG path for a draw-on animation (tween strokeDashoffset to 0). */
  function stroke(path) {
    const len = path.getTotalLength();
    path.style.strokeDasharray = len;
    path.style.strokeDashoffset = len;
    return len;
  }

  /** Sound cue for the music engine, e.g. cue(5.5, 'drop'), cue(2, 'tick', { until: 3 }). */
  function cue(t, type, opts = {}) {
    cues.push({ t: Number(t), type, ...opts });
  }

  // ---------- composition entry ----------
  let build = null;
  const MG = {
    FORMAT, W, H, RENDER, manifest, theme,
    composition(fn) { build = fn; },
  };
  window.MG = MG;

  window.__duration = DURATION;
  window.__seek = (t) => { tl.seek(t, false); };
  window.__mg = { scenes, cues, manifest, format: FORMAT };

  // Visible words of an element: text nodes joined by spaces (<br> and nested blocks separate words).
  const textOf = (el) => {
    const parts = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) parts.push(walk.currentNode.textContent);
    return parts.join(' ').replace(/\s+/g, ' ').trim();
  };

  // Readability/layout probe used by the automated checks (app/checks/runtime.py).
  window.__mgProbe = () => {
    const sr = stage.getBoundingClientRect();
    return $$('[data-text-id]').map((el) => {
      const r = el.getBoundingClientRect();
      let opacity = 1;
      let hidden = false;
      let blurred = false;
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const cs = getComputedStyle(n);
        opacity *= Number(cs.opacity);
        if (cs.visibility === 'hidden' || cs.display === 'none') hidden = true;
        const m = /blur\(([\d.]+)px\)/.exec(cs.filter);
        if (m && Number(m[1]) > 1) blurred = true;
      }
      const ws = $$('.wi', el);
      let shown = 0;
      for (const wi of ws) {
        const a = wi.getBoundingClientRect();
        const b = wi.parentElement.getBoundingClientRect();
        if (a.top >= b.top - 2 && a.bottom <= b.bottom + 2) shown++;
      }
      // <br> separates words visually but not in textContent, so prefer the word masks.
      const text = ws.length
        ? ws.map((w) => w.textContent).join(' ')
        : textOf(el);
      return {
        id: el.dataset.textId,
        text,
        words: ws.length || (text ? text.split(' ').length : 0),
        opacity,
        hidden,
        blurred,
        wordsShown: ws.length ? shown / ws.length : 1,
        rect: { x: r.left - sr.left, y: r.top - sr.top, w: r.width, h: r.height },
      };
    });
  };

  async function start() {
    try {
      if (!build) throw new Error('composition.js did not call MG.composition(fn)');
      if (!(DURATION > 0)) throw new Error('manifest.duration missing');
      $$('.split').forEach(split);
      build({ tl, gsap, W, H, FORMAT, isStory: FORMAT === 'story', RENDER, manifest, theme,
        $, $$, scene, words, fadeUp, exitUp, EXIT, stroke, cue, split });
      tl.set({}, {}, DURATION); // fixed total length
      const families = (theme.fonts || []).map((f) => f.family);
      await Promise.all(families.map((fam) => document.fonts.load(`40px "${fam}"`)));
      await document.fonts.ready;
      await Promise.all($$('img').map((img) => img.decode().catch(() => {})));
      tl.seek(0);
      window.__ready = true;
      if (!RENDER) preview();
    } catch (err) {
      window.__mgFailed = String(err && err.stack ? err.stack : err);
      console.error(window.__mgFailed);
    }
  }

  // ---------- preview controls (browser only) ----------
  function preview() {
    const controls = document.getElementById('mg-controls');
    if (!controls) return;
    controls.hidden = false;
    const scrub = $('#mg-scrub');
    const time = $('#mg-time');
    const play = $('#mg-play');
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
      play.setAttribute('aria-label', playing ? 'Pause preview' : 'Play preview');
      if (playing) { last = performance.now(); requestAnimationFrame(loop); }
    };
    play.onclick = toggle;
    addEventListener('keydown', (e) => { if (e.code === 'Space') { e.preventDefault(); toggle(); } });
    scrub.oninput = () => { tl.seek(scrub.value * DURATION); update(); };
  }

  document.addEventListener('DOMContentLoaded', start);
})();
