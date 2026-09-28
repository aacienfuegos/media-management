"use strict";

// El token viaja en el fragmento (#...), que el navegador nunca envía al servidor:
// así no acaba en ningún access log ni en el Referer. Se manda en el cuerpo de un POST.
// La credencial (contraseña o código) vive solo en memoria mientras la página está abierta.

const app = document.getElementById("app");
const state = { token: null, password: null, code: null };

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
  screen({ center: true }, badge(iconName, "b-error"), el("h1", {}, title), el("p", { class: "muted" }, text), ...extra);
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
  return (unit === 0 ? String(size) : size.toFixed(1).replace(".", ",")) + " " + units[unit];
}

function formatDate(iso) {
  return new Date(iso).toLocaleString("es-ES", {
    timeZone: "Europe/Madrid", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
  });
}

async function post(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit",
      cache: "no-store",
    });
  } catch {
    return { status: 0, data: {} };
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { status: response.status, data };
}

function credentials() {
  const body = { token: state.token };
  if (state.password) body.password = state.password;
  if (state.code) body.code = state.code;
  return body;
}

async function openLink() {
  loading();
  const { status, data } = await post("/api/share", credentials());
  if (status === 200) return showFiles(data);
  if (status === 202 && data.status === "pending") return showPending();
  if (status === 401 && data.need === "password") return showPassword(data.error === "bad_credentials");
  if (status === 401 && data.need === "code") return showRequest(data.error === "bad_credentials");
  if (status === 404) return message("link", "Este enlace ya no está disponible", "Puede que haya caducado o que lo hayan retirado. Si lo necesitas, pide a quien te lo envió uno nuevo.");
  if (status === 429) return message("clock", "Demasiados intentos", "Por seguridad, espera unos minutos antes de volver a intentarlo.", retryButton());
  return message("alert", "No se ha podido abrir el enlace", "Comprueba tu conexión a internet e inténtalo de nuevo.", retryButton());
}

function retryButton(label = "Reintentar") {
  return el("button", { type: "button", class: "primary block", on: { click: openLink } }, label);
}

function errorLine(text) {
  return el("p", { class: "error", role: "alert" }, icon("alert", 18), text);
}

function showPassword(wrong) {
  document.title = "Contraseña";
  const input = el("input", { type: "password", id: "password", autocomplete: "current-password", required: "", maxlength: "256",
    "data-focus": "", ...(wrong ? { "aria-invalid": "true", "aria-describedby": "password-error" } : {}) });
  const toggle = el("button", { type: "button", "aria-controls": "password", "aria-pressed": "false" }, "Mostrar");
  toggle.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    toggle.textContent = show ? "Ocultar" : "Mostrar";
    toggle.setAttribute("aria-pressed", String(show));
    input.focus();
  });
  const form = el("form", { on: { submit: (e) => { e.preventDefault(); state.password = input.value; openLink(); } } },
    el("label", { for: "password" }, "Contraseña"),
    el("div", { class: "with-toggle" }, input, toggle),
    wrong ? el("p", { class: "error", id: "password-error", role: "alert" }, icon("alert", 18), "Esa contraseña no es correcta. Revísala e inténtalo otra vez.") : null,
    el("button", { class: "primary block big" }, "Ver los archivos"));
  screen({ center: true }, badge("lock"), el("h1", {}, "Este enlace tiene contraseña"),
    el("p", { class: "muted" }, "Escribe la contraseña que te ha dado quien te envió el enlace."), form);
}

function codeForm(wrong) {
  const input = el("input", { id: "code", class: "code-input", autocomplete: "off", autocapitalize: "characters", spellcheck: "false",
    required: "", maxlength: "32", placeholder: "XXXXX-XXXXX", inputmode: "text",
    ...(wrong ? { "aria-invalid": "true", "data-focus": "" } : {}) });
  return el("form", { on: { submit: (e) => { e.preventDefault(); state.code = input.value; openLink(); } } },
    el("label", { for: "code" }, "Tu código personal"),
    input,
    wrong ? errorLine("Ese código no vale para este enlace. Revisa que esté completo.") : null,
    el("button", { class: "primary block" }, "Entrar con mi código"));
}

function showRequest(wrongCode) {
  state.code = null;
  document.title = "Pedir acceso";
  const name = el("input", { id: "name", required: "", maxlength: "80", autocomplete: "name" });
  const note = el("textarea", { id: "note", maxlength: "500", rows: "3", placeholder: "Por ejemplo: soy la prima de Ana" });
  const send = el("button", { class: "primary block big" }, "Pedir acceso");
  const requestForm = el("form", { on: { submit: (e) => { e.preventDefault(); send.disabled = true; requestAccess(name.value, note.value); } } },
    el("label", { for: "name" }, "Tu nombre"),
    name,
    el("label", { for: "note" }, "Mensaje ", el("span", { class: "muted" }, "(opcional)")),
    note,
    send);
  const slot = el("div", { class: "alt" });
  const reveal = el("button", { type: "button", class: "link" }, "Ya tengo un código");
  reveal.addEventListener("click", () => {
    slot.replaceChildren(codeForm(false));
    slot.querySelector("input").focus();
  });
  slot.append(reveal);
  if (wrongCode) slot.replaceChildren(codeForm(true));
  screen({ center: true }, badge("person"), el("h1", {}, "Pide acceso para descargar"),
    el("p", { class: "muted" }, "Quien compartió estos archivos quiere saber quién los descarga. Escribe tu nombre y recibirás un código personal para entrar cuando te lo aprueben."),
    requestForm,
    el("p", { class: "divider" }, "¿Ya lo pediste antes?"),
    slot);
}

async function requestAccess(name, note) {
  loading("Enviando la solicitud…");
  const { status, data } = await post("/api/request", { token: state.token, name, note });
  if (status === 200 && data.code) return showNewCode(data.code);
  if (status === 429 && data.error === "queue_full") return message("clock", "Ahora no se aceptan más solicitudes", "Hay demasiadas solicitudes pendientes para este enlace. Inténtalo más tarde.", retryButton("Volver"));
  if (status === 429) return message("clock", "Demasiadas solicitudes", "Espera un rato antes de volver a pedir acceso.", retryButton("Volver"));
  if (status === 404) return openLink();
  return message("alert", "No se ha podido enviar la solicitud", "Comprueba tu conexión e inténtalo de nuevo.", retryButton("Volver"));
}

function showNewCode(code) {
  document.title = "Solicitud enviada";
  const copy = el("button", { type: "button", class: "block" }, "Copiar el código");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.replaceChildren(icon("check", 18), "Copiado");
    } catch {
      copy.textContent = "Mantén pulsado el código para copiarlo";
    }
  });
  const saved = el("button", { class: "primary block big", type: "button", on: { click: () => { state.code = code; openLink(); } } },
    "Ya lo he guardado");
  screen({ center: true }, badge("check", "b-ok"), el("h1", {}, "Solicitud enviada"),
    el("p", {}, "Este es tu código personal:"),
    el("output", { class: "code", "aria-label": "Tu código personal" }, code),
    copy,
    el("div", { class: "notice", role: "note" }, icon("alert", 20),
      el("span", {}, "Guárdalo ahora (una captura de pantalla vale). No se puede recuperar: si lo pierdes, tendrás que volver a pedir acceso.")),
    el("p", { class: "muted small" }, "Cuando aprueben tu solicitud, abre de nuevo este mismo enlace y escribe el código. Sirve desde cualquier dispositivo."),
    saved);
}

function showPending() {
  document.title = "Pendiente de aprobar";
  screen({ center: true }, badge("clock", "b-wait"), el("h1", {}, "Tu solicitud está pendiente"),
    el("p", { class: "muted" }, "Todavía no la han aprobado. Vuelve a abrir este enlace más tarde y entra con tu código."),
    retryButton("Comprobar de nuevo"));
}

const KINDS = { video: "Vídeo", photo: "Foto" };

function thumbFor(file) {
  if (file.thumb) return el("img", { class: "thumb", src: file.thumb, alt: "" });
  return el("div", { class: "thumb icon" }, icon(KINDS[file.kind] ? file.kind : "file", 28));
}

function showFiles(data) {
  document.title = data.title;
  const count = data.files.length;
  const total = data.files.reduce((sum, f) => sum + f.size, 0);
  const rows = data.files.map(fileRow);
  const head = el("header", { class: "share-head" },
    el("p", { class: "eyebrow" }, "Archivos compartidos contigo"),
    el("h1", {}, data.title),
    el("ul", { class: "meta" },
      el("li", {}, `${count} archivo${count === 1 ? "" : "s"} · ${formatSize(total)}`),
      el("li", {}, icon("clock", 16), `Disponible hasta el ${formatDate(data.expires_at)}`)));
  const body = count === 0
    ? el("p", { class: "muted" }, "Este enlace ya no tiene archivos disponibles.")
    : el("ul", { class: count === 1 ? "files single" : "files", "aria-label": "Archivos" }, ...rows.map((r) => r.node));
  screen({ wide: true }, head,
    count > 1 ? downloadAll(rows, data.zip) : null,
    body,
    count ? el("p", { class: "foot" }, "Si una descarga se corta, puedes reanudarla desde el navegador durante unas horas.") : null);
}

function fileRow(file) {
  const status = el("p", { class: "status", role: "status" });
  const button = el("button", { class: "primary", type: "button", "aria-label": `Descargar ${file.name}` },
    icon("download"), el("span", { class: "label" }, "Descargar"));
  const node = el("li", { class: "file" }, thumbFor(file),
    el("div", {}, el("div", { class: "name" }, file.name),
      el("div", { class: "info" }, `${KINDS[file.kind] || "Archivo"} · ${formatSize(file.size)}`), status),
    button);
  const row = { file, button, status, node };
  button.addEventListener("click", () => download(row));
  return row;
}

function downloadAll(rows, zip) {
  const status = el("p", { class: "status", role: "status" });
  const zipSlot = el("div", { class: "zip-slot" });
  const separate = el("button", { type: "button", class: zip ? "link" : "primary big" }, zip ? "Descargar uno a uno" : "Descargar todos");
  separate.addEventListener("click", async () => {
    separate.disabled = true;
    status.className = "status";
    status.textContent = "Si el navegador pregunta si permites descargar varios archivos, acepta.";
    for (const row of rows) {
      if (!await download(row)) {
        status.textContent = "Se ha parado en " + row.file.name + ". Puedes seguir con el botón de cada archivo.";
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    separate.disabled = false;
  });
  const box = el("div", { class: "all" }, zipSlot, separate, status);
  showZip(zipSlot, zip, status);
  return box;
}

// Cada comprobación vuelve a validar la credencial (argon2 en los enlaces con
// contraseña): se espacian y se paran, y después se comprueba a mano.
const ZIP_POLL_FIRST_MS = 15000;
const ZIP_POLL_MAX_MS = 120000;
const ZIP_POLL_ATTEMPTS = 8;

function showZip(slot, zip, status, attempt = 0) {
  slot.hidden = false;
  if (zip && zip.state === "ready") {
    const button = el("button", { class: "primary big", type: "button" }, icon("download"),
      el("span", { class: "nowrap" }, "Descargar todo"), el("span", { class: "sub" }, `ZIP · ${formatSize(zip.size)}`));
    button.addEventListener("click", () => downloadZip(button, status));
    slot.replaceChildren(button);
  } else if (zip && zip.state === "pending" && attempt < ZIP_POLL_ATTEMPTS) {
    slot.replaceChildren(el("button", { type: "button", class: "primary big", disabled: "" },
      el("span", { class: "spinner sm", "aria-hidden": "true" }), "Preparando el ZIP…"));
    const delay = Math.min(ZIP_POLL_FIRST_MS * 2 ** attempt, ZIP_POLL_MAX_MS);
    setTimeout(() => refreshZip(slot, status, attempt + 1), delay);
  } else if (zip && zip.state === "pending") {
    const check = el("button", { type: "button", class: "big" }, "El ZIP aún se prepara · comprobar");
    check.addEventListener("click", () => { check.disabled = true; refreshZip(slot, status, 0); });
    slot.replaceChildren(check);
  } else {
    slot.replaceChildren();
    slot.hidden = true;
  }
}

async function refreshZip(slot, status, attempt) {
  if (!slot.isConnected) return;
  const { status: code, data } = await post("/api/share", credentials());
  if (code === 200) showZip(slot, data.zip, status, attempt);
  else showZip(slot, { state: "pending" }, status, ZIP_POLL_ATTEMPTS);
}

function startDownload(url) {
  const a = el("a", { href: url, download: "" });
  document.body.append(a);
  a.click();
  a.remove();
}

function report(status, code, what) {
  status.className = "status bad";
  if (code === 404) status.textContent = `${what} ya no está disponible. Vuelve a cargar la página.`;
  else if (code === 429) status.textContent = "Demasiados intentos. Espera unos minutos.";
  else status.textContent = "No se ha podido iniciar la descarga. Inténtalo de nuevo.";
}

async function downloadZip(button, status) {
  button.disabled = true;
  status.className = "status";
  status.textContent = "Preparando la descarga…";
  const { status: code, data } = await post("/api/zip", credentials());
  button.disabled = false;
  if (code === 200 && data.url) {
    status.className = "status ok";
    status.textContent = "Descarga del ZIP iniciada. Mira la barra o la carpeta de descargas de tu navegador.";
    startDownload(data.url);
    return;
  }
  if (code === 401 || code === 202) return openLink();
  report(status, code, "El ZIP");
}

async function download(row) {
  const { file, button, status } = row;
  button.disabled = true;
  status.className = "status";
  status.textContent = "Preparando…";
  const { status: code, data } = await post("/api/ticket", { ...credentials(), file: file.id });
  button.disabled = false;
  if (code === 200 && data.url) {
    status.className = "status ok";
    status.textContent = "Descarga iniciada";
    button.classList.add("done");
    button.replaceChildren(icon("check"), el("span", { class: "label" }, "Otra vez"));
    button.setAttribute("aria-label", `Descargar ${file.name} otra vez`);
    row.node.classList.add("done");
    startDownload(data.url);
    return true;
  }
  if (code === 401 || code === 202) {
    openLink();
    return false;
  }
  report(status, code, "Este archivo");
  return false;
}

const token = location.hash.slice(1);
if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) {
  message("link", "Enlace incompleto", "Abre el enlace completo que te han enviado, incluida la parte que va detrás del símbolo #. Si lo copiaste a mano, prueba a pulsarlo directamente.");
} else {
  state.token = token;
  openLink();
}
window.addEventListener("hashchange", () => location.reload());
// Una página restaurada desde la caché de historial conservaría la credencial en
// memoria: se descarta y se vuelve a pedir.
window.addEventListener("pagehide", () => { state.password = null; state.code = null; });
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
