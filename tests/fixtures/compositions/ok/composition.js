MG.composition(({ tl, scene, words, exitUp, EXIT, cue }) => {
  const a = scene('s-a', 0, 2);
  words(`${a.sel} h1`, 0.1);
  exitUp(`${a.sel} h1`, a.end - EXIT);
  const b = scene('s-b', 2, 2);
  words(`${b.sel} h1`, b.T + 0.1);
  cue(2, 'drop');
  cue(2, 'impact');
});
