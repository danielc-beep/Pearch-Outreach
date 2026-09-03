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
