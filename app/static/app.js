document.addEventListener('submit', (event) => {
  if (event.defaultPrevented) return;
  const form = event.target;
  if (form.dataset.submitting) {
    event.preventDefault();
    return;
  }
  // Preserve the chosen review action when its submit button becomes disabled.
  const button = event.submitter;
  if (button?.name) {
    const action = document.createElement('input');
    action.type = 'hidden';
    action.name = button.name;
    action.value = button.value;
    action.dataset.submittedAction = '';
    form.append(action);
  }
  form.dataset.submitting = 'true';
  form.querySelectorAll('button[type="submit"]:enabled').forEach((button) => {
    button.dataset.submitted = '';
    button.disabled = true;
  });
});

// Back/forward cache restores the original DOM, including disabled buttons.
addEventListener('pageshow', () => {
  document.querySelectorAll('[data-submitting]').forEach((form) => delete form.dataset.submitting);
  document.querySelectorAll('[data-submitted-action]').forEach((input) => input.remove());
  document.querySelectorAll('[data-submitted]').forEach((button) => {
    button.disabled = false;
    delete button.dataset.submitted;
  });
});

// Moving preserved cards during polling can reset their nested scroll positions.
let insightScrollPositions = [];
document.addEventListener('htmx:beforeSwap', (event) => {
  if (event.detail.target.id !== 'progress-status') return;
  insightScrollPositions = [...document.querySelectorAll('#progress-status .insight-scroll')]
    .map((element) => [element.id, element.scrollTop]);
});
document.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target.id !== 'progress-status') return;
  for (const [id, top] of insightScrollPositions) {
    const element = document.getElementById(id);
    if (element) element.scrollTop = top;
  }
  insightScrollPositions = [];
});
