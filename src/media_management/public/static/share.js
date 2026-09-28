"use strict";

// El token viaja en el fragmento (#...), que el navegador nunca envía al servidor:
// así no acaba en ningún access log ni en el Referer. Se manda en el cuerpo de un POST.
// La credencial (contraseña o código) vive solo en memoria mientras la página está abierta.

const app = document.getElementById("app");
const state = { token: null, password: null, code: null };

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

function screen(...children) {
  app.replaceChildren(el("div", { class: "card" }, ...children));
}

function message(title, text, ...extra) {
  screen(el("h1", {}, title), el("p", { class: "muted" }, text), ...extra);
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
  screen(el("p", { class: "muted" }, "Cargando…"));
  const { status, data } = await post("/api/share", credentials());
  if (status === 200) return showFiles(data);
  if (status === 202 && data.status === "pending") return showPending();
  if (status === 401 && data.need === "password") return showPassword(data.error === "bad_credentials");
  if (status === 401 && data.need === "code") return showCode(data.error === "bad_credentials");
  if (status === 404) return message("Este enlace no está disponible", "Puede que haya caducado, que lo hayan retirado o que no esté completo.");
  if (status === 429) return message("Demasiados intentos", "Espera unos minutos antes de volver a intentarlo.", retryButton());
  return message("No se ha podido abrir el enlace", "Comprueba la conexión e inténtalo de nuevo.", retryButton());
}

function retryButton() {
  return el("button", { type: "button", on: { click: openLink } }, "Reintentar");
}

function showPassword(wrong) {
  const input = el("input", { type: "password", id: "password", autocomplete: "current-password", required: "", maxlength: "256" });
  const form = el("form", { on: { submit: (e) => { e.preventDefault(); state.password = input.value; openLink(); } } },
    el("label", { for: "password" }, "Contraseña"),
    input,
    wrong ? el("p", { class: "error" }, "Contraseña incorrecta.") : null,
    el("button", { class: "primary" }, "Entrar"));
  screen(el("h1", {}, "Este enlace tiene contraseña"),
    el("p", { class: "muted" }, "Quien te lo ha enviado debería habértela dado por otro medio."), form);
  input.focus();
}

function showCode(wrong) {
  state.code = null;
  const codeInput = el("input", { id: "code", autocomplete: "off", autocapitalize: "characters", spellcheck: "false",
    required: "", maxlength: "32", placeholder: "XXXXX-XXXXX" });
  const codeForm = el("form", { on: { submit: (e) => { e.preventDefault(); state.code = codeInput.value; openLink(); } } },
    el("label", { for: "code" }, "Tu código personal"),
    codeInput,
    wrong ? el("p", { class: "error" }, "Código no válido para este enlace.") : null,
    el("button", { class: "primary" }, "Entrar"));
  const name = el("input", { id: "name", required: "", maxlength: "80", autocomplete: "name" });
  const note = el("textarea", { id: "note", maxlength: "500", rows: "3" });
  const requestForm = el("form", { on: { submit: (e) => { e.preventDefault(); requestAccess(name.value, note.value); } } },
    el("label", { for: "name" }, "Tu nombre"),
    name,
    el("label", { for: "note" }, "Nota (opcional)"),
    note,
    el("button", {}, "Pedir acceso"));
  screen(el("h1", {}, "Acceso por solicitud"),
    el("p", { class: "muted" }, "Para descargar necesitas que aprueben tu acceso. Si ya lo pediste, entra con tu código."),
    el("section", {}, el("h2", {}, "Tengo un código"), codeForm),
    el("section", {}, el("h2", {}, "Pedir acceso"),
      el("p", { class: "muted" }, "Escribe tu nombre para que sepan quién eres. Recibirás un código personal."),
      requestForm));
}

async function requestAccess(name, note) {
  const { status, data } = await post("/api/request", { token: state.token, name, note });
  if (status === 200 && data.code) return showNewCode(data.code);
  if (status === 429 && data.error === "queue_full") return message("Ahora mismo no se aceptan más solicitudes", "Hay demasiadas solicitudes pendientes para este enlace. Inténtalo más tarde.", retryButton());
  if (status === 429) return message("Demasiadas solicitudes", "Espera un rato antes de volver a pedir acceso.", retryButton());
  return message("No se ha podido enviar la solicitud", "Inténtalo de nuevo más tarde.", retryButton());
}

function showNewCode(code) {
  const saved = el("button", { class: "primary", type: "button", on: { click: () => { state.code = code; openLink(); } } },
    "Ya lo he guardado");
  screen(el("h1", {}, "Solicitud enviada"),
    el("p", {}, "Este es tu código personal:"),
    el("p", { class: "code" }, code),
    el("p", { class: "warning" }, "Guárdalo ahora. No se puede recuperar: si lo pierdes tendrás que volver a pedir acceso."),
    el("p", { class: "muted" }, "Cuando aprueben tu solicitud, vuelve a abrir este mismo enlace e introduce el código, desde este o desde cualquier otro dispositivo."),
    saved);
}

function showPending() {
  screen(el("h1", {}, "Pendiente de aprobar"),
    el("p", { class: "muted" }, "Tu solicitud todavía no se ha aprobado. Vuelve a abrir este enlace más tarde con tu código."),
    el("button", { type: "button", on: { click: openLink } }, "Comprobar de nuevo"));
}

function showFiles(data) {
  const list = el("ul", { class: "files" });
  for (const file of data.files) {
    const thumb = file.thumb
      ? el("img", { class: "thumb", src: file.thumb, alt: "" })
      : el("div", { class: "thumb icon" }, file.kind === "video" ? "Vídeo" : file.kind === "photo" ? "Foto" : "Fichero");
    const status = el("span", { class: "status", role: "status" });
    const button = el("button", { class: "primary", type: "button" }, "Descargar");
    button.addEventListener("click", () => download(file, button, status));
    list.append(el("li", {}, thumb,
      el("div", { class: "meta" }, el("span", { class: "name" }, file.name), el("span", { class: "muted" }, formatSize(file.size)), status),
      button));
  }
  const total = data.files.reduce((sum, f) => sum + f.size, 0);
  app.replaceChildren(el("div", { class: "card wide" },
    el("h1", {}, data.title),
    el("p", { class: "muted" }, `${data.files.length} fichero${data.files.length === 1 ? "" : "s"} · ${formatSize(total)} · disponible hasta el ${formatDate(data.expires_at)}`),
    data.files.length ? list : el("p", { class: "muted" }, "Este enlace ya no tiene ficheros disponibles.")));
}

async function download(file, button, status) {
  button.disabled = true;
  status.textContent = "Preparando…";
  const { status: code, data } = await post("/api/ticket", { ...credentials(), file: file.id });
  button.disabled = false;
  if (code === 200 && data.url) {
    status.textContent = "Descarga iniciada. Si se corta, puedes reanudarla desde el navegador durante unas horas.";
    const a = el("a", { href: data.url, download: "" });
    document.body.append(a);
    a.click();
    a.remove();
    return;
  }
  if (code === 404) status.textContent = "Este fichero ya no está disponible.";
  else if (code === 401 || code === 202) openLink();
  else if (code === 429) status.textContent = "Demasiados intentos. Espera unos minutos.";
  else status.textContent = "No se ha podido iniciar la descarga. Inténtalo de nuevo.";
}

const token = location.hash.slice(1);
if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) {
  message("Enlace incompleto", "Abre el enlace completo que te han enviado, incluida la parte que va detrás de #.");
} else {
  state.token = token;
  openLink();
}
window.addEventListener("hashchange", () => location.reload());
// Una página restaurada desde la caché de historial conservaría la credencial en
// memoria: se descarta y se vuelve a pedir.
window.addEventListener("pagehide", () => { state.password = null; state.code = null; });
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
