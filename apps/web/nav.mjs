// One header/title template for every page, and the "show the result" helper.

export const PAGES = {
  "runs.html": "Runs",
  "telephone.html": "Telephone",
  "manual.html": "Manual test",
  "bakeoff.html": "Bake-off",
  "compare.html": "Compare",
  "scenarios.html": "Scenarios",
  "review.html": "Review & holds",
  "coverage.html": "Provider coverage",
};

/** Pure: page label -> { title, breadcrumb }. */
export function pageMeta(label) {
  return { title: label + " · Voice Harness", breadcrumb: "Voice Harness / " + label };
}

/** Scroll `node` into view, move focus to it and flash a highlight. Honours prefers-reduced-motion. */
export function revealResult(node) {
  if (!node) return;
  const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!node.hasAttribute("tabindex")) node.tabIndex = -1;
  node.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  node.focus({ preventScroll: true });
  node.classList.add("reveal-flash");
  setTimeout(() => node.classList.remove("reveal-flash"), 1600);
}
