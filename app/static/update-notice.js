const updateLink = document.querySelector('[data-update-link]');
if (updateLink) {
  async function refreshUpdateNotice() {
    try {
      const response = await fetch('/admin/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok || response.redirected) return;
      const status = await response.json();
      updateLink.textContent = status.busy ? 'Updating…' : status.available?.length ? 'Update available' : 'Updates';
    } catch { /* A failed check must not interrupt normal work. */ }
    setTimeout(refreshUpdateNotice, 60000);
  }
  refreshUpdateNotice();
}
