import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';

test('home update notice follows releases, progress and recovery without injecting HTML', async () => {
  const heading = {}, description = {}, action = {};
  const notice = {hidden: true, querySelector: (key) => ({
    h2: heading, '[data-update-description]': description, '[data-update-action]': action,
  }[key])};
  const links = [{dataset: {updateLabel: 'Backstage'}}, {dataset: {}}];
  let next, status = {available: [], busy: false}, failure = '';
  const context = {
    document: {querySelectorAll: () => links, querySelector: () => notice},
    AbortSignal,
    fetch: async () => {
      if (failure === 'network') throw Error('offline');
      return {ok: failure !== 'http', redirected: failure === 'redirect', json: async () => status};
    },
    setTimeout: (callback, delay) => { assert.equal(delay, 60000); next = callback; },
  };
  await runInNewContext(readFileSync(new URL('../app/static/update-notice.js', import.meta.url), 'utf8'), context);
  assert.equal(notice.hidden, true);
  assert.equal(links[1].textContent, 'Updates');

  status.available = [{version: '0.1.4'}, {version: '0.1.5<script>bad()</script>'}];
  await next();
  assert.equal(notice.hidden, false);
  assert.equal(heading.textContent, 'Update available');
  assert.equal(description.textContent, 'Kiehnema 0.1.5<script>bad()</script> is ready to install.');
  assert.equal(action.textContent, 'Review update ↗');
  assert.equal(links[0].textContent, 'Backstage · Update available');

  status = {available: [], busy: true};
  await next();
  assert.equal(notice.hidden, false);
  assert.equal(heading.textContent, 'Update in progress');
  assert.equal(action.textContent, 'View progress ↗');
  assert.equal(links[0].textContent, 'Backstage · Updating…');

  status.busy = false;
  await next();
  assert.equal(notice.hidden, true);
  assert.equal(links[0].textContent, 'Backstage');
  for (failure of ['http', 'redirect', 'network']) {
    const refresh = next;
    next = null;
    await refresh();
    assert.equal(typeof next, 'function');
  }
  failure = '';
  status.available = [{version: '0.1.6'}];
  await next();
  assert.equal(notice.hidden, false);
});
