/* Temporary visual ideas inside the isolated preview. Never used by the renderer. */
(() => {
  if (parent === window || MG.RENDER) return;
  const stage = document.getElementById('stage');
  let context = null, layers = [], selected = '', drag = null;
  const originals = new Map();
  const added = [];
  const selection = document.createElement('div');
  selection.dataset.designSelection = '';
  Object.assign(selection.style, {position: 'absolute', pointerEvents: 'none', zIndex: '2147483647'});
  for (const [edge, label, x, y] of [['nw', 'top left', 0, 0], ['n', 'top', 50, 0], ['ne', 'top right', 100, 0],
    ['e', 'right', 100, 50], ['se', 'bottom right', 100, 100], ['s', 'bottom', 50, 100],
    ['sw', 'bottom left', 0, 100], ['w', 'left', 0, 50]]) {
    const handle = document.createElement('button');
    handle.type = 'button'; handle.dataset.resize = edge;
    handle.setAttribute('aria-label', `Resize ${label}`);
    handle.title = `Resize ${label}. Arrow keys adjust size.`;
    Object.assign(handle.style, {position: 'absolute', left: `${x}%`, top: `${y}%`, transform: 'translate(-50%, -50%)',
      background: '#fffdf8', borderColor: '#886334', borderStyle: 'solid', borderRadius: '2px', padding: '0', margin: '0',
      pointerEvents: 'auto', touchAction: 'none', cursor: `${edge}-resize`});
    selection.append(handle);
  }
  const post = (type, data = {}) => parent.postMessage({type, format: MG.FORMAT, ...data}, location.origin);
  const rect = (node) => {
    const box = node.getBoundingClientRect(), base = stage.getBoundingClientRect();
    return {x: (box.left - base.left) / base.width, y: (box.top - base.top) / base.height,
      width: box.width / base.width, height: box.height / base.height};
  };
  function clear() {
    selection.remove();
    originals.forEach((style, node) => { node.style.cssText = style; });
    added.forEach((node) => node.remove());
    originals.clear(); added.length = 0; layers = []; context = null; drag = null;
    stage.style.cursor = '';
    stage.style.touchAction = '';
  }
  function emit() {
    post('kiehnema.design.state', {scene_id: context.scene_id, time: context.time, selected,
      layers: layers.map(({node, base, ...layer}) => layer)});
  }
  function paint(layer) {
    const node = layer.node;
    if (!node) return;
    if (layer.removed) { node.style.visibility = 'hidden'; node.style.pointerEvents = 'none'; return; }
    if (layer.kind === 'existing') {
      node.style.cssText = originals.get(node);
      const original = getComputedStyle(node);
      const scale = original.scale === 'none' ? [1, 1] : original.scale.split(' ').map(Number);
      const translate = original.translate === 'none' ? ['0px', '0px'] : original.translate.split(' ');
      node.style.scale = `${scale[0] * layer.width / layer.base.width} ${(scale[1] ?? scale[0]) * layer.height / layer.base.height}`;
      const box = rect(node);
      node.style.translate = `calc(${translate[0]} + ${(layer.x - box.x) * MG.W}px) calc(${translate[1] || '0px'} + ${(layer.y - box.y) * MG.H}px)`;
      if (getComputedStyle(node).position === 'static') node.style.position = 'relative';
    } else {
      Object.assign(node.style, {position: 'absolute', left: `${layer.x * MG.W}px`, top: `${layer.y * MG.H}px`,
        width: `${layer.width * MG.W}px`, height: `${layer.height * MG.H}px`, margin: '0',
        objectFit: 'contain', font: `600 ${layer.size}px/1.15 var(--font-display, sans-serif)`,
        color: layer.color, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', cursor: 'move'});
      if (layer.kind === 'text' && !node.isContentEditable) node.textContent = layer.text;
    }
    node.style.zIndex = String(100 + layer.layer);
    if (layer.id === selected) drawSelection(layer);
  }
  function drawSelection(layer) {
    if (!layer.node) return;
    const unit = MG.W / stage.getBoundingClientRect().width;
    const box = rect(layer.node);
    Object.assign(selection.style, {left: `${box.x * MG.W}px`, top: `${box.y * MG.H}px`,
      width: `${box.width * MG.W}px`, height: `${box.height * MG.H}px`, border: `${1.5 * unit}px solid #d5ad68`});
    [...selection.children].forEach((handle) => Object.assign(handle.style, {
      width: `${14 * unit}px`, height: `${14 * unit}px`, borderWidth: `${1.5 * unit}px`}));
    if (!selection.isConnected) stage.append(selection);
  }
  function resize(layer, start, edge, dx, dy, proportional) {
    const west = edge.includes('w'), north = edge.includes('n');
    const maxWidth = Math.min(3, west ? start.x + start.width + .9 : 3);
    const maxHeight = Math.min(3, north ? start.y + start.height + .9 : 3);
    let width = edge.match(/[we]/) ? start.width + (west ? -dx : dx) : start.width;
    let height = edge.match(/[ns]/) ? start.height + (north ? -dy : dy) : start.height;
    if (proportional && edge.length === 2) {
      const factor = Math.abs(dx / start.width) > Math.abs(dy / start.height) ? width / start.width : height / start.height;
      const bounded = Math.max(Math.max(.05 / start.width, .03 / start.height),
        Math.min(factor, maxWidth / start.width, maxHeight / start.height));
      width = start.width * bounded; height = start.height * bounded;
    } else {
      width = Math.max(.05, Math.min(maxWidth, width)); height = Math.max(.03, Math.min(maxHeight, height));
    }
    Object.assign(layer, {width, height, x: west ? start.x + start.width - width : start.x,
      y: north ? start.y + start.height - height : start.y});
    paint(layer);
  }
  function choose(id) {
    selected = id;
    layers.forEach(paint);
    emit();
  }
  function setup(data) {
    clear();
    if (!data.active) return;
    context = data;
    const scene = window.__mg.scenes.find((s) => data.time >= s.start && data.time < s.start + s.duration) || window.__mg.scenes.at(-1);
    const container = scene && document.getElementById(scene.id);
    if (!container) { post('kiehnema.design.error', {message: 'No editable scene at this moment.'}); return; }
    [...container.children].forEach((node, i) => {
      const box = rect(node), style = getComputedStyle(node);
      if (box.width <= 0 || box.width > 3 || box.height <= 0 || box.height > 3 || box.x < -1 || box.x > 1 || box.y < -1 || box.y > 1 || style.visibility === 'hidden' || Number(style.opacity) < .02) return;
      const text = [...node.querySelectorAll('.wi')].map((word) => word.textContent).join(' ') || node.textContent;
      const label = (text.replace(/\s+/g, ' ').trim() || node.getAttribute('alt') || node.className.baseVal || node.className || node.tagName).toString().slice(0, 120);
      originals.set(node, node.style.cssText);
      layers.push({id: `existing-${i}`, kind: 'existing', selector: `#${CSS.escape(scene.id)} > :nth-child(${i + 1})`, label,
        text_ids: [node, ...node.querySelectorAll('[data-text-id]')].map((n) => n.getAttribute('data-text-id')).filter(Boolean),
        text: '', asset: '', color: '#ffffff', size: 64, ...box, layer: 0, node, base: box,
        originalOrder: Number.parseInt(style.zIndex) || 0});
    });
    layers.sort((a, b) => a.originalOrder - b.originalOrder);
    layers.forEach((layer, i) => { layer.layer = i; delete layer.originalOrder; });
    for (const patch of data.elements || []) {
      if (patch.kind === 'existing') {
        const layer = layers.find((l) => l.selector === patch.selector);
        if (layer) Object.assign(layer, patch, {changed: true});
        // Keep ideas for artwork that is currently hidden by its animation.
        else layers.push({...patch, changed: true, node: null});
      } else if (['text', 'image'].includes(patch.kind)) {
        const node = document.createElement(patch.kind === 'image' ? 'img' : 'div');
        node.dataset.designId = patch.id;
        if (patch.kind === 'image') {
          if (!/^[a-f0-9]{32}\.png$/.test(patch.asset)) continue;
          node.src = 'assets/user/' + patch.asset;
          node.alt = 'Uploaded design image';
        }
        container.append(node); added.push(node);
        layers.push({...patch, changed: true, node});
        if (patch.kind === 'text') {
          node.addEventListener('dblclick', (event) => {
            event.stopPropagation(); node.contentEditable = 'plaintext-only'; node.focus();
          });
          node.addEventListener('input', () => {
            const layer = layers.find((l) => l.node === node);
            layer.text = node.innerText.slice(0, 500); emit();
          });
          node.addEventListener('blur', () => { node.contentEditable = 'false'; });
        }
      }
    }
    selected = data.selected || '';
    stage.style.cursor = 'move';
    stage.style.touchAction = 'none';
    layers.forEach(paint);
    emit();
  }
  window.__mgDesign = {clear, active: () => Boolean(context)};
  addEventListener('message', (event) => {
    if (event.source !== parent || !event.data || event.data.type !== 'kiehnema.design') return;
    setup(event.data);
  });
  stage.addEventListener('pointerdown', (event) => {
    if (!context || event.button !== 0 || event.target.isContentEditable) return;
    const edge = event.target.dataset.resize;
    const layer = layers.find((l) => !l.removed && (edge ? l.id === selected : l.node === event.target || l.node?.contains(event.target)));
    if (!layer) return;
    event.preventDefault(); event.stopPropagation();
    choose(layer.id);
    drag = {layer, edge, x: event.clientX, y: event.clientY, startX: layer.x, startY: layer.y,
      width: layer.width, height: layer.height};
    (edge ? event.target : layer.node).setPointerCapture(event.pointerId);
  }, true);
  stage.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const bounds = stage.getBoundingClientRect();
    if (drag.edge) {
      resize(drag.layer, {x: drag.startX, y: drag.startY, width: drag.width, height: drag.height}, drag.edge,
        (event.clientX - drag.x) / bounds.width, (event.clientY - drag.y) / bounds.height,
        drag.layer.kind !== 'text' || event.shiftKey);
      return;
    }
    drag.layer.x = Math.max(-.9, Math.min(.95, drag.startX + (event.clientX - drag.x) / bounds.width));
    drag.layer.y = Math.max(-.9, Math.min(.95, drag.startY + (event.clientY - drag.y) / bounds.height));
    paint(drag.layer);
  });
  const finish = () => {
    if (!drag) return;
    if (drag.layer.x !== drag.startX || drag.layer.y !== drag.startY || drag.layer.width !== drag.width || drag.layer.height !== drag.height) drag.layer.changed = true;
    drag = null; emit();
  };
  stage.addEventListener('pointerup', finish);
  stage.addEventListener('pointercancel', finish);
  stage.addEventListener('click', (event) => { if (context) event.preventDefault(); }, true);
  addEventListener('keydown', (event) => {
    if (!context || event.target.isContentEditable || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    const layer = layers.find((l) => l.id === selected);
    if (!layer) return;
    event.preventDefault();
    const step = event.shiftKey ? .02 : .005;
    if (event.target.dataset.resize) {
      resize(layer, {...layer}, event.target.dataset.resize,
        event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0,
        event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0, layer.kind !== 'text');
      layer.changed = true; emit(); return;
    }
    layer.x = Math.max(-.9, Math.min(.95, layer.x + (event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0)));
    layer.y = Math.max(-.9, Math.min(.95, layer.y + (event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0)));
    layer.changed = true; paint(layer); emit();
  });
  addEventListener('resize', () => { const layer = layers.find((l) => l.id === selected); if (context && layer) drawSelection(layer); });
  // Forward files out of the sandbox; only the parent can upload them.
  stage.addEventListener('dragover', (event) => { if (context && event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  stage.addEventListener('drop', async (event) => {
    if (!context) return;
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (!file || file.size > 10 * 1024 * 1024) { post('kiehnema.design.error', {message: 'Choose an image smaller than 10 MB.'}); return; }
    const bounds = stage.getBoundingClientRect();
    post('kiehnema.design.drop', {bytes: await file.arrayBuffer(), x: (event.clientX - bounds.left) / bounds.width,
      y: (event.clientY - bounds.top) / bounds.height});
  });
  post('kiehnema.design.ready');
})();
