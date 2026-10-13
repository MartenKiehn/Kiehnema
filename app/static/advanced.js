(() => {
  const root = document.getElementById('review-workspace');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const input = $('#advanced-draft'), format = $('#design-format');
  const frames = [...root.querySelectorAll('iframe.preview')];
  const history = [];
  let designs;
  try { designs = JSON.parse(input.value).frames; } catch { designs = []; }
  let active = false, layers = [], selected = '', uploading = false;
  let time = Number($('#timeline-position').value);
  let scene = root.querySelector('.timeline-frame[aria-pressed=true]')?.dataset.scene;
  const editor = () => frames.find((frame) => frame.classList.contains(format.value));
  const draft = () => designs.find((d) => d.scene_id === scene && d.format === format.value);
  const status = (message) => { $('#design-status').textContent = message; };
  const elementData = (layer) => Object.fromEntries(['id', 'kind', 'selector', 'label', 'text', 'comment', 'asset', 'x', 'y', 'width', 'height', 'layer', 'color', 'size', 'removed', 'text_ids'].map((key) => [key, layer[key]]));
  function store() {
    const value = JSON.stringify({frames: designs});
    if (value !== input.value) {
      history.push(input.value);
      if (history.length > 30) history.shift();
      input.value = value;
      input.dispatchEvent(new Event('input', {bubbles: true}));
    }
    $('#design-undo').disabled = !history.length;
    renderDrafts();
  }
  function saveLayers() {
    const elements = layers.filter((l) => l.changed || l.kind !== 'existing').map(elementData);
    designs = designs.filter((d) => d.scene_id !== scene || d.format !== format.value);
    if (elements.length) designs.push({scene_id: scene, format: format.value, time, elements});
    store();
  }
  function refresh() {
    frames.forEach((frame) => {
      frame.parentElement.hidden = active && frame !== editor();
      frame.contentWindow?.postMessage({type: 'kiehnema.design', active: active && frame === editor(),
        scene_id: scene, time, selected, elements: draft()?.elements || []}, '*');
    });
  }
  function renderDrafts() {
    $('#design-drafts').hidden = !designs.length;
    const list = $('#design-frames'); list.replaceChildren();
    designs.forEach((design) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'button secondary small';
      const index = [...root.querySelectorAll('.timeline-frame')].findIndex((b) => b.dataset.scene === design.scene_id) + 1;
      button.textContent = `Scene ${index} · ${design.format === 'feed' ? '4:5' : '9:16'}`;
      button.addEventListener('click', () => {
        format.value = design.format;
        $('#timeline-position').value = design.time;
        $('#timeline-position').dispatchEvent(new Event('input', {bubbles: true}));
      });
      list.append(button);
    });
  }
  function properties() {
    const layer = layers.find((l) => l.id === selected);
    $('#design-properties').hidden = !layer;
    if (!layer) return;
    $('#design-text').hidden = layer.kind !== 'text';
    $('[for="design-text"]').hidden = layer.kind !== 'text';
    $('#design-type').hidden = layer.kind !== 'text';
    $('#design-reset').hidden = layer.kind !== 'existing';
    $('#design-comment-target').textContent = `For: ${(layer.text || layer.label || 'Image').slice(0, 120)}`;
    const values = {'design-text': layer.text, 'design-comment': layer.comment || '', 'design-x': Math.round(layer.x * 100), 'design-y': Math.round(layer.y * 100),
      'design-width': Math.round(layer.width * 100), 'design-height': Math.round(layer.height * 100), 'design-font-size': layer.size, 'design-color': layer.color};
    Object.entries(values).forEach(([id, value]) => { if (document.activeElement !== $(`#${id}`)) $(`#${id}`).value = value; });
    const order = layers.filter((l) => !l.removed).sort((a, b) => a.layer - b.layer);
    $('#design-back').disabled = layer === order[0];
    $('#design-forward').disabled = layer === order.at(-1);
  }
  function renderLayers() {
    const list = $('#design-layers'); list.replaceChildren();
    layers.filter((l) => !l.removed).sort((a, b) => b.layer - a.layer).forEach((layer) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'design-layer';
      button.setAttribute('aria-pressed', String(layer.id === selected));
      button.textContent = `${layer.kind === 'text' ? 'T' : layer.kind === 'image' ? '▧' : '◇'}  ${layer.text || layer.label || 'Image'}`;
      if (layer.comment?.trim()) button.textContent = '● Comment · ' + button.textContent;
      button.title = button.textContent;
      button.addEventListener('click', () => { selected = layer.id; refresh(); });
      list.append(button);
    });
    properties();
  }
  function add(kind, asset = '', ratio = 1, x = .15, y = .2) {
    if (!active || !scene || layers.length >= 40) { status('Select a frame first. Use up to 40 elements per frame.'); return; }
    if (!draft() && designs.length >= 20) { status('Use up to 20 frame ideas per revision.'); return; }
    selected = crypto.randomUUID().replaceAll('-', '');
    layers.push({id: selected, kind, selector: '', label: kind === 'image' ? 'Uploaded image' : 'Text box',
      text: kind === 'text' ? 'Your text' : '', asset, x: Math.min(.9, Math.max(0, x)), y: Math.min(.9, Math.max(0, y)),
      width: .55, height: kind === 'text' ? .16 : Math.min(.8, .55 / ratio * 1080 / (format.value === 'feed' ? 1350 : 1920)),
      layer: Math.max(-1, ...layers.map((l) => l.layer)) + 1, color: '#ffffff', size: 64, changed: true});
    saveLayers(); refresh();
    if (kind === 'text') { properties(); $('#design-text').focus(); $('#design-text').select(); }
  }
  async function upload(file, x, y) {
    if (uploading || !active) return;
    if (file.size > 10 * 1024 * 1024) { status('Choose an image smaller than 10 MB.'); return; }
    const target = {scene, time, format: format.value};
    uploading = true; status('Uploading image…');
    try {
      const response = await fetch(`${root.dataset.url}/design-assets`, {method: 'POST', headers: {'X-Preview-Revision': root.dataset.revision}, body: file});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Could not upload this image.');
      if (scene !== target.scene || time !== target.time || format.value !== target.format || !active) throw new Error('Frame changed during upload. Return to the intended frame and add the image again.');
      add('image', result.asset, result.width / result.height, x, y);
      status('Image added. Drag it into place; AI will integrate it when you revise.');
    } catch (error) { status(error.message); }
    finally { uploading = false; $('#design-image').value = ''; }
  }
  root.addEventListener('review:panel', (event) => {
    active = event.detail === 'advanced';
    if (active) status('Drag an element, add text or drop an image. These are layout ideas for the revision.');
    refresh();
  });
  root.addEventListener('review:seek', (event) => {
    ({time, scene_id: scene} = event.detail);
    selected = ''; layers = [];
    if (active) refresh();
  });
  format.addEventListener('change', () => { selected = ''; layers = []; refresh(); });
  $('#design-add-text').addEventListener('click', () => add('text'));
  $('#design-add-image').addEventListener('click', () => $('#design-image').click());
  $('#design-image').addEventListener('change', () => { if ($('#design-image').files[0]) upload($('#design-image').files[0]); });
  addEventListener('message', (event) => {
    if (event.source !== editor()?.contentWindow || !active || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'kiehnema.design.ready') refresh();
    if (data.type === 'kiehnema.design.error') status(String(data.message));
    if (data.type === 'kiehnema.design.drop' && data.bytes instanceof ArrayBuffer) upload(new Blob([data.bytes]), data.x, data.y);
    if (data.type !== 'kiehnema.design.state' || data.scene_id !== scene || data.time !== time || !Array.isArray(data.layers)) return;
    layers = data.layers; selected = data.selected;
    // A snapshot alone is not an edit; only changed layers enter the revision.
    if (layers.some((l) => l.changed)) saveLayers();
    renderLayers();
  });
  const fields = {'design-text': ['text', 1], 'design-comment': ['comment', 1], 'design-x': ['x', 100], 'design-y': ['y', 100],
    'design-width': ['width', 100], 'design-height': ['height', 100], 'design-font-size': ['size', 1], 'design-color': ['color', 1]};
  Object.entries(fields).forEach(([id, [key, divisor]]) => {
    $(`#${id}`).addEventListener('input', () => {
      const field = $(`#${id}`), layer = layers.find((l) => l.id === selected);
      if (!layer || !field.checkValidity()) return;
      layer[key] = ['text', 'comment', 'color'].includes(key) ? field.value : Number(field.value) / divisor;
      layer.changed = true; saveLayers(); refresh();
    });
  });
  [$('#design-back'), $('#design-forward')].forEach((button, i) => button.addEventListener('click', () => {
    const order = layers.filter((l) => !l.removed).sort((a, b) => a.layer - b.layer), index = order.findIndex((l) => l.id === selected);
    const other = order[index + (i ? 1 : -1)];
    if (!other) return;
    [order[index].layer, other.layer] = [other.layer, order[index].layer];
    order[index].changed = other.changed = true; saveLayers(); refresh();
  }));
  $('#design-remove').addEventListener('click', () => {
    const layer = layers.find((l) => l.id === selected);
    if (!layer) return;
    if (layer.kind === 'existing') { layer.removed = true; layer.changed = true; }
    else layers = layers.filter((l) => l.id !== selected);
    selected = ''; saveLayers(); refresh();
    status('Element removed from this idea. Undo restores it; Revise preview applies the removal to the video.');
  });
  $('#design-reset').addEventListener('click', () => {
    layers = layers.filter((l) => l.id !== selected); selected = ''; saveLayers(); refresh();
  });
  $('#design-undo').addEventListener('click', () => {
    const value = history.pop(); if (!value) return;
    input.value = value; designs = JSON.parse(value).frames;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    $('#design-undo').disabled = !history.length; selected = ''; renderDrafts(); refresh();
    status('Last change undone.');
  });
  $('#review-form').addEventListener('submit', (event) => {
    if (uploading || designs.some((d) => d.elements.some((e) => e.kind === 'text' && !e.text.trim()))) {
      event.preventDefault(); $('#advanced-tab').click();
      status(uploading ? 'Wait for the image upload to finish.' : 'Fill or remove the empty text box before revising.');
    }
  });
  renderDrafts();
})();
