const updateLinks = document.querySelectorAll('[data-update-link]');
if (updateLinks.length) {
  async function refreshUpdateNotice() {
    try {
      const response = await fetch('/admin/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok || response.redirected) return;
      const status = await response.json();
      updateLinks.forEach((link) => {
        const notice = status.busy ? 'Updating…' : status.available?.length ? 'Update available' : '';
        const label = link.dataset.updateLabel;
        link.textContent = label ? label + (notice ? ' · ' + notice : '') : notice || 'Updates';
      });
    } catch { /* A failed check must not interrupt normal work. */ }
    setTimeout(refreshUpdateNotice, 60000);
  }
  refreshUpdateNotice();
}
