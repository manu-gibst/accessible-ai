export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/ws') {
      if (request.method !== 'GET' || request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
        return new Response('WebSocket upgrade required.', { status: 426 });
      }
      if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(env.DESKTOP_ORIGIN || '')) {
        return new Response('Desktop connector is offline.', { status: 503 });
      }
      const upstream = new URL('/ws', env.DESKTOP_ORIGIN);
      const headers = new Headers(request.headers);
      headers.set('Host', upstream.host);
      headers.delete('Cookie');
      try {
        return await fetch(upstream, { method: 'GET', headers, redirect: 'manual' });
      } catch {
        return new Response('Desktop connector is offline.', { status: 503 });
      }
    }
    const response = await env.ASSETS.fetch(request);
    const headers = new Headers(response.headers);
    headers.set('Cache-Control', 'no-store');
    headers.set('Referrer-Policy', 'no-referrer');
    headers.set('X-Content-Type-Options', 'nosniff');
    headers.set('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'");
    return new Response(response.body, { status: response.status, headers });
  },
};
