"use strict";

const app = document.getElementById("app");
const ICONS = {
  download: "M12 3a1 1 0 0 1 1 1v9.6l3.3-3.3 1.4 1.4-5.7 5.7-5.7-5.7 1.4-1.4 3.3 3.3V4a1 1 0 0 1 1-1ZM5 19h14v2H5z",
  check: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  lock: "M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5Zm-3 8V7a3 3 0 1 1 6 0v3H9Zm3 3a2 2 0 0 1 1 3.7V19h-2v-2.3a2 2 0 0 1 1-3.7Z",
  person: "M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.4 0-8 2.2-8 5v2h16v-2c0-2.8-3.6-5-8-5Z",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16Zm1 3h-2v6l5 3 1-1.7-4-2.3V7Z",
  alert: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm-1 5h2v7h-2V7Zm0 9h2v2h-2v-2Z",
  link: "M7 7h4v2H7a3 3 0 0 0 0 6h4v2H7A5 5 0 0 1 7 7Zm6 0h4a5 5 0 0 1 0 10h-4v-2h4a3 3 0 0 0 0-6h-4V7Zm-5 4h8v2H8v-2Z",
  video: "M4 5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5l5 3.5V6l-5 3.5V7a2 2 0 0 0-2-2H4Z",
  photo: "M4 4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4Zm4 3.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM4 18l5-6 3.5 4 2.5-3 5 5H4Z",
  zoom: "M10 3a7 7 0 1 0 4.2 12.6l5.1 5.1 1.4-1.4-5.1-5.1A7 7 0 0 0 10 3Zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm-1 2h2v2h2v2h-2v2H9v-2H7V9h2V7Z",
  file: "M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm7 1.5V9h5.5L13 3.5Z",
};

function icon(name, size = 20) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("fill-rule", "evenodd");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function screen(options, ...children) {
  const card = el("div", { class: options.wide ? "card wide" : "card" + (options.center ? " center" : "") }, ...children);
  app.replaceChildren(card);
  const target = card.querySelector("[data-focus]") || card.querySelector("h1");
  if (target) {
    if (target.tagName === "H1") target.tabIndex = -1;
    target.focus({ preventScroll: target.tagName === "H1" });
  }
}

function badge(name, kind = "") {
  return el("div", { class: "badge " + kind }, icon(name, 28));
}

function message(iconName, title, text, ...extra) {
  document.title = title;
  const kind = iconName === "alert" ? "b-error" : iconName === "link" ? "b-neutral" : "b-wait";
  screen({ center: true }, badge(iconName, kind), el("h1", {}, title), el("p", { class: "muted" }, text), ...extra);
}

function loading(text = "Cargando…") {
  app.replaceChildren(el("div", { class: "card center" },
    el("div", { class: "loading", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), el("p", {}, text))));
}

function formatSize(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  return (unit === 0 ? String(size) : size.toFixed(1).replace(".", ",")) + "\u00a0" + units[unit];
}

function formatDate(iso) {
  return new Date(iso).toLocaleString("es-ES", {
    timeZone: "Europe/Madrid", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
  });
}

