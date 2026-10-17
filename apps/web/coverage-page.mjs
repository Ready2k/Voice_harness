import { load, render } from "./coverage.mjs";

const form = document.getElementById("auth");
const error = document.getElementById("error");
const root = document.getElementById("root");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.textContent = "";
  try {
    render(root, await load(location.origin, document.getElementById("token").value));
  } catch (e) {
    root.replaceChildren();
    error.textContent = "Could not load coverage: " + e.message;
  }
});
