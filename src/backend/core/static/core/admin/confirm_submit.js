"use strict";

// A form with a data-confirm attribute asks before it submits. The
// Content-Security-Policy blocks inline event handlers, so the check
// cannot be an onsubmit attribute.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});
