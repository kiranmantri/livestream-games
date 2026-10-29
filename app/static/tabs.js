// "Minhas frases | Outros participantes" tabs (.cktabbed, see ui.result_tabs). The panels are all in the HTML; this only shows one.
// The chosen tab is kept across htmx redraws (the 5 s poll of /checker re-draws the block with "mine" visible).
(function () {
  var tab = 'mine';
  function showTab() {
    document.querySelectorAll('.cktabbed [data-cktab]').forEach(function (b) { b.setAttribute('aria-selected', b.dataset.cktab === tab ? 'true' : 'false'); });
    document.querySelectorAll('.cktabbed [data-ckpanel]').forEach(function (p) { p.hidden = p.dataset.ckpanel !== tab; });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.cktabbed [data-cktab]');
    if (b) { tab = b.dataset.cktab; showTab(); }
  });
  document.body.addEventListener('htmx:afterSwap', showTab);
})();
