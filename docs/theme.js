// Apply a saved theme before first paint, so the page never flashes the wrong colors.
// Loaded as a file (not inline) so the Content-Security-Policy can forbid inline scripts.
try {
  const t = localStorage.getItem("theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {
  // Storage blocked: fall back to the system theme.
}
