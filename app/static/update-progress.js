const panel = document.querySelector('[data-update-progress]');
if (panel) {
  const message = panel.querySelector('[data-update-message]');
  const bar = panel.querySelector('[data-update-bar]');
  const back = panel.querySelector('[data-update-return]');
  async function poll() {
    let finished = false;
    try {
      const response = await fetch('/_update/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('unavailable');
      const status = await response.json();
      if (Date.now() / 1000 - status.updated_at > 30) {
        message.textContent = 'The update service is not responding. Reconnecting…';
        bar.removeAttribute('value');
      } else {
        message.textContent = status.message;
        if (status.total > 0 && status.received >= 0) {
          bar.max = status.total;
          bar.value = Math.min(status.received, status.total);
          message.textContent += ` ${Math.round(status.received / 1048576)} / ${Math.round(status.total / 1048576)} MB`;
        } else bar.removeAttribute('value');
        finished = (['succeeded', 'failed', 'cancelled', 'idle'].includes(status.phase) && !status.maintenance) || status.needs_repair;
        bar.hidden = finished;
        if (finished && !status.maintenance) back.hidden = false;
        if (status.needs_repair) {
          message.textContent += ' Recovery needs help from the server operator. Your backup has been retained.';
        }
      }
    } catch {
      message.textContent = 'Connection interrupted. Reconnecting to the update service…';
      bar.removeAttribute('value');
    }
    if (!finished) setTimeout(poll, 2000);
  }
  poll();
}

const checking = document.querySelector('[data-update-checking]');
if (checking) {
  async function waitForCheck() {
    try {
      const response = await fetch('/admin/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (response.ok && !(await response.json()).checking) {
        location.reload();
        return;
      }
    } catch { /* A transient disconnect can be retried. */ }
    setTimeout(waitForCheck, 2000);
  }
  setTimeout(waitForCheck, 2000);
}
