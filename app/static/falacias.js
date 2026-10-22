// Falácias panes + selection picker.
//  1. Panes stay glued to the newest line unless the player scrolled up OR holds a selection; then a "N novas" pill shows.
//     Lines arrive by htmx out-of-band appends; a MutationObserver sees them, so the poll needs no knowledge of scrolling.
//  2. A selection inside ONE pane is captured as (kind, start line id, start offset, end line id, end offset). Offsets are
//     UTF-16 code units inside the line's .tx span (what the server expects). The server rebuilds the quote itself.
(function () {
  var NEAR = 40;                                   // px from the bottom that still counts as "following the live feed"

  function holdsSelection(pane) {
    var sel = window.getSelection();
    return !!sel && !sel.isCollapsed && sel.rangeCount > 0 && pane.contains(sel.anchorNode);
  }

  document.querySelectorAll('.scroll[data-kind]').forEach(function (pane) {
    var pill = document.querySelector('[data-pill="' + pane.dataset.kind + '"]'), unseen = 0;
    function atBottom() { return pane.scrollHeight - pane.scrollTop - pane.clientHeight <= NEAR; }
    function toBottom() { pane.scrollTop = pane.scrollHeight; unseen = 0; show(); }
    function show() {
      if (!pill) return;
      pill.hidden = unseen === 0;
      pill.textContent = unseen + ' novas · voltar ao vivo';
    }
    var wasAtBottom = true;
    pane.addEventListener('scroll', function () { wasAtBottom = atBottom(); if (wasAtBottom) { unseen = 0; show(); } });
    new MutationObserver(function (muts) {
      var added = 0;
      muts.forEach(function (m) { added += m.addedNodes.length; });
      if (wasAtBottom && !holdsSelection(pane)) toBottom(); else { unseen += added; show(); }
    }).observe(pane, { childList: true });
    if (pill) pill.addEventListener('click', toBottom);
    toBottom();
  });

  // ---------------------------------------------------------------- selection -> picker
  var picker = document.getElementById('picker');
  if (!picker) return;
  var timer = null;

  function field(n) { return document.getElementById('pk-' + n); }
  function unitsBefore(tx, node, off) {            // UTF-16 length of the text of `tx` before (node, off)
    var r = document.createRange();
    r.setStart(tx, 0);
    r.setEnd(node, off);
    return r.toString().length;
  }

  // Lines inside the picker's current selection get a highlight (a class, so it survives the browser collapsing the native selection).
  function linesOf(pane) { return Array.prototype.slice.call(pane.querySelectorAll('.pline')); }
  function markPicked() {
    document.querySelectorAll('.pline.picked').forEach(function (l) { l.classList.remove('picked'); });
    var kind = field('kind').value, a = field('start_id').value, b = field('end_id').value;
    if (!kind || !a || !b) return;
    var on = false;
    linesOf(document.getElementById('pane-' + kind)).forEach(function (l) {
      if (l.dataset.id === a) on = true;
      if (on) l.classList.add('picked');
      if (l.dataset.id === b) on = false;
    });
  }

  function capture() {
    var sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    var range = sel.getRangeAt(0);
    var anc = range.commonAncestorContainer;
    var pane = (anc.nodeType === 1 ? anc : anc.parentElement).closest('.scroll[data-kind]');
    if (!pane) return;                             // e.g. inside the picker: keep the previous capture
    var lines = Array.prototype.filter.call(pane.querySelectorAll('.pline'), function (l) {
      var tx = l.querySelector('.tx');
      return tx && range.intersectsNode(tx);
    });
    if (!lines.length) return;
    var first = lines[0], last = lines[lines.length - 1];
    var ftx = first.querySelector('.tx'), ltx = last.querySelector('.tx');
    var so = ftx.contains(range.startContainer) ? unitsBefore(ftx, range.startContainer, range.startOffset) : 0;
    var eo = ltx.contains(range.endContainer) ? unitsBefore(ltx, range.endContainer, range.endOffset) : ltx.textContent.length;
    if (eo < 1 || (first === last && eo <= so)) return;
    field('kind').value = pane.dataset.kind;
    field('start_id').value = first.dataset.id;
    field('start_off').value = so;
    field('end_id').value = last.dataset.id;
    field('end_off').value = eo;
    field('quote').textContent = '“' + sel.toString().replace(/\s+/g, ' ').trim().slice(0, 200) + '”';
    picker.hidden = false;
    markPicked();
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(capture, 200); }
  document.addEventListener('selectionchange', schedule);          // mouse, keyboard and touch handles
  document.addEventListener('mouseup', schedule);
  document.addEventListener('touchend', schedule);

  function clear() {
    picker.hidden = true;
    ['kind', 'start_id', 'start_off', 'end_id', 'end_off'].forEach(function (n) { field(n).value = ''; });
    field('quote').textContent = '';
    markPicked();
    var sel = window.getSelection();
    if (sel) sel.removeAllRanges();
    picker.querySelectorAll('input[type=radio]').forEach(function (r) { r.checked = false; });
    field('filter').value = '';
    filter();
  }
  document.getElementById('pk-clear').addEventListener('click', clear);
  document.body.addEventListener('fal:msg-sent', function () { var t = document.getElementById('hm-text'); if (t) t.value = ''; });
  document.body.addEventListener('fal:spoken-sent', function () { var t = document.getElementById('sp-text'); if (t) t.value = ''; });
  document.body.addEventListener('fal:sent', clear);               // sent by the server (HX-Trigger) after a stored pair

  // ---------------------------------------------------------------- click a line = select / deselect it
  // Faster than dragging. Lines of one pane stay contiguous: a click next to the current selection extends it, a click on its first or
  // last line shrinks it, a click on a middle line cuts the selection there, any other click starts a new one-line selection.
  // Drags (the pointer moved), shift-clicks and clicks on links keep the browser's own text selection.
  var down = null;
  document.addEventListener('mousedown', function (e) { down = { x: e.clientX, y: e.clientY }; });

  function selectLines(pane, lines) {
    if (!lines.length) { clear(); return; }
    var ftx = lines[0].querySelector('.tx'), ltx = lines[lines.length - 1].querySelector('.tx');
    var r = document.createRange();
    r.setStart(ftx, 0);
    r.setEnd(ltx, ltx.childNodes.length);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    clearTimeout(timer);
    capture();
  }

  function onLineClick(e) {
    var line = e.target.closest ? e.target.closest('.pline') : null;
    if (!line || e.shiftKey || e.target.closest('a')) return;
    if (down && Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) > 4) return;      // a drag selected text on purpose
    var pane = line.closest('.scroll[data-kind]');
    if (!pane) return;
    var all = linesOf(pane), i = all.indexOf(line), lo = -1, hi = -1;
    if (field('kind').value === pane.dataset.kind) {
      lo = all.findIndex(function (l) { return l.dataset.id === field('start_id').value; });
      hi = all.findIndex(function (l) { return l.dataset.id === field('end_id').value; });
    }
    var has = lo >= 0 && hi >= lo;
    var next;
    if (has && i >= lo && i <= hi) next = i === lo ? all.slice(lo + 1, hi + 1) : all.slice(lo, i);     // deselect: first/last line shrinks, a middle one cuts
    else if (has && i === hi + 1) next = all.slice(lo, i + 1);                                          // extend down
    else if (has && i === lo - 1) next = all.slice(i, hi + 1);                                          // extend up
    else next = [line];
    selectLines(pane, next);
  }
  document.querySelectorAll('.scroll[data-kind]').forEach(function (pane) { pane.addEventListener('click', onLineClick); });

  // ---------------------------------------------------------------- fallacy filter (accent/case-insensitive)
  function fold(s) { return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
  function filter() {
    var q = fold(field('filter').value.trim());
    picker.querySelectorAll('.grp').forEach(function (g) {
      var any = false;
      g.querySelectorAll('label').forEach(function (l) {
        var show = !q || fold(l.dataset.name || l.textContent).indexOf(q) !== -1;
        l.hidden = !show;
        any = any || show;
      });
      g.hidden = !any;
    });
  }
  field('filter').addEventListener('input', filter);
})();
