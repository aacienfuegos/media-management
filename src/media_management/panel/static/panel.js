"use strict";

document.documentElement.classList.add("js");

const boxes = () => Array.from(document.querySelectorAll("input[type=checkbox][name=file_id]"));
const SELECTION_KEY = "mm-selection";

function loadSelection() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(SELECTION_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

function saveSelection(selection) {
  try {
    if (selection.size) sessionStorage.setItem(SELECTION_KEY, JSON.stringify([...selection]));
    else sessionStorage.removeItem(SELECTION_KEY);
  } catch {}
}

function updateSelection(selection) {
  const counter = document.getElementById("selected-count");
  if (!counter) return;
  const all = boxes();
  const checked = all.filter((b) => b.checked);
  const elsewhere = selection.size - checked.length;
  counter.textContent = String(selection.size);
  document.getElementById("selected-label").textContent =
    (selection.size === 1 ? "seleccionado" : "seleccionados") + (elsewhere > 0 ? ` (${elsewhere} en otras páginas)` : "");
  document.querySelector(".selbar").toggleAttribute("data-empty", selection.size === 0);
  for (const box of all) box.closest("tr").classList.toggle("selected", box.checked);
  for (const master of document.querySelectorAll("input[data-select-all]")) {
    master.checked = checked.length > 0 && checked.length === all.length;
    master.indeterminate = checked.length > 0 && checked.length < all.length;
  }
}

function setupSelection() {
  const form = document.querySelector("form[data-selection]");
  if (!form) return;
  const selection = loadSelection();
  const all = boxes();
  let last = null;
  const sync = () => {
    for (const box of all) {
      if (box.checked) selection.add(box.value);
      else selection.delete(box.value);
    }
    saveSelection(selection);
    updateSelection(selection);
  };
  for (const box of all) box.checked = selection.has(box.value);
  for (const master of document.querySelectorAll("input[data-select-all]")) {
    master.addEventListener("change", () => {
      for (const box of all) box.checked = master.checked;
      sync();
    });
  }
  for (const box of all) {
    box.addEventListener("click", (event) => {
      if (event.shiftKey && last && last !== box) {
        const [a, b] = [all.indexOf(last), all.indexOf(box)].sort((x, y) => x - y);
        for (const other of all.slice(a, b + 1)) other.checked = box.checked;
      }
      last = box;
      sync();
    });
    const row = box.closest("tr");
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, input, button, label")) return;
      box.click();
    });
  }
  for (const clear of document.querySelectorAll("[data-clear-selection]")) {
    clear.addEventListener("click", () => {
      for (const box of all) box.checked = false;
      selection.clear();
      sync();
    });
  }
  form.addEventListener("submit", () => {
    const onPage = new Set(all.map((b) => b.value));
    for (const id of selection) {
      if (onPage.has(id)) continue;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "file_id";
      hidden.value = id;
      form.appendChild(hidden);
    }
  });
  updateSelection(selection);
}

function makeSortable(table) {
  const headers = table.tHead.rows[0].cells;
  for (let i = 0; i < headers.length; i++) {
    const th = headers[i];
    if (th.dataset.sort === undefined) continue;
    th.classList.add("sortable");
    th.tabIndex = 0;
    const sort = () => {
      const asc = !th.classList.contains("sorted-asc");
      for (const other of headers) {
        other.classList.remove("sorted-asc", "sorted-desc");
        other.removeAttribute("aria-sort");
      }
      th.classList.add(asc ? "sorted-asc" : "sorted-desc");
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      const numeric = th.dataset.sort === "num";
      const rows = Array.from(table.tBodies[0].rows).filter((r) => r.cells.length === headers.length);
      rows.sort((a, b) => {
        const va = a.cells[i].dataset.value ?? a.cells[i].textContent.trim();
        const vb = b.cells[i].dataset.value ?? b.cells[i].textContent.trim();
        const cmp = numeric ? (parseFloat(va) || 0) - (parseFloat(vb) || 0) : va.localeCompare(vb, "es");
        return asc ? cmp : -cmp;
      });
      for (const row of rows) table.tBodies[0].appendChild(row);
    };
    th.addEventListener("click", sort);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
  }
}

async function copyFrom(button) {
  const source = document.getElementById(button.dataset.copy);
  const original = button.dataset.label ?? button.textContent;
  button.dataset.label = original;
  try {
    await navigator.clipboard.writeText(source.textContent.trim());
    button.textContent = "Copiado ✓";
  } catch {
    if (!source.hidden) {
      const range = document.createRange();
      range.selectNodeContents(source);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
    button.textContent = "No se ha podido copiar: selecciónalo a mano";
  }
  setTimeout(() => { button.textContent = original; }, 2500);
}

function setupModeToggle() {
  for (const radio of document.querySelectorAll("input[name=mode]")) {
    const toggle = () => {
      const mode = document.querySelector("input[name=mode]:checked")?.value;
      for (const el of document.querySelectorAll("[data-show-mode]")) {
        for (const input of el.querySelectorAll("input[data-required-when-shown]")) {
          input.required = el.dataset.showMode === mode;
        }
      }
    };
    radio.addEventListener("change", toggle);
    toggle();
  }
}

function setupPresets() {
  for (const button of document.querySelectorAll("button[data-days]")) {
    const input = button.closest(".field").querySelector("input[type=number]");
    const sync = () => {
      for (const b of button.parentElement.querySelectorAll("button[data-days]")) {
        b.setAttribute("aria-pressed", String(b.dataset.days === input.value));
      }
    };
    button.addEventListener("click", () => { input.value = button.dataset.days; sync(); });
    input.addEventListener("input", sync);
    sync();
  }
}

function setupShortcuts() {
  let pendingG = 0;
  const targets = { b: "/library", e: "/links", s: "/requests", p: "/trash" };
  const dialog = document.getElementById("shortcuts");
  for (const opener of document.querySelectorAll("[data-open]")) {
    opener.addEventListener("click", () => document.getElementById(opener.dataset.open).showModal());
  }
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const typing = e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "Escape") {
      if (typing && e.target.matches("[data-search]")) e.target.blur();
      else document.querySelector("[data-clear-selection]")?.click();
      return;
    }
    if (typing) return;
    if (e.key === "/") {
      e.preventDefault();
      const search = document.querySelector("[data-search]");
      if (search) { search.focus(); search.select(); } else location.href = "/library#buscar";
    } else if (e.key === "?") {
      dialog.showModal();
    } else if (e.key === "g") {
      pendingG = Date.now();
    } else if (pendingG && Date.now() - pendingG < 1200 && targets[e.key]) {
      location.href = targets[e.key];
    } else {
      pendingG = 0;
    }
  });
  document.addEventListener("click", (e) => {
    for (const menu of document.querySelectorAll("details.more[open]")) {
      if (!menu.contains(e.target)) menu.open = false;
    }
  });
  if (location.hash === "#buscar") document.querySelector("[data-search]")?.focus();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.querySelector("[data-link-created]")) saveSelection(new Set());
  setupSelection();
  for (const table of document.querySelectorAll("table[data-sortable]")) makeSortable(table);

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
    button.addEventListener("click", () => copyFrom(button));
  }

  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (e) => { if (!confirm(form.dataset.confirm)) e.preventDefault(); });
  }

  for (const form of document.querySelectorAll("form[data-autosubmit]")) {
    for (const select of form.querySelectorAll("select")) {
      select.addEventListener("change", () => form.requestSubmit());
    }
  }

  const stem = document.querySelector("input[data-select-stem]");
  if (stem) {
    const dot = stem.value.lastIndexOf(".");
    stem.setSelectionRange(0, dot > 0 ? dot : stem.value.length);
  }
  document.querySelector("[data-autofocus]")?.focus();

  setupModeToggle();
  setupPresets();
  setupShortcuts();

  const url = new URL(location.href);
  if (url.searchParams.has("hecho")) {
    url.searchParams.delete("hecho");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
});
