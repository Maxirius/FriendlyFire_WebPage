/* FriendlyFire auth-pages background: gentle Minecraft-flavored ambience.
   Floating grass/dirt/stone/cobble blocks with PROCEDURAL 16x16 pixel
   textures (original-inspired, no Mojang assets redistributed), XP orbs
   and diamond sparkles rising slowly over the dark grid.
   Pure canvas 2D, no external assets, no deps.

   Interaction:
   - pointer parallax (bigger block = nearer plane, moves more)
   - proximity glow: entities near the cursor brighten + swell
   - click/tap burst: ring of xp/diamond shards at the pointer

   Behavior contract:
   - pauses when the tab is hidden (document.hidden read live each frame)
   - honours prefers-reduced-motion: static textured scatter, no pointer FX
   - DPR-aware, fps-capped (30), entity count scales with viewport area
*/
(function () {
  'use strict';

  var PALETTE = {
    grassTop:  [124, 189, 84],
    grassSide: [106, 90, 62],
    dirtDark:  [89, 73, 47],
    dirt:      [134, 96, 67],
    stone:     [125, 125, 125],
    stoneDark: [100, 100, 100],
    cobble:    [138, 138, 138],
    mortar:    [86, 86, 86],
    leaves:    [63, 143, 47],
    xp:        [255, 195, 92],
    diamond:   [127, 215, 232],
    glowWhite: [233, 238, 226]
  };

  var KIND_WEIGHTS = [
    { kind: 'grass',  weight: 24 },
    { kind: 'cobble', weight: 14 },
    { kind: 'stone',  weight: 12 },
    { kind: 'leaves', weight: 12 },
    { kind: 'xp',     weight: 22 },
    { kind: 'diamond', weight: 8 },
    { kind: 'spark',  weight: 8 }
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
  document.body.insertBefore(canvas, document.body.firstChild);
  var ctx = canvas.getContext('2d');

  var W = 0, H = 0, DPR = 1;
  var entities = [];
  var shards = [];               // click-burst debris
  var FRAME_MS = 1000 / 30;      // cap at 30fps
  var lastTs = 0;

  // pointer state (null = off page)
  var mx = null, my = null;

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

  // ======================================================================
  // Procedural 16x16 tiles — original-INSPIRED pixel art, drawn once.
  // (Mojang assets may not be redistributed; these evoke the classics.)
  // ======================================================================
  var TILE = 16;
  var tiles = {};

  function makeTile(painter) {
    var t = document.createElement('canvas');
    t.width = TILE; t.height = TILE;
    var c = t.getContext('2d');
    painter(c);
    return t;
  }

  function noisyFill(c, base, jitter, x0, y0, w, h) {
    for (var y = y0; y < y0 + h; y++) {
      for (var x = x0; x < x0 + w; x++) {
        var j = (Math.random() * 2 - 1) * jitter;
        c.fillStyle = rgba([
          Math.round(base[0] + j),
          Math.round(base[1] + j),
          Math.round(base[2] + j)], 1);
        c.fillRect(x, y, 1, 1);
      }
    }
  }

  function buildTiles() {
    tiles.grassTopTex = makeTile(function (c) {
      noisyFill(c, PALETTE.grassTop, 14, 0, 0, TILE, TILE);
      for (var i = 0; i < 26; i++) {
        c.fillStyle = rgba([96, 156, 62], 0.8);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 1, 1);
      }
    });

    tiles.dirt = makeTile(function (c) {
      noisyFill(c, PALETTE.dirt, 16, 0, 0, TILE, TILE);
      for (var i = 0; i < 14; i++) {
        c.fillStyle = rgba(PALETTE.dirtDark, 0.85);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 1, 1);
      }
      for (i = 0; i < 5; i++) {
        c.fillStyle = rgba([155, 118, 83], 0.7);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 1, 1);
      }
    });

    tiles.grass = makeTile(function (c) {
      // grass SIDE: dirt body + green fringe with drip-downs (classic look)
      noisyFill(c, PALETTE.dirt, 16, 0, 0, TILE, TILE);
      for (var i = 0; i < 10; i++) {
        c.fillStyle = rgba(PALETTE.dirtDark, 0.85);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(3, TILE)), 1, 1);
      }
      noisyFill(c, PALETTE.grassTop, 12, 0, 0, TILE, 3);
      for (i = 0; i < TILE; i++) {
        if (Math.random() < 0.55) {
          noisyFill(c, PALETTE.grassTop, 10, i, 3, 1, 1);
        }
        if (Math.random() < 0.25) {
          noisyFill(c, PALETTE.grassTop, 8, i, 4, 1, 1);
        }
      }
    });

    tiles.stone = makeTile(function (c) {
      noisyFill(c, PALETTE.stone, 9, 0, 0, TILE, TILE);
      c.strokeStyle = rgba(PALETTE.stoneDark, 0.9);
      c.lineWidth = 1;
      for (var i = 0; i < 3; i++) {
        c.beginPath();
        var sx = rnd(2, 13), sy = rnd(2, 13);
        c.moveTo(sx, sy);
        c.lineTo(sx + rnd(-4, 4), sy + rnd(-3, 3));
        c.stroke();
      }
      for (i = 0; i < 6; i++) {
        c.fillStyle = rgba([148, 148, 148], 0.7);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 2, 1);
      }
    });

    tiles.cobble = makeTile(function (c) {
      noisyFill(c, PALETTE.mortar, 6, 0, 0, TILE, TILE);
      var stones = [[1,1,6,5],[8,0,7,4],[0,7,5,5],[6,5,5,6],
                    [12,5,3,7],[2,13,6,3],[9,12,6,4]];
      for (var i = 0; i < stones.length; i++) {
        var s = stones[i];
        c.fillStyle = rgba(PALETTE.cobble, 1);
        c.fillRect(s[0], s[1], s[2], s[3]);
        noisyFill(c, PALETTE.cobble, 12, s[0], s[1], s[2], s[3]);
        c.fillStyle = rgba([70, 70, 70], 0.55);
        c.fillRect(s[0], s[1] + s[3] - 1, s[2], 1);   // bottom shade
        c.fillStyle = rgba([165, 165, 165], 0.5);
        c.fillRect(s[0], s[1], s[2], 1);              // top light
      }
    });

    tiles.leaves = makeTile(function (c) {
      noisyFill(c, PALETTE.leaves, 12, 0, 0, TILE, TILE);
      for (var i = 0; i < 30; i++) {
        c.fillStyle = rgba([45, 110, 34], 0.9);
        c.fillRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 1, 1);
      }
      for (i = 0; i < 14; i++) {
        c.clearRect(Math.floor(rnd(0, TILE)), Math.floor(rnd(0, TILE)), 1, 1);
      }
    });
  }

  function tileFor(kind) {
    switch (kind) {
      case 'grass':  return tiles.grass;
      case 'cobble': return tiles.cobble;
      case 'stone':  return tiles.stone;
      case 'leaves': return tiles.leaves;
      default:       return null;
    }
  }

  // ======================================================================
  // Entities
  // ======================================================================
  function makeEntity(initialSpread) {
    var kind = weightedKind();
    var sizePx = kind === 'xp' || kind === 'spark' || kind === 'diamond'
      ? rnd(3, 7)
      : rnd(12, 30);
    return {
      kind: kind,
      size: Math.round(sizePx),
      x: rnd(0, W),
      y: initialSpread ? rnd(0, H) : H + sizePx * 2,
      vy: -(rnd(6, 22) * (kind === 'xp' ? 1.4 : 1)),   // px/s upward
      swayFreq: rnd(0.2, 0.7),
      swayAmp: rnd(4, 16),
      phase: rnd(0, Math.PI * 2),
      alphaPeak: kind === 'spark' ? rnd(0.35, 0.8) : rnd(0.28, 0.55),
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

  // ======================================================================
  // Painters
  // ======================================================================
  function paintDot(e, alpha, color) {
    ctx.beginPath();
    ctx.arc(e.x, e.y, e.size / 2, 0, Math.PI * 2);
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

  // pointer influence: glow + swell within HOVER_R, parallax shift
  var HOVER_R = 130;

  function pointerFx(e) {
    if (mx === null) return { boost: 0, dx: 0, dy: 0 };
    var nx = (mx - W / 2) / (W / 2);      // -1..1
    var ny = (my - H / 2) / (H / 2);
    var depth = e.size / 30;              // bigger = nearer = stronger
    var dx = nx * e.size * 0.45 * depth;
    var dy = ny * e.size * 0.3 * depth;
    var ddx = e.x + dx - mx, ddy = e.y + dy - my;
    var d = Math.sqrt(ddx * ddx + ddy * ddy);
    var boost = d < HOVER_R ? (1 - d / HOVER_R) : 0;
    return { boost: boost, dx: dx, dy: dy };
  }

  function paint(e, now) {
    var age = (now - e.born) / 1000;
    var fadeIn = Math.min(1, age / 2.5);
    var fadeOut = Math.min(1, Math.max(0, (e.lifeSpan - age) / 3));
    var fx = pointerFx(e);
    var alpha = e.alphaPeak * Math.min(fadeIn, fadeOut) * (1 + fx.boost * 0.9);
    if (alpha <= 0.01) return;
    var sway = Math.sin(now / 1000 * e.swayFreq + e.phase) * e.swayAmp;
    var x = e.x + sway + fx.dx;
    var y = e.y + fx.dy;
    var size = e.size * (1 + fx.boost * 0.1);

    var tile = tileFor(e.kind);
    if (tile) {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate((e.angle + e.tumble * age) * Math.PI / 180);
      ctx.globalAlpha = Math.min(1, alpha);
      ctx.drawImage(tile, -size / 2, -size / 2, size, size);
      ctx.globalAlpha = 1;
      ctx.restore();
      return;
    }
    switch (e.kind) {
      case 'xp':
        paintDot({ x: x, y: y, size: size }, alpha, PALETTE.xp);
        if (fx.boost > 0.25) {   // halo when hovered
          paintDot({ x: x, y: y, size: size * 2.2 }, alpha * 0.25, PALETTE.xp);
        }
        break;
      case 'diamond': paintDiamond({ x: x, y: y, size: size }, alpha); break;
      case 'spark': paintSparkle({ x: x, y: y, size: size * 1.6 }, alpha, PALETTE.glowWhite); break;
    }
  }

  // ======================================================================
  // Click bursts
  // ======================================================================
  function spawnBurst(x, y) {
    if (reduced) return;
    var n = 10 + Math.floor(Math.random() * 5);
    for (var i = 0; i < n; i++) {
      var ang = (i / n) * Math.PI * 2 + rnd(-0.3, 0.3);
      var spd = rnd(50, 130);
      shards.push({
        x: x, y: y,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd - rnd(20, 60),
        size: rnd(2, 5),
        kind: pick(['xp', 'xp', 'diamond']),
        born: performance.now(),
        life: rnd(0.7, 1.2)
      });
    }
  }

  function paintShards(now, dt) {
    for (var i = shards.length - 1; i >= 0; i--) {
      var s = shards[i];
      var age = (now - s.born) / 1000;
      if (age > s.life) { shards.splice(i, 1); continue; }
      s.vy += 150 * dt;                       // gentle gravity
      s.x += s.vx * dt;
      s.y += s.vy * dt;
      var a = Math.max(0, 1 - age / s.life);
      if (s.kind === 'diamond') {
        paintDiamond({ x: s.x, y: s.y, size: s.size }, a);
      } else {
        paintDot({ x: s.x, y: s.y, size: s.size }, a, PALETTE.xp);
      }
    }
  }

  // ======================================================================
  // Loop
  // ======================================================================
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
    paintShards(ts, dt);
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

  // ======================================================================
  // Lifecycle
  // ======================================================================
  buildTiles();
  resize();
  window.addEventListener('resize', function () {
    resize();
    if (reduced) paintStaticScene();
  });

  window.addEventListener('pointermove', function (ev) {
    mx = ev.clientX; my = ev.clientY;
  }, { passive: true });
  window.addEventListener('pointerdown', function (ev) {
    mx = ev.clientX; my = ev.clientY;
    spawnBurst(ev.clientX, ev.clientY);
  }, { passive: true });
  document.documentElement.addEventListener('mouseleave', function () {
    mx = null; my = null;
  });

  if (reduced) {
    paintStaticScene();
  } else {
    requestAnimationFrame(tick);
  }
})();
