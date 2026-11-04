const updateLinks = document.querySelectorAll('[data-update-link]');
const updateNotice = document.querySelector('[data-update-notice]');
if (updateLinks.length) {
  async function refreshUpdateNotice() {
    try {
      const response = await fetch('/admin/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (response.ok && !response.redirected) {
        const status = await response.json();
        updateLinks.forEach((link) => {
          const notice = status.busy ? 'Updating…' : status.available?.length ? 'Update available' : '';
          const label = link.dataset.updateLabel;
          link.textContent = label ? label + (notice ? ' · ' + notice : '') : notice || 'Updates';
        });
        if (updateNotice) {
          updateNotice.hidden = !status.busy && !status.available?.length;
          if (!updateNotice.hidden) {
            updateNotice.querySelector('h2').textContent = status.busy ? 'Update in progress' : 'Update available';
            updateNotice.querySelector('[data-update-description]').textContent = status.busy
              ? 'Kiehnema is being updated. Follow the progress in Backstage.'
              : `Kiehnema ${status.available.at(-1).version} is ready to install.`;
            updateNotice.querySelector('[data-update-action]').textContent = status.busy ? 'View progress ↗' : 'Review update ↗';
          }
        }
      }
    } catch { /* A failed check must not interrupt normal work. */ }
    setTimeout(refreshUpdateNotice, 60000);
  }
  refreshUpdateNotice();
}
