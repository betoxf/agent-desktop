// JXA backend for ucu's `safari` tool.
//   osascript -l JavaScript safari.js '<json request>'   ->  prints a JSON reply
// Every operation addresses a tab as {window_id, tab_index} (1-based index) and never activates
// Safari. Needs Safari's Develop > Allow JavaScript from Apple Events and Automation access to Safari.

const safari = Application("Safari");

function run(argv) {
  const request = JSON.parse(argv[0]);
  try {
    return JSON.stringify({ ok: true, result: OPS[request.op](request) });
  } catch (error) {
    return JSON.stringify({ ok: false, error: String(error && error.message || error) });
  }
}

function windowById(id) {
  const match = safari.windows().find((w) => w.id() === id);
  if (!match) throw new Error(`No Safari window with id ${id}`);
  return match;
}

function tabOf(req) {
  const win = windowById(req.window_id);
  const count = win.tabs.length;
  if (!(req.tab_index >= 1 && req.tab_index <= count)) {
    throw new Error(`Window ${req.window_id} has ${count} tab(s); tab_index ${req.tab_index} is out of range`);
  }
  return { win, tab: win.tabs[req.tab_index - 1] };
}

function js(tab, code) {
  return safari.doJavaScript(code, { in: tab });
}

// Run a page-side function with JSON arguments; the function returns a JSON-serialisable value.
function page(tab, fn, args) {
  const code = `(function(){try{return JSON.stringify({v:(${fn})(${JSON.stringify(args || {})})})}catch(e){return JSON.stringify({e:String(e&&e.message||e)})}})()`;
  const raw = js(tab, code);
  if (raw === undefined || raw === null) throw new Error("The page returned nothing (is it still loading, or a non-HTML page?)");
  const reply = JSON.parse(raw);
  if (reply.e) throw new Error(reply.e);
  return reply.v;
}

function waitForLoad(tab, previousHref, timeoutSeconds) {
  const deadline = Date.now() + (timeoutSeconds || 20) * 1000;
  while (Date.now() < deadline) {
    try {
      const href = js(tab, "location.href");
      if (href && href !== previousHref && !/^(about:blank|favorites:)/.test(href) && js(tab, "document.readyState") === "complete") return true;
    } catch (e) { /* page between documents */ }
    delay(0.1);
  }
  return false;
}

function describe(win, tab, index) {
  let url = null, title = null;
  try { url = tab.url(); } catch (e) {}
  try { title = tab.name(); } catch (e) {}
  return { window_id: win.id(), tab_index: index, url, title };
}

const OPS = {
  list(req) {
    return safari.windows().filter((w) => {
      try { return w.document() !== null; } catch (e) { return false; }
    }).map((w) => {
      let current = null;
      try { current = w.currentTab.index(); } catch (e) {}
      const tabs = [];
      const count = w.tabs.length;
      for (let i = 0; i < count; i++) tabs.push(describe(w, w.tabs[i], i + 1));
      return { window_id: w.id(), visible: w.visible(), current_tab_index: current, tabs };
    });
  },

  // Create a hidden window (no focus change; the window can flash for a moment when created).
  create_window(req) {
    // The mark tells this window from one the user or another chat opens at the same moment.
    const mark = `about:blank#ucu-${req.mark}`;
    safari.Document({ url: mark }).make();
    let win = null;
    for (let i = 0; i < 200 && !win; i++) {
      win = safari.windows().find((w) => {
        try { return w.currentTab.url() === mark; } catch (e) { return false; }
      }) || null;
      if (!win) delay(0.01);
    }
    if (!win) throw new Error("Safari did not create a window");
    win.visible = false;
    return { window_id: win.id() };
  },

  // Open a URL in a new tab at the end of a window without selecting it.
  open_tab(req) {
    const win = windowById(req.window_id);
    let tab, index;
    const count = win.tabs.length;
    const first = count === 1 ? win.tabs[0] : null;
    let firstUrl = null;
    if (first) { try { firstUrl = first.url(); } catch (e) {} }
    if (req.reuse_blank && first && (!firstUrl || /^(about:blank|favorites:)/.test(firstUrl))) {
      tab = first; index = 1;
      tab.url = req.url;
    } else {
      win.tabs.push(safari.Tab({ url: req.url }));
      index = win.tabs.length;
      tab = win.tabs[index - 1];
    }
    const loaded = req.wait === false ? null : waitForLoad(tab, null, req.timeout_seconds);
    return Object.assign(describe(win, tab, index), { loaded });
  },

  navigate(req) {
    const { win, tab } = tabOf(req);
    let previous = null;
    try { previous = js(tab, "location.href"); } catch (e) {}
    tab.url = req.url;
    const loaded = req.wait === false ? null : waitForLoad(tab, previous === req.url ? null : previous, req.timeout_seconds);
    return Object.assign(describe(win, tab, req.tab_index), { loaded });
  },

  close_tab(req) {
    const { tab } = tabOf(req);
    tab.close();
    return { closed: true };
  },

  close_window(req) {
    const win = windowById(req.window_id);
    win.close();
    return { closed: true };
  },

  // Show a hidden window on the agent's desktop. Showing makes it the main window, which would take the user's
  // keyboard, so the window they were in gets that back right away.
  show_window(req) {
    const win = windowById(req.window_id);
    const theirs = safari.windows().find((w) => w.visible() && w.id() !== req.window_id);
    win.bounds = req.bounds;
    win.visible = true;
    if (theirs) theirs.index = 1;
    return { shown: true };
  },

  hide_window(req) {
    windowById(req.window_id).visible = false;
    return { hidden: true };
  },

  // A screenshot shows a window's current tab. Only the agent's own window gets here.
  select_tab(req) {
    const { win, tab } = tabOf(req);
    const changed = win.currentTab.index() !== req.tab_index;
    if (changed) win.currentTab = tab;
    return { changed };
  },

  eval(req) {
    const { tab } = tabOf(req);
    return page(tab, `function(){ ${req.script} }`, {});
  },

  read(req) {
    const { win, tab } = tabOf(req);
    const result = page(tab, function (a) {
      const root = a.selector ? document.querySelector(a.selector) : document.body || document.documentElement;
      if (!root) throw new Error("No element matches " + a.selector);
      const clip = (s) => (s.length > a.max_chars ? s.slice(0, a.max_chars) + "\n[truncated]" : s);
      if (a.what === "html") return clip(root.outerHTML);
      if (a.what === "links") {
        return Array.from(root.querySelectorAll("a[href]")).slice(0, 500)
          .map((el) => ({ text: (el.innerText || el.title || "").trim().slice(0, 120), href: el.href }));
      }
      if (a.what === "interactive") {
        const selector = "a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[contenteditable=true],[onclick]";
        const out = [];
        let n = 0;
        for (const el of root.querySelectorAll(selector)) {
          const style = getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden" || el.disabled) continue;
          const id = "c" + (++n);
          el.setAttribute("data-ucu-ref", id);
          out.push({
            ref: id, tag: el.tagName.toLowerCase(), type: el.type || el.getAttribute("role") || undefined,
            text: (el.innerText || el.value || el.getAttribute("aria-label") || el.placeholder || el.title || el.name || "").trim().slice(0, 100),
            href: el.href || undefined,
          });
          if (out.length >= 300) break;
        }
        return out;
      }
      return clip((root.innerText || "").replace(/\n{3,}/g, "\n\n"));
    }, { what: req.what || "text", selector: req.selector || null, max_chars: req.max_chars || 20000 });
    return Object.assign(describe(win, tab, req.tab_index), { content: result });
  },

  click(req) {
    const { tab } = tabOf(req);
    return page(tab, function (a) {
      let el = null;
      if (a.ref) el = document.querySelector(`[data-ucu-ref="${a.ref}"]`);
      else if (a.selector) el = document.querySelector(a.selector);
      else if (a.text) {
        const want = a.text.trim().toLowerCase();
        const candidates = document.querySelectorAll("a,button,input[type=submit],input[type=button],[role=button],[role=link],summary,label");
        el = Array.from(candidates).find((c) => (c.innerText || c.value || "").trim().toLowerCase() === want)
          || Array.from(candidates).find((c) => (c.innerText || c.value || "").trim().toLowerCase().includes(want));
      }
      if (!el) throw new Error("No element found for " + JSON.stringify(a));
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      const at = { bubbles: true, cancelable: true, view: window, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
        el.dispatchEvent(type.startsWith("pointer") ? new PointerEvent(type, at) : new MouseEvent(type, at));
      }
      el.click();
      return { clicked: el.tagName.toLowerCase(), text: (el.innerText || el.value || "").trim().slice(0, 100) };
    }, { ref: req.ref || null, selector: req.selector || null, text: req.text || null });
  },

  type(req) {
    const { tab } = tabOf(req);
    return page(tab, function (a) {
      const el = a.ref ? document.querySelector(`[data-ucu-ref="${a.ref}"]`) : document.querySelector(a.selector);
      if (!el) throw new Error("No element found for " + (a.ref || a.selector));
      el.focus();
      if (el.isContentEditable) {
        if (a.clear) el.textContent = "";
        el.textContent += a.text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: a.text, inputType: "insertText" }));
      } else {
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, "value").set;  // works with React-controlled inputs
        setter.call(el, (a.clear ? "" : el.value) + a.text);
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: a.text, inputType: "insertText" }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (a.submit) {
        const key = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
        const proceed = el.dispatchEvent(new KeyboardEvent("keydown", key));
        el.dispatchEvent(new KeyboardEvent("keyup", key));
        if (proceed && el.form) { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); }
      }
      return { typed: a.text.length, value: el.isContentEditable ? el.textContent.slice(0, 200) : String(el.value).slice(0, 200) };
    }, { ref: req.ref || null, selector: req.selector || null, text: req.text, clear: req.clear !== false, submit: !!req.submit });
  },

  wait_for(req) {
    const { tab } = tabOf(req);
    const deadline = Date.now() + (req.timeout_seconds || 10) * 1000;
    while (Date.now() < deadline) {
      try {
        const found = page(tab, function (a) {
          if (a.selector) return !!document.querySelector(a.selector);
          return (document.body && document.body.innerText || "").includes(a.text);
        }, { selector: req.selector || null, text: req.text || null });
        if (found) return { found: true };
      } catch (e) { /* navigating */ }
      delay(0.2);
    }
    return { found: false };
  },
};
