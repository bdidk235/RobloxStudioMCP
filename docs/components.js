"use strict";
/* Reusable components. Classic script on purpose: no modules, no build step,
   works from file://. Data lives in content.js, rendering in app.js. */

class DocSection extends HTMLElement {
  connectedCallback() {
    const title = this.getAttribute("title") || "";
    const id = this.getAttribute("section-id") || "";
    const h = document.createElement("h2");
    h.id = id;
    h.textContent = title;
    this.prepend(h);
  }
}

class DocCallout extends HTMLElement {
  connectedCallback() {
    const kind = this.getAttribute("kind") || "info";
    const label = { info: "Note", warn: "Gotcha", story: "Found live" }[kind] || "Note";
    const k = document.createElement("div");
    k.className = "k";
    k.textContent = label;
    this.prepend(k);
  }
}

class ArchDiagram extends HTMLElement {
  connectedCallback() {
    const box = (x, label, sub) =>
      `<g><rect x="${x}" y="30" width="150" height="64" rx="8" fill="#15151b" stroke="#7aa2f7"/>` +
      `<text x="${x + 75}" y="58" fill="#f4f4f6" font-size="12" text-anchor="middle">${label}</text>` +
      `<text x="${x + 75}" y="76" fill="#b3b3be" font-size="10" text-anchor="middle">${sub}</text></g>`;
    const arrow = (x) =>
      `<line x1="${x}" y1="62" x2="${x + 40}" y2="62" stroke="#9a9aa5" stroke-width="1.5"/>` +
      `<polygon points="${x + 40},62 ${x + 33},58 ${x + 33},66" fill="#b3b3be"/>`;
    const note = (x, y, label) =>
      `<text x="${x}" y="${y}" fill="#b3b3be" font-size="10" text-anchor="middle">${label}</text>`;
    this.innerHTML =
      `<svg viewBox="0 0 830 130" role="img" aria-label="MCP connection chain">` +
      box(0, "opencode", "MCP client") + arrow(150) +
      box(190, "extendedServer", "stdio JSON-RPC") + arrow(340) +
      box(380, "StudioMCP.exe", "proxy process") + arrow(530) +
      box(570, "WS mesh :13469", "hub + clients") + arrow(720) +
      `<g><rect x="760" y="30" width="70" height="64" rx="8" fill="#15151b" stroke="#4caf7d"/>` +
      `<text x="795" y="58" fill="#f4f4f6" font-size="12" text-anchor="middle">Studio</text>` +
      `<text x="795" y="76" fill="#b3b3be" font-size="10" text-anchor="middle">place</text></g>` +
      note(75, 115, "spawns / speaks to") + note(265, 115, "forwards tools/*") +
      note(455, 115, "first proxy hosts hub") + note(645, 115, "others join as clients") +
      note(795, 115, "attaches w/ place+MCP") +
      `</svg>`;
  }
}

class PerfTable extends HTMLElement {
  connectedCallback() {
    const rows = JSON.parse(this.getAttribute("rows") || "[]");
    let html = `<table class="perf"><tr><th>Op</th><th>Python 3.12</th><th>Read</th></tr>`;
    for (const r of rows) {
      html += `<tr><td><code>${r[0]}</code></td><td>${r[1]}</td><td>${r[2]}</td></tr>`;
    }
    this.innerHTML = html + "</table>";
  }
}

customElements.define("doc-section", DocSection);
customElements.define("doc-callout", DocCallout);
customElements.define("arch-diagram", ArchDiagram);
function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

class ToolFull extends HTMLElement {
  connectedCallback() {
    const d = this.data || {};
    const req = (d.req || []).join(", ") || "—";
    const opt = (d.opt || []).join(", ") || "—";
    this.innerHTML =
      `<div class="thead"><code class="tname">${esc(d.n || "")}</code>` +
      `<span class="tbadge${d.o === "ext" ? " ext" : ""}">${d.o === "ext" ? "extended" : "raw"}</span></div>` +
      `<div class="tdesc">${esc(d.d || "")}</div>` +
      `<dl class="tio">` +
      `<div><dt>Inputs (required)</dt><dd>${esc(req)}</dd></div>` +
      `<div><dt>Inputs (optional)</dt><dd>${esc(opt)}</dd></div>` +
      `<div><dt>Returns</dt><dd>${esc(d.ret || "")}</dd></div>` +
      `</dl>`;
  }
}

customElements.define("perf-table", PerfTable);
customElements.define("tool-full", ToolFull);
