// Game-screen preferences (kept in this browser): hide the stream+chat, others' cards as grid (join order) or list (most marked first).
// The classes live on the page container, which the 5 s poll never replaces, so they survive every re-render.
(function () {
  var root = document.querySelector('.game');
  if (!root) return;
  var KEY = 'lg_prefs', prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { prefs = {}; }
  // video and chat start hidden on every screen (the board comes first); a saved choice always wins
  if (prefs.hideLive === undefined) prefs.hideLive = true;

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

  // Others' cards: while the mouse is over one, a copy 3x as WIDE floats above it (the box is a size container, so the
  // container-query fonts grow too and phrases hidden on tiny cards appear). Click-through (pointer-events: none), so the
  // mouse never leaves the real card because of it. Mouse/trackpad only (pointerType): touch keeps the long-press tooltip.
  var ZOOM = 3, zoom = document.getElementById('cardzoom'), shown = null, timer = null;
  function hideZoom() {
    clearTimeout(timer); shown = null;
    if (zoom) { zoom.hidden = true; zoom.textContent = ''; }
  }
  function showZoom(card) {
    if (!zoom || root.classList.contains('view-list')) return hideZoom();   // the list view has no grid to enlarge
    var rect = card.getBoundingClientRect(), copy = card.cloneNode(true);
    var w = Math.min(rect.width * ZOOM, window.innerWidth - 16);
    copy.removeAttribute('style');                                          // drop the grid `order` vars
    copy.style.width = w + 'px';
    zoom.textContent = ''; zoom.appendChild(copy); zoom.hidden = false; shown = card;
    var h = copy.getBoundingClientRect().height;                            // centred on the card, kept inside the window
    var left = Math.max(8, Math.min(rect.left + rect.width / 2 - w / 2, window.innerWidth - w - 8));
    var top = Math.max(8, Math.min(rect.top + rect.height / 2 - h / 2, window.innerHeight - h - 8));
    copy.style.left = left + 'px'; copy.style.top = top + 'px';
  }
  if (zoom) {
    root.addEventListener('pointerover', function (e) {
      if (e.pointerType !== 'mouse') return;
      var card = e.target.closest('.cardbox.other');
      if (!card || card === shown) return;
      clearTimeout(timer);
      timer = setTimeout(function () { showZoom(card); }, 120);            // no flashing while the mouse just crosses the grid
    });
    root.addEventListener('pointerout', function (e) {
      var card = e.target.closest('.cardbox.other');
      if (card && !card.contains(e.relatedTarget)) hideZoom();
    });
    // the 5 s poll replaces the cards (outerHTML): redraw the copy from the new card still under the mouse, or close it
    document.body.addEventListener('htmx:afterSettle', function () {
      if (!shown || document.contains(shown)) return;
      var card = root.querySelector('.cardbox.other:hover');
      if (card) showZoom(card); else hideZoom();
    });
    window.addEventListener('scroll', hideZoom, { passive: true });
    window.addEventListener('blur', hideZoom);
  }
  apply();
})();
