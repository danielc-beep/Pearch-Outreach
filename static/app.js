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


/* ---------------------------------------------------------------
   Type-to-find on a long dropdown.

   Seventy-eight mastheads in a native select is a scroll, and the browser's
   own type-ahead only matches from the first letter — so finding the Bega
   District News means typing "The B" and hoping, because every title starts
   with "The".

   This upgrades any <select data-search> into a box you type into and a list
   that narrows as you do, matching anywhere in the name. The select itself
   stays in the DOM and stays the value the form submits: with no JavaScript
   the page is exactly what it was, and every existing change handler and
   form post carries on working untouched.
   --------------------------------------------------------------- */
(function () {
  function optionsOf(select) {
    return Array.from(select.options).map(function (option, index) {
      return {
        index: index,
        value: option.value,
        label: option.textContent.trim(),
        group: option.parentElement.tagName === 'OPTGROUP'
          ? option.parentElement.label : '',
      };
    });
  }

  function upgrade(select) {
    if (select.dataset.searchReady) return;
    select.dataset.searchReady = '1';

    var items = optionsOf(select);
    var wrap = document.createElement('div');
    wrap.className = 'picker';
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'picker-input';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-autocomplete', 'list');
    input.autocomplete = 'off';
    if (select.id) input.id = select.id + '-search';
    var label = select.labels && select.labels[0];
    if (label && select.id) label.setAttribute('for', input.id);
    else if (select.getAttribute('aria-label')) {
      input.setAttribute('aria-label', select.getAttribute('aria-label'));
    }
    var list = document.createElement('ul');
    list.className = 'picker-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(input);
    wrap.appendChild(list);
    wrap.appendChild(select);
    select.classList.add('picker-native');

    var showing = [];
    var active = -1;

    function currentLabel() {
      var chosen = items[select.selectedIndex];
      return chosen ? chosen.label : '';
    }

    // "Every masthead" and "Choose a masthead…" are the empty choice, so they
    // belong in the placeholder. Sitting in the box as typed text they read
    // as a filter someone applied.
    function showChosen() {
      var chosen = items[select.selectedIndex];
      input.value = (chosen && chosen.value) ? chosen.label : '';
    }
    input.placeholder = (items[0] && !items[0].value) ? items[0].label : 'Type to find one';
    showChosen();

    function render(term) {
      var needle = term.trim().toLowerCase();
      showing = needle
        ? items.filter(function (i) { return i.label.toLowerCase().indexOf(needle) > -1; })
        : items;
      list.innerHTML = '';
      if (!showing.length) {
        var none = document.createElement('li');
        none.className = 'picker-none';
        none.textContent = 'No masthead matches that';
        list.appendChild(none);
        return;
      }
      var lastGroup = null;
      showing.forEach(function (item, position) {
        if (item.group && item.group !== lastGroup) {
          var head = document.createElement('li');
          head.className = 'picker-group';
          head.textContent = item.group;
          list.appendChild(head);
          lastGroup = item.group;
        }
        var row = document.createElement('li');
        row.className = 'picker-option';
        row.setAttribute('role', 'option');
        row.textContent = item.label;
        row.dataset.position = position;
        if (item.index === select.selectedIndex) row.classList.add('is-chosen');
        list.appendChild(row);
      });
      active = -1;
    }

    function open() {
      render(input.value === currentLabel() ? '' : input.value);
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      showChosen();                      // never leave a half-typed name showing
    }

    function choose(position) {
      var item = showing[position];
      if (!item) return;
      select.selectedIndex = item.index;
      input.value = item.label;
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      // The page's own handlers listen to the select, not to this.
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function highlight(step) {
      var rows = list.querySelectorAll('.picker-option');
      if (!rows.length) return;
      active = Math.max(0, Math.min(rows.length - 1, active + step));
      rows.forEach(function (r) { r.classList.remove('is-active'); });
      rows[active].classList.add('is-active');
      rows[active].scrollIntoView({block: 'nearest'});
    }

    input.addEventListener('focus', open);
    input.addEventListener('input', function () {
      render(input.value);
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) open(); highlight(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); highlight(-1); }
      else if (e.key === 'Enter') {
        if (!list.hidden) {
          e.preventDefault();
          var rows = list.querySelectorAll('.picker-option');
          // Enter with nothing highlighted takes the only match, which is
          // what typing a name almost to the end and pressing Enter means.
          choose(Number((rows[active] || rows[0] || {dataset: {}}).dataset.position));
        }
      } else if (e.key === 'Escape') { close(); input.blur(); }
    });
    list.addEventListener('mousedown', function (e) {
      var row = e.target.closest('.picker-option');
      if (!row) return;
      e.preventDefault();                 // keep focus off the blur handler
      choose(Number(row.dataset.position));
    });
    input.addEventListener('blur', function () {
      window.setTimeout(function () { if (!wrap.contains(document.activeElement)) close(); }, 0);
    });
    // Something else set the value — the align page's "accept all", say.
    select.addEventListener('change', showChosen);
  }

  function upgradeAll(root) {
    (root || document).querySelectorAll('select[data-search]').forEach(upgrade);
  }

  document.addEventListener('DOMContentLoaded', function () { upgradeAll(); });
  window.upgradePickers = upgradeAll;     // for rows added after load
})();
