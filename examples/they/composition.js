/* they consulting – 30 s golden example (harness contract).
 * Ported from the hand-built main.js; serves as reference composition for the generator. */
MG.composition(({ tl, W, H, manifest, $, $$, scene, words, fadeUp, exitUp, EXIT, stroke, cue }) => {
  const S = manifest.scenes;
  const M = manifest.marks;
  const DURATION = manifest.duration;

  // ---------- setup ----------
  $$('.mark-slot').forEach((slot) => slot.appendChild($('#mark-tpl').content.cloneNode(true)));

  // EU star ring
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

  // Logo mark assembles: every shape flies in from a different direction.
  const MARK_FROM = {
    '.m-orange': { x: -420, y: -520, rotation: -120 },
    '.m-yellow': { x: 520, y: -360, rotation: 140 },
    '.m-dark': { x: -560, y: 160, rotation: -160 },
    '.m-mint': { x: -260, y: 620, rotation: 110 },
    '.m-dot': { x: 520, y: 480, rotation: 0 },
  };
  const ASSEMBLE_BELLS = [81, 84, 88, 91, 93];
  function assembleMark(root, at, spread = 1, gain = 0.07) {
    Object.entries(MARK_FROM).forEach(([sel, from], i) => {
      tl.fromTo($(sel, root),
        { x: from.x * spread, y: from.y * spread, rotation: from.rotation, scale: 0.4, autoAlpha: 0, transformOrigin: '50% 50%' },
        { x: 0, y: 0, rotation: 0, scale: 1, autoAlpha: 1, duration: 0.8, ease: 'back.out(1.3)' },
        at + i * 0.125);
    });
    cue(at + 0.4, 'bells', { notes: ASSEMBLE_BELLS, step: 0.125, gain });
  }

  // Background: slow drift over the whole duration
  tl.fromTo('.orb-1', { x: 0, y: 0 }, { x: -220, y: 260, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-2', { x: 0, y: 0 }, { x: 300, y: -220, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-3', { x: 0, y: 0, scale: 1 }, { x: -260, y: -340, scale: 1.4, duration: DURATION / 3, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 0);
  tl.fromTo('.bg .dots', { y: 0 }, { y: -72, duration: DURATION, ease: 'none' }, 0);

  // 1 · Hook: prototype heads into the drawer – gets rescued and goes live ----------
  {
    const { T, D, sel: s } = scene('s-hook', ...S.hook);
    const card = `${s} .proto-card`;

    words(`${s} .question`, T + 0.15, 0.06);
    tl.fromTo(`${s} .cabinet, ${s} .drawer-front`, { autoAlpha: 0, y: 60 }, { autoAlpha: 1, y: 0, duration: 0.6 }, T + 0.1);
    tl.fromTo(card, { autoAlpha: 0, y: -280, rotation: 14, scale: 0.62, backgroundColor: '#d6e1e4' },
      { autoAlpha: 1, y: 0, rotation: -5, scale: 0.62, duration: 0.8, ease: 'back.out(1.4)' }, T + 0.3);
    tl.set(`${s} .pc-live`, { autoAlpha: 0 }, 0);
    tl.fromTo(`${s} .pc-bars i`, { scaleY: 0 }, { scaleY: 0, duration: 0.01 }, 0);
    tl.fromTo(`${s} .strike-line`, { scaleX: 0 }, { scaleX: 0, duration: 0.01 }, 0);
    tl.fromTo(`${s} .slot`, { scaleY: 0 }, { scaleY: 0, duration: 0.01 }, 0);
    cue(0.1, 'tick', { until: M.slam - 0.05 });

    // drawer opens, prototype sinks in
    tl.to(`${s} .drawer-front`, { y: 26, scale: 1.05, duration: 0.45, ease: 'power2.out' }, M.drawerOpen);
    tl.to(`${s} .slot`, { scaleY: 1, duration: 0.45, ease: 'power2.out' }, M.drawerOpen);
    tl.to(card, { y: 150, rotation: 0, duration: 0.65, ease: 'power2.in' }, M.drawerOpen + 0.3);
    cue(M.drawerOpen, 'slide');

    // stop: card is pulled out, drawer slams shut, "Schublade?" is struck through
    tl.to(card, { y: -40, rotation: 4, duration: 0.45, ease: 'back.out(2)' }, M.slam - 0.08);
    tl.to(`${s} .drawer-front`, { y: 0, scale: 1, duration: 0.12, ease: 'power4.in' }, M.slam);
    tl.to(`${s} .slot`, { scaleY: 0, duration: 0.12, ease: 'power4.in' }, M.slam);
    tl.to(`${s} .cabinet, ${s} .drawer-front`, { y: 7, duration: 0.05, ease: 'none', yoyo: true, repeat: 3 }, M.slam + 0.12);
    tl.to(`${s} .strike-line`, { scaleX: 1, duration: 0.25, ease: 'power3.out' }, M.slam);
    cue(M.slam - 0.12, 'swoosh');
    cue(M.slam + 0.12, 'thud');
    cue(M.slam, 'stop', { until: M.answer });

    // question leaves, drawer drops away
    tl.to($$(`${s} .question .wi`), { yPercent: -118, duration: EXIT, ease: 'power3.in', stagger: 0.015 }, M.answer - 0.3);
    tl.to(`${s} .strike-line`, { autoAlpha: 0, y: -40, duration: EXIT, ease: 'power3.in' }, M.answer - 0.25);
    tl.to(`${s} .cabinet, ${s} .drawer-front`, { y: 160, autoAlpha: 0, duration: 0.45, ease: 'power2.in' }, M.answer - 0.15);

    // answer: the prototype becomes a solution in production
    words(`${s} .answer .h-xl`, M.answer, 0.06);
    words(`${s} .answer .h-m`, M.answer + 0.3, 0.04);
    tl.to(card, { y: 110, rotation: 0, scale: 1.12, backgroundColor: '#ffffff', duration: 0.7, ease: 'power3.inOut' }, M.answer - 0.05);
    tl.to(`${s} .pc-proto`, { autoAlpha: 0, duration: 0.25 }, M.answer);
    tl.to(`${s} .pc-live`, { autoAlpha: 1, duration: 0.3 }, M.answer + 0.2);
    tl.to(`${s} .pc-bars i`, { scaleY: 1, duration: 0.5, ease: 'back.out(1.8)', stagger: 0.1 }, M.answer + 0.35);
    tl.fromTo(`${s} .live-ring`, { scale: 1, autoAlpha: 1 }, { scale: 2.6, autoAlpha: 0, duration: 0.7, ease: 'power1.out', repeat: 3 }, M.answer + 0.4);
    tl.to(`${s} .pc-bars i:last-child`, { scaleY: 1.06, duration: 0.5, ease: 'sine.inOut', yoyo: true, repeat: 3 }, M.answer + 1.1);
    cue(M.answer, 'arp');
    cue(M.answer, 'chime');
    cue(M.answer + 0.4, 'bells', { notes: [69, 72, 74, 76, 79, 81], step: 0.1, gain: 0.03 });

    exitUp([`${s} .answer .h-xl`, `${s} .answer .h-m`, card], T + D - EXIT - 0.05);
  }

  // 2 · Claim on light paper: logo in original colours, "they make AI work." ----
  {
    const [T, D] = S.claim;
    const { sel: s } = scene('s-claim', T, D, { visibleFrom: T - 0.1 });
    const R = Math.ceil(Math.hypot(W, H)) + 100;
    tl.set('.paper', { visibility: 'visible' }, T - 0.3);
    tl.set('.paper', { visibility: 'hidden' }, T + D + 0.05);
    tl.fromTo('.paper', { clipPath: 'circle(0px at 50% 35%)' }, { clipPath: `circle(${R}px at 50% 35%)`, duration: 0.6, ease: 'power3.inOut' }, T - 0.3);
    assembleMark($(s), T - 0.1);
    tl.fromTo(`${s} .mark`, { y: 0 }, { y: -12, duration: 1.5, ease: 'sine.inOut', yoyo: true, repeat: 1 }, T + 0.7);
    fadeUp(`${s} .chip`, T + 0.45);
    words(`${s} .h-xl`, T + 0.5, 0.06);
    tl.fromTo(`${s} .ul-line`, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power2.inOut' }, T + 0.9);
    words(`${s} .lead`, T + 0.9, 0.035);
    cue(T, 'riser', { length: 1.5, gain: 0.5 });
    cue(T, 'drop');
    cue(T, 'impact', { gain: 0.7 });

    // exit: shapes fly apart, paper contracts
    Object.entries(MARK_FROM).forEach(([sel, to], i) => {
      tl.to(`${s} ${sel}`, { x: -to.x, y: -to.y * 0.6, rotation: -to.rotation, autoAlpha: 0, duration: 0.45, ease: 'power3.in' }, T + D - 0.45 + i * 0.02);
    });
    exitUp([`${s} .chip`, `${s} .h-xl`, `${s} .lead`], T + D - 0.45);
    tl.to('.paper', { clipPath: 'circle(0px at 50% 35%)', duration: 0.45, ease: 'power3.in' }, T + D - 0.4);
  }

  // 3 · Process: 5 phases ---------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-process', ...S.process);
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
    cue(T, 'groove');
    cue(T + 0.9, 'bells', { notes: [69, 72, 76, 79, 81], step: 0.25, gain: 0.035 });
    exitUp([`${s} .chip`, `${s} .h-l`, `${s} .badge-12`, ...$$(`${s} .step`), `${s} .tl-track`], T + D - EXIT);
  }

  // 4 · Budget guarantee ----------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-budget', ...S.budget);
    tl.fromTo(`${s} .quote-mark`, { autoAlpha: 0, y: -120, rotation: -14 }, { autoAlpha: 1, y: 0, rotation: 0, duration: 0.9, ease: 'expo.out' }, T);
    fadeUp(`${s} .chip`, T + 0.05);
    $$(`${s} .q`).forEach((q, i) => {
      tl.fromTo($$('.wi', q), { yPercent: 118 }, { yPercent: 0, duration: 0.6, ease: 'power4.out', stagger: 0.04 }, T + 0.15 + i * 0.4);
    });
    tl.fromTo(`${s} .author`, { autoAlpha: 0, x: -40 }, { autoAlpha: 1, x: 0, duration: 0.5 }, T + 1.4);
    exitUp([`${s} .quote-mark`, `${s} .chip`, ...$$(`${s} .q`), `${s} .author`], T + D - EXIT);
  }

  // 5 · Digital sovereignty -------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-sovereign', ...S.sovereign);
    tl.fromTo(`${s} .eu-ring`, { rotation: -90 }, { rotation: 30, duration: D, ease: 'power2.out' }, T);
    tl.fromTo($$(`${s} .eu-ring path`), { scale: 0, rotation: -180, transformOrigin: '50% 50%' },
      { scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)', stagger: 0.03 }, T + 0.05);
    tl.fromTo(`${s} .eu-shield`, { scale: 0 }, { scale: 1, duration: 0.7, ease: 'back.out(1.8)' }, T + 0.15);
    const tick = $(`${s} .tick`);
    stroke(tick);
    tl.to(tick, { strokeDashoffset: 0, duration: 0.4, ease: 'power2.inOut' }, T + 0.55);
    cue(T + 0.6, 'bell', { note: 88, gain: 0.05 });
    words(`${s} .h-l`, T + 0.25, 0.06);
    $$(`${s} .card`).forEach((card, i) => {
      const at = T + 0.7 + i * 0.1;
      tl.fromTo(card, { autoAlpha: 0, y: 40, scale: 0.94 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.6)' }, at);
      const p = $('path', card);
      stroke(p);
      tl.to(p, { strokeDashoffset: 0, duration: 0.35, ease: 'power2.out' }, at + 0.2);
    });
    exitUp([`${s} .eu`, `${s} .h-l`, ...$$(`${s} .card`)], T + D - EXIT);
  }

  // 6 · Clients --------------------------------------------------------------
  {
    const [T, D] = S.clients;
    const { sel: s } = scene('s-clients', T, D, { visibleUntil: T + D + 0.6 });
    fadeUp(`${s} .chip`, T);
    words(`${s} .h-l`, T + 0.05, 0.05);
    tl.fromTo(`${s} .logos div`, { autoAlpha: 0, scale: 0.6, y: 40 },
      { autoAlpha: 1, scale: 1, y: 0, duration: 0.5, ease: 'back.out(1.6)', stagger: { each: 0.05, from: 'center', grid: [3, 3] } }, T + 0.35);
    tl.to(`${s} .logos`, { scale: 1.03, duration: D, ease: 'none' }, T + 0.35);
  }

  // 7 · Outro: colour wipe in the logo colours, logo, call to action ----------
  {
    const [T, D] = S.outro;
    const { sel: s } = scene('s-outro', T, D, { visibleFrom: T + 0.25, visibleUntil: DURATION + 1 });
    $$('.wipe-layer').forEach((layer, i) => {
      tl.fromTo(layer, { scale: 0 }, { scale: 1, duration: 0.7, ease: 'power3.inOut' }, T - 0.3 + i * 0.09);
    });
    tl.set('.bg', { visibility: 'hidden' }, T + 0.6);
    assembleMark($(s), T + 0.3, 0.6, 0.06);
    tl.fromTo(`${s} .wordmark`, { clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0 0% 0 0)', duration: 0.7, ease: 'power3.inOut' }, T + 0.7);
    tl.fromTo(`${s} .wordmark img`, { x: -60 }, { x: 0, duration: 0.7, ease: 'power3.out' }, T + 0.7);
    words(`${s} .h-m`, T + 0.95, 0.06);
    fadeUp(`${s} .cta`, T + 1.2, { ease: 'back.out(1.8)' });
    fadeUp(`${s} .url`, T + 1.4);
    tl.to(`${s} .cta svg`, { x: 10, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 3 }, T + 2);
    tl.fromTo(`${s} .cta`, { scale: 1 }, { scale: 1.04, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 1 }, T + 2);
    cue(T, 'riser', { length: 1.5, gain: 0.45 });
    cue(T, 'outro');
    cue(T, 'impact', { gain: 0.6 });
  }
});
