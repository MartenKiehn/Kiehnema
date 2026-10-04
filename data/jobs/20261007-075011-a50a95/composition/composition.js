/* HCU – Bau die Zukunft Hamburgs (30 s). */
MG.composition(({ tl, W, H, isStory, manifest, $, $$, scene, words, fadeUp, exitUp, EXIT, stroke, cue }) => {
  const DURATION = manifest.duration || 30;
  const DEF = {
    's-hook': [0, 4], 's-disciplines': [4, 5], 's-lab': [9, 5],
    's-research': [14, 5], 's-numbers': [19, 4.5], 's-brand': [23.5, 6.5],
  };
  const SC = manifest.scenes || {};
  const timing = (id) => {
    let v;
    if (Array.isArray(SC)) {
      const f = SC.find((x) => x && x.id === id);
      if (f) v = [f.start, f.duration];
    } else if (SC[id] != null) {
      const x = SC[id];
      v = Array.isArray(x) ? x : [x.start, x.duration];
    } else {
      const short = id.replace(/^s-/, '');
      const x = SC[short];
      if (x != null) v = Array.isArray(x) ? x : [x.start, x.duration];
    }
    return v && v[0] != null && v[1] != null ? [Number(v[0]), Number(v[1])] : DEF[id];
  };
  const svgNS = 'http://www.w3.org/2000/svg';
  const R = Math.ceil(Math.hypot(W, H)) + 100;

  // ---------- living background ----------
  tl.fromTo('.bg .grid', { x: 0, y: 0 }, { x: -72, y: -144, duration: DURATION, ease: 'none' }, 0);
  tl.fromTo('.g1', { x: 0, y: 0 }, { x: -260, y: 300, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.g2', { x: 0, y: 0, scale: 1 }, { x: 320, y: -260, scale: 1.25, duration: DURATION / 3, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 0);
  tl.fromTo('.paper .grid', { y: 0 }, { y: -72, duration: 7, ease: 'none' }, 3);
  tl.fromTo('.paper-end .grid', { y: 0 }, { y: -72, duration: 8, ease: 'none' }, 23);
  tl.fromTo('.stripes', { x: 0 }, { x: 136, duration: 6, ease: 'none' }, 18.5);

  // 1 · Hook ---------------------------------------------------------------
  {
    const [T, D] = timing('s-hook');
    const { sel: s } = scene('s-hook', T, D, { visibleUntil: T + D + 0.35 });
    words(`${s} .hook-head`, T + 0.15, 0.07);

    const groups = $$(`${s} .bp-g`);
    const bps = $$(`${s} .bp`);
    groups.forEach((g, i) => {
      tl.fromTo(g, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.25, ease: 'none' }, T + 0.2 + i * 0.35);
    });
    bps.forEach((p, i) => {
      stroke(p);
      tl.to(p, { strokeDashoffset: 0, duration: 0.75, ease: 'power2.inOut' }, T + 0.2 + i * 0.35);
    });
    cue(T + 0.05, 'tick', { until: T + 1.8 });

    // the roof closes over all four
    const roof = $(`${s} .roof`);
    stroke(roof);
    tl.fromTo(roof, { y: -50 }, { y: 0, duration: 0.55, ease: 'power3.out' }, T + 1.8);
    tl.to(roof, { strokeDashoffset: 0, duration: 0.5, ease: 'power3.inOut' }, T + 1.8);
    cue(T + 2.0, 'slide');

    // the plans connect into a Hamburg skyline
    tl.to(bps, { opacity: 0.3, strokeOpacity: 1, duration: 0.4, ease: 'power1.out', stagger: 0.1 }, T + 2.0);
    const sky = $$(`${s} .skyline`);
    sky.forEach((p, i) => {
      stroke(p);
      tl.to(p, { strokeDashoffset: 0, duration: i ? 0.35 : 0.45, ease: 'power2.inOut' }, T + 2.1 + i * 0.1);
    });
    tl.fromTo(`${s} .hook-art`, { scale: 0.97 }, { scale: 1.02, duration: D, ease: 'none' }, T);

    words(`${s} .hook-payoff`, T + 1.4, 0.035, 0.5);
    cue(T + 2.5, 'chime');

    exitUp([`${s} .hook-head`, `${s} .hook-art`], T + D - EXIT - 0.05);
    exitUp(`${s} .hook-payoff`, T + D - 0.1);
  }

  // 2 · Disciplines ----------------------------------------------------------
  {
    const [T, D] = timing('s-disciplines');
    const { sel: s } = scene('s-disciplines', T, D);
    tl.set('.paper', { visibility: 'visible' }, T - 0.3);
    tl.fromTo('.paper', { clipPath: 'inset(100% 0% 0% 0%)' }, { clipPath: 'inset(0% 0% 0% 0%)', duration: 0.6, ease: 'power3.inOut' }, T - 0.3);

    words(`${s} .disc-head`, T + 0.1, 0.08);
    tl.fromTo(`${s} .floors`, { clipPath: 'inset(100% 0% 0% 0%)' }, { clipPath: 'inset(0% 0% 0% 0%)', duration: 0.7, ease: 'power3.out' }, T + 0.2);
    const broof = $(`${s} .b-roof path`);
    stroke(broof);
    tl.to(broof, { strokeDashoffset: 0, duration: 0.6, ease: 'power2.inOut' }, T + 0.6);

    const STEP = [1.0, 1.5, 2.0, 2.5];
    $$(`${s} .floor`).forEach((f, i) => {
      const at = T + STEP[i];
      tl.fromTo($('.fchip', f), { autoAlpha: 0, x: -90 }, { autoAlpha: 1, x: 0, duration: 0.5, ease: 'back.out(1.6)' }, at);
      tl.fromTo($('.beam', f), { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: 'power2.out' }, at + 0.15);
      tl.fromTo($('.node', f), { scale: 0 }, { scale: 1, duration: 0.4, ease: 'back.out(3)' }, at + 0.4);
      cue(at, 'tick', { until: at + 0.12 });
    });
    tl.fromTo(`${s} .shaft-fill`, { scaleY: 0 }, { scaleY: 1, duration: 0.9, ease: 'power2.inOut' }, T + 2.75);
    tl.to(`${s} .node`, { scale: 1.45, duration: 0.22, ease: 'power2.out', yoyo: true, repeat: 1, stagger: 0.12 }, T + 3.5);
    cue(T + 3.0, 'bells', { notes: [72, 76, 79, 84], step: 0.12, gain: 0.035 });
    fadeUp(`${s} .disc-sub`, T + 3.0);

    exitUp([`${s} .disc-head`, `${s} .b-roof`, `${s} .floors`, `${s} .disc-sub`], T + D - EXIT - 0.05);
  }

  // 3 · Lab: HafenCity ----------------------------------------------------
  {
    const [T, D] = timing('s-lab');
    const { sel: s } = scene('s-lab', T, D);
    const PX = '58%';
    const PY = isStory ? '30%' : '32%';
    tl.set('.photo', { visibility: 'visible' }, T - 0.25);
    tl.fromTo('.photo', { clipPath: `circle(0px at ${PX} ${PY})` }, { clipPath: `circle(${R}px at ${PX} ${PY})`, duration: 0.65, ease: 'power3.inOut' }, T - 0.25);
    tl.set('.paper', { visibility: 'hidden' }, T + 0.5);
    tl.fromTo('.photo img', { scale: 1 }, { scale: 1.08, duration: D, ease: 'none' }, T);

    // blueprint survey field spreads from the pin
    tl.fromTo(`${s} .lab-grid`, { clipPath: `circle(0px at ${PX} ${PY})`, autoAlpha: 0.35 },
      { clipPath: `circle(${R}px at ${PX} ${PY})`, duration: 2.4, ease: 'power2.out' }, T + 0.9);
    tl.fromTo(`${s} .lab-grid`, { scale: 1.08 }, { scale: 1, duration: D, ease: 'none' }, T);
    tl.fromTo(`${s} .lab-h`, { scaleX: 0 }, { scaleX: 1, duration: 0.8, ease: 'power3.out' }, T + 0.95);
    tl.fromTo(`${s} .lab-v`, { scaleY: 0, transformOrigin: '50% 0%' }, { scaleY: 1, duration: 0.8, ease: 'power3.out' }, T + 0.95);

    tl.fromTo(`${s} .pin`, { y: -320, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.3, ease: 'power2.in' }, T + 0.7);
    tl.fromTo(`${s} .pin svg`, { scaleY: 1, scaleX: 1 }, { scaleY: 0.82, scaleX: 1.1, transformOrigin: '50% 100%', duration: 0.08, ease: 'power1.out', yoyo: true, repeat: 1 }, T + 1.0);
    tl.fromTo(`${s} .pin-ring`, { scale: 0.2, autoAlpha: 1 }, { scale: 1.9, autoAlpha: 0, duration: 1.1, ease: 'power2.out', stagger: 0.4, repeat: 2 }, T + 1.0);
    cue(T + 1.0, 'thud');

    tl.fromTo(`${s} .lab-bar`, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power3.out' }, T + 0.3);
    words(`${s} .h-l`, T + 0.4, 0.07);
    words(`${s} .lead`, T + 0.9, 0.06);

    exitUp([`${s} .lab-bar`, `${s} .h-l`, `${s} .lead`, `${s} .pin-spot`], T + D - EXIT - 0.05);
    tl.to([`${s} .lab-grid`, `${s} .lab-cross`], { autoAlpha: 0, duration: 0.3 }, T + D - 0.4);
    tl.to('.photo', { clipPath: `circle(0px at ${PX} ${PY})`, duration: 0.5, ease: 'power3.in' }, T + D - 0.35);
    tl.set('.photo', { visibility: 'hidden' }, T + D + 0.2);
  }

  // 4 · Research --------------------------------------------------------
  {
    const [T, D] = timing('s-research');
    const { sel: s } = scene('s-research', T, D);

    // data network – geometry mirrors the CSS layer sizes
    const LH = isStory ? 150 : 120;
    const GAP = isStory ? 40 : 26;
    const OFF = 36, LW = 816, SW = 888;
    const SH = 3 * LH + 2 * GAP;
    const net = $(`${s} .res-net`);
    net.setAttribute('viewBox', `0 0 ${SW} ${SH}`);
    net.setAttribute('width', SW);
    net.setAttribute('height', SH);
    const cy = (i) => i * (LH + GAP) + LH / 2;
    const nodes = [0, 1, 2].map((i) => [i * OFF + LW - 48, cy(i)]);
    const mk = (tag, attrs) => {
      const el = document.createElementNS(svgNS, tag);
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
      net.appendChild(el);
      return el;
    };
    const lines = [];
    [0, 1].forEach((g) => {
      const y = (g + 1) * LH + g * GAP + GAP / 2;
      lines.push(mk('path', { class: 'net-gap', d: `M0 ${y}H${SW}` }));
    });
    lines.push(mk('path', { class: 'net-line', d: `M${nodes[0][0]} ${nodes[0][1]}L${nodes[1][0]} ${nodes[1][1]}L${nodes[2][0]} ${nodes[2][1]}` }));
    lines.push(mk('path', { class: 'net-line', d: `M${nodes[0][0]} ${nodes[0][1]}C${SW + 20} ${nodes[0][1]} ${SW + 20} ${nodes[2][1]} ${nodes[2][0]} ${nodes[2][1]}` }));
    const dots = nodes.map(([x, y]) => mk('circle', { class: 'net-node', cx: x, cy: y, r: 11 }));

    words(`${s} .res-head`, T + 0.15, 0.08);
    tl.fromTo($$(`${s} .rl`), { autoAlpha: 0, x: -60 }, { autoAlpha: 1, x: 0, duration: 0.6, ease: 'power3.out', stagger: 0.22 }, T + 0.3);
    lines.forEach((p, i) => {
      stroke(p);
      tl.to(p, { strokeDashoffset: 0, duration: 0.8, ease: 'power2.inOut' }, T + 1.1 + i * 0.15);
    });
    tl.fromTo(dots, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.4, ease: 'back.out(3)', stagger: 0.15 }, T + 1.0);
    tl.to(dots, { scale: 1.35, duration: 0.35, ease: 'sine.inOut', yoyo: true, repeat: 5, stagger: 0.12 }, T + 1.8);
    cue(T + 0.5, 'arp');

    words(`${s} .res-sub`, T + 2.35, 0.06);
    cue(T + 2.5, 'bell', { note: 88, gain: 0.05 });

    exitUp([`${s} .r-intro`, ...$$(`${s} .rl`), net, `${s} .res-sub`], T + D - EXIT - 0.05);
  }

  // 5 · Numbers ---------------------------------------------------------
  {
    const [T, D] = timing('s-numbers');
    const { sel: s } = scene('s-numbers', T, D);
    tl.set('.blue', { visibility: 'visible' }, T - 0.3);
    tl.fromTo('.blue', { clipPath: 'inset(0% 100% 0% 0%)' }, { clipPath: 'inset(0% 0% 0% 0%)', duration: 0.6, ease: 'power3.inOut' }, T - 0.3);

    // dots build a building outline from the ground up
    const svg = $(`${s} .num-dots`);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const CELL = 40;
    const cols = Math.floor(W / CELL);
    const x0 = (W - (cols - 1) * CELL) / 2;
    const wall = isStory ? 10 : 6;
    const roofH = isStory ? 7 : 5;
    const left = 5, right = cols - 6;
    const mid = (left + right) / 2, half = (right - left) / 2;
    const ndots = [];
    for (let rb = 0; rb < wall + roofH + 1; rb++) {
      for (let c = left; c <= right; c++) {
        if (rb > wall + roofH * (1 - Math.abs(c - mid) / half)) continue;
        const el = document.createElementNS(svgNS, 'circle');
        el.setAttribute('cx', (x0 + c * CELL).toFixed(1));
        el.setAttribute('cy', (H - 70 - rb * CELL).toFixed(1));
        el.setAttribute('r', 5);
        if ((c * 7 + rb * 3) % 11 === 0) el.setAttribute('class', 'hot');
        svg.appendChild(el);
        ndots.push(el);
      }
    }
    tl.fromTo(ndots, { scale: 0, transformOrigin: '50% 50%' },
      { scale: 1, duration: 0.35, ease: 'back.out(2.5)', stagger: { each: 2.2 / Math.max(ndots.length, 1) } }, T + 0.1);

    const fmt = (n) => (n >= 1000 ? `${Math.floor(n / 1000)}.${String(n % 1000).padStart(3, '0')}` : String(n));
    const n1 = $(`${s} .n1`);
    const n2 = $(`${s} .n2`);
    const c1 = { v: 0 };
    const c2 = { v: 0 };
    fadeUp(`${s} .nb1`, T + 0.15);
    tl.fromTo(c1, { v: 0 }, { v: 2400, duration: 1.4, ease: 'power2.out', onUpdate: () => { n1.textContent = fmt(Math.round(c1.v / 10) * 10); } }, T + 0.2);
    tl.fromTo(`${s} .n1`, { scale: 0.85 }, { scale: 1, duration: 0.8, ease: 'back.out(1.6)' }, T + 0.2);
    tl.fromTo(`${s} .num-rule`, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power3.out' }, T + 1.0);
    fadeUp(`${s} .nb2`, T + 1.3);
    tl.fromTo(c2, { v: 0 }, { v: 70, duration: 1.0, ease: 'power2.out', onUpdate: () => { n2.textContent = String(Math.round(c2.v)); } }, T + 1.35);
    cue(T, 'groove');
    cue(T + 1.6, 'bells', { notes: [76, 79, 84], step: 0.14, gain: 0.03 });

    exitUp([`${s} .nb1`, `${s} .num-rule`, `${s} .nb2`], T + D - EXIT - 0.05);
    tl.to(ndots, { autoAlpha: 0, duration: 0.3, ease: 'power1.in' }, T + D - 0.4);
  }

  // 6 · Brand -----------------------------------------------------------
  {
    const [T, D] = timing('s-brand');
    const { sel: s } = scene('s-brand', T, D, { visibleUntil: DURATION + 1 });
    tl.set('.paper-end', { visibility: 'visible' }, T - 0.35);
    tl.fromTo('.paper-end', { clipPath: 'circle(0px at 50% 40%)' }, { clipPath: `circle(${R}px at 50% 40%)`, duration: 0.6, ease: 'power3.inOut' }, T - 0.35);
    tl.set('.blue', { visibility: 'hidden' }, T + 0.4);

    const broof = $(`${s} .brand-roof path`);
    stroke(broof);
    tl.to(broof, { strokeDashoffset: 0, duration: 0.8, ease: 'power3.inOut' }, T + 0.05);
    tl.fromTo(`${s} .brand-logo img`, { autoAlpha: 0, y: 30, scale: 0.92 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.7, ease: 'back.out(1.6)' }, T + 0.25);
    tl.fromTo(`${s} .brand-logo`, { scale: 1 }, { scale: 1.03, duration: D, ease: 'none' }, T);
    fadeUp(`${s} .brand-tag`, T + 0.6);
    words(`${s} .brand-head`, T + 0.85, 0.07);
    fadeUp(`${s} .cta`, T + 2.5, { ease: 'back.out(1.8)' });
    fadeUp(`${s} .url`, T + 2.8);
    tl.to(`${s} .cta svg`, { x: 10, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 3 }, T + 3.5);
    tl.fromTo(`${s} .cta`, { scale: 1 }, { scale: 1.04, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 1 }, T + 3.5);

    cue(T, 'riser', { length: 1.5, gain: 0.45 });
    cue(T, 'drop');
    cue(T, 'impact', { gain: 0.65 });
    cue(T + 2.5, 'chime');
    cue(Math.min(T + 4.5, DURATION - 0.5), 'outro');
  }
});