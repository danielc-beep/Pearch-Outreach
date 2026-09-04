/* ---------------------------------------------------------------
   Fairy lights — the drifting swarm behind the ACM night sky.

   Thousands of very small, very dim points. Two rules keep it from
   ever competing with the page: nothing is drawn above alpha 0.55,
   and the canvas is pinned behind the content with pointer events
   off, so it is decoration you can neither click nor read through.

   The field is drawn once and left still for anyone who has asked
   their system for reduced motion, and the loop stops entirely
   while the tab is hidden rather than burning a background CPU.
   --------------------------------------------------------------- */
(function () {
  var canvas = document.getElementById('fairy-lights');
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext('2d', { alpha: true });
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var lights = [];
  var w = 0, h = 0, dpr = 1;
  var frame = null;

  // A cool ACM palette: mostly white-blue dust with a few cyan sparks.
  var COLOURS = ['255,255,255', '167,226,247', '53,198,240', '0,154,199'];

  function rand(min, max) { return min + Math.random() * (max - min); }

  function build() {
    // One light per ~700 css px of viewport, so a laptop carries a couple
    // of thousand and a phone is not asked to draw the same swarm.
    var count = Math.round(Math.min(3000, Math.max(600, (w * h) / 700)));
    lights = new Array(count);
    for (var i = 0; i < count; i++) {
      var big = Math.random() < 0.06;           // a few larger, softer sparks
      lights[i] = {
        x: Math.random() * w,
        y: Math.random() * h,
        r: big ? rand(1.4, 2.4) : rand(0.35, 1.1),
        vx: rand(-0.16, 0.16),
        vy: rand(-0.14, 0.06),                  // a gentle upward bias
        base: big ? rand(0.20, 0.40) : rand(0.14, 0.52),
        phase: Math.random() * Math.PI * 2,
        speed: rand(0.006, 0.022),              // twinkle rate
        colour: COLOURS[Math.floor(Math.random() * COLOURS.length)],
        glow: big
      };
    }
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
    if (reduce) draw(false);
  }

  function draw(animate) {
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < lights.length; i++) {
      var p = lights[i];

      if (animate) {
        p.x += p.vx;
        p.y += p.vy;
        p.phase += p.speed;
        // Wrap at the edges so the swarm never thins out at one side.
        if (p.x < -4) p.x = w + 4; else if (p.x > w + 4) p.x = -4;
        if (p.y < -4) p.y = h + 4; else if (p.y > h + 4) p.y = -4;
      }

      var alpha = p.base * (0.55 + 0.45 * Math.sin(p.phase));
      if (alpha <= 0.01) continue;

      if (p.glow) {
        ctx.shadowBlur = 6;
        ctx.shadowColor = 'rgba(' + p.colour + ',' + (alpha * 0.8).toFixed(3) + ')';
      } else {
        ctx.shadowBlur = 0;
      }
      ctx.fillStyle = 'rgba(' + p.colour + ',' + alpha.toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, 6.283185307179586);
      ctx.fill();
    }
    ctx.shadowBlur = 0;
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
