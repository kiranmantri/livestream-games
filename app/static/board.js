// "Placar atual": opens the score modal. htmx (hx-get on the link) fills #boardbody; without JS the link opens the board as a page.
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-board]");
  const dlg = document.getElementById("scoredlg");
  if (link && dlg && dlg.showModal) {
    if (!dlg.open) dlg.showModal();
    return;                                    // htmx performs the GET of the link; do not navigate
  }
  if (e.target === dlg) dlg.close();           // a click on the backdrop
});
