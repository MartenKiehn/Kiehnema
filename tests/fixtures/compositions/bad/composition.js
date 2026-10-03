MG.composition(({ tl, scene, words, exitUp, cue, $ }) => {
  const a = scene('s-a', 0, 1);
  words(`${a.sel} h1`, 0.1);
  exitUp(`${a.sel} h1`, 0.6);
  const b = scene('s-b', 2.5, 1.5);   // gap 1.0 -> 2.5
  words(`${b.sel} h1`, b.T);
  cue(3, 'laser');
  // nondeterministic: depends on wall-clock time
  const j = $('#jitter');
  j.style.cssText = 'position:absolute;width:40px;height:40px;background:#f00;top:10px';
  window.__seekOrig = window.__seek;
  window.__seek = (t) => { window.__seekOrig(t); j.style.left = `${(performance.now() * 7) % 900}px`; };
});
