/* Atlas – KI, die im Haus bleibt (30 s). */
MG.composition(({ tl, W, H, isStory, manifest, $, $$, scene, words, fadeUp, exitUp, EXIT, stroke, cue }) => {
  const DURATION = manifest.duration || 30;
  const FALLBACK = {
    's-hook': [0, 4], 's-payoff': [4, 4], 's-infra': [8, 5], 's-data': [13, 5.5],
    's-modules': [18.5, 4.5], 's-trust': [23, 3], 's-cta': [26, 4],
  };
  const SC = manifest.scenes || {};
  const timing = (id) => {
    let v = Array.isArray(SC) ? SC.find((x) => x && x.id === id) : (SC[id] !== undefined ? SC[id] : SC[id.replace(/^s-/, '')]);
    if (Array.isArray(v)) return [Number(v[0]), Number(v[1])];
    if (v && typeof v === 'object') {
      const st = Number(v.start);
      const du = v.duration !== undefined ? Number(v.duration) : Number(v.end) - st;
      if (!isNaN(st) && !isNaN(du)) return [st, du];
    }
    return FALLBACK[id];
  };

  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs, parent) => {
    const el = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach((k) => el.setAttribute(k, attrs[k]));
    parent.appendChild(el);
    return el;
  };

  // ---------- setup: generated SVG parts ----------
  const bits = $('.bits');
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i;
    mk('rect', { class: 'bit', x: (540 + Math.cos(a) * 66 - 6).toFixed(1), y: (420 + Math.sin(a) * 66 - 6).toFixed(1), width: 12, height: 12, rx: 3 }, bits);
  }
  const probes = $('.probes');
  for (let i = 0; i < 9; i++) {
    const k = i / 8;
    mk('circle', { class: 'probe', cx: (812 - k * 246).toFixed(1), cy: (300 + k * 102).toFixed(1), r: 6 }, probes);
  }
  const starPts = Array.from({ length: 10 }, (_, i) => {
    const r = i % 2 ? 4.6 : 11;
    const a = (Math.PI / 5) * i - Math.PI / 2;
    return `${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`;
  });
  const stars = $('.eu-stars');
  for (let i = 0; i < 12; i++) {
    const a = (Math.PI / 6) * i - Math.PI / 2;
    const g = mk('g', { transform: `translate(${(800 + Math.cos(a) * 94).toFixed(2)} ${(440 + Math.sin(a) * 94).toFixed(2)})` }, stars);
    mk('path', { class: 'eu-star', d: `M${starPts.join('L')}Z` }, g);
  }

  // ---------- layout constants (positions only, timing identical) ----------
  const WORLD_TOP = isStory ? 1000 : 640;
  const INF = isStory ? { s: 1, top: 760 } : { s: 0.85, top: 450 };
  const infraY = INF.top - (WORLD_TOP + 320 + (150 - 320) * INF.s);
  const R = Math.ceil(Math.hypot(W, H)) + 100;

  // ---------- living background ----------
  tl.fromTo('.orb-1', { x: 0, y: 0 }, { x: -240, y: 280, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-2', { x: 0, y: 0 }, { x: 320, y: -240, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-3', { x: 0, y: 0, scale: 1 }, { x: -260, y: -320, scale: 1.4, duration: DURATION / 3, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 0);
  tl.fromTo('.grid', { y: 0, x: 0 }, { y: -90, x: -45, duration: DURATION, ease: 'none' }, 0);
  tl.fromTo('.paper-dots', { y: 0 }, { y: -72, duration: DURATION, ease: 'none' }, 0);

  // ---------- world: building + data core ----------
  tl.set('.world-inner', { transformOrigin: '50% 50%', y: 0, scale: 1, autoAlpha: 1 }, 0);
  tl.set('.bld', { x: -170 }, 0);
  tl.set('.wall-hit', { autoAlpha: 0 }, 0);
  const bOut = $('.b-out');
  stroke(bOut);
  const bIn = $('.b-in');
  stroke(bIn);
  const wins = $$('.b-win');
  wins.forEach((p) => stroke(p));
  tl.fromTo(bOut, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.01 }, 0);
  tl.to(bOut, { strokeDashoffset: 0, duration: 1.0, ease: 'power2.inOut' }, 0.02);
  tl.fromTo(wins, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.01, stagger: 0.07 }, 0.6);
  tl.to(wins, { strokeDashoffset: 0, duration: 0.4, ease: 'power2.out', stagger: 0.07 }, 0.6);
  tl.fromTo('.core', { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.6, ease: 'back.out(2.5)' }, 0.45);
  tl.fromTo('.core-glow', { scale: 0.8, opacity: 0.45, transformOrigin: '50% 50%' },
    { scale: 1.25, opacity: 0.12, duration: 1, ease: 'sine.inOut', yoyo: true, repeat: 11, stagger: 0.25 }, 0.6);
  tl.fromTo('.bit', { autoAlpha: 0, scale: 0, transformOrigin: '50% 50%' }, { autoAlpha: 0.85, scale: 1, duration: 0.3, stagger: 0.05 }, 0.8);
  tl.fromTo('.bits', { rotation: 0, svgOrigin: '540 420' }, { rotation: 300, svgOrigin: '540 420', duration: 13, ease: 'none' }, 0);
  tl.set('.ring', { scale: 0, autoAlpha: 0, transformOrigin: '50% 50%' }, 0);

  // 1 · Hook ------------------------------------------------------------------
  {
    const [T, D] = timing('s-hook');
    const { sel: s } = scene('s-hook', T, D);
    words(`${s} .h-l`, T + 0.15, 0.06);
    words(`${s} .sub`, T + 1.4, 0.05);
    cue(T, 'thud');

    // external AI cloud approaches and reaches for the data
    tl.fromTo('.cloud', { x: 220, autoAlpha: 0, scale: 1, transformOrigin: '50% 50%' },
      { x: 0, autoAlpha: 1, duration: 0.9, ease: 'power3.out' }, T + 0.5);
    tl.fromTo('.cloud', { y: 0 }, { y: -10, duration: 0.6, ease: 'sine.inOut', yoyo: true, repeat: 3 }, T + 1.4);
    tl.fromTo('.probe', { autoAlpha: 0, scale: 0, transformOrigin: '50% 50%' },
      { autoAlpha: 1, scale: 1, duration: 0.15, stagger: 0.065, ease: 'back.out(3)' }, T + 1.4);

    // the wall holds – hard stop, red flicker
    tl.fromTo('.wall-hit', { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.06, ease: 'none', yoyo: true, repeat: 5 }, T + 2.0);
    tl.to('.wall-hit', { autoAlpha: 1, duration: 0.1 }, T + 2.4);
    tl.to('.wall-hit', { autoAlpha: 0, duration: 0.5 }, T + 3.2);
    tl.fromTo('.cloud-alert', { opacity: 0 }, { opacity: 1, duration: 0.06, ease: 'none', yoyo: true, repeat: 5 }, T + 2.0);
    tl.to('.cloud', { x: 14, duration: 0.05, ease: 'none', yoyo: true, repeat: 5 }, T + 2.0);
    tl.to('.probe', { x: 24, y: -10, autoAlpha: 0.35, duration: 0.4, ease: 'back.out(3)', stagger: { each: 0.02, from: 'end' } }, T + 2.02);
    cue(T + 2.0, 'stop', { until: T + 2.5 });
    cue(T + 2.05, 'thud', { gain: 0.6 });
    cue(T + 2.5, 'arp');
    cue(T + 4.0, 'riser', { length: 1.5, gain: 0.5 });

    exitUp([`${s} .h-l`, `${s} .sub`], T + D - EXIT - 0.05);
    tl.to('.probe', { autoAlpha: 0, duration: 0.3 }, T + D - 0.45);
  }

  // 2 · Payoff ------------------------------------------------------------------
  {
    const [T, D] = timing('s-payoff');
    const { sel: s } = scene('s-payoff', T, D);
    // cloud dissolves, intelligence grows inside
    tl.to('.cloud', { scale: 1.45, autoAlpha: 0, duration: 0.6, ease: 'power2.out' }, T - 0.25);
    tl.to('.bld', { x: 0, duration: 1.0, ease: 'power3.inOut' }, T - 0.2);
    tl.to('.ring', { scale: 1, autoAlpha: 1, duration: 0.9, ease: 'expo.out' }, T + 0.05);
    tl.fromTo('.ring-wave', { scale: 1, opacity: 0.7, transformOrigin: '50% 50%' },
      { scale: 1.55, opacity: 0, duration: 1.0, ease: 'power1.out', repeat: 3 }, T + 0.4);
    tl.to(bIn, { strokeDashoffset: 0, duration: 0.9, ease: 'power2.inOut' }, T + 0.3);

    tl.fromTo(`${s} .brand`, { autoAlpha: 0, y: 40, scale: 0.9 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.6, ease: 'back.out(1.6)' }, T + 0.1);
    tl.fromTo(`${s} .brand-rule`, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power3.out' }, T + 0.35);
    words(`${s} .h-m`, T + 0.45, 0.06);
    cue(T, 'drop');
    cue(T, 'impact', { gain: 0.7 });
    cue(T + 0.5, 'groove');
    cue(T + 0.5, 'bells', { notes: [74, 77, 81, 84], step: 0.12, gain: 0.04 });

    exitUp([`${s} .brand`, `${s} .brand-rule`, `${s} .h-m`], T + D - EXIT - 0.05);
  }

  // 3 · Infrastructure ------------------------------------------------------------
  {
    const [T, D] = timing('s-infra');
    const { sel: s } = scene('s-infra', T, D);
    tl.to('.world-inner', { y: infraY, scale: INF.s, duration: 0.9, ease: 'power3.inOut' }, T - 0.3);
    tl.to('.bld', { x: -220, duration: 0.9, ease: 'power3.inOut' }, T - 0.3);

    const euFrame = $('.eu-frame');
    stroke(euFrame);
    const euCloud = $('.eu-cloud');
    stroke(euCloud);
    const link = $('.link');
    stroke(link);
    tl.fromTo('.eu', { autoAlpha: 0, x: 60 }, { autoAlpha: 1, x: 0, duration: 0.7, ease: 'power3.out' }, T + 0.3);
    tl.to(euFrame, { strokeDashoffset: 0, duration: 0.8, ease: 'power2.inOut' }, T + 0.3);
    tl.fromTo('.eu-star', { scale: 0, rotation: -120, transformOrigin: '50% 50%' },
      { scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)', stagger: 0.04 }, T + 0.6);
    tl.to(euCloud, { strokeDashoffset: 0, duration: 0.6, ease: 'power2.out' }, T + 0.9);
    tl.fromTo(link, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.01 }, T + 1.1);
    tl.to(link, { strokeDashoffset: 0, duration: 0.5, ease: 'power2.inOut' }, T + 1.1);
    tl.set('.packet', { autoAlpha: 0 }, 0);
    tl.set('.packet', { autoAlpha: 1 }, T + 1.6);
    tl.fromTo('.packet', { x: 0 }, { x: 160, duration: 0.8, ease: 'power1.inOut', repeat: 3, stagger: 0.4 }, T + 1.6);
    cue(T + 1.1, 'bell', { note: 81, gain: 0.04 });

    words(`${s} .h-l`, T + 0.1, 0.05);
    $$(`${s} .chip`).forEach((chip, i) => {
      const at = T + 1.5 + i * 0.5;
      tl.fromTo(chip, { autoAlpha: 0, y: 40, scale: 0.9 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.8)' }, at - 0.12);
      const no = $('.no', chip);
      stroke(no);
      tl.to(no, { strokeDashoffset: 0, duration: 0.3, ease: 'power2.out' }, at + 0.15);
      cue(at, 'tick');
    });

    exitUp([`${s} .h-l`, ...$$(`${s} .chip`)], T + D - EXIT);
    tl.to('.world-inner', { autoAlpha: 0, y: infraY - 60, duration: 0.45, ease: 'power3.in' }, T + D - 0.45);
  }

  // 4 · Data chat (light) -------------------------------------------------------
  {
    const [T, D] = timing('s-data');
    const [TM, DM] = timing('s-modules');
    const { sel: s } = scene('s-data', T, D);
    tl.set('.paper', { visibility: 'visible' }, T - 0.35);
    tl.fromTo('.paper', { clipPath: 'circle(0px at 50% 55%)' }, { clipPath: `circle(${R}px at 50% 55%)`, duration: 0.65, ease: 'power3.inOut' }, T - 0.35);
    tl.to('.paper', { clipPath: 'circle(0px at 50% 50%)', duration: 0.45, ease: 'power3.in' }, TM + DM - 0.4);
    tl.set('.paper', { visibility: 'hidden' }, TM + DM + 0.08);

    words(`${s} .h-m`, T + 0.1, 0.05);
    tl.fromTo(`${s} .bubble`, { autoAlpha: 0, y: 30, scale: 0.9, transformOrigin: '100% 100%' },
      { autoAlpha: 1, y: 0, scale: 1, duration: 0.45, ease: 'back.out(1.8)' }, T + 0.3);
    words(`${s} .bubble .split`, T + 0.5, 0.11, 0.3);
    tl.fromTo(`${s} .caret`, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.01 }, T + 0.45);
    tl.to(`${s} .caret`, { autoAlpha: 0, duration: 0.25, ease: 'steps(1)', yoyo: true, repeat: 2 }, T + 1.2);
    cue(T + 0.5, 'tick', { until: T + 1.3 });

    // thinking → SQL flickers → folds into the answer card
    tl.fromTo(`${s} .typing`, { autoAlpha: 0, y: 20 }, { autoAlpha: 1, y: 0, duration: 0.25 }, T + 1.15);
    tl.fromTo(`${s} .typing i`, { y: 0 }, { y: -10, duration: 0.14, ease: 'sine.inOut', yoyo: true, repeat: 1, stagger: 0.08 }, T + 1.25);
    tl.to(`${s} .typing`, { autoAlpha: 0, duration: 0.15 }, T + 1.55);
    tl.fromTo(`${s} .sql`, { autoAlpha: 0, scaleX: 0.6, scaleY: 1, transformOrigin: '0% 0%' },
      { autoAlpha: 1, scaleX: 1, duration: 0.25, ease: 'power3.out' }, T + 1.55);
    tl.to(`${s} .sql i`, { autoAlpha: 0.3, duration: 0.05, ease: 'none', yoyo: true, repeat: 3, stagger: 0.025 }, T + 1.7);
    tl.to(`${s} .sql`, { scaleY: 0, autoAlpha: 0, duration: 0.2, ease: 'power2.in' }, T + 1.88);
    tl.fromTo(`${s} .card`, { autoAlpha: 0, scale: 0.92, y: 30, transformOrigin: '50% 0%' },
      { autoAlpha: 1, scale: 1, y: 0, duration: 0.6, ease: 'back.out(1.4)' }, T + 2.0);
    tl.fromTo(`${s} .chart i`, { scaleY: 0, transformOrigin: '50% 100%' },
      { scaleY: 1, duration: 0.5, ease: 'back.out(1.8)', stagger: 0.07 }, T + 2.2);
    tl.to(`${s} .chart i.hi`, { scaleY: 1.05, duration: 0.5, ease: 'sine.inOut', yoyo: true, repeat: 3 }, T + 3.0);
    cue(T + 2.0, 'chime');

    words(`${s} .sub`, T + 2.4, 0.05);
    exitUp([`${s} .h-m`, `${s} .bubble`, `${s} .answer-zone`, `${s} .sub`], T + D - EXIT);
  }

  // 5 · Modules (light) ----------------------------------------------------------
  {
    const [T, D] = timing('s-modules');
    const { sel: s } = scene('s-modules', T, D);
    words(`${s} .h-m`, T + 0.1, 0.05);
    tl.fromTo(`${s} .frame`, { autoAlpha: 0, y: 40, scale: 0.97 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.55, ease: 'power3.out' }, T + 0.15);
    $$(`${s} .slot:not(.plus)`).forEach((slot, i) => {
      const at = T + 0.5 + i * 0.5;
      tl.fromTo($('.mod', slot), { autoAlpha: 0, y: -90, scale: 1.06 },
        { autoAlpha: 1, y: 0, scale: 1, duration: 0.4, ease: 'back.out(1.5)' }, at - 0.2);
      tl.to(slot, { scale: 0.97, duration: 0.08, ease: 'power1.out', yoyo: true, repeat: 1 }, at + 0.18);
      cue(at, 'slide');
    });
    const plus = `${s} .slot.plus`;
    tl.fromTo(plus, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3 }, T + 2.4);
    tl.fromTo(`${plus} svg`, { scale: 0, rotation: -90 }, { scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.4)' }, T + 2.5);
    tl.to(`${plus} svg`, { scale: 1.15, duration: 0.35, ease: 'sine.inOut', yoyo: true, repeat: 3 }, T + 3.0);
    cue(T + 2.5, 'bell', { note: 84, gain: 0.04 });

    exitUp([`${s} .h-m`, `${s} .frame`], T + D - EXIT);
  }

  // 6 · Trust --------------------------------------------------------------------
  {
    const [T, D] = timing('s-trust');
    const { sel: s } = scene('s-trust', T, D);
    tl.fromTo(`${s} .seal`, { autoAlpha: 0, scale: 0.8 }, { autoAlpha: 1, scale: 1, duration: 0.7, ease: 'power3.out' }, T);
    const ring = $(`${s} .seal-ring`);
    stroke(ring);
    const bld = $(`${s} .seal-bld`);
    stroke(bld);
    tl.to(ring, { strokeDashoffset: 0, duration: 0.9, ease: 'power2.inOut' }, T + 0.05);
    tl.to(bld, { strokeDashoffset: 0, duration: 0.8, ease: 'power2.inOut' }, T + 0.15);
    tl.fromTo(`${s} .seal-dash`, { rotation: 0, svgOrigin: '150 150' }, { rotation: 70, svgOrigin: '150 150', duration: D, ease: 'none' }, T);
    tl.fromTo(`${s} .seal-core`, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.5, ease: 'back.out(3)' }, T + 0.5);
    tl.to(`${s} .seal-core`, { scale: 1.3, duration: 0.45, ease: 'sine.inOut', yoyo: true, repeat: 3 }, T + 1.0);
    cue(T + 0.5, 'bell', { note: 84, gain: 0.05 });
    words(`${s} .h-l`, T + 0.25, 0.06);
    exitUp([`${s} .seal`, `${s} .h-l`], T + D - EXIT);
  }

  // 7 · CTA ----------------------------------------------------------------------
  {
    const [T, D] = timing('s-cta');
    const { sel: s } = scene('s-cta', T, D, { visibleUntil: DURATION + 1 });
    tl.fromTo(`${s} .cta-glow`, { scale: 0.4, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 1.0, ease: 'expo.out' }, T + 0.05);
    tl.to(`${s} .cta-glow`, { scale: 1.12, duration: 0.9, ease: 'sine.inOut', yoyo: true, repeat: 2 }, T + 1.1);
    tl.fromTo(`${s} .cta-brand`, { autoAlpha: 0, y: 40, scale: 0.9 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.6, ease: 'back.out(1.6)' }, T + 0.2);
    words(`${s} .cta-tag`, T + 0.55, 0.04);
    fadeUp(`${s} .btn`, T + 1.0, { ease: 'back.out(1.8)' });
    fadeUp(`${s} .url`, T + 1.3);
    tl.to(`${s} .btn svg`, { x: 10, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 3 }, T + 2.0);
    tl.fromTo(`${s} .btn`, { scale: 1 }, { scale: 1.04, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 1 }, T + 2.0);
    cue(T, 'riser', { length: 1.5, gain: 0.45 });
    cue(T, 'outro');
    cue(T, 'impact', { gain: 0.5 });
    cue(T + 1.0, 'chime');
  }
});