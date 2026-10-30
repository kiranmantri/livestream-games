// Bingo celebration effects, called by game.js when a tap MARKS a cell (window.bingoFx.tap):
//   - every mark: a small spark burst drawn on a canvas laid exactly over the tapped cell (clipped to it);
//   - the mark completes a row / column / diagonal: bursts along the new line(s) + confetti, clipped to MY card;
//   - the mark completes the whole card: fireworks over the whole window that NEVER stop by themselves. They stop when I unmark a
//     cell (game.js -> stop()) or when the admin clears / blocks one: game.js re-reads my board every 5 s (GET /myboard) and a
//     redraw that leaves it not full stops them. A reload with a full card starts them again; on the end screen they
//     run forever when the server marks it with data-fx="fireworks" (I won with a full card).
// Each effect is its own throwaway <canvas> (pointer-events: none, removed when done). The cell canvas is a child of the
// .board, not of the cell, because the server's answer replaces the cell (outerHTML) while the sparks still fly.
// Users who asked the OS for reduced motion get no effects.
(function () {
  var CELL_MS = 2800, CARD_MS = 6400;                                     // durations: tune here
  var COLORS = ['#facc15', '#f472b6', '#38bdf8', '#4ade80', '#fb923c', '#ffffff', '#a78bfa'];
  var scr = null;                                                          // the running window fireworks: {L, list, on}

  function reduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function pick(a) { return a[Math.floor(Math.random() * a.length)]; }
  function rnd(a, b) { return a + Math.random() * (b - a); }

  // a canvas of w x h CSS px at (x, y) inside `parent` (sharp on retina screens)
  function layer(parent, cls, x, y, w, h) {
    var cv = document.createElement('canvas');
    cv.className = 'fx ' + cls;
    cv.setAttribute('aria-hidden', 'true');
    if (x !== null) { cv.style.left = x + 'px'; cv.style.top = y + 'px'; }
    parent.appendChild(cv);
    var L = { cv: cv, ctx: cv.getContext('2d') };
    size(L, w, h);
    return L;
  }
  function size(L, w, h) {
    var dpr = window.devicePixelRatio || 1;
    L.w = w; L.h = h;
    L.cv.style.width = w + 'px'; L.cv.style.height = h + 'px';             // CSS size = drawing size (a canvas's own size is w*dpr)
    L.cv.width = Math.max(1, Math.round(w * dpr)); L.cv.height = Math.max(1, Math.round(h * dpr));
    L.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // particles: sparks (lines along the velocity), confetti (spinning rectangles) and rockets (explode when they stop)
  function burst(list, x, y, n, speed, life, delay) {
    for (var i = 0; i < n; i++) {
      var a = Math.random() * Math.PI * 2, v = speed * rnd(0.35, 1);
      list.push({ k: 'spark', x: x, y: y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, t0: delay || 0,
                  life: life * rnd(0.6, 1), c: pick(COLORS), s: rnd(1.8, 3.2), tw: rnd(0, 6.3) });
    }
    list.push({ k: 'ring', x: x, y: y, t0: delay || 0, life: life * 0.6, c: '#ffffff', s: 2.5, v: speed * 0.22 });   // the flash
  }
  function confetti(list, w, h, n, life, delay) {           // falls from above the top edge, across about the whole height
    for (var i = 0; i < n; i++) {
      list.push({ k: 'conf', x: rnd(0, w), y: rnd(-40, -4), vx: rnd(-30, 30), vy: h * rnd(0.3, 0.7) / (life / 1000),
                  t0: (delay || 0) + rnd(0, 500),
                  life: life, c: pick(COLORS), s: rnd(4, 8), r: rnd(0, 6.3), vr: rnd(-8, 8) });
    }
  }

  // draws `list` until `ms` have passed and no particle is left; with `o.tick` (endless) it goes on while o.alive() says so
  function run(L, list, ms, gravity, drag, done, o) {
    var start = performance.now(), last = start;
    function frame(now) {
      var dt = Math.min(0.05, (now - last) / 1000), t = now - start; last = now;
      if (o) o.tick(t, list);
      L.ctx.clearRect(0, 0, L.w, L.h);
      for (var i = list.length - 1; i >= 0; i--) {
        var p = list[i];
        if (t < p.t0) continue;
        var age = t - p.t0;
        if (age > p.life) {
          if (p.k === 'rocket') burst(list, p.x, p.y, p.n, p.v, 1300, t);   // starts now
          list.splice(i, 1); continue;
        }
        var fade = 1 - age / p.life;
        if (p.k === 'ring') {                                                 // an expanding circle that fades out
          L.ctx.globalAlpha = fade * 0.9; L.ctx.shadowBlur = 0;
          L.ctx.strokeStyle = p.c; L.ctx.lineWidth = p.s * fade + 0.5;
          L.ctx.beginPath(); L.ctx.arc(p.x, p.y, p.v * (1 - fade * fade) + 1, 0, Math.PI * 2); L.ctx.stroke();
          continue;
        }
        var g = p.k === 'spark' ? gravity : 0, d = p.k === 'spark' ? drag : 1;   // rockets and confetti: straight, steady
        p.vx *= Math.pow(d, dt * 60); p.vy = p.vy * Math.pow(d, dt * 60) + g * dt;
        p.x += p.vx * dt; p.y += p.vy * dt;
        var twinkle = p.k === 'spark' ? 0.7 + 0.3 * Math.sin(age / 45 + p.tw) : 1;   // sparks glitter while they fade
        L.ctx.globalAlpha = Math.max(0, Math.min(1, fade * 1.6)) * twinkle;
        L.ctx.shadowColor = p.c; L.ctx.shadowBlur = p.k === 'conf' ? 0 : 6;   // glow: readable on the green marked cell
        if (p.k === 'conf') {
          p.r += p.vr * dt;
          L.ctx.save(); L.ctx.translate(p.x, p.y); L.ctx.rotate(p.r);
          L.ctx.fillStyle = p.c; L.ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); L.ctx.restore();
        } else {
          L.ctx.strokeStyle = p.c; L.ctx.lineWidth = p.s; L.ctx.lineCap = 'round';
          L.ctx.beginPath(); L.ctx.moveTo(p.x, p.y); L.ctx.lineTo(p.x - p.vx * 0.05, p.y - p.vy * 0.05); L.ctx.stroke();
        }
      }
      L.ctx.globalAlpha = 1;
      if (o ? o.alive() || list.length : t < ms && list.length) requestAnimationFrame(frame);
      else { L.cv.remove(); if (done) done(); }
    }
    requestAnimationFrame(frame);
  }

  // small burst inside the tapped cell, from the tap point (keyboard: the centre)
  // The cell's box comes from the board's grid tracks (exact sub-pixel sizes): offsetWidth is rounded (a 0.5 px overflow)
  // and getBoundingClientRect is distorted by the cell's own tap animation (a scale transform).
  function cellBox(board, idx, n) {
    var st = getComputedStyle(board), cols = st.gridTemplateColumns.split(' ').map(parseFloat),
        rows = st.gridTemplateRows.split(' ').map(parseFloat), cg = parseFloat(st.columnGap) || 0, rg = parseFloat(st.rowGap) || 0,
        r = Math.floor(idx / n), c = idx % n, x = parseFloat(st.paddingLeft) || 0, y = parseFloat(st.paddingTop) || 0, k;
    for (k = 0; k < c; k++) x += cols[k] + cg;
    for (k = 0; k < r; k++) y += rows[k] + rg;
    return { x: x, y: y, w: cols[c], h: rows[r] };
  }

  function cellFx(cell, idx, n, e) {
    var board = cell.parentElement, b = cellBox(board, idx, n), w = b.w, h = b.h;
    if (!(w > 0 && h > 0)) return;
    var L = layer(board, 'cell-fx', b.x, b.y, w, h);
    L.cv.style.borderRadius = getComputedStyle(cell).borderRadius;
    var r = cell.getBoundingClientRect(), x = w / 2, y = h / 2;     // tap point: relative to the (scaled) cell, scaled back
    if (e && e.detail && e.clientX) { x = (e.clientX - r.left) * w / r.width; y = (e.clientY - r.top) * h / r.height; }
    var list = [], m = Math.min(w, h);
    burst(list, x, y, 30, m * 2.6, CELL_MS);
    run(L, list, CELL_MS, m * 0.12, 0.9);                            // light gravity: the sparks float in the cell, not drop out
  }

  // bursts along the completed line(s), one cell after the other, plus confetti: all inside my card
  function cardFx(box, cells, lines) {
    var L = layer(box, 'card-fx', null, null, box.clientWidth, box.clientHeight);
    var br = box.getBoundingClientRect(), list = [], m = Math.min(L.w, L.h);
    lines.forEach(function (line, li) {
      line.forEach(function (idx, k) {
        var r = cells[idx].getBoundingClientRect();
        burst(list, r.left - br.left - box.clientLeft + r.width / 2, r.top - br.top - box.clientTop + r.height / 2,
              22, m * 0.9, CARD_MS * 0.6, li * 400 + k * 180);
      });
    });
    confetti(list, L.w, L.h, 60, CARD_MS);
    run(L, list, CARD_MS + 1200, m * 0.08, 0.93);
  }

  // fireworks over the whole window, endless: a rocket from the bottom every ~0.5 s that explodes in the upper part, plus a steady
  // confetti rain. stop() lets the ones in the air finish, then the canvas goes away. Calling it again while running is a no-op.
  function screenFx() {
    if (scr) { scr.on = true; return; }
    var L = layer(document.body, 'screen-fx', null, null, window.innerWidth, window.innerHeight), list = [];
    var nextRocket = 0, nextConf = 0, me = { L: L, list: list, on: true };
    scr = me;
    run(L, list, 0, 0, 0.95, function () { if (scr === me) scr = null; }, {
      alive: function () { return me.on; },
      tick: function (t, l) {
        if (!me.on) return;
        if (t >= nextRocket) {
          var rise = L.h * rnd(0.45, 0.8), life = rnd(700, 950);          // climbs `rise` px in `life` ms, then explodes
          l.push({ k: 'rocket', x: L.w * rnd(0.12, 0.88), y: L.h, vx: rnd(-40, 40), vy: -rise / (life / 1000),
                   t0: t, life: life, c: '#fde68a', s: 2.5, n: 90, v: Math.min(L.w, L.h) * 0.8 });
          nextRocket = t + rnd(350, 750);
        }
        if (t >= nextConf) { confetti(l, L.w, L.h, 5, 3400, t); nextConf = t + 150; }
      }
    });
  }
  function stop() { if (scr) scr.on = false; }
  window.addEventListener('resize', function () { if (scr) size(scr.L, window.innerWidth, window.innerHeight); });

  // my card on the game page is full (every phrase cell marked, none revoked); false where there is no tappable card
  function myFull() {
    var b = document.querySelectorAll('.cardbox.mine button.cell');
    return b.length > 0 && Array.prototype.every.call(b, function (x) {
      return x.getAttribute('aria-pressed') === 'true' && !x.classList.contains('revoked');
    });
  }
  // game.js redrew my board (the admin cleared or blocked a mark): no longer full -> the fireworks wind down
  document.body.addEventListener('htmx:afterSettle', function () {
    if (scr && scr.on && document.querySelector('.cardbox.mine button.cell') && !myFull()) stop();
  });

  // the lines through cell `idx` of an n x n card that are complete in `on` (array of booleans): arrays of cell indexes
  function newLines(on, n, idx) {
    var r = Math.floor(idx / n), c = idx % n, out = [], k;
    var cand = [[], [], [], []];
    for (k = 0; k < n; k++) { cand[0].push(r * n + k); cand[1].push(k * n + c); cand[2].push(k * n + k); cand[3].push(k * n + n - 1 - k); }
    if (r !== c) cand[2] = null;
    if (r + c !== n - 1) cand[3] = null;
    cand.forEach(function (line) {
      if (line && line.every(function (i) { return on[i]; })) out.push(line);
    });
    return out;
  }

  // `cell` was just marked (its aria-pressed is already 'true'); `e` is the click
  function tap(cell, e) {
    if (reduced()) return;
    var board = cell.parentElement, box = cell.closest('.cardbox.mine');
    if (!board || !box) return;
    var cells = Array.prototype.filter.call(board.children, function (x) { return x.classList.contains('cell'); });
    var n = Math.round(Math.sqrt(cells.length)), idx = cells.indexOf(cell);
    var on = cells.map(function (x) {          // the avatar free cell counts as marked; a revoked cell cannot be
      return x.classList.contains('free') || (x.getAttribute('aria-pressed') === 'true' && !x.classList.contains('revoked'));
    });
    cellFx(cell, idx, n, e);
    var full = on.every(Boolean);
    if (full) screenFx();
    else {
      var lines = newLines(on, n, idx);
      if (lines.length) cardFx(box, cells, lines);
    }
  }

  window.bingoFx = { tap: tap, stop: stop, newLines: newLines, cell: cellFx, card: cardFx, screen: screenFx };
  if (!reduced() && (document.querySelector('[data-fx=fireworks]') || myFull())) screenFx();   // end screen / reload with a full card
})();
