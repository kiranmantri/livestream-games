// /admin/checker: the toggle link carries the search text; the host-score picker (open, place, close); "Editar" opens it.
// The pending/all toggle is a plain link (works without JS); with JS it also carries what is typed in the search box.
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[data-ckall]');
  var box = document.querySelector('form.ck-search input[name=q]');
  if (!a || !box) return;
  var u = new URL(a.href, location.href);
  if (box.value) u.searchParams.set('q', box.value); else u.searchParams.delete('q');
  a.href = u.pathname + u.search;
});

// The host-score picker closes on Esc or on a click outside it.
function closePicks(except) { document.querySelectorAll('details.hostpick[open]').forEach(function (d) { if (d !== except) d.open = false; }); }
document.addEventListener('click', function (e) { closePicks(e.target.closest && e.target.closest('details.hostpick')); });
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePicks(null); });

// "Editar" opens the host-score picker of its row.
document.addEventListener('click', function (e) {
  var b = e.target.closest && e.target.closest('button[data-ckedit]');
  if (!b) return;
  var d = b.closest('tbody').querySelector('details.hostpick');
  if (d) { e.stopPropagation(); closePicks(d); d.open = !d.open; }
}, true);

// The picker is position: fixed (the table area scrolls and would clip it): place it under its "Host" line when it opens.
function placePick(d) {
  var f = d.querySelector('form'), r = d.getBoundingClientRect();
  if (!f) return;
  var w = f.offsetWidth || 250, h = f.offsetHeight || 100;
  var top = r.bottom + 6; if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 6);
  f.style.top = top + 'px'; f.style.left = Math.max(8, Math.min(r.left - 6, innerWidth - w - 8)) + 'px';
}
document.addEventListener('toggle', function (e) { if (e.target.matches && e.target.matches('details.hostpick') && e.target.open) placePick(e.target); }, true);
document.addEventListener('scroll', function () { closePicks(null); }, true);

// Folds and pickers that were open stay open when the panel is re-drawn (the 3 s poll).
(function () {
  var open = {}, sel = 'details.hostpick';
  document.addEventListener('toggle', function (e) { var d = e.target; if (d.matches && d.matches(sel)) { if (d.open) open[d.dataset.id] = 1; else delete open[d.dataset.id]; } }, true);
  document.addEventListener('click', function (e) {              // choosing a score closes the picker for good (the re-drawn row must not reopen it)
    var b = e.target.closest && e.target.closest('button.pick'), d = b && b.closest('details.hostpick');
    if (d) { delete open[d.dataset.id]; d.open = false; }
  }, true);
  document.body.addEventListener('htmx:afterSwap', function () { document.querySelectorAll(sel).forEach(function (d) { if (open[d.dataset.id]) { d.open = true; placePick(d); } }); });
})();
