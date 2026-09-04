/* ---------------------------------------------------------------
   Fairy lights — the drifting swarm behind the ACM night sky.

   Thousands of soft points on three depth layers: far ones small,
   dim and slow, near ones larger, brighter and quicker, so the
   field has parallax and reads as alive rather than as a static
   starfield. A slow current pushes the whole swarm, and every
   light twinkles on its own clock.

   Readability is what shapes the numbers. The canvas sits on a
   negative z-index behind the content, and the panels above it are
   90% opaque — so a light passing behind a paragraph shows through
   at a tenth of its brightness, as a shimmer, while the open parts
   of the page get the swarm at full strength.

   Each light is drawn as a pre-rendered glow sprite rather than an
   arc with a shadow blur. Shadow blur at this many points drops a
   laptop to single-figure frame rates; drawImage does not.
   --------------------------------------------------------------- */
(function () {
  var canvas = document.getElementById('fairy-lights');
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext('2d', { alpha: true });
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var lights = [];
  var sprites = [];
  var w = 0, h = 0, dpr = 1;
  var frame = null;
  var t = 0;

  // Mostly white-blue dust, with ACM cyan running through it.
  var COLOURS = [[255, 255, 255], [186, 232, 250], [110, 214, 245], [0, 154, 199]];

  // Three depths. Far lights are small, dim and slow; near ones carry the
  // sense of movement. The weights decide how many land on each layer.
  // Sizes are small on purpose. Larger glows read as out-of-focus bokeh
  // rather than fairy lights, and behind a headline they compete with it.
  // Alphas are set against the lifted navy ground. These composite as
  // 'lighter', so raising the page background eats the dimmest lights —
  // the low ends carry more than they look like they should.
  var LAYERS = [
    { weight: 0.62, size: 0.85, speed: 0.10, alpha: [0.26, 0.58] },
    { weight: 0.30, size: 1.45, speed: 0.26, alpha: [0.36, 0.76] },
    { weight: 0.08, size: 2.40, speed: 0.52, alpha: [0.40, 0.88] }
  ];

  function rand(min, max) { return min + Math.random() * (max - min); }

  /* A radial-gradient dot, drawn once per colour and size, then stamped. */
  function buildSprites() {
    sprites = [];
    for (var c = 0; c < COLOURS.length; c++) {
      var row = [];
      for (var l = 0; l < LAYERS.length; l++) {
        var r = LAYERS[l].size * 2.2;            // room for the falloff
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

  function pickLayer() {
    var r = Math.random(), acc = 0;
    for (var i = 0; i < LAYERS.length; i++) {
      acc += LAYERS[i].weight;
      if (r <= acc) return i;
    }
    return 0;
  }

  function build() {
    // One light per ~620 css px of viewport. A 1440x900 laptop carries about
    // 2,100; a phone is not asked to draw the same swarm.
    var count = Math.round(Math.min(3200, Math.max(650, (w * h) / 620)));
    lights = new Array(count);
    for (var i = 0; i < count; i++) {
      var l = pickLayer();
      var layer = LAYERS[l];
      var angle = rand(0, Math.PI * 2);
      lights[i] = {
        x: Math.random() * w,
        y: Math.random() * h,
        layer: l,
        colour: Math.floor(Math.random() * COLOURS.length),
        vx: Math.cos(angle) * layer.speed * rand(0.5, 1.3),
        vy: Math.sin(angle) * layer.speed * rand(0.5, 1.3) - layer.speed * 0.35,
        base: rand(layer.alpha[0], layer.alpha[1]),
        phase: Math.random() * Math.PI * 2,
        speed: rand(0.010, 0.045),
        scale: rand(0.75, 1.15)
      };
    }
  }

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
    build();
    if (reduce) draw(false);
  }

  function draw(animate) {
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';   // lights add, they don't occlude

    if (animate) t += 0.0016;
    // A slow current, so the whole swarm drifts together as well as apart.
    var curX = Math.cos(t) * 0.16;
    var curY = Math.sin(t * 0.7) * 0.10;

    for (var i = 0; i < lights.length; i++) {
      var p = lights[i];

      if (animate) {
        var pull = LAYERS[p.layer].speed;
        p.x += p.vx + curX * pull;
        p.y += p.vy + curY * pull;
        p.phase += p.speed;
        // Wrap at the edges so the swarm never thins out at one side.
        if (p.x < -12) p.x = w + 12; else if (p.x > w + 12) p.x = -12;
        if (p.y < -12) p.y = h + 12; else if (p.y > h + 12) p.y = -12;
      }

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
