import { load, render } from "./review.mjs";

const error = document.getElementById("error");
const root = document.getElementById("root");

async function show(token) {
  error.textContent = "";
  try {
    render(root, await load(token));
    try { sessionStorage.setItem("harness-operator-token", token); } catch { /* storage may be blocked */ }
  } catch (e) {
    root.replaceChildren();
    error.textContent = "Could not load holds: " + e.message;
  }
}

document.getElementById("auth").addEventListener("submit", (event) => {
  event.preventDefault();
  show(document.getElementById("token").value);
});

let saved = "";
try { saved = sessionStorage.getItem("harness-operator-token") ?? ""; } catch { /* ignore */ }
if (saved) {
  document.getElementById("token").value = saved;
  show(saved);
}
