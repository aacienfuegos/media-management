"use strict";

document.addEventListener("DOMContentLoaded", () => {
  for (const all of document.querySelectorAll("input[data-select-all]")) {
    const scope = document.getElementById(all.dataset.selectAll);
    all.addEventListener("change", () => {
      for (const box of scope.querySelectorAll("input[type=checkbox][name=file_id]")) {
        box.checked = all.checked;
      }
      updateSelection();
    });
  }
  for (const box of document.querySelectorAll("input[type=checkbox][name=file_id]")) {
    box.addEventListener("change", updateSelection);
  }
  updateSelection();

  for (const table of document.querySelectorAll("table[data-sortable]")) {
    makeSortable(table);
  }

  for (const input of document.querySelectorAll("input[data-filter]")) {
    const table = document.getElementById(input.dataset.filter);
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      for (const row of table.tBodies[0].rows) {
        row.hidden = q !== "" && !row.textContent.toLowerCase().includes(q);
      }
    });
  }

  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = "Copiado";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(source);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        button.textContent = "Selecciónalo y cópialo";
      }
    });
  }

  for (const select of document.querySelectorAll("select[data-mode]")) {
    const toggle = () => {
      for (const el of document.querySelectorAll("[data-show-mode]")) {
        el.hidden = el.dataset.showMode !== select.value;
        for (const input of el.querySelectorAll("input")) {
          input.required = !el.hidden && input.dataset.requiredWhenShown !== undefined;
        }
      }
    };
    select.addEventListener("change", toggle);
    toggle();
  }
});

function updateSelection() {
  const counter = document.getElementById("selected-count");
  if (!counter) return;
  const boxes = document.querySelectorAll("input[type=checkbox][name=file_id]:checked");
  counter.textContent = String(boxes.length);
  const submit = document.getElementById("selected-submit");
  if (submit) submit.disabled = boxes.length === 0;
}

function makeSortable(table) {
  const headers = table.tHead.rows[0].cells;
  for (let i = 0; i < headers.length; i++) {
    const th = headers[i];
    if (th.dataset.sort === undefined) continue;
    th.classList.add("sortable");
    th.addEventListener("click", () => {
      const asc = !th.classList.contains("sorted-asc");
      for (const other of headers) other.classList.remove("sorted-asc", "sorted-desc");
      th.classList.add(asc ? "sorted-asc" : "sorted-desc");
      const numeric = th.dataset.sort === "num";
      const rows = Array.from(table.tBodies[0].rows);
      rows.sort((a, b) => {
        const va = a.cells[i].dataset.value ?? a.cells[i].textContent.trim();
        const vb = b.cells[i].dataset.value ?? b.cells[i].textContent.trim();
        const cmp = numeric ? (parseFloat(va) || 0) - (parseFloat(vb) || 0) : va.localeCompare(vb, "es");
        return asc ? cmp : -cmp;
      });
      for (const row of rows) table.tBodies[0].appendChild(row);
    });
  }
}
