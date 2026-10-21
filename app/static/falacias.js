// Falácias panes: stay glued to the newest line unless the player scrolled up to read/select; then show a "N novas" pill.
// Lines arrive by htmx out-of-band appends; a MutationObserver sees them, so the poll needs no knowledge of scrolling.
(function () {
  var NEAR = 40;                                   // px from the bottom that still counts as "following the live feed"
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
      if (wasAtBottom) toBottom(); else { unseen += added; show(); }
    }).observe(pane, { childList: true });
    if (pill) pill.addEventListener('click', toBottom);
    toBottom();
  });
})();
