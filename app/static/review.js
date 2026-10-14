(() => {
  const root = document.getElementById('review-workspace');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const form = $('#review-form');
  const duration = Number(root.dataset.duration);
  const frames = [...root.querySelectorAll('iframe.preview')];
  const slider = $('#timeline-position');
  const text = $('#comment-text');
  const timeInput = $('#comment-time');
  const formatInput = $('#comment-format');
  const sceneButtons = [...root.querySelectorAll('.timeline-frame')];
  let activeScene = '';
  let comments = JSON.parse(root.dataset.comments);
  let editing = null;
  let selected = '';
  let position = 0;
  let playing = false;
  let saving = false;
  const initialForm = JSON.stringify([...new FormData(form)]);
  const formatTime = (t) => `${Number(t).toFixed(1)} s`;
  const status = (message) => { if ($('#comment-status')) $('#comment-status').textContent = message; };
  const unsavedComment = () => text && (text.value.trim() !== (editing?.text || '') ||
    editing && (Number(timeInput.value) !== editing.time || formatInput.value !== editing.format));
  const send = (message) => frames.forEach((frame) => frame.contentWindow?.postMessage(message, '*'));

  function selectScene(id) {
    const cards = [...root.querySelectorAll('#scene-editor > .scene-card')];
    if (!cards.some((card) => card.dataset.sceneId === id)) return;
    const changed = activeScene !== id;
    activeScene = id;
    if (changed) {
      const frame = sceneButtons.find((b) => b.dataset.scene === id);
      if (frame) {
        const strip = frame.parentElement;
        strip.scrollLeft += frame.getBoundingClientRect().left - strip.getBoundingClientRect().left - strip.clientWidth / 2 + frame.clientWidth / 2;
      }
      $('.scene-inspector').scrollTop = 0;
    }
    cards.forEach((card) => { card.hidden = card.dataset.sceneId !== id; });
    sceneButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scene === id)));
    const index = sceneButtons.findIndex((b) => b.dataset.scene === id);
    $('#selected-scene-number').textContent = String(index + 1).padStart(2, '0');
    $('#scene-previous').disabled = index <= 0;
    $('#scene-next').disabled = index === sceneButtons.length - 1;
  }
  function sceneAt(t) {
    return sceneButtons.find((b) => t >= Number(b.dataset.start) && t < Number(b.dataset.start) + Number(b.dataset.duration)) || sceneButtons.at(-1);
  }
  function setInspector(panel) {
    ['scene', 'comments', 'advanced'].forEach((name) => {
      const active = panel === name;
      $(name === 'comments' ? '#timeline-comments' : `#${name}-panel`).hidden = !active;
      const tab = $(`#${name}-tab`);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    $('.review-studio').classList.toggle('comment-mode', panel === 'comments');
    $('.review-studio').classList.toggle('advanced-mode', panel === 'advanced');
    if (panel === 'advanced') seek(position);
    $('#timeline-play').disabled = panel === 'advanced' || !frames.length;
    root.dispatchEvent(new CustomEvent('review:panel', {detail: panel}));
  }
  function openComments() {
    setInspector('comments');
    $('.studio-grid').scrollIntoView({block: 'start'});
  }
  const tabs = [$('#scene-tab'), $('#comments-tab'), $('#advanced-tab')];
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => setInspector(['scene', 'comments', 'advanced'][i]));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[index].click(); tabs[index].focus();
    });
  });
  function showTime(t) {
    position = Math.min(duration, Math.max(0, t));
    if (!slider) return;
    slider.value = position;
    $('#timeline-time').textContent = formatTime(position);
    if (!editing && !text.value.trim()) timeInput.value = position.toFixed(1);
    root.querySelectorAll('.timeline-frame').forEach((button) => {
      const start = Number(button.dataset.start), end = start + Number(button.dataset.duration);
      button.classList.toggle('selected', position >= start && (position < end || position === duration && end === duration));
    });
  }
  function seek(t) {
    playing = false;
    showTime(t);
    if (!$('#timeline-comments').hidden && !saving) timeInput.value = String(position);
    if ($('#timeline-play')) $('#timeline-play').textContent = 'Play both previews';
    selectScene(sceneAt(position)?.dataset.scene);
    send({type: 'kiehnema.seek', time: position});
    root.dispatchEvent(new CustomEvent('review:seek', {detail: {time: position, scene_id: sceneAt(position)?.dataset.scene}}));
  }
  function resetComment() {
    editing = null;
    text.value = '';
    timeInput.value = position.toFixed(1);
    formatInput.value = 'both';
    $('#comment-editor-title').textContent = 'Add a comment';
    $('#save-comment').textContent = 'Confirm comment';
    $('#cancel-comment').textContent = 'Clear draft';
  }
  function editComment(comment) {
    if (saving) return;
    if (unsavedComment() && !confirm('Discard the unconfirmed comment changes?')) return;
    seek(comment.time);
    editing = comment;
    selected = comment.id;
    text.value = comment.text;
    timeInput.value = comment.time;
    formatInput.value = comment.format;
    $('#comment-editor-title').textContent = 'Edit comment';
    $('#save-comment').textContent = 'Confirm changes';
    $('#cancel-comment').textContent = 'Cancel editing';
    renderComments();
    openComments();
    text.focus();
    status('Changes to this comment must be confirmed.');
  }
  function button(label, handler) {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = 'button secondary small';
    element.textContent = label;
    element.addEventListener('click', handler);
    return element;
  }
  function renderComments() {
    if (!slider) return;
    const list = $('#comment-list'), markers = $('#comment-markers');
    list.replaceChildren();
    markers.replaceChildren();
    $('#comment-count').textContent = comments.length;
    $('#confirmed-comment-count').textContent = comments.length;
    $('#comments-empty').hidden = comments.length > 0;
    let previous = -100, row = 0, rows = 1;
    comments.forEach((comment, i) => {
      const sceneIndex = [...root.querySelectorAll('.timeline-frame')].findIndex((b) => b.dataset.scene === comment.scene_id) + 1;
      const label = `${formatTime(comment.time)} · Scene ${sceneIndex} · ${comment.format === 'both' ? 'Both formats' : comment.format === 'feed' ? '4:5 Feed' : '9:16 Story'}`;
      const marker = button(String(i + 1), () => editComment(comment));
      marker.className = 'comment-marker';
      marker.title = `${label}: ${comment.text}`;
      marker.setAttribute('aria-label', `Comment ${i + 1}: ${label}`);
      marker.setAttribute('aria-pressed', String(selected === comment.id));
      const x = comment.time / duration * markers.clientWidth;
      row = x - previous < 34 ? row + 1 : 0;
      previous = x;
      rows = Math.max(rows, row + 1);
      marker.style.left = `clamp(16px, ${comment.time / duration * 100}%, calc(100% - 16px))`;
      marker.style.top = `${row * 34}px`;
      markers.append(marker);
      const item = document.createElement('li');
      item.classList.toggle('selected', selected === comment.id);
      const jump = button(`${i + 1} · ${label}`, () => editComment(comment));
      jump.classList.add('comment-jump');
      const body = document.createElement('p');
      body.textContent = comment.text;
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.append(button('Edit', () => editComment(comment)), button('Delete', () => saveComment({action: 'delete', id: comment.id})));
      item.append(jump, body, actions);
      list.append(item);
    });
    markers.style.height = `${comments.length ? rows * 34 : 12}px`;
  }
  async function saveComment(data) {
    if (saving) return;
    saving = true;
    root.querySelectorAll('.comment-workspace button, .comment-workspace input, .comment-workspace textarea, .comment-workspace select, .comment-marker, #comment-here').forEach((b) => b.disabled = true);
    status('Saving…');
    try {
      const response = await fetch(`${root.dataset.url}/comments`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content},
        body: JSON.stringify({revision: root.dataset.revision, ...data}),
      });
      if (!response.ok) throw new Error(response.status === 409 ? 'This preview has changed. Copy your draft, then reload the page.' : 'Could not save. Check the time and comment, then try again.');
      comments = (await response.json()).comments;
      if (data.action !== 'delete' || editing?.id === data.id) resetComment();
      selected = data.action === 'delete' ? '' : (data.id || comments.find((c) => c.time === data.time && c.text === data.text)?.id || '');
      renderComments();
      updateScenes();
      status(data.action === 'delete' ? 'Comment deleted.' : 'Comment confirmed and saved. Choose Revise preview when all your changes are ready.');
    } catch (error) {
      status(error.message);
    } finally {
      saving = false;
      root.querySelectorAll('.comment-workspace button, .comment-workspace input, .comment-workspace textarea, .comment-workspace select, .comment-marker, #comment-here').forEach((b) => b.disabled = false);
    }
  }

  if (slider) {
    slider.addEventListener('input', () => seek(Number(slider.value)));
    root.querySelectorAll('.timeline-frame').forEach((b) => {
      b.style.flexGrow = b.dataset.duration;
      b.addEventListener('click', () => seek(Number(b.dataset.time)));
    });
    $('#timeline-play').addEventListener('click', () => {
      playing = !playing;
      send({type: 'kiehnema.play', playing});
      $('#timeline-play').textContent = playing ? 'Pause both previews' : 'Play both previews';
    });
    $('#comment-here').addEventListener('click', () => {
      if (unsavedComment() && !confirm('Discard the unconfirmed comment changes?')) return;
      seek(position);
      resetComment();
      openComments();
      text.focus();
    });
    timeInput.addEventListener('change', () => { if (timeInput.checkValidity()) seek(Number(timeInput.value)); });
    $('#save-comment').addEventListener('click', () => {
      if (!timeInput.reportValidity() || !text.reportValidity() || !text.value.trim()) {
        status('Choose a valid time and write a comment first.');
        text.focus();
        return;
      }
      saveComment({action: 'save', id: editing?.id || '', time: Number(timeInput.value), text: text.value.trim(), format: formatInput.value});
    });
    $('#cancel-comment').addEventListener('click', () => { resetComment(); status('Unconfirmed changes cleared.'); });
    addEventListener('message', (event) => {
      const frame = frames.find((f) => f.contentWindow === event.source);
      if (!frame || !event.data || typeof event.data !== 'object') return;
      if (event.data.type === 'kiehnema.ready') frame.contentWindow.postMessage({type: 'kiehnema.seek', time: position}, '*');
      if (event.data.type === 'kiehnema.time' && frame === frames[0] && Number.isFinite(event.data.time) && event.data.time >= 0 && event.data.time <= duration) {
        showTime(event.data.time);
        playing = event.data.playing === true;
        $('#timeline-play').textContent = playing ? 'Pause both previews' : 'Play both previews';
      }
    });
    addEventListener('resize', renderComments);
    seek(Number(sceneButtons[0]?.dataset.time || 0));
    if (comments.length) setInspector('comments');
    renderComments();
  }

  function updateScenes() {
    const cards = [...root.querySelectorAll('#scene-editor > .scene-card')];
    let total = 0, editedCount = 0, timingChanged = false;
    const plan = $('#planned-order');
    plan.replaceChildren();
    cards.forEach((card, i) => {
      const input = card.querySelector('[name^="scene-duration:"]');
      const seconds = Number(input.value);
      const previewIndex = sceneButtons.findIndex((b) => b.dataset.scene === card.dataset.sceneId);
      const moved = i !== previewIndex;
      const changed = moved || [...card.querySelectorAll('[data-original-value]')].some((field) =>
        field.type === 'number' ? Number(field.value) !== Number(field.dataset.originalValue) : field.value !== field.dataset.originalValue);
      timingChanged ||= moved || seconds !== Number(input.dataset.originalValue);
      editedCount += changed ? 1 : 0;
      const frame = sceneButtons[previewIndex];
      if (frame) frame.querySelector('.frame-edit').hidden = !changed;
      card.querySelector('.scene-number').textContent = String(previewIndex + 1).padStart(2, '0');
      card.querySelector('.scene-new-time').textContent = `After revision: ${formatTime(total)}–${formatTime(total + seconds)}`;
      card.querySelector('[data-move="-1"]').disabled = i === 0;
      card.querySelector('[data-move="1"]').disabled = i === cards.length - 1;
      const chip = button(`${String(previewIndex + 1).padStart(2, '0')} · ${formatTime(seconds)}`, () => {
        if (frame) seek(Number(frame.dataset.time));
      });
      chip.title = `Draft position ${i + 1}: ${card.querySelector('h3').textContent}`;
      chip.setAttribute('aria-label', chip.title);
      plan.append(chip);
      total += seconds;
    });
    plan.hidden = !timingChanged;
    $('#planned-duration').textContent = `After revision · ${formatTime(total)}${timingChanged ? ' · Scene order below' : ''}`;
    const invalid = total < 15 || total > 60;
    $('#planned-duration').classList.toggle('invalid-duration', invalid);
    if (invalid) $('#planned-duration').textContent += ' · Choose a total of 15–60 seconds';
    const hasChanges = editedCount || comments.length || $('#feedback').value.trim() || $('#advanced-draft').value !== '{"frames":[]}';
    $('#edit-status').textContent = hasChanges ? 'Draft changes · revise to apply' : 'Preview up to date';
    $('#edit-status').classList.toggle('status-review', Boolean(hasChanges));
    $('#review-form [value="revise"]').classList.toggle('secondary', !hasChanges);
    $('#review-form [value="approve"]').classList.toggle('secondary', Boolean(hasChanges));
  }
  [$('#scene-previous'), $('#scene-next')].forEach((button, direction) => {
    button.addEventListener('click', () => {
      const index = sceneButtons.findIndex((b) => b.dataset.scene === activeScene);
      const next = sceneButtons[index + (direction ? 1 : -1)];
      if (next) seek(Number(next.dataset.time));
    });
  });
  $('.timeline-frames')?.addEventListener('keydown', (event) => {
    const index = sceneButtons.indexOf(event.target);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? sceneButtons[0] : event.key === 'End' ? sceneButtons.at(-1) : sceneButtons[index + (event.key === 'ArrowRight' ? 1 : -1)];
    if (next) { next.focus(); next.click(); }
  });
  $('#scene-editor').addEventListener('click', (event) => {
    const move = event.target.closest('[data-move]');
    if (!move) return;
    const card = move.closest('.scene-card');
    if (move.dataset.move === '-1' && card.previousElementSibling) card.previousElementSibling.before(card);
    else if (move.dataset.move === '1' && card.nextElementSibling) card.nextElementSibling.after(card);
    updateScenes();
    if (move.disabled) card.querySelector('[data-move]:not(:disabled)')?.focus();
    else move.focus();
  });
  form.addEventListener('input', updateScenes);
  form.addEventListener('invalid', (event) => {
    const card = form.querySelector(':invalid')?.closest('.scene-card');
    if (card) { setInspector('scene'); selectScene(card.dataset.sceneId); }
    else if (event.target.closest('#timeline-comments')) openComments();
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
  form.addEventListener('submit', (event) => {
    if (saving || unsavedComment()) {
      event.preventDefault();
      status(saving ? 'Wait until the comment has been saved.' : 'Confirm or clear your unfinished comment before continuing.');
      openComments();
      text?.focus();
    }
  });
  addEventListener('beforeunload', (event) => {
    if (!form.dataset.submitting && (unsavedComment() || JSON.stringify([...new FormData(form)]) !== initialForm)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  updateScenes();
})();
