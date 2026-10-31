// Runs pieces of static/index.html under node, without a browser.
//   mode "pure": the PURE block (filters, ranking, paging, the plan) evaluated on its own.
//   mode "page": the whole script against a small stand-in for the page, with canned answers
//                for fetch, then a list of steps (type, click, read).
// The job arrives as JSON on stdin; the results leave as JSON on stdout.
"use strict";
const fs = require("fs");
const vm = require("vm");

const job = JSON.parse(fs.readFileSync(0, "utf8"));
const html = fs.readFileSync(job.html, "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function runPure() {
  const m = script.match(/\/\* PURE:BEGIN[^*]*\*\/([\s\S]*?)\/\* PURE:END \*\//);
  const api = new Function(m[1] + "\n;return {GREEN,AMBER,PAGE,MAX_BAGS,norm,band,allocate,rankRows,matches," +
    "visibleRows,pageSlice,selectedVisible,buildPlan,pictureUrl,cashEach,creditEach};")();
  const out = [];
  for (const expr of job.exprs) {
    try {
      out.push(new Function("api", "D", "with(api){return (" + expr + ")}")(api, job.data));
    } catch (e) {
      out.push({ __error: String(e) });
    }
  }
  return out;
}

function makeDom() {
  const initiallyHidden = {};
  const body = html.replace(/<script>[\s\S]*<\/script>/, "");
  for (const m of body.matchAll(/<[a-z0-9]+\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    initiallyHidden[m[1]] = /\shidden(\s|>|=|\/)/.test(m[0]);
  }
  class El {
    constructor(id) {
      this.id = id; this.hidden = !!initiallyHidden[id]; this.value = ""; this.checked = false;
      this.innerHTML = ""; this.textContent = ""; this.href = ""; this.disabled = false; this.className = "";
      this.style = {}; this.dataset = {}; this.attrs = {}; this.listeners = {}; this.indeterminate = false;
      const s = new Set();
      this.classList = { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c),
                         toggle: (c, f) => { if (f === undefined ? !s.has(c) : f) s.add(c); else s.delete(c); } };
      this.children = []; this.firstChild = { };
    }
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    focus() {} click() {} select() {} scrollIntoView() {}
    getBoundingClientRect() { return { left: 0, right: 100, top: 0, bottom: 20, width: 100, height: 20 }; }
    closest() { return null; } contains() { return false; }
    appendChild() {} removeChild() {} querySelector() { return null; }
    fire(type, ev) {
      for (const f of this.listeners[type] || []) {
        f(Object.assign({ target: this, preventDefault() {}, stopPropagation() {}, key: "" }, ev || {}));
      }
    }
  }
  const els = {};
  const get = (id) => els[id] || (els[id] = new El(id));
  const group = (n, attrsList) => attrsList.map((a, i) => {
    const e = new El(n + ":" + i);
    Object.entries(a).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  });
  const lists = {
    "#steps button": group("steps", [{ "data-s": 1 }, { "data-s": 2 }, { "data-s": 3 }]),
    "#mode button": group("mode", [{ "data-m": "replace", "aria-pressed": "true" }, { "data-m": "add", "aria-pressed": "false" }]),
    "#pay button": group("pay", [{ "data-p": "cash", "aria-pressed": "true" }, { "data-p": "credit", "aria-pressed": "false" }]),
  };
  const docListeners = new El("document");
  const document = {
    getElementById: get,
    querySelectorAll: (sel) => lists[sel] || [],
    querySelector: (sel) => {
      let m = sel.match(/^#steps button\[data-s="(\d)"\]$/);
      if (m) return lists["#steps button"][+m[1] - 1];
      if (sel === "#mode button[aria-pressed=true]") return lists["#mode button"].find((b) => b.getAttribute("aria-pressed") === "true");
      return null;
    },
    createElement: (t) => new El(t),
    addEventListener: (t, f) => docListeners.addEventListener(t, f),
    documentElement: get("html"), body: get("body"), activeElement: get("active"),
  };
  document.documentElement.setAttribute("data-theme", "dark");
  return { document, els, lists, docListeners, get };
}

async function runPage() {
  const dom = makeDom();
  const calls = [];
  const store = {};
  const fetchStub = (url, opts) => {
    opts = opts || {};
    calls.push({ method: opts.method || "GET", url, body: opts.body === undefined ? null : opts.body });
    const key = (opts.method || "GET") + " " + url.split("?")[0];
    const answer = job.responses && job.responses[key] !== undefined ? job.responses[key] : (key === "GET /api/state" ? job.state : {});
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(answer))) });
  };
  const win = { innerWidth: 1200, innerHeight: 900, scrollTo() {}, addEventListener() {} };
  const sandbox = {
    document: dom.document, window: win, fetch: fetchStub, console, Date, Promise, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    Array, Object, JSON, Math, String, Number, parseInt, parseFloat, isNaN, Set,
  };
  vm.createContext(sandbox);
  const results = [];
  try {
    vm.runInContext(script, sandbox);
  } catch (e) {
    return { error: "script failed: " + e.stack, results };
  }
  await sleep(30);
  for (const step of job.steps) {
    try {
      if (step.op === "wait") await sleep(step.ms || 30);
      else if (step.op === "set") Object.assign(dom.get(step.id), step.props);
      else if (step.op === "fire") {
        const el = dom.get(step.id);
        el.fire(step.event, Object.assign(step.ev ? { target: Object.assign(Object.create(el), step.ev) } : {}, step.evt || {}));
      }
      else if (step.op === "fireList") dom.lists[step.list][step.index].fire(step.event);
      else if (step.op === "docclick") {
        const t = step.target;
        const target = { id: t.id || "", closest: (sel) => (t.closest && t.closest[sel] ? Object.assign({ getAttribute: (k) => (t.attrs || {})[k] || null, disabled: false, getBoundingClientRect: () => ({ left: 0, right: 100, top: 0, bottom: 20, width: 100, height: 20 }) }, t.closest[sel]) : null) };
        dom.docListeners.fire("click", { target });
      }
      else if (step.op === "rowEvent") {   // a change inside the binder table
        const el = dom.get("tbody");
        const t = step.target;
        const target = { classList: { contains: (c) => c === t.kind }, getAttribute: () => String(t.id), value: t.value, checked: !!t.checked, closest: () => null };
        el.fire("change", { target });
      }
      else if (step.op === "read") results.push(dom.get(step.id)[step.prop]);
      else if (step.op === "count") results.push((dom.get(step.id).innerHTML.match(new RegExp(step.pattern, "g")) || []).length);
      else if (step.op === "attr") results.push(dom.get(step.id).getAttribute(step.name));
      else if (step.op === "calls") results.push(JSON.parse(JSON.stringify(calls)));
      else if (step.op === "clearcalls") calls.length = 0;
      if (step.op !== "wait") await sleep(5);
    } catch (e) {
      results.push({ __error: String(e && e.stack || e) });
    }
  }
  return { results };
}

process.on("unhandledRejection", (e) => { process.stderr.write("unhandled: " + (e && e.stack || e) + "\n"); });
(async () => {
  const out = job.mode === "pure" ? { results: runPure() } : await runPage();
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
