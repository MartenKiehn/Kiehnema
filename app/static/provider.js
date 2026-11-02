const form = document.querySelector('[data-provider-form]');
if (form) {
  const provider = form.elements.provider;
  const auth = form.elements.auth;
  const test = form.querySelector('[data-provider-test]');
  const save = form.querySelector('[data-provider-save]');
  const status = form.querySelector('[data-provider-test-status]');
  let revision = 0;
  let expiry;
  const reset = () => {
    clearTimeout(expiry);
    revision += 1;
    save.hidden = true;
    save.disabled = true;
    status.textContent = 'Test the connection before saving these settings.';
    delete status.dataset.state;
  };
  const update = () => {
    for (const section of form.querySelectorAll('[data-provider-fields]')) {
      const show = section.dataset.providerFields === (provider.value === 'bedrock' ? 'bedrock' : 'api');
      section.hidden = !show;
      for (const field of section.querySelectorAll('input, select')) field.disabled = !show;
    }
    const keys = form.querySelector('[data-aws-keys]');
    keys.hidden = provider.value !== 'bedrock' || auth.value === 'environment';
    for (const field of keys.querySelectorAll('input')) field.disabled = keys.hidden;
    const reuse = form.dataset.credentialsSaved === 'true' && provider.value === form.dataset.savedProvider &&
      (provider.value !== 'bedrock' || auth.value === form.dataset.savedAuth);
    for (const name of ['api_key', 'access_key', 'secret_key']) {
      const field = form.elements[name];
      field.required = !field.disabled && !reuse;
    }
  };
  form.addEventListener('input', reset);
  form.addEventListener('change', reset);
  addEventListener('pageshow', reset);
  provider.addEventListener('change', update);
  auth.addEventListener('change', update);
  test.addEventListener('click', async () => {
    reset();
    if (!form.reportValidity()) return;
    const current = revision;
    const body = new URLSearchParams(new FormData(form));
    test.disabled = true;
    test.textContent = 'Testing connection…';
    status.textContent = 'Waiting for a response from the selected model…';
    try {
      const response = await fetch(form.action + '/test', {method: 'POST', body});
      const result = await response.json();
      if (current !== revision) return;
      if (!response.ok) throw new Error(result.error || 'Connection test failed.');
      status.textContent = result.message;
      status.dataset.state = 'success';
      save.hidden = false;
      save.disabled = false;
      expiry = setTimeout(() => {
        reset();
        status.textContent = 'Connection test expired. Test again before saving.';
      }, 300000);
    } catch (error) {
      if (current !== revision) return;
      status.textContent = error instanceof SyntaxError ? 'The test could not finish. Reload the page and try again.' : error.message;
      status.dataset.state = 'error';
    } finally {
      test.disabled = false;
      test.textContent = 'Test connection';
    }
  });
  update();
  reset();
}
