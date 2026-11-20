// Apply the saved theme before first paint, so the page never flashes the wrong colors.
// Loaded as a file (not inline) so the Content-Security-Policy can forbid inline scripts.
let saved = null;
try {
  saved = localStorage.getItem("theme");
} catch (e) {
  // Storage blocked: use the default.
}
document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
