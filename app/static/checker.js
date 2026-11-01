// Verdade ou Lorota: the claim cards (<details class=ckcard|ckfold data-id>; ckfold = the admin table's folds) stay open while "Minhas frases" is re-drawn by the 5 s poll or after a send.
(function () {
  var open = {};
  document.addEventListener('toggle', function (e) {            // `toggle` does not bubble: listen in the capture phase
    var d = e.target;
    if (d.classList && (d.classList.contains('ckcard') || d.classList.contains('ckfold'))) { if (d.open) open[d.dataset.id] = 1; else delete open[d.dataset.id]; }
  }, true);
  function restore() {
    document.querySelectorAll('details.ckcard, details.ckfold').forEach(function (d) { if (open[d.dataset.id]) d.open = true; });
  }
  document.body.addEventListener('htmx:beforeSwap', function () {
    document.querySelectorAll('details.ckcard, details.ckfold').forEach(function (d) { if (d.open) open[d.dataset.id] = 1; });
  });
  document.body.addEventListener('htmx:afterSwap', restore);
})();
