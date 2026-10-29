const form = document.querySelector('[data-provider-form]');
if (form) {
  const provider = form.elements.provider;
  const auth = form.elements.auth;
  const update = () => {
    for (const section of form.querySelectorAll('[data-provider-fields]')) {
      const show = section.dataset.providerFields === (provider.value === 'bedrock' ? 'bedrock' : 'api');
      section.hidden = !show;
      for (const field of section.querySelectorAll('input, select')) field.disabled = !show;
    }
    const keys = form.querySelector('[data-aws-keys]');
    keys.hidden = provider.value !== 'bedrock' || auth.value === 'environment';
    for (const field of keys.querySelectorAll('input')) field.disabled = keys.hidden;
  };
  provider.addEventListener('change', update);
  auth.addEventListener('change', update);
  update();
}
