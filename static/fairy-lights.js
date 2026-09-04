/* ---------------------------------------------------------------
   Fairy lights — a coordinated swarm behind the ACM night sky.

   The lights flock. Each one steers on the three classic boid
   rules — match your neighbours' heading, drift toward the middle
   of them, but keep your distance — over a slow flow field that
   gives the whole swarm a shared current. The result reads as one
   body moving together rather than a few thousand independent
   dots, which is the difference between a swarm and a starfield.

   They also stay out of the content. Every panel on the page is
   an obstacle the swarm steers around, so lights are never behind
   a card, a table or the header — they gather in the open space
   and part around the boxes as you scroll.

   Two things keep it cheap enough to run on every page. Neighbour
   lookups go through a uniform grid rather than comparing every
   light to every other, which is the difference between 40,000
   checks a frame and four million. And each light is drawn as a
   pre-rendered glow sprite, because a shadow blur at this many
   points drops a laptop to single-figure frame rates.
   --------------------------------------------------------------- */
(function () {
  var canvas = document.getElementById('fairy-lights');
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext('2d', { alpha: true });
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The boxes the swarm must keep out of. Text that is not in a panel — a
  // hero headline, a section heading — is not an obstacle: lights passing
  // behind it are the effect, and it has no edges to part around.
  var PANELS = '.topbar, .footer, .card, .table-wrap, .searchbar, .source-card, .notice';
  var PANEL_MARGIN = 10;      // keep clear of the edge, not flush against it

  var PERCEPTION = 58;        // how far a light can see its neighbours
  var TOO_CLOSE = 15;         // below this they push apart
  var W_ALIGN = 0.045, W_COHERE = 0.026, W_SEPARATE = 0.13;
  // The shared current is deliberately weak. Turned up, every light in a
  // region simply obeys the same angle and the swarm slides like a sheet —
  // measured at 0.96 on the alignment order parameter, which looks rigid.
  // Kept light, the coordination comes from the flocking itself and sits
  // in the 0.75-0.88 range a real flock holds.
  var W_FLOW = 0.008;
  var W_WANDER = 0.010;       // each light's own mind, so the flock breathes
  var W_AVOID = 0.42;         // steering away from a panel beats everything

  var lights = [];
  var sprites = [];
  var obstacles = [];
  var grid = null, cols = 0, rows = 0;
  var w = 0, h = 0, dpr = 1;
  var frame = null;
  var t = 0;

  var COLOURS = [[255, 255, 255], [186, 232, 250], [110, 214, 245], [0, 154, 199]];

  // Three depths. Far lights are small, dim and slow; near ones carry the
  // sense of movement. Speed is a top speed here, not a fixed velocity —
  // flocking decides the direction.
  var LAYERS = [
    { weight: 0.62, size: 0.85, speed: 0.34, alpha: [0.26, 0.58] },
    { weight: 0.30, size: 1.45, speed: 0.58, alpha: [0.36, 0.76] },
    { weight: 0.08, size: 2.40, speed: 0.86, alpha: [0.40, 0.88] }
  ];

  function rand(min, max) { return min + Math.random() * (max - min); }

  /* ---------- The panels to avoid ---------- */

  function readObstacles() {
    obstacles = [];
    var nodes = document.querySelectorAll(PANELS);
    for (var i = 0; i < nodes.length; i++) {
      var r = nodes[i].getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      if (r.bottom < -80 || r.top > h + 80) continue;   // off-screen, ignore
      obstacles.push({
        x1: r.left - PANEL_MARGIN, y1: r.top - PANEL_MARGIN,
        x2: r.right + PANEL_MARGIN, y2: r.bottom + PANEL_MARGIN
      });
    }
  }

  function insideAny(x, y) {
    for (var i = 0; i < obstacles.length; i++) {
      var o = obstacles[i];
      if (x > o.x1 && x < o.x2 && y > o.y1 && y < o.y2) return o;
    }
    return null;
  }

  /* How much open sky is left once the panels are taken out. Used to set the
     population, so a page that is mostly table gets a swarm sized for its
     margins instead of the same count crammed into them. */
  function freeArea() {
    var covered = 0;
    for (var i = 0; i < obstacles.length; i++) {
      var o = obstacles[i];
      var cw = Math.min(o.x2, w) - Math.max(o.x1, 0);
      var ch = Math.min(o.y2, h) - Math.max(o.y1, 0);
      if (cw > 0 && ch > 0) covered += cw * ch;
    }
    return Math.max(w * h * 0.12, w * h - covered);
  }

  /* ---------- Sprites ---------- */

  function buildSprites() {
    sprites = [];
    for (var c = 0; c < COLOURS.length; c++) {
      var row = [];
      for (var l = 0; l < LAYERS.length; l++) {
        var r = LAYERS[l].size * 2.2;
        var s = document.createElement('canvas');
        s.width = s.height = Math.ceil(r * 2);
        var sc = s.getContext('2d');
        var g = sc.createRadialGradient(r, r, 0, r, r, r);
        var rgb = COLOURS[c].join(',');
        g.addColorStop(0, 'rgba(' + rgb + ',1)');
        g.addColorStop(0.32, 'rgba(' + rgb + ',0.55)');
        g.addColorStop(1, 'rgba(' + rgb + ',0)');
        sc.fillStyle = g;
        sc.fillRect(0, 0, s.width, s.height);
        row.push(s);
      }
      sprites.push(row);
    }
  }

  /* ---------- The swarm ---------- */

  function pickLayer() {
    var r = Math.random(), acc = 0;
    for (var i = 0; i < LAYERS.length; i++) {
      acc += LAYERS[i].weight;
      if (r <= acc) return i;
    }
    return 0;
  }

  function openSpot() {
    // Try for a gap; after a few attempts take what we get and let the
    // avoidance push it out on the first frames.
    for (var i = 0; i < 12; i++) {
      var x = Math.random() * w, y = Math.random() * h;
      if (!insideAny(x, y)) return { x: x, y: y };
    }
    return { x: Math.random() * w, y: Math.random() * h };
  }

  function build() {
    var count = Math.round(Math.min(2600, Math.max(500, freeArea() / 620)));
    lights = new Array(count);
    for (var i = 0; i < count; i++) {
      var l = pickLayer();
      var layer = LAYERS[l];
      var spot = openSpot();
      var angle = rand(0, Math.PI * 2);
      lights[i] = {
        x: spot.x, y: spot.y,
        vx: Math.cos(angle) * layer.speed * 0.6,
        vy: Math.sin(angle) * layer.speed * 0.6,
        layer: l,
        colour: Math.floor(Math.random() * COLOURS.length),
        base: rand(layer.alpha[0], layer.alpha[1]),
        phase: Math.random() * Math.PI * 2,
        wander: rand(0, Math.PI * 2),
        twinkle: rand(0.010, 0.045),
        scale: rand(0.75, 1.15)
      };
    }
  }

  /* A uniform grid, refilled each frame. Every light only ever compares
     itself to the nine cells around it.

     The buckets are allocated once and emptied by setting length to 0 —
     allocating a few thousand arrays every frame is the kind of garbage
     that shows up as stutter rather than as a lower average frame rate. */
  function allocGrid() {
    cols = Math.max(1, Math.ceil(w / PERCEPTION));
    rows = Math.max(1, Math.ceil(h / PERCEPTION));
    grid = new Array(cols * rows);
    for (var i = 0; i < grid.length; i++) grid[i] = [];
  }

  function refillGrid() {
    for (var i = 0; i < grid.length; i++) grid[i].length = 0;
    for (var j = 0; j < lights.length; j++) {
      var p = lights[j];
      var cx = p.x / PERCEPTION | 0, cy = p.y / PERCEPTION | 0;
      if (cx < 0) cx = 0; else if (cx >= cols) cx = cols - 1;
      if (cy < 0) cy = 0; else if (cy >= rows) cy = rows - 1;
      grid[cx + cy * cols].push(p);
    }
  }

  /* The shared current: a smooth field every light feels, which is what
     turns local flocking into one swarm rather than a dozen. */
  function flowAngle(x, y) {
    return Math.sin(x * 0.0017 + t) * 1.5
         + Math.cos(y * 0.0021 - t * 0.8) * 1.5
         + Math.sin((x + y) * 0.0009 + t * 0.5);
  }

  function steer(p) {
    var ax = 0, ay = 0;
    p.hidden = false;

    // --- Flocking, over the nine cells around this light ---
    var cx = p.x / PERCEPTION | 0, cy = p.y / PERCEPTION | 0;
    var sumVX = 0, sumVY = 0, sumX = 0, sumY = 0, n = 0;
    var sepX = 0, sepY = 0;
    var MAX_NEIGHBOURS = 12;

    for (var gy = cy - 1; gy <= cy + 1; gy++) {
      if (gy < 0 || gy >= rows) continue;
      for (var gx = cx - 1; gx <= cx + 1; gx++) {
        if (gx < 0 || gx >= cols) continue;
        var cell = grid[gx + gy * cols];
        if (!cell) continue;
        for (var i = 0; i < cell.length; i++) {
          var q = cell[i];
          if (q === p) continue;
          var dx = q.x - p.x, dy = q.y - p.y;
          var d2 = dx * dx + dy * dy;
          if (d2 > PERCEPTION * PERCEPTION || d2 === 0) continue;
          sumVX += q.vx; sumVY += q.vy;
          sumX += q.x;   sumY += q.y;
          n++;
          if (d2 < TOO_CLOSE * TOO_CLOSE) {
            sepX -= dx / d2; sepY -= dy / d2;   // harder the closer they are
          }
          if (n >= MAX_NEIGHBOURS) { gy = cy + 2; gx = cx + 2; break; }
        }
      }
    }

    if (n > 0) {
      ax += (sumVX / n - p.vx) * W_ALIGN;        // match their heading
      ay += (sumVY / n - p.vy) * W_ALIGN;
      ax += (sumX / n - p.x) * W_COHERE * 0.02;  // drift toward the middle
      ay += (sumY / n - p.y) * W_COHERE * 0.02;
      ax += sepX * W_SEPARATE;                   // but keep some room
      ay += sepY * W_SEPARATE;
    }

    // --- The shared current, plus a little of its own mind ---
    var a = flowAngle(p.x, p.y);
    ax += Math.cos(a) * W_FLOW;
    ay += Math.sin(a) * W_FLOW;
    p.wander += (Math.random() - 0.5) * 0.5;
    ax += Math.cos(p.wander) * W_WANDER;
    ay += Math.sin(p.wander) * W_WANDER;

    // --- Panels: steer out by the shortest way, hardest when deepest in ---
    for (var o = 0; o < obstacles.length; o++) {
      var b = obstacles[o];
      if (p.x <= b.x1 || p.x >= b.x2 || p.y <= b.y1 || p.y >= b.y2) continue;
      p.hidden = true;
      var left = p.x - b.x1, right = b.x2 - p.x;
      var top = p.y - b.y1, bottom = b.y2 - p.y;
      var min = Math.min(left, right, top, bottom);
      if (min === left)        ax -= W_AVOID;
      else if (min === right)  ax += W_AVOID;
      else if (min === top)    ay -= W_AVOID;
      else                     ay += W_AVOID;
      break;   // one panel is enough; they rarely overlap
    }

    p.vx += ax; p.vy += ay;

    // Hold a natural pace: flocking otherwise stalls some lights and
    // slingshots others.
    var top_ = LAYERS[p.layer].speed;
    var sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
    if (sp > top_) { p.vx = p.vx / sp * top_; p.vy = p.vy / sp * top_; }
    else if (sp < top_ * 0.35 && sp > 0.0001) {
      var lift = (top_ * 0.35) / sp;
      p.vx *= lift; p.vy *= lift;
    }
  }

  /* ---------- Frame ---------- */

  function resize() {
    // Capped at 1.5 rather than the usual 2. These are soft, blurred glows —
    // nobody can see the extra pixels, but on a retina screen the fill rate
    // they cost is what decides whether the swarm runs at 60fps or 18.
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildSprites();
    readObstacles();
    build();
    allocGrid();
    if (reduce) draw(false);
  }

  function draw(animate) {
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';

    if (animate) {
      t += 0.0016;
      refillGrid();
    }

    for (var i = 0; i < lights.length; i++) {
      var p = lights[i];

      if (animate) {
        steer(p);
        p.x += p.vx;
        p.y += p.vy;
        p.phase += p.twinkle;
        // Wrap at the edges so the swarm never thins out at one side.
        if (p.x < -12) p.x = w + 12; else if (p.x > w + 12) p.x = -12;
        if (p.y < -12) p.y = h + 12; else if (p.y > h + 12) p.y = -12;
      }

      // Never drawn over a panel, whatever the steering is doing. A light
      // shoved deep inside one by a scroll gets a frame or two to steer out,
      // and must not be visible while it does. steer() sets this as it walks
      // the obstacles, so the search is not repeated here.
      if (animate ? p.hidden : insideAny(p.x, p.y)) continue;

      var alpha = p.base * (0.35 + 0.65 * (0.5 + 0.5 * Math.sin(p.phase)));
      if (alpha <= 0.012) continue;

      var sprite = sprites[p.colour][p.layer];
      var size = sprite.width * p.scale;
      ctx.globalAlpha = alpha;
      ctx.drawImage(sprite, p.x - size / 2, p.y - size / 2, size, size);
    }

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function tick() {
    draw(true);
    frame = window.requestAnimationFrame(tick);
  }

  function start() {
    if (reduce || frame !== null) return;
    frame = window.requestAnimationFrame(tick);
  }

  function stop() {
    if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
  }

  /* ---------- Keeping the obstacles current ---------- */

  // The canvas is fixed and the panels are not, so their positions change
  // on every scroll. Re-read at most once a frame.
  var pending = false;
  function refreshSoon() {
    if (pending) return;
    pending = true;
    window.requestAnimationFrame(function () {
      pending = false;
      readObstacles();
      if (reduce) draw(false);
    });
  }
  window.addEventListener('scroll', refreshSoon, { passive: true });

  // Filters, purges and drafts all change the page height without a scroll
  // or a resize, so watch the document itself as well.
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(refreshSoon);
    ro.observe(document.documentElement);
  }

  var resizeTimer = null;
  window.addEventListener('resize', function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resize, 180);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop(); else start();
  });

  resize();
  start();
})();
