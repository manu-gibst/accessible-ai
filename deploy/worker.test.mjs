import assert from 'node:assert/strict';
import { test, mock } from 'node:test';
import worker from './worker.mjs';

test('static page stays available without a desktop connector', async () => {
  const env = { ASSETS: { fetch: async () => new Response('<html>Upload</html>') } };
  const response = await worker.fetch(new Request('https://manu.wiphk.com/'), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Referrer-Policy'), 'no-referrer');
  assert.match(await response.text(), /Upload/);
});

test('websocket route rejects ordinary HTTP', async () => {
  const response = await worker.fetch(new Request('https://manu.wiphk.com/ws'), {});
  assert.equal(response.status, 426);
});

test('websocket route refuses unconfigured or arbitrary upstreams', async () => {
  for (const origin of [undefined, 'http://127.0.0.1', 'https://example.com', 'https://valid.trycloudflare.com.evil.com']) {
    const response = await worker.fetch(new Request('https://manu.wiphk.com/ws', { headers: { Upgrade: 'websocket' } }), { DESKTOP_ORIGIN: origin });
    assert.equal(response.status, 503);
  }
});

test('websocket route proxies the upgrade without buffering or forwarding cookies', async () => {
  const upgrade = { status: 101, webSocket: {} };
  const upstream = mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(String(url), 'https://desktop-test.trycloudflare.com/ws');
    assert.equal(options.headers.get('Upgrade'), 'websocket');
    assert.equal(options.headers.get('Cookie'), null);
    assert.equal(options.headers.get('Host'), 'desktop-test.trycloudflare.com');
    return upgrade;
  });
  try {
    const response = await worker.fetch(new Request('https://manu.wiphk.com/ws', { headers: { Upgrade: 'websocket', Cookie: 'private=1' } }), { DESKTOP_ORIGIN: 'https://desktop-test.trycloudflare.com' });
    assert.equal(response, upgrade);
    assert.equal(upstream.mock.calls.length, 1);
  } finally {
    upstream.mock.restore();
  }
});
