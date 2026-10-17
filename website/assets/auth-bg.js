/* FriendlyFire auth-pages background: gentle Minecraft-flavored ambience.
   Floating grass/dirt blocks, stone, XP orbs and diamond sparkles rising
   slowly over the dark grid. Pure canvas 2D, no assets, no deps.

   Behavior contract:
   - pauses when the tab is hidden (visibilitychange)
   - honours prefers-reduced-motion: paints a static scatter instead
   - DPR-aware, fps-capped, entity count scales with viewport area
*/
(function () {
  'use strict';

  var PALETTE = {
    grassTop: [119, 207, 82],   // --accent
    grassSide: [106, 90, 62],   // dirt
    dirtDark: [89, 73, 47],
    stone: [58, 66, 74],
    cobbleLight: [92, 102, 112],
    leaves: [63, 143, 47],
    xp: [255, 195, 92],         // --amber
    diamond: [127, 215, 232],
    glowWhite: [233, 238, 226]
  };

  var KIND_WEIGHTS = [
    { kind: 'grass', weight: 26 },
    { kind: 'cobble', weight: 18 },
    { kind: 'leaves', weight: 14 },
    { kind: 'xp', weight: 26 },
    { kind: 'diamond', weight: 8 },
    { kind: 'spark', weight: 8 }
  ];

  var reduced = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.position = 'fixed';
  canvas.style.inset = '0';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.pointerEvents = 'none';
  canvas.style.imageRendering = 'pixelated';
  document.body.appendChild(canvas);
  var ctx = canvas.getContext('2d');

  var W = 0, H = 0, DPR = 1;
  var entities = [];
  var FRAME_MS = 1000 / 30;          // cap at 30fps
  var lastTs = 0;

  function rnd(min, max) { return min + Math.random() * (max - min); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function weightedKind() {
    var total = 0, i;
    for (i = 0; i < KIND_WEIGHTS.length; i++) total += KIND_WEIGHTS[i].weight;
    var roll = Math.random() * total;
    for (i = 0; i < KIND_WEIGHTS.length; i++) {
      roll -= KIND_WEIGHTS[i].weight;
      if (roll <= 0) return KIND_WEIGHTS[i].kind;
    }
    return 'xp';
  }

  function rgba(c, a) {
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a.toFixed(3) + ')';
  }

  function makeEntity(initialSpread) {
    var kind = weightedKind();
    var sizePx = kind === 'xp' || kind === 'spark' || kind === 'diamond'
      ? rnd(3, 7)
      : rnd(10, 26);
    var spreadFactor = initialSpread ? 1 : 0.35;
    return {
      kind: kind,
      size: Math.round(sizePx),
      x: rnd(0, W),
      y: initialSpread ? rnd(0, H) : H + sizePx * 2,
      vy: -(rnd(6, 22) * (kind === 'xp' ? 1.4 : 1)),   // px/s upward
      swayFreq: rnd(0.2, 0.7),
      swayAmp: rnd(4, 16),
      phase: rnd(0, Math.PI * 2),
      alphaPeak: kind === 'spark' ? rnd(0.35, 0.8) : rnd(0.22, 0.5),
      tumble: rnd(-14, 14),                            // deg/s quarter-stepped
      angle: Math.floor(rnd(0, 4)) * 90,
      born: performance.now(),
      lifeSpan: rnd(18, 42)                            // seconds
    };
  }

  function targetCount() {
    var area = W * H;
    var n = Math.round(area / 38000);
    return Math.max(14, Math.min(46, n));
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.imageSmoothingEnabled = false;
    while (entities.length < targetCount()) {
      // warmup: 70% start distributed across the viewport so the scene is
      // alive immediately, the rest trickle in from below
      entities.push(makeEntity(
          entities.length < Math.floor(targetCount() * 0.7)));
    }
    if (entities.length > targetCount()) {
      entities = entities.slice(0, targetCount());
    }
  }

  // -------- painters -------------------------------------------------------
  function px(x, y, w, h, color, alpha) {
    ctx.fillStyle = rgba(color, alpha);
    ctx.fillRect(Math.round(x), Math.round(y), Math.ceil(w), Math.ceil(h));
  }

  function paintBlock(e, alpha) {
    var s = e.size;
    var x = e.x - s / 2, y = e.y - s / 2;
    if (e.kind === 'grass') {
      var top = Math.round(s * 0.32);
      px(x, y, s, top, PALETTE.grassTop, alpha);
      px(x, y + top, s, s - top, PALETTE.grassSide, alpha * 0.9);
      px(x, y + s - Math.max(1, s * 0.12), s, Math.max(1, s * 0.12),
         PALETTE.dirtDark, alpha * 0.8);
    } else if (e.kind === 'cobble') {
      px(x, y, s, s, PALETTE.stone, alpha);
      px(x + s * 0.15, y + s * 0.15, s * 0.3, s * 0.3,
         PALETTE.cobbleLight, alpha * 0.9);
      px(x + s * 0.55, y + s * 0.55, s * 0.3, s * 0.28,
         PALETTE.cobbleLight, alpha * 0.7);
    } else if (e.kind === 'leaves') {
      px(x, y, s, s, PALETTE.leaves, alpha);
      px(x + s * 0.5, y, s * 0.5, s * 0.5, PALETTE.grassTop, alpha * 0.55);
    }
  }

  function paintDot(e, alpha, color) {
    var s = e.size;
    ctx.beginPath();
    ctx.arc(e.x, e.y, s / 2, 0, Math.PI * 2);
    ctx.fillStyle = rgba(color, alpha);
    ctx.fill();
  }

  function paintSparkle(e, alpha, color) {
    var s = e.size;
    ctx.strokeStyle = rgba(color, alpha);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(e.x - s, e.y); ctx.lineTo(e.x + s, e.y);
    ctx.moveTo(e.x, e.y - s); ctx.lineTo(e.x, e.y + s);
    ctx.stroke();
    paintDot(e, alpha * 0.5, color);
  }

  function paintDiamond(e, alpha) {
    var s = e.size * 1.4;
    ctx.beginPath();
    ctx.moveTo(e.x, e.y - s / 2);
    ctx.lineTo(e.x + s / 2, e.y);
    ctx.lineTo(e.x, e.y + s / 2);
    ctx.lineTo(e.x - s / 2, e.y);
    ctx.closePath();
    ctx.fillStyle = rgba(PALETTE.diamond, alpha);
    ctx.fill();
    ctx.fillStyle = rgba(PALETTE.glowWhite, alpha * 0.5);
    ctx.fillRect(e.x - s * 0.1, e.y - s * 0.28, s * 0.2, s * 0.2);
  }

  function paint(e, now) {
    var age = (now - e.born) / 1000;
    var fadeIn = Math.min(1, age / 2.5);
    var fadeOut = Math.min(1, Math.max(0, (e.lifeSpan - age) / 3));
    var alpha = e.alphaPeak * Math.min(fadeIn, fadeOut);
    if (alpha <= 0.01) return;
    var sway = Math.sin(now / 1000 * e.swayFreq + e.phase) * e.swayAmp;
    var x = e.x + sway;
    switch (e.kind) {
      case 'grass':
      case 'cobble':
      case 'leaves':
        ctx.save();
        ctx.translate(x, e.y);
        ctx.rotate((e.angle + e.tumble * age) * Math.PI / 180);
        ctx.translate(-(x), -(e.y));
        paintBlock({ x: x, y: e.y, size: e.size, kind: e.kind }, alpha);
        ctx.restore();
        break;
      case 'xp': paintDot({ x: x, y: e.y, size: e.size }, alpha, PALETTE.xp); break;
      case 'diamond': paintDiamond({ x: x, y: e.y, size: e.size }, alpha); break;
      case 'spark': paintSparkle({ x: x, y: e.y, size: e.size * 1.6 }, alpha, PALETTE.glowWhite); break;
    }
  }

  function tick(ts) {
    // read hidden-state LIVE: a cached paused flag sticks forever if the
    // visibilitychange event fired before this listener existed
    if (document.hidden) { requestAnimationFrame(tick); return; }
    if (ts - lastTs < FRAME_MS) { requestAnimationFrame(tick); return; }
    var dt = Math.min(0.1, (ts - lastTs) / 1000);
    lastTs = ts;
    ctx.clearRect(0, 0, W, H);
    var i, e;
    for (i = 0; i < entities.length; i++) {
      e = entities[i];
      e.y += e.vy * dt;
      if (e.y < -e.size * 3 ||
          (performance.now() - e.born) / 1000 > e.lifeSpan) {
        entities[i] = makeEntity(false);
        continue;
      }
      paint(e, ts);
    }
    requestAnimationFrame(tick);
  }

  function paintStaticScene() {
    ctx.clearRect(0, 0, W, H);
    var n = targetCount();
    for (var i = 0; i < n; i++) {
      var e = makeEntity(true);
      // mature the entity so fadeIn/fadeOut are at full alpha instantly
      // (age 0 would multiply alphaPeak by 0 and paint nothing)
      e.born = performance.now() - 5000;
      paint(e, performance.now());
    }
  }

  // -------- lifecycle -----------------------------------------------------
  resize();
  window.addEventListener('resize', function () {
    resize();
    if (reduced) paintStaticScene();
  });

  if (reduced) {
    paintStaticScene();
  } else {
    requestAnimationFrame(tick);
  }
})();
