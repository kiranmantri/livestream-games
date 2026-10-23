// "Iniciar nova partida": opens the start dialog as a modal. Without JS the link (?start=1) shows the same dialog inline.
document.addEventListener("click", (e) => {
  const opener = e.target.closest("[data-open]");
  if (opener) {
    const d = document.getElementById(opener.dataset.open);
    if (d && d.showModal) { e.preventDefault(); d.showModal(); d.querySelector("select, input[type=url]")?.focus(); }
    return;
  }
  if (e.target.closest("[data-close]")) { e.target.closest("dialog").close(); return; }
  if (e.target.tagName === "DIALOG") e.target.close();      // a click on the backdrop (the form fills the dialog box)
});
// Server-rendered open dialogs (?start=1, or an answer with a message) become real modals.
document.querySelectorAll("dialog[open], dialog[data-autoopen]").forEach((d) => {
  if (d.showModal) { d.removeAttribute("open"); d.showModal(); }
});
