// Forms with a data-confirm attribute ask before submitting. The message is
// read as plain text, so names containing quotes can't break out of it.
document.addEventListener("submit", e => {
  const message = e.target.dataset && e.target.dataset.confirm;
  if (message && !window.confirm(message)) e.preventDefault();
});

// A checkbox with data-shows="<id>" shows that element only while it is ticked.
document.querySelectorAll("input[type=checkbox][data-shows]").forEach(box => {
  const target = document.getElementById(box.dataset.shows);
  const sync = () => { if (target) target.hidden = !box.checked; };
  box.addEventListener("change", sync);
  sync();
});

// A checkbox with data-autorefresh reloads the page every 5 seconds while ticked (remembered per tab).
document.querySelectorAll("input[type=checkbox][data-autorefresh]").forEach(box => {
  const key = "autorefresh:" + location.pathname;
  box.checked = sessionStorage.getItem(key) === "1";
  box.addEventListener("change", () => sessionStorage.setItem(key, box.checked ? "1" : "0"));
  setInterval(() => { if (box.checked) location.reload(); }, 5000);
});
