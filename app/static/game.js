// Game-screen preferences (kept in this browser): hide the stream+chat, others' cards as grid (join order) or list (most marked first).
// The classes live on the page container, which the 5 s poll never replaces, so they survive every re-render.
(function () {
  var root = document.querySelector('.game');
  if (!root) return;
  var KEY = 'lg_prefs', prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { prefs = {}; }

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
  apply();
})();
