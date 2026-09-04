/**
 * POST JSON and get JSON back, with a readable error when we don't.
 *
 * A hosting proxy answers a timed-out or failed request with an HTML error
 * page, and calling .json() on that throws "Unexpected token '<'" — which
 * tells the user nothing. Read the body as text first, then decide.
 */
window.postJSON = async function (url, body) {
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
  } catch (err) {
    throw new Error('Could not reach the server. Check your connection and try again.');
  }

  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    if (res.status === 502 || res.status === 504 || /^\s*</.test(text)) {
      throw new Error(
        'The server took too long to answer (HTTP ' + res.status + '). Any work it '
        + 'finished has been saved — reload the page to see how far it got.');
    }
    throw new Error('The server sent something unreadable (HTTP ' + res.status + ').');
  }
  if (!res.ok) throw new Error(data.detail || ('Request failed (HTTP ' + res.status + ')'));
  return data;
};

// Shared behaviour. Deliberately tiny — each page carries its own logic.
document.addEventListener('DOMContentLoaded', () => {
  // Submitting a filter form should drop you back on page 1.
  document.querySelectorAll('form.filters').forEach((form) => {
    form.addEventListener('submit', () => {
      const page = form.querySelector('[name=page_no]');
      if (page) page.value = 1;
    });
  });

  // Copy any element with data-copy on click.
  document.querySelectorAll('[data-copy]').forEach((el) => {
    el.style.cursor = 'copy';
    el.addEventListener('click', () => {
      navigator.clipboard?.writeText(el.dataset.copy || el.textContent.trim());
      const original = el.textContent;
      el.textContent = 'copied';
      setTimeout(() => { el.textContent = original; }, 900);
    });
  });
});


/* ---------------------------------------------------------------
   Entrances and counting.

   Both are deliberately cheap: a stagger capped so a hundred-row table
   does not take three seconds to arrive, and a count-up short enough
   that nobody waits to read a number they can already see.
   --------------------------------------------------------------- */
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  document.addEventListener('DOMContentLoaded', function () {
    if (reduce) return;

    // Stagger the things that arrive as a group.
    ['.board-item', '.address-row', '.grid-4 > .card', '.pipeline .stage']
      .forEach(function (selector) {
        var nodes = document.querySelectorAll(selector);
        for (var i = 0; i < nodes.length && i < 12; i++) {
          nodes[i].style.animationDelay = (i * 45) + 'ms';
          nodes[i].classList.add('js-enter');
        }
      });

    // Count the big numbers up. Only the big ones — a count-up on a table
    // cell is noise, and on a number above a few thousand it is a slot
    // machine, so both are left alone.
    document.querySelectorAll('.stat .num, .board-n').forEach(function (el) {
      var text = el.firstChild && el.firstChild.nodeValue;
      var target = parseInt((text || '').trim(), 10);
      if (!target || target < 2 || target > 9999) return;

      var started = null;
      var duration = 420;
      el.firstChild.nodeValue = '0';
      (function step(now) {
        if (started === null) started = now;
        var through = Math.min(1, (now - started) / duration);
        // Ease out, so it lands rather than stopping dead.
        var eased = 1 - Math.pow(1 - through, 3);
        el.firstChild.nodeValue = String(Math.round(target * eased));
        if (through < 1) window.requestAnimationFrame(step);
      })(performance.now());
    });
  });
})();
