"""
Inline HTML for the admin dashboard. Kept as a Python string to avoid
shipping a static file the runtime image has to special-case.

Vanilla HTML/JS — no frameworks, no build step. Polls the JSON
endpoints at /admin/api/* every 2 seconds and renders the result.
"""

PORTAL_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Casual-SST — Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg:#0e1116; --panel:#161b22; --panel-2:#1c222b; --border:#2a313c;
      --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff; --good:#56d364;
      --warn:#d29922; --bad:#f85149;
    }
    * { box-sizing: border-box; }
    body { margin:0; font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;
           background:var(--bg); color:var(--text); }
    header { padding:10px 18px; background:var(--panel); border-bottom:1px solid var(--border);
             display:flex; align-items:center; gap:12px; position:sticky; top:0; z-index:10; }
    header .name { font-weight:600; font-size:14px; }
    header .tag { color:var(--muted); font-size:12px; }
    header .spacer { flex:1; }
    header input { background:var(--panel-2); color:var(--text); border:1px solid var(--border);
                   border-radius:6px; padding:5px 9px; font:inherit; min-width:220px; }
    header button { background:var(--accent); color:#0e1116; border:none; border-radius:6px;
                    padding:6px 12px; cursor:pointer; font-weight:600; }
    main { max-width:1280px; margin:0 auto; padding:14px; display:grid; gap:14px;
           grid-template-columns: repeat(auto-fit, minmax(380px,1fr)); }
    .card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
            padding:12px 14px; min-height:120px; }
    .card h2 { margin:0 0 8px; font-size:11px; text-transform:uppercase;
               letter-spacing:.1em; color:var(--muted); }
    .kv { display:grid; grid-template-columns:auto 1fr; gap:4px 14px; font-family:ui-monospace,monospace;
          font-size:12px; }
    .kv .k { color:var(--muted); }
    .kv .v { color:var(--text); text-align:right; }
    .row { padding:6px 8px; background:var(--panel-2); border:1px solid var(--border);
           border-radius:6px; margin-bottom:5px; font-family:ui-monospace,monospace; font-size:12px;
           display:grid; grid-template-columns:auto 60px 1fr; gap:8px; align-items:baseline; }
    .row .ts { color:var(--muted); }
    .row .pill { color:var(--accent); }
    .log { padding:3px 8px; background:var(--panel-2); border:1px solid var(--border);
           border-radius:4px; margin-bottom:3px; font-family:ui-monospace,monospace; font-size:11px;
           color:var(--muted); white-space:pre-wrap; word-break:break-word; }
    .log.WARNING { border-left:3px solid var(--warn); }
    .log.ERROR, .log.CRITICAL { border-left:3px solid var(--bad); color:var(--text); }
    .pill { background:var(--panel-2); border:1px solid var(--border); border-radius:999px;
            padding:1px 8px; font-size:10px; color:var(--accent); display:inline-block; }
    pre.config { background:var(--panel-2); border:1px solid var(--border); border-radius:6px;
                 padding:10px; max-height:380px; overflow:auto; font-size:11px;
                 color:var(--text); margin:0; }
    .scroll { max-height:340px; overflow:auto; }
    .dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:5px; }
    .dot.connected { background:var(--good); }
    .dot.disconnected { background:var(--bad); }
    .muted { color:var(--muted); }
    .empty { color:var(--muted); font-style:italic; font-size:12px; padding:18px;
             text-align:center; }
  </style>
</head>
<body>

<header>
  <span class="name">● Casual-SST</span>
  <span class="tag">admin</span>
  <span class="spacer"></span>
  <input id="token" placeholder="X-Admin-Token" autocomplete="off">
  <button id="apply">Save</button>
  <button id="reset" title="Zero counters (keeps recent_finals)">Reset counters</button>
  <span class="muted" id="poll-status">…</span>
</header>

<main>
  <section class="card">
    <h2>Status</h2>
    <div id="status" class="kv"></div>
  </section>

  <section class="card">
    <h2>Backends</h2>
    <div id="backends" class="kv"></div>
  </section>

  <section class="card">
    <h2>Language distribution</h2>
    <div id="langs" class="kv"></div>
  </section>

  <section class="card" style="grid-column: 1/-1;">
    <h2>Active meetings</h2>
    <div id="meetings" class="scroll"></div>
  </section>

  <section class="card" style="grid-column: 1/-1;">
    <h2>Recent finals</h2>
    <div id="finals" class="scroll"></div>
  </section>

  <section class="card" style="grid-column: 1/-1;">
    <h2>Logs</h2>
    <div id="logs" class="scroll"></div>
  </section>

  <section class="card" style="grid-column: 1/-1;">
    <h2>Resolved config</h2>
    <pre class="config" id="config">…</pre>
  </section>
</main>

<script>
  const $ = id => document.getElementById(id);
  const tokenInput = $('token');
  // Auto-fill from the Basic Auth password the browser cached when
  // it prompted for /admin/ — Basic credentials are accessible via
  // performance entries on most modern browsers, but the simplest
  // path is just letting the user paste once and persist in localStorage.
  tokenInput.value = localStorage.getItem('casual_admin_token') || '';

  $('apply').onclick = () => {
    localStorage.setItem('casual_admin_token', tokenInput.value.trim());
    poll();
  };
  $('reset').onclick = async () => {
    await api('/admin/api/reset-metrics', 'POST');
    poll();
  };

  async function api(path, method='GET') {
    const r = await fetch(path, {
      method,
      headers: { 'X-Admin-Token': tokenInput.value.trim() },
    });
    if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
    return r.json();
  }

  function fmtUptime(s) {
    const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
    if (h) return `${h}h ${m}m ${sec}s`;
    if (m) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  function kv(target, obj, formatter=String) {
    const el = $(target);
    if (!obj || Object.keys(obj).length === 0) {
      el.innerHTML = '<div class="empty">no data</div>';
      return;
    }
    el.innerHTML = Object.entries(obj).map(([k,v]) =>
      `<div class="k">${k}</div><div class="v">${formatter(v)}</div>`).join('');
  }

  function renderMeetings(data) {
    const el = $('meetings');
    if (!data.count) { el.innerHTML = '<div class="empty">no active meetings</div>'; return; }
    el.innerHTML = data.active.map(m => `
      <div class="row" style="display:block; padding:8px 10px;">
        <div style="margin-bottom:6px;">
          <span class="dot ${m.connected ? 'connected':'disconnected'}"></span>
          <code>${m.meeting_id}</code>
          <span class="muted"> · ${m.participants.length} participants</span>
        </div>
        ${m.participants.map(p => `
          <div class="kv" style="margin-left:18px; margin-top:4px;">
            <div class="k">participant_id</div><div class="v">${p.participant_id}</div>
            <div class="k">active_lang / mode</div>
            <div class="v">${p.active_lang} <span class="pill">${p.mode}</span></div>
            <div class="k">header_lang</div><div class="v">${p.header_lang}</div>
            <div class="k">finals / dominant</div>
            <div class="v">${p.finals_total} · ${p.dominant_lang || '—'}${p.locked_strong ? ' <span class="pill">LOCKED</span>':''}</div>
            <div class="k">buffer</div><div class="v">${p.buffer_seconds.toFixed(2)}s ${p.is_transcribing ? ' <span class="pill">tx</span>':''} ${p.long_silence ? ' <span class="pill">silence</span>':''}</div>
            <div class="k">avg confidence</div><div class="v">${p.avg_confidence.toFixed(2)}</div>
          </div>
        `).join('')}
      </div>
    `).join('');
  }

  function renderFinals(list) {
    const el = $('finals');
    if (!list.length) { el.innerHTML = '<div class="empty">no recent finals</div>'; return; }
    el.innerHTML = list.slice().reverse().map(f => {
      const d = new Date(f.ts);
      const t = d.toLocaleTimeString([], { hour12: false });
      return `<div class="row">
        <span class="ts">${t}</span>
        <span class="pill">${f.language || '—'}</span>
        <span>${escapeHtml(f.text)}</span>
      </div>`;
    }).join('');
  }

  function renderLogs(entries) {
    const el = $('logs');
    if (!entries.length) { el.innerHTML = '<div class="empty">no log entries</div>'; return; }
    el.innerHTML = entries.slice().reverse().slice(0, 200).map(e => {
      const t = new Date(e.ts * 1000).toLocaleTimeString([], { hour12:false });
      return `<div class="log ${e.level}"><span class="muted">${t} ${e.level} ${e.logger}</span>  ${escapeHtml(e.message)}</div>`;
    }).join('');
  }

  function escapeHtml(s) {
    return String(s||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
  }

  async function poll() {
    const status = $('poll-status');
    try {
      const [s, m, l, c] = await Promise.all([
        api('/admin/api/status'),
        api('/admin/api/meetings'),
        api('/admin/api/logs?n=200'),
        api('/admin/api/config'),
      ]);
      const x = s.metrics;
      kv('status', {
        uptime: fmtUptime(x.uptime_seconds),
        total_meetings: x.total_meetings,
        total_events: x.total_events,
        total_finals: x.total_finals,
        total_interims: x.total_interims,
        language_changes: x.total_language_changes,
      });
      kv('backends',
        Object.fromEntries(Object.entries(x.backend_calls).map(([k,v]) =>
          [k, `${v} calls · ${x.backend_avg_ms[k]||0} ms avg${x.backend_errors[k]?' · '+x.backend_errors[k]+' err':''}`])));
      kv('langs', x.lang_distribution);
      renderMeetings(m);
      renderFinals(x.recent_finals);
      renderLogs(l.entries);
      $('config').textContent = JSON.stringify(c, null, 2);
      status.textContent = '✓ ' + new Date().toLocaleTimeString();
      status.style.color = 'var(--good)';
    } catch (err) {
      status.textContent = '✗ ' + err.message;
      status.style.color = 'var(--bad)';
    }
  }

  poll();
  setInterval(poll, 2000);
</script>

</body>
</html>
"""
