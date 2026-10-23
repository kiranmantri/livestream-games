// Header 🔗: copy the personal link instead of following it (the link still works as a plain anchor without JS).
(function () {
  var a = document.querySelector('a.mylink');
  if (!a) return;
  var note = document.createElement('span');
  note.className = 'mylink-note';
  note.setAttribute('role', 'status');
  a.parentNode.appendChild(note);
  var timer;
  function say(text) {
    note.textContent = text;
    note.classList.add('show');
    clearTimeout(timer);
    timer = setTimeout(function () { note.classList.remove('show'); }, 2500);
  }
  function fallback(text) {
    var t = document.createElement('textarea');
    t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(t);
    return ok;
  }
  a.addEventListener('click', function (e) {
    e.preventDefault();
    var url = new URL(a.getAttribute('href'), location.origin).href;
    var done = function (ok) { say(ok ? a.dataset.copied : a.dataset.failed); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { done(true); }, function () { done(fallback(url)); });
    } else {
      done(fallback(url));
    }
  });
})();
