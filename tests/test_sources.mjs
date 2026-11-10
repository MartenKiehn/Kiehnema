import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';

for (const action of ['cancel', 'confirm', 'escape']) {
  const accept = action === 'confirm';
  test(`large PDF confirmation: ${action}`, async () => {
    const node = (tag) => ({
      tag, children: [],
      events: {}, addEventListener(name, callback) { this.events[name] = callback; },
      append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
      setAttribute() {}, focus() { this.focused = true; },
    });
    const picker = node(), list = node(), error = node(), status = node(), button = node();
    const modes = [node(), node()];
    const showcases = ['url', 'files'].map((mode) => ({dataset: {sourceShowcase: mode}}));
    const fields = {
      '#source-files': picker, '[data-source-list]': list, '[data-source-error]': error,
      '[data-source-status]': status, 'button[type="submit"]': button,
      '[name="source_mode"]:checked': {value: 'url'},
    };
    const form = {...node(), querySelector: (selector) => fields[selector],
      querySelectorAll: (selector) => selector === '[name="source_mode"]' ? modes : [],
      elements: {csrf: {value: 'token'}, duration: {value: '30'}, description: {value: 'A brief'}}};
    const requests = [], prompts = [], destinations = [];
    const cancel = node(), proceed = node(), pageCount = node();
    const dialog = {...node(), returnValue: 'confirm',
      querySelector: (selector) => ({'[data-confirm-cancel]': cancel, '[data-confirm-continue]': proceed, '[data-confirm-pages]': pageCount}[selector]),
      close(value = '') { this.returnValue = value; this.events.close(); },
      showModal() {
        assert.equal(this.returnValue, '');
        prompts.push(pageCount.textContent);
        if (action === 'escape') this.close();
        else (accept ? proceed : cancel).events.click();
      },
    };
    const previews = new Map();
    let previewId = 0;
    runInNewContext(readFileSync(new URL('../app/static/sources.js', import.meta.url), 'utf8'), {
      URL: {
        createObjectURL(file) { const url = `blob:test-${++previewId}`; previews.set(url, file); return url; },
        revokeObjectURL(url) { assert.ok(previews.delete(url)); },
      },
      document: {querySelector: (selector) => selector === '#source-confirm' ? dialog : form,
        querySelectorAll: (selector) => {
          assert.equal(selector, '[data-source-showcase]');
          return showcases;
        }, createElement: node},
      FileReader: class { readAsDataURL() { this.result = 'data:application/pdf;base64,cGRm'; this.onload(); } },
      location: {assign: (url) => destinations.push(url)},
      fetch: async (url, options) => {
        assert.equal(url, '/jobs');
        assert.equal(options.headers['X-CSRF-Token'], 'token');
        requests.push(JSON.parse(options.body));
        return requests.length === 1
          ? {status: 409, ok: false, json: async () => ({confirmation_required: true, pdf_pages: 42})}
          : {status: 201, ok: true, json: async () => ({location: '/jobs/created'})};
      },
    });
    assert.deepEqual(showcases.map((panel) => panel.hidden), [false, true]);
    fields['[name="source_mode"]:checked'].value = 'files';
    modes[1].events.change();
    assert.deepEqual(showcases.map((panel) => panel.hidden), [true, false]);
    const images = ['beach.webp', 'portrait.PNG', 'logo.jpg'].map((name) => ({name, size: 1024}));
    picker.files = images;
    picker.events.change();
    assert.equal(list.children.length, 3);
    list.children.forEach((row, index) => {
      assert.equal(row.children[0].tag, 'img');
      assert.equal(previews.get(row.children[0].src), images[index]);
      assert.equal(row.children[0].alt, '');
      assert.equal(row.children[1].children[0].textContent, images[index].name);
    });
    list.children[0].children[0].events.error();
    assert.equal(list.children[0].children[0].hidden, true);
    while (list.children.length) list.children[0].children.at(-1).events.click();
    assert.equal(previews.size, 0);
    assert.equal(picker.required, true);
    picker.files = [{name: 'guide.pdf', size: 100 * 1024 * 1024}];
    picker.events.change();
    assert.equal(list.children[0].children[0].tag, 'span');
    await form.events.submit({preventDefault() {}});
    assert.equal(prompts.length, 1);
    assert.equal(prompts[0], 42);
    assert.equal(requests.length, accept ? 2 : 1);
    assert.equal(requests[0].confirm_pdf_pages, undefined);
    if (accept) {
      assert.equal(requests[1].confirm_pdf_pages, true);
      assert.deepEqual(requests[1].files, requests[0].files);
    }
    assert.deepEqual(destinations, accept ? ['/jobs/created'] : []);
    assert.equal(button.disabled, false);
    assert.equal(button.focused, true);
    assert.equal(picker.disabled, false);
    assert.equal(picker.required, false); // Selection survives cancelling the prompt.
    assert.equal(status.hidden, true);
    assert.equal(error.hidden, true);
    fields['[name="source_mode"]:checked'].value = 'url';
    modes[0].events.change();
    assert.deepEqual(showcases.map((panel) => panel.hidden), [false, true]);
  });
}
