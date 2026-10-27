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

// Board rows filter the claim cards below them: a click (or Enter / Space on a focused row) toggles that player; several players at once;
// none selected = every card. The selection lives in sessionStorage per session, so the 3 s poll (which re-draws board and cards) and a reload keep it.
(function () {
  function box() { return document.getElementById('ckcards'); }
  function key() { var b = box(); return 'ckpick:' + ((b && b.dataset.session) || ''); }
  function load() { try { return JSON.parse(sessionStorage.getItem(key()) || '[]'); } catch (e) { return []; } }
  function save(sel) { try { sessionStorage.setItem(key(), JSON.stringify(sel)); } catch (e) { /* private mode: the filter just resets */ } }
  function apply() {
    var sel = load(), any = sel.length > 0, shown = 0;
    document.querySelectorAll('tr.ckpick').forEach(function (tr) { tr.setAttribute('aria-selected', sel.indexOf(tr.dataset.user) >= 0 ? 'true' : 'false'); });
    var cards = document.querySelectorAll('#ckcards li[data-user]');
    cards.forEach(function (li) { var on = !any || sel.indexOf(li.dataset.user) >= 0; li.hidden = !on; if (on) shown++; });
    var none = document.getElementById('ckcards-none'); if (none) none.hidden = !(any && shown === 0);
    var all = document.querySelector('#ckcards button[data-ckshowall]'); if (all) all.hidden = !any;
  }
  function toggle(uid) {
    var sel = load(), i = sel.indexOf(uid);
    if (i >= 0) sel.splice(i, 1); else sel.push(uid);
    save(sel); apply();
  }
  document.addEventListener('click', function (e) {
    var tr = e.target.closest && e.target.closest('tr.ckpick');
    if (tr) { toggle(tr.dataset.user); return; }
    if (e.target.closest && e.target.closest('button[data-ckshowall]')) { save([]); apply(); }
  });
  document.addEventListener('keydown', function (e) {
    var tr = e.target.closest && e.target.closest('tr.ckpick');
    if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(tr.dataset.user); }
  });
  document.body.addEventListener('htmx:afterSettle', apply);
  apply();
})();
