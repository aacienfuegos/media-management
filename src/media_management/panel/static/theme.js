"use strict";

try {
  const theme = localStorage.getItem("mm-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {}
