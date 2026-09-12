"use strict";
/* Renders window.DOC with the components. Runs after content.js + components.js. */
(function () {
  const app = document.getElementById("app");
  const toc = document.getElementById("toc");

  function block(b) {
    if (b.type === "p") {
      const p = document.createElement("p");
      p.innerHTML = b.html;
      return p;
    }
    if (b.type === "code") {
      const pre = document.createElement("pre");
      pre.className = "code";
      pre.textContent = b.text;
      return pre;
    }
    if (b.type === "list") {
      const ul = document.createElement("ul");
      ul.className = "tight";
      for (const item of b.items) {
        const li = document.createElement("li");
        li.innerHTML = item;
        ul.appendChild(li);
      }
      return ul;
    }
    if (b.type === "tools") {
      const wrap = document.createElement("div");
      for (const group of window.CATALOG) {
        const h = document.createElement("h3");
        h.className = "cat";
        h.textContent = `${group.category} — ${group.tools.length}`;
        wrap.appendChild(h);
        const grid = document.createElement("div");
        grid.className = "tools";
        for (const t of group.tools) {
          const card = document.createElement("tool-full");
          card.data = t;
          grid.appendChild(card);
        }
        wrap.appendChild(grid);
      }
      return wrap;
    }
    if (b.type === "perf") {
      const table = document.createElement("perf-table");
      table.setAttribute("rows", JSON.stringify(window.DOC.perf));
      return table;
    }
    if (b.type === "diagram") {
      return document.createElement("arch-diagram");
    }
    if (b.type === "callout") {
      const c = document.createElement("doc-callout");
      c.setAttribute("kind", b.kind || "info");
      c.innerHTML = b.html;
      return c;
    }
    return document.createTextNode("");
  }

  for (const s of window.DOC.sections) {
    const sec = document.createElement("doc-section");
    sec.setAttribute("title", s.title);
    sec.setAttribute("section-id", s.id);
    for (const b of s.blocks) {
      sec.appendChild(block(b));
    }
    app.appendChild(sec);

    const link = document.createElement("a");
    link.href = "#" + s.id;
    link.textContent = s.title;
    toc.appendChild(link);
  }
})();
