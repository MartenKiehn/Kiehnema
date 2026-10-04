/* they make AI work – nichts für die Schublade (30 s) */
MG.composition((ctx) => {
  const { tl, W, H, manifest, $, $$, scene, words, fadeUp, exitUp, EXIT, stroke, cue } = ctx;
  const isStory = ctx.isStory;
  const DURATION = manifest.duration || 30;
  const RR = Math.ceil(Math.hypot(W, H)) + 100;

  const DEFAULTS = {
    's-hook': [0, 4], 's-brand': [4, 3.5], 's-sprint': [7.5, 4.5], 's-process': [12, 5],
    's-proof': [17, 4.5], 's-trust': [21.5, 3.5], 's-cta': [25, 5],
  };
  function timing(id) {
    const S = manifest.scenes;
    let v;
    if (Array.isArray(S)) v = S.find((x) => x && x.id === id);
    else if (S) v = S[id] || S[id.replace(/^s-/, '')];
    if (Array.isArray(v)) return [Number(v[0]), Number(v[1])];
    if (v && typeof v === 'object' && v.start != null) return [Number(v.start), Number(v.duration)];
    return DEFAULTS[id];
  }

  // ---------- living background ----------
  tl.fromTo('.orb-1', { x: 0, y: 0 }, { x: -220, y: 260, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-2', { x: 0, y: 0 }, { x: 300, y: -220, duration: DURATION / 2, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 0);
  tl.fromTo('.orb-3', { x: 0, y: 0, scale: 1 }, { x: -260, y: -340, scale: 1.4, duration: DURATION / 3, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 0);
  tl.fromTo('.bg .dots', { y: 0 }, { y: -72, duration: DURATION, ease: 'none' }, 0);
  tl.fromTo('.paper .dots', { y: 0 }, { y: -72, duration: DURATION, ease: 'none' }, 0);
  tl.fromTo('.cta-bg .dots', { y: 0 }, { y: -40, duration: DURATION, ease: 'none' }, 0);

  // position where the dashboard collapses into a dot (used by the brand reveal)
  let dotX = W / 2;
  let dotY = H * 0.45;

  // 1 · Hook ---------------------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-hook', ...timing('s-hook'));
    const card = $(`${s} .proto-card`);
    const dash = $(`${s} .dash`);
    const front = $(`${s} .drawer-front`);
    const slot = $(`${s} .slot`);
    const ds = $(`${s} .drawer-scene`);
    dotY = ds.offsetTop - 30 + 32;

    words(`${s} .hook-headline`, T + 0.1, 0.06);
    tl.fromTo(ds, { autoAlpha: 0, y: 70 }, { autoAlpha: 1, y: 0, duration: 0.55, ease: 'power3.out' }, T + 0.02);

    // initial states
    tl.fromTo(slot, { scaleY: 0 }, { scaleY: 0, duration: 0.01 }, 0);
    tl.fromTo(dash, { autoAlpha: 0, y: 360, scale: 0.55, transformOrigin: '50% 0%' }, { autoAlpha: 0, duration: 0.01 }, 0);
    tl.fromTo(`${s} .dash-cover`, { autoAlpha: 0 }, { autoAlpha: 0, duration: 0.01 }, 0);
    tl.fromTo($$(`${s} .dash-bars i`), { scaleY: 0.04 }, { scaleY: 0.04, duration: 0.01 }, 0);
    const spark = $(`${s} .spark-line`);
    stroke(spark);

    // prototype card with PILOT stamp
    tl.fromTo(card, { autoAlpha: 0, y: -70, rotation: -8, transformOrigin: '50% 0%' },
      { autoAlpha: 1, y: 0, rotation: 2, duration: 0.45, ease: 'back.out(1.6)' }, T + 0.12);
    tl.fromTo(`${s} .stamp`, { autoAlpha: 0, scale: 2.2, rotation: -40 },
      { autoAlpha: 1, scale: 1, rotation: -12, duration: 0.2, ease: 'power4.in' }, T + 0.28);

    // drawer opens, card slides in
    tl.to(front, { y: 60, scale: 1.05, duration: 0.3, ease: 'power2.out' }, T + 0.3);
    tl.to(slot, { scaleY: 1, duration: 0.3, ease: 'power2.out' }, T + 0.3);
    tl.to(card, { y: 295, scale: 0.75, rotation: 0, duration: 0.45, ease: 'power2.in' }, T + 0.52);
    cue(T + 0.5, 'slide');

    // slam
    tl.to(front, { y: 0, scale: 1, duration: 0.09, ease: 'power4.in' }, T + 0.92);
    tl.to(slot, { scaleY: 0, duration: 0.09, ease: 'power4.in' }, T + 0.92);
    tl.set(card, { autoAlpha: 0 }, T + 1.0);
    tl.fromTo(`${s} .drawer-rig`, { y: 0 }, { y: 7, duration: 0.05, ease: 'none', yoyo: true, repeat: 3 }, T + 1.0);
    tl.fromTo(`${s} .hook-headline`, { x: 0 }, { x: 5, duration: 0.05, ease: 'none', yoyo: true, repeat: 3 }, T + 1.0);
    cue(T + 1.0, 'thud');

    // springs open – live dashboard rises
    tl.to(front, { y: 70, scale: 1.07, duration: 0.35, ease: 'back.out(2.2)' }, T + 1.95);
    tl.to(slot, { scaleY: 1, duration: 0.3, ease: 'power2.out' }, T + 1.95);
    cue(T + 2.0, 'swoosh');
    tl.set(dash, { autoAlpha: 1 }, T + 2.0);
    tl.to(dash, { y: 0, scale: 1, duration: 0.65, ease: 'back.out(1.3)' }, T + 2.02);
    words(`${s} .hook-payoff`, T + 2.0, 0.04);

    const bars = $$(`${s} .dash-bars i`);
    const V = [
      [0.2, 0.34, 0.3, 0.5, 0.62, 0.74, 0.88],
      [0.24, 0.38, 0.36, 0.55, 0.66, 0.8, 0.92],
      [0.26, 0.42, 0.4, 0.6, 0.7, 0.84, 0.96],
      [0.3, 0.46, 0.44, 0.64, 0.75, 0.88, 1],
    ];
    tl.to(bars, { scaleY: (i) => V[0][i], duration: 0.4, ease: 'back.out(1.8)', stagger: 0.04 }, T + 2.15);
    for (let k = 1; k < V.length; k++) {
      tl.to(bars, { scaleY: (i) => V[k][i], duration: 0.22, ease: 'power2.out' }, T + 2.55 + k * 0.25);
    }
    tl.fromTo(`${s} .spark-area`, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.5 }, T + 2.25);
    tl.to(spark, { strokeDashoffset: 0, duration: 0.6, ease: 'power2.out' }, T + 2.2);
    tl.fromTo(`${s} .live-ring`, { scale: 1, autoAlpha: 1 }, { scale: 2.6, autoAlpha: 0, duration: 0.5, ease: 'power1.out', repeat: 2 }, T + 2.3);
    cue(T + 2.5, 'tick', { until: T + 3.4 });

    // exit: texts up, drawer drops, dashboard collapses into a dot
    const X = T + D - EXIT - 0.03;
    exitUp([`${s} .hook-headline`, `${s} .hook-payoff`], X);
    tl.to(front, { y: 210, autoAlpha: 0, duration: 0.4, ease: 'power2.in' }, T + 3.55);
    tl.to([$(`${s} .cabinet`), $(`${s} .lower`), slot], { y: 140, autoAlpha: 0, duration: 0.4, ease: 'power2.in' }, T + 3.55);
    tl.to(`${s} .dash-inner`, { autoAlpha: 0, duration: 0.15 }, T + 3.45);
    tl.to(`${s} .dash-cover`, { autoAlpha: 1, duration: 0.15 }, T + 3.45);
    tl.to(dash, { scaleX: 0.12, scaleY: 0.2, borderRadius: '50%', duration: 0.35, ease: 'power3.in' }, T + 3.5);
    tl.to(dash, { autoAlpha: 0, duration: 0.12 }, T + 3.86);
  }

  // 2 · Brand on white ---------------------------------------------------
  {
    const [T0, D0] = timing('s-brand');
    const { T, D, sel: s } = scene('s-brand', T0, D0, { visibleFrom: T0 - 0.1 });
    tl.set('.paper-white', { visibility: 'visible' }, T - 0.3);
    tl.set('.paper-white', { visibility: 'hidden' }, T + D + 0.05);
    tl.fromTo('.paper-white', { clipPath: `circle(0px at ${dotX}px ${dotY}px)` },
      { clipPath: `circle(${RR}px at ${dotX}px ${dotY}px)`, duration: 0.5, ease: 'power3.inOut' }, T - 0.3);

    tl.fromTo(`${s} .brand-logo`, { autoAlpha: 0, scale: 0.55, y: 40 },
      { autoAlpha: 1, scale: 1, y: 0, duration: 0.8, ease: 'back.out(1.6)' }, T);
    tl.fromTo(`${s} .brand-logo img`, { y: 0 }, { y: -10, duration: 1.3, ease: 'sine.inOut', yoyo: true, repeat: 1 }, T + 0.8);
    words(`${s} .h-xl`, T + 0.45, 0.07);
    tl.fromTo(`${s} .mk-line`, { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: 'power2.inOut' }, T + 1.5);

    cue(T, 'riser', { length: 1.0, gain: 0.5 });
    cue(T, 'drop');
    cue(T, 'impact', { gain: 0.7 });
    cue(T + 1.5, 'chime');

    exitUp([`${s} .brand-logo`, `${s} .h-xl`], T + D - 0.42);
    tl.to('.paper-white', { clipPath: `circle(0px at 50% 50%)`, duration: 0.38, ease: 'power3.in' }, T + D - 0.36);
  }

  // 3 · Sprint ------------------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-sprint', ...timing('s-sprint'));
    fadeUp(`${s} .chip`, T + 0.05);
    words(`${s} .h-l`, T + 0.12, 0.05);
    tl.fromTo(`${s} .note`, { autoAlpha: 0, rotation: -16, y: 40, scale: 0.8 },
      { autoAlpha: 1, rotation: -4, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.8)' }, T + 0.3);
    tl.fromTo($$(`${s} .wk`), { autoAlpha: 0, y: 30 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: 'power3.out', stagger: 0.05 }, T + 0.32);
    tl.fromTo(`${s} .mvp`, { autoAlpha: 0, scale: 0.85 }, { autoAlpha: 1, scale: 1, duration: 0.4, ease: 'power2.out' }, T + 0.5);
    tl.fromTo(`${s} .mvp-tile`, { autoAlpha: 0, x: 80, scale: 1.3, rotation: 8 }, { autoAlpha: 0, duration: 0.01 }, 0);

    $$(`${s} .wk`).forEach((wk, i) => {
      const at = T + 0.5 + i * 0.5;
      tl.fromTo($('.wk-fill', wk), { scaleX: 0 }, { scaleX: 1, duration: 0.42, ease: 'power2.inOut' }, at);
      tl.fromTo($$('.pips b', wk), { scale: 0 }, { scale: 1, duration: 0.3, ease: 'back.out(3)', stagger: 0.04 }, at + 0.1);
      cue(at, 'tick', { until: at + 0.1 });
    });

    // MVP snaps in, goes live
    tl.to(`${s} .mvp-tile`, { autoAlpha: 1, x: 0, scale: 1, rotation: 0, duration: 0.45, ease: 'back.out(2.4)' }, T + 2.42);
    tl.fromTo(`${s} .cloud-tick`, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.4, ease: 'back.out(3)' }, T + 2.6);
    tl.fromTo(`${s} .mvp-tile .live-ring`, { scale: 1, autoAlpha: 1 }, { scale: 2.8, autoAlpha: 0, duration: 0.6, ease: 'power1.out', repeat: 2 }, T + 2.7);
    cue(T + 2.5, 'bell', { note: 88, gain: 0.05 });
    words(`${s} .lead`, T + 2.35, 0.045);

    exitUp([`${s} .chip`, `${s} .h-l`, `${s} .note`, `${s} .weeks`, `${s} .mvp`, `${s} .lead`], T + D - EXIT);
  }

  // 4 · Process path on paper ----------------------------------------------
  {
    const [T0, D0] = timing('s-process');
    const { T, D, sel: s } = scene('s-process', T0, D0, { visibleFrom: T0 - 0.1 });
    tl.set('.paper-grey', { visibility: 'visible' }, T - 0.3);
    tl.set('.paper-grey', { visibility: 'hidden' }, T + D + 0.1);
    tl.fromTo('.paper-grey', { clipPath: 'inset(100% 0% 0% 0%)' }, { clipPath: 'inset(0% 0% 0% 0%)', duration: 0.45, ease: 'power3.inOut' }, T - 0.3);
    tl.to('.paper-grey', { clipPath: 'inset(0% 0% 100% 0%)', duration: 0.4, ease: 'power3.inOut' }, T + D - 0.3);

    const AW = 888;
    const AH = isStory ? 1000 : 640;
    const NS = 'http://www.w3.org/2000/svg';
    const svg = $(`${s} .path-svg`);
    svg.setAttribute('viewBox', `0 0 ${AW} ${AH}`);
    const pts = [0, 1, 2, 3, 4].map((i) => ({ x: 50 + i * 165, y: AH - 50 - i * ((AH - 250) / 4) }));
    const gear = { x: 820, y: 80 };
    const k = ((AH - 250) / 4) * 0.55;
    const segD = [`M-30 ${AH - 8} Q20 ${AH - 8} ${pts[0].x} ${pts[0].y}`];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      segD.push(`M${a.x} ${a.y} C${a.x} ${a.y - k} ${b.x} ${b.y + k} ${b.x} ${b.y}`);
    }
    const p4 = pts[4];
    segD.push(`M${p4.x} ${p4.y} C${p4.x} ${p4.y - 70} ${p4.x + 20} ${gear.y} ${gear.x - 58} ${gear.y}`);

    const guide = document.createElementNS(NS, 'path');
    guide.setAttribute('class', 'guide');
    guide.setAttribute('d', segD.join(' '));
    svg.appendChild(guide);
    const segs = segD.map((d) => {
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('class', 'seg');
      p.setAttribute('d', d);
      svg.appendChild(p);
      return p;
    });
    const wps = pts.map((p) => {
      const c = document.createElementNS(NS, 'circle');
      c.setAttribute('class', 'wp');
      c.setAttribute('cx', p.x);
      c.setAttribute('cy', p.y);
      c.setAttribute('r', 15);
      svg.appendChild(c);
      return c;
    });

    const chips = $$(`${s} .wp-chip`);
    chips.forEach((chip, i) => {
      const p = pts[i];
      const right = i <= 2;
      chip.style.left = `${right ? p.x + 36 : p.x - 36}px`;
      chip.style.top = `${p.y}px`;
      ctx.gsap.set(chip, { xPercent: right ? 0 : -100, yPercent: -50, transformOrigin: right ? '0% 50%' : '100% 50%' });
    });

    // gear
    const N = 10, ro = 54, ri = 42, st = (Math.PI * 2) / N;
    let gd = '';
    for (let i = 0; i < N; i++) {
      const a = i * st;
      [[ri, a - st * 0.5], [ri, a - st * 0.26], [ro, a - st * 0.16], [ro, a + st * 0.16], [ri, a + st * 0.26]].forEach(([r, an], j) => {
        gd += `${i === 0 && j === 0 ? 'M' : 'L'}${(Math.cos(an) * r).toFixed(1)} ${(Math.sin(an) * r).toFixed(1)}`;
      });
    }
    $(`${s} .gear-body`).setAttribute('d', `${gd}Z`);
    const gearEl = $(`${s} .gear`);
    gearEl.style.left = `${gear.x - 56}px`;
    gearEl.style.top = `${gear.y - 56}px`;

    words(`${s} .h-l`, T + 0.1, 0.05);
    tl.fromTo(guide, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.5 }, T + 0.3);
    segs.forEach((p) => stroke(p));
    tl.to(segs[0], { strokeDashoffset: 0, duration: 0.5, ease: 'power1.inOut' }, T + 0.5);
    for (let i = 1; i <= 4; i++) {
      tl.to(segs[i], { strokeDashoffset: 0, duration: 0.5, ease: 'power1.inOut' }, T + 1.0 + (i - 1) * 0.5);
    }
    tl.to(segs[5], { strokeDashoffset: 0, duration: 0.45, ease: 'power1.inOut' }, T + 3.0);
    cue(T + 0.5, 'arp');

    wps.forEach((c, i) => {
      const at = T + 1.0 + i * 0.5;
      tl.fromTo(c, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.4, ease: 'back.out(3)' }, at);
      tl.fromTo(chips[i], { autoAlpha: 0, scale: 0.6 }, { autoAlpha: 1, scale: 1, duration: 0.45, ease: 'back.out(2)' }, at);
      cue(at, 'tick', { until: at + 0.1 });
    });

    tl.fromTo(gearEl, { scale: 0, rotation: -90 }, { scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(2.2)' }, T + 3.3);
    tl.fromTo(`${s} .gear svg`, { rotation: 0 }, { rotation: 300, duration: D - 3.5, ease: 'none' }, T + 3.5);
    cue(T + 3.5, 'groove');

    exitUp([`${s} .h-l`, svg, ...chips, gearEl], T + D - EXIT - 0.05);
  }

  // 5 · Proof: customer quote over team photo ---------------------------------
  {
    const { T, D, sel: s } = scene('s-proof', ...timing('s-proof'));
    tl.fromTo(`${s} .photo`, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.45, ease: 'power2.out' }, T);
    tl.fromTo(`${s} .photo img`, { scale: 1.04 }, { scale: 1.16, duration: D, ease: 'none' }, T);
    tl.fromTo(`${s} .qmark`, { autoAlpha: 0, rotation: -25, y: -80, scale: 0.6, transformOrigin: '0% 100%' },
      { autoAlpha: 1, rotation: 0, y: 0, scale: 1, duration: 0.9, ease: 'elastic.out(1, 0.6)' }, T + 0.15);
    ['.ln1', '.ln2', '.ln3'].forEach((ln, i) => {
      tl.fromTo($$(`${s} ${ln} .wi`), { yPercent: 118 }, { yPercent: 0, duration: 0.6, ease: 'power4.out', stagger: 0.05 }, T + 0.3 + i * 0.3);
    });
    tl.fromTo(`${s} .attrib-line`, { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: 'power3.out' }, T + 1.35);
    tl.fromTo(`${s} .attrib span`, { autoAlpha: 0, x: -30 }, { autoAlpha: 1, x: 0, duration: 0.45, ease: 'power3.out' }, T + 1.45);
    cue(T + 0.5, 'chime');

    exitUp([`${s} .qmark`, `${s} .q`, `${s} .attrib`], T + D - EXIT - 0.05);
    tl.to(`${s} .photo`, { autoAlpha: 0, duration: 0.35, ease: 'power2.in' }, T + D - 0.35);
  }

  // 6 · Trust seals --------------------------------------------------------
  {
    const { T, D, sel: s } = scene('s-trust', ...timing('s-trust'));
    // EU star ring
    const NS = 'http://www.w3.org/2000/svg';
    const g = $(`${s} .eu-stars`);
    const star = Array.from({ length: 10 }, (_, i) => {
      const r = i % 2 ? 2.4 : 5.6;
      const a = (Math.PI / 5) * i - Math.PI / 2;
      return `${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`;
    });
    for (let i = 0; i < 12; i++) {
      const a = (Math.PI / 6) * i - Math.PI / 2;
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', `M${star.join('L')}Z`);
      p.setAttribute('transform', `translate(${(60 + Math.cos(a) * 50).toFixed(2)} ${(60 + Math.sin(a) * 50).toFixed(2)})`);
      g.appendChild(p);
    }

    const seals = $$(`${s} .seal`);
    const rot = [-6, 4, -3];
    const tw = ['.tw1', '.tw2', '.tw3'];
    const tick = $(`${s} .shield-tick`);
    stroke(tick);
    seals.forEach((seal, i) => {
      const at = T + 0.5 + i * 0.5;
      tl.fromTo(seal, { autoAlpha: 0, scale: 2.4, rotation: -24 },
        { autoAlpha: 1, scale: 1, rotation: rot[i], duration: 0.22, ease: 'power4.in' }, at - 0.22);
      tl.to(seal, { scale: 1.06, duration: 0.08, ease: 'power1.out', yoyo: true, repeat: 1 }, at);
      tl.fromTo($('.seal-ring', seal), { scale: 1, autoAlpha: 0.9 }, { scale: 1.45, autoAlpha: 0, duration: 0.5, ease: 'power2.out' }, at);
      tl.fromTo($$(`${s} ${tw[i]} .wi`), { yPercent: 118 }, { yPercent: 0, duration: 0.55, ease: 'power4.out' }, at - 0.05);
      cue(at, 'thud');
    });
    tl.fromTo(`${s} .seals`, { y: 0 }, { y: 5, duration: 0.05, ease: 'none', yoyo: true, repeat: 1 }, T + 0.5);
    tl.to(tick, { strokeDashoffset: 0, duration: 0.35, ease: 'power2.out' }, T + 1.6);
    tl.fromTo(`${s} .eu-stars path`, { scale: 0, transformOrigin: '50% 50%' }, { scale: 1, duration: 0.3, ease: 'back.out(3)', stagger: 0.02 }, T + 1.05);
    words(`${s} .lead`, T + 1.4, 0.045);

    exitUp([`${s} .seals`, `${s} .trust-head`, `${s} .lead`], T + D - EXIT - 0.05);
  }

  // 7 · CTA on white -------------------------------------------------------
  {
    const [T0, D0] = timing('s-cta');
    const { T, D, sel: s } = scene('s-cta', T0, D0, { visibleFrom: T0 - 0.1, visibleUntil: DURATION + 1 });
    tl.set('.cta-bg', { visibility: 'visible' }, T - 0.35);
    tl.fromTo('.cta-bg', { clipPath: 'circle(0px at 50% 50%)' }, { clipPath: `circle(${RR}px at 50% 50%)`, duration: 0.6, ease: 'power3.inOut' }, T - 0.35);

    // the drawer returns – closes empty and clean
    tl.fromTo(`${s} .mini-drawer`, { autoAlpha: 0, y: 40, scale: 0.8 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.5, ease: 'back.out(1.8)' }, T + 0.1);
    tl.fromTo(`${s} .md-front`, { y: 30, scale: 1.06 }, { y: 30, scale: 1.06, duration: 0.01 }, 0);
    tl.fromTo(`${s} .md-slot`, { scaleY: 1 }, { scaleY: 1, duration: 0.01 }, 0);
    tl.to(`${s} .md-front`, { y: 0, scale: 1, duration: 0.12, ease: 'power4.in' }, T + 0.75);
    tl.to(`${s} .md-slot`, { scaleY: 0, duration: 0.12, ease: 'power4.in' }, T + 0.75);
    tl.to(`${s} .mini-drawer`, { y: 4, duration: 0.05, ease: 'none', yoyo: true, repeat: 1 }, T + 0.87);
    cue(T + 0.87, 'thud');

    tl.fromTo(`${s} .logo`, { autoAlpha: 0, scale: 0.7, y: 30 }, { autoAlpha: 1, scale: 1, y: 0, duration: 0.8, ease: 'back.out(1.6)' }, T + 0.4);
    words(`${s} .h-m`, T + 0.85, 0.06);
    fadeUp(`${s} .cta-btn`, T + 1.5, { ease: 'back.out(1.8)' });
    fadeUp(`${s} .url`, T + 1.8);
    tl.fromTo(`${s} .cta-btn`, { scale: 1 }, { scale: 1.05, duration: 0.5, ease: 'sine.inOut', yoyo: true, repeat: 4 }, T + 2.2);
    tl.fromTo(`${s} .cta-btn svg`, { x: 0 }, { x: 10, duration: 0.25, ease: 'power2.inOut', yoyo: true, repeat: 3 }, T + 2.2);
    tl.fromTo(`${s} .logo`, { y: 0 }, { y: -8, duration: 1.6, ease: 'sine.inOut', yoyo: true, repeat: 1 }, T + 1.3);

    cue(T, 'outro');
    cue(T + 1.5, 'bells', { notes: [72, 76, 79, 84], step: 0.12, gain: 0.05 });
    cue(T + D - 0.5, 'stop', { until: T + D });
  }
});