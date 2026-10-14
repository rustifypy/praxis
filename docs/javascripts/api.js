// Route tables keep the method as plain text — `| GET | `/api/...` |` is what tests/test_docs.py
// checks every route against — and get their badge here, at render time.
const METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

document$.subscribe(() => {
  for (const row of document.querySelectorAll(".md-typeset table tbody tr")) {
    const [method, path] = row.cells;
    const verb = method?.textContent.trim();
    if (!METHODS.has(verb) || !path?.querySelector("code")) continue;
    method.innerHTML = `<span class="api-method ${verb.toLowerCase()}">${verb}</span>`;
    path.classList.add("api-path");
  }
});
