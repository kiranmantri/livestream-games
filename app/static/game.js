// Game-screen preferences (kept in this browser): hide the stream+chat, others' cards as grid (join order) or list (most marked first).
// The classes live on the page container, which the 5 s poll never replaces, so they survive every re-render.
(function () {
  var root = document.querySelector('.game');
  if (!root) return;
  var KEY = 'lg_prefs', prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { prefs = {}; }
  // phones: video and chat start hidden (the board comes first); a saved choice always wins
  if (prefs.hideLive === undefined && window.matchMedia && window.matchMedia('(max-width: 760px)').matches) prefs.hideLive = true;

  function apply() {
    root.classList.toggle('hide-live', !!prefs.hideLive);
    root.classList.toggle('view-list', prefs.view === 'list');
    // a hidden <iframe> keeps playing: unload it while hidden, restore it when shown again
    root.querySelectorAll('.leftcol iframe').forEach(function (f) {
      if (prefs.hideLive) {
        if (f.src && f.src !== 'about:blank') { f.dataset.src = f.src; f.src = 'about:blank'; }
      } else if (f.dataset.src) { f.src = f.dataset.src; delete f.dataset.src; }
    });
    root.querySelectorAll('[data-pref]').forEach(function (b) {
      var kv = b.dataset.pref.split(':'), on;
      if (kv[0] === 'live') on = !!prefs.hideLive;
      else on = (prefs[kv[0]] || 'grid') === kv[1];
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  root.addEventListener('click', function (e) {
    var b = e.target.closest('[data-pref]');
    if (!b) return;
    var kv = b.dataset.pref.split(':');
    if (kv[0] === 'live') prefs.hideLive = !prefs.hideLive; else prefs[kv[0]] = kv[1];
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (err) { /* private mode: works for this visit */ }
    apply();
  });
  // Taps: flip my cell at once, then send the taps ONE AT A TIME in the order they were made. htmx's own queue cannot be used: after
  // the first answer the buttons it queued for have been replaced, and it silently drops them. Each answer (cell + count + lines)
  // replaces the live cell, so the screen always ends equal to the server.
  var chain = Promise.resolve();
  root.addEventListener('click', function (e) {
    var c = e.target.closest('.cardbox.mine button.cell');
    if (!c || !c.id) return;
    e.stopPropagation();                                  // htmx must not also send this tap
    var id = c.id;
    c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    c.classList.add('pending');
    chain = chain.then(function () {
      return htmx.ajax('POST', '/mark/' + id.replace('cell-', ''), { target: '#' + id, swap: 'outerHTML' });
    }).catch(function () { location.reload(); });
  }, true);
  document.body.addEventListener('htmx:sendError', function () { location.reload(); });   // offline: resync with the server
  apply();
})();
