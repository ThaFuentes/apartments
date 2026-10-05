(function () {
  document.querySelectorAll("[data-open-dialog]").forEach(function (button) {
    button.addEventListener("click", function () {
      const dialog = document.getElementById(button.getAttribute("data-open-dialog"));
      if (!dialog || !dialog.showModal || dialog.open) return;
      dialog.showModal();
      const field = dialog.querySelector("input:not([type=hidden]), textarea, select");
      if (field) field.focus();
    });
  });
  document.querySelectorAll("dialog.sheet-dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) dialog.close();
    });
    dialog.querySelectorAll("[data-close-dialog]").forEach(function (button) {
      button.addEventListener("click", function () { dialog.close(); });
    });
  });
})();
