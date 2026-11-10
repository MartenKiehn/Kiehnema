const sourceForm = document.querySelector('[data-source-form]');
if (sourceForm) {
  const modes = sourceForm.querySelectorAll('[name="source_mode"]');
  const picker = sourceForm.querySelector('#source-files');
  const list = sourceForm.querySelector('[data-source-list]');
  const error = sourceForm.querySelector('[data-source-error]');
  const status = sourceForm.querySelector('[data-source-status]');
  const button = sourceForm.querySelector('button[type="submit"]');
  const confirmation = document.querySelector('#source-confirm');
  confirmation.querySelector('[data-confirm-cancel]').addEventListener('click', () => confirmation.close('cancel'));
  confirmation.querySelector('[data-confirm-continue]').addEventListener('click', () => confirmation.close('confirm'));
  const confirmPages = (pages) => new Promise((resolve) => {
    confirmation.querySelector('[data-confirm-pages]').textContent = pages;
    confirmation.returnValue = '';
    confirmation.addEventListener('close', () => resolve(confirmation.returnValue === 'confirm'), {once: true});
    confirmation.showModal();
  });
  let files = [];
  let busy = false;
  const fileMode = () => sourceForm.querySelector('[name="source_mode"]:checked').value === 'files';
  const showError = (message) => {
    error.textContent = message;
    error.hidden = !message;
  };
  const updateMode = () => {
    sourceForm.querySelectorAll('[data-source-panel]').forEach((panel) => {
      const active = (panel.dataset.sourcePanel === 'files') === fileMode();
      panel.hidden = !active;
      panel.querySelectorAll('input').forEach((input) => { input.disabled = !active; });
    });
    picker.required = fileMode() && !files.length;
    showError('');
  };
  modes.forEach((mode) => mode.addEventListener('change', updateMode));
  const renderFiles = () => {
    list.replaceChildren();
    files.forEach((file, index) => {
      const row = document.createElement('li');
      const label = document.createElement('span');
      const name = document.createElement('strong');
      name.textContent = file.name;
      const size = document.createElement('small');
      size.textContent = file.size < 1024 * 1024 ? `${Math.ceil(file.size / 1024)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
      label.append(name, size);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'button small secondary';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${file.name}`);
      remove.disabled = busy;
      remove.addEventListener('click', () => {
        files.splice(index, 1);
        renderFiles();
      });
      row.append(label, remove);
      list.append(row);
    });
    picker.required = fileMode() && !files.length;
  };
  picker.addEventListener('change', () => {
    const selected = [...files, ...picker.files];
    picker.value = '';
    if (selected.length > 10 ||
        selected.reduce((sum, file) => sum + file.size, 0) > 100 * 1024 * 1024) {
      showError('Choose up to 10 files, at most 100 MB in total. A single file can use the full 100 MB.');
      return;
    }
    if (selected.some((file) => !/\.(png|jpe?g|webp|pdf)$/i.test(file.name) || !file.size)) {
      showError('Choose non-empty PNG, JPG, WebP or PDF files.');
      return;
    }
    files = selected;
    showError('');
    renderFiles();
  });
  const encode = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name: file.name, content: reader.result.split(',')[1]});
    reader.onerror = () => reject(new Error(`Could not read ${file.name}. Select it again.`));
    reader.readAsDataURL(file);
  });
  sourceForm.addEventListener('submit', async (event) => {
    if (!fileMode()) return;
    event.preventDefault();
    if (busy) return;
    if (!files.length) { showError('Choose at least one image or PDF.'); return; }
    busy = true;
    button.disabled = true;
    picker.disabled = true;
    modes.forEach((mode) => { mode.disabled = true; });
    renderFiles();
    showError('');
    status.textContent = 'Uploading and checking your source files…';
    status.hidden = false;
    try {
      const payload = {
        duration: Number(sourceForm.elements.duration.value),
        description: sourceForm.elements.description.value,
        files: await Promise.all(files.map(encode)),
      };
      const send = () => fetch('/jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': sourceForm.elements.csrf.value},
        body: JSON.stringify(payload),
      });
      let response = await send();
      let result = await response.json().catch(() => ({}));
      if (response.status === 409 && result.confirmation_required) {
        status.hidden = true;
        if (!await confirmPages(result.pdf_pages)) return;
        status.textContent = 'Preparing your preview…';
        status.hidden = false;
        payload.confirm_pdf_pages = true;
        response = await send();
        result = await response.json().catch(() => ({}));
      }
      if (!response.ok) throw new Error(result.error || result.detail || 'Upload failed. Reload the page or try again.');
      location.assign(result.location);
    } catch (failure) {
      showError(failure.message);
    } finally {
      busy = false;
      button.disabled = false;
      picker.disabled = false;
      modes.forEach((mode) => { mode.disabled = false; });
      status.hidden = true;
      renderFiles();
      button.focus();
    }
  });
  updateMode();
}
