// Admin pages: ticking clocks, "updated N s ago", the pending-only filter and the C/E/S keyboard shortcuts of the Falácias queue.
(function () {
  var PREFS = 'lg_admin_prefs', prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(PREFS) || '{}') || {}; } catch (e) { prefs = {}; }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function elapsed(s) {
    s = Math.max(0, Math.floor(s));
    if (s < 60) return s + ' s';
    if (s < 3600) return Math.floor(s / 60) + ' min ' + pad(s % 60) + ' s';
    return Math.floor(s / 3600) + ' h ' + pad(Math.floor(s % 3600 / 60)) + ' min';
  }
  function ago(s) {
    s = Math.max(0, Math.floor(s));
    return s < 60 ? 'há ' + s + ' s' : s < 3600 ? 'há ' + Math.floor(s / 60) + ' min' : 'há ' + Math.floor(s / 3600) + ' h';
  }

  // ---- clocks. The server only sends a start time; the browser's clock is used for both ends, so skew does not matter much
  var loaded = Date.now(), lastUpdate = Date.now();
  function tick() {
    document.querySelectorAll('time.elapsed[data-since]').forEach(function (t) {
      t.textContent = elapsed((Date.now() - Number(t.getAttribute('data-since'))) / 1000);
    });
    document.querySelectorAll('time.age[data-us]').forEach(function (t) {
      t.textContent = ago((Date.now() - Number(t.getAttribute('data-us')) / 1000) / 1000);
    });
    var u = document.getElementById('updated');
    if (u) { var s = Math.floor((Date.now() - lastUpdate) / 1000); u.textContent = s < 3 ? 'atualizado agora' : 'atualizado há ' + s + ' s'; }
  }
  document.body.addEventListener('htmx:afterSwap', function (e) {
    var p = e.detail && e.detail.pathInfo && e.detail.pathInfo.requestPath;
    if (p && p.indexOf('/admin/live') === 0) lastUpdate = Date.now();
  });
  setInterval(tick, 1000);
  tick();

  // ---- Falácias queue
  var queue = document.getElementById('queue');
  if (!queue) return;
  var box = document.getElementById('onlypending');
  function applyFilter() {
    queue.classList.toggle('only-pending', !!box.checked);
  }
  if (box) {
    if (prefs.onlyPending === false) box.checked = false;
    box.addEventListener('change', function () {
      prefs.onlyPending = box.checked;
      try { localStorage.setItem(PREFS, JSON.stringify(prefs)); } catch (e) { /* private mode */ }
      applyFilter();
      mark();
    });
    applyFilter();
  }

  // the highlighted item = the OLDEST pending one (new pairs only ever arrive at the bottom, so it never moves under the admin's hand)
  var VERDICT = { c: 'correct', e: 'wrong', s: 'spam' };
  function mark() {
    var first = queue.querySelector('.qitem.pending');
    queue.querySelectorAll('.qitem.current').forEach(function (x) { if (x !== first) x.classList.remove('current'); });
    if (first && !first.classList.contains('current')) {
      first.classList.add('current');
      first.scrollIntoView({ block: 'nearest' });
    }
  }
  new MutationObserver(mark).observe(queue, { childList: true });
  mark();

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target && e.target.isContentEditable)) return;
    var verdict = VERDICT[(e.key || '').toLowerCase()];
    if (!verdict) return;
    if (document.querySelector('.vform.htmx-request, .qitem.htmx-request')) return;     // a verdict is still on its way: never double-send
    var item = queue.querySelector('.qitem.pending');
    if (!item) return;
    var btn = item.querySelector(e.shiftKey ? '.vrow.group .vbtn.' + verdict : '.vrow:not(.group) .vbtn.' + verdict);
    if (!btn) return;
    e.preventDefault();
    btn.click();
  });
})();
