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
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(capture, 200); }
  document.addEventListener('selectionchange', schedule);          // mouse, keyboard and touch handles
  document.addEventListener('mouseup', schedule);
  document.addEventListener('touchend', schedule);

  function clear() {
    picker.hidden = true;
    ['kind', 'start_id', 'start_off', 'end_id', 'end_off'].forEach(function (n) { field(n).value = ''; });
    field('quote').textContent = '';
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
