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
