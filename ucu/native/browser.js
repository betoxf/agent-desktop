// JXA backend for ucu's `safari` and `chrome` tools.
//   osascript -l JavaScript browser.js '<json request>'   ->  prints a JSON reply
// A request names its browser ("safari" or "chrome") and addresses a tab as {window_id, tab_index} (1-based index).
// Nothing here activates the browser, except that Chrome comes forward when it makes a window: ucu-window puts the
// user's app back. Operations that run in a page need Allow JavaScript from Apple Events (Safari: Develop menu; Chrome:
// View > Developer), and every one needs Automation access to the browser.
ObjC.import("AppKit");

const BLANK = /^(about:blank|favorites:|chrome:\/\/newtab)/;
const CHUNK = 1 << 20;  // characters of a file's base64 that go to a page at a time

const BROWSERS = {
  safari: {
    app: () => Application("com.apple.Safari"),
    // Safari's Downloads and other panels are windows too, without a document.
    windows: (b) => b.windows().filter((w) => {
      try { return w.document() !== null; } catch (e) { return false; }
    }),
    id: (w) => w.id(),
    currentIndex: (w) => w.currentTab.index(),
    select: (w, tab) => { w.currentTab = tab; },
    title: (tab) => tab.name(),
    js: (b, tab, code) => b.doJavaScript(code, { in: tab }),
  },
  chrome: {
    app: () => Application("com.google.Chrome"),
    windows: (b) => b.windows(),
    id: (w) => Number(w.id()),
    currentIndex: (w) => w.activeTabIndex(),
    select: (w, tab, index) => { w.activeTabIndex = index; },
    title: (tab) => tab.title(),
    js: (b, tab, code) => b.execute(tab, { javascript: code }),
  },
};

let B = null, browser = null;

function run(argv) {
  const request = JSON.parse(argv[0]);
  try {
    B = BROWSERS[request.browser || "safari"];
    if (!B) throw new Error(`Unknown browser ${request.browser}`);
    browser = B.app();
    const op = (request.browser === "chrome" && CHROME_OPS[request.op]) || OPS[request.op];
    if (!op) throw new Error(`Unknown operation ${request.op}`);
    return JSON.stringify({ ok: true, result: op(request) });
  } catch (error) {
    return JSON.stringify({ ok: false, error: String(error && error.message || error) });
  }
}

function windowById(id) {
  const match = B.windows(browser).find((w) => B.id(w) === id);
  if (!match) throw new Error(`No window with id ${id}`);
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
  return B.js(browser, tab, code);
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

function urlOf(tab) {
  try { return tab.url(); } catch (e) { return null; }
}

function waitForLoad(tab, previousHref, timeoutSeconds) {
  const deadline = Date.now() + (timeoutSeconds || 20) * 1000;
  while (Date.now() < deadline) {
    try {
      const href = js(tab, "location.href");
      if (href && href !== previousHref && !BLANK.test(href) && js(tab, "document.readyState") === "complete") return true;
    } catch (e) { /* page between documents */ }
    delay(0.1);
  }
  return false;
}

// Chrome says whether a tab is loading, so waiting needs no JavaScript there.
function chromeLoaded(tab, previousHref, timeoutSeconds) {
  const started = Date.now(), deadline = started + (timeoutSeconds || 20) * 1000;
  let seen = false;
  while (Date.now() < deadline) {
    try {
      const url = tab.url() || "";
      if (tab.loading()) seen = true;
      else if (!BLANK.test(url) && (seen || url !== previousHref || Date.now() - started > 1000)) return true;
    } catch (e) { /* tab between documents */ }
    delay(0.1);
  }
  return false;
}

function loaded(tab, previousHref, timeoutSeconds) {
  return B === BROWSERS.chrome ? chromeLoaded(tab, previousHref, timeoutSeconds) : waitForLoad(tab, previousHref, timeoutSeconds);
}

function describe(win, tab, index) {
  let title = null;
  try { title = B.title(tab); } catch (e) {}
  return { window_id: B.id(win), tab_index: index, url: urlOf(tab), title };
}

const OPS = {
  list(req) {
    return B.windows(browser).map((w) => {
      let current = null;
      try { current = B.currentIndex(w); } catch (e) {}
      const tabs = [];
      const count = w.tabs.length;
      for (let i = 0; i < count; i++) tabs.push(describe(w, w.tabs[i], i + 1));
      return { window_id: B.id(w), visible: w.visible(), current_tab_index: current, tabs };
    });
  },

  // Create a hidden window (no focus change; the window can flash for a moment when created).
  create_window(req) {
    // The mark tells this window from one the user or another chat opens at the same moment.
    const mark = `about:blank#ucu-${req.mark}`;
    browser.Document({ url: mark }).make();
    let win = null;
    for (let i = 0; i < 200 && !win; i++) {
      win = browser.windows().find((w) => {
        try { return w.currentTab.url() === mark; } catch (e) { return false; }
      }) || null;
      if (!win) delay(0.01);
    }
    if (!win) throw new Error("Safari did not create a window");
    win.visible = false;
    return { window_id: win.id() };
  },

  // Open a URL in a new tab at the end of a window, or in its only tab while that is blank.
  open_tab(req) {
    const win = windowById(req.window_id);
    let tab, index;
    const count = win.tabs.length;
    const first = count === 1 ? win.tabs[0] : null;
    const firstUrl = first ? urlOf(first) : null;
    if (req.reuse_blank && first && (!firstUrl || BLANK.test(firstUrl))) {
      tab = first; index = 1;
      tab.url = req.url;
    } else {
      win.tabs.push(browser.Tab({ url: req.url }));
      index = win.tabs.length;
      tab = win.tabs[index - 1];
    }
    const done = req.wait === false ? null : loaded(tab, null, req.timeout_seconds);
    return Object.assign(describe(win, tab, index), { loaded: done });
  },

  navigate(req) {
    const { win, tab } = tabOf(req);
    let previous = null;
    try { previous = B === BROWSERS.chrome ? tab.url() : js(tab, "location.href"); } catch (e) {}
    tab.url = req.url;
    const done = req.wait === false ? null : loaded(tab, previous === req.url ? null : previous, req.timeout_seconds);
    return Object.assign(describe(win, tab, req.tab_index), { loaded: done });
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
    const theirs = B.windows(browser).find((w) => w.visible() && B.id(w) !== req.window_id);
    win.bounds = req.bounds;
    win.visible = true;
    if (theirs) theirs.index = 1;
    return { shown: true };
  },

  // After a turn the window goes out of sight, unless it has left the agent's desktop: then the user has it.
  hide_window(req) {
    const win = windowById(req.window_id);
    const at = win.bounds();
    if (req.only_at && (Math.abs(at.x - req.only_at.x) > 4 || Math.abs(at.y - req.only_at.y) > 4)) {
      return { hidden: false, moved: true };
    }
    win.visible = false;
    return { hidden: true };
  },

  // The user asked to see the agent's tab: its window comes to their screen, the one with the menu bar, in front.
  show_to_user(req) {
    const { win, tab } = tabOf(req);
    if (B.currentIndex(win) !== req.tab_index) B.select(win, tab, req.tab_index);
    const screen = $.NSScreen.screens.objectAtIndex(0), full = screen.frame, area = screen.visibleFrame;
    const width = Math.min(1280, area.size.width - 80), height = Math.min(900, area.size.height - 80);
    win.bounds = { x: area.origin.x + 40, y: full.size.height - area.origin.y - area.size.height + 40, width, height };
    win.visible = true;
    win.index = 1;
    browser.activate();
    return { shown: true };
  },

  // A screenshot shows a window's current tab. Only the agent's own window gets here.
  select_tab(req) {
    const { win, tab } = tabOf(req);
    const changed = B.currentIndex(win) !== req.tab_index;
    if (changed) B.select(win, tab, req.tab_index);
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
          // Pages often hide their file input behind a button of their own; `upload` still needs it.
          if ((style.display === "none" || style.visibility === "hidden") && el.type !== "file" || el.disabled) continue;
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

  // Give a page local files without its file picker, which needs the browser in front: they go into a file input as
  // if chosen there, or are dropped on any other element, such as a drop zone. The files are read here, so their
  // contents never pass through the command line, and go to the page in pieces.
  upload(req) {
    const { tab } = tabOf(req);
    js(tab, "window.__ucuFiles = []");
    req.files.forEach((file, index) => {
      const data = $.NSData.dataWithContentsOfFile($(file.path));
      if (!data || data.isNil()) throw new Error(`Can't read ${file.path}`);
      const text = data.base64EncodedStringWithOptions(0).js;
      js(tab, `window.__ucuFiles[${index}] = ''`);
      for (let at = 0; at < text.length; at += CHUNK) {
        js(tab, `window.__ucuFiles[${index}] += '${text.slice(at, at + CHUNK)}'`);
      }
    });
    return page(tab, function (a) {
      const el = a.ref ? document.querySelector(`[data-ucu-ref="${a.ref}"]`) : document.querySelector(a.selector || "input[type=file]");
      if (!el) throw new Error("No element found for " + (a.ref || a.selector || "input[type=file]"));
      const transfer = new DataTransfer();
      a.files.forEach((file, index) => {
        const raw = atob(window.__ucuFiles[index]);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        transfer.items.add(new File([bytes], file.name, { type: file.type }));
      });
      delete window.__ucuFiles;
      const names = a.files.map((file) => file.name);
      if (el instanceof HTMLInputElement && el.type === "file") {
        el.files = transfer.files;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { uploaded: names, into: "input", accept: el.accept || undefined };
      }
      for (const type of ["dragenter", "dragover", "drop"]) {
        el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer }));
      }
      return { uploaded: names, into: "drop" };
    }, { ref: req.ref || null, selector: req.selector || null, files: req.files.map((f) => ({ name: f.name, type: f.type })) });
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

// Where Chrome differs: its window ids are numbers in text, and making a window brings it forward and shows it where
// the user is, so it goes straight to the agent's desktop, or out of sight. Showing it later doesn't take the user's
// keyboard, so their window is left alone. Setting a tab's address brings Chrome forward too, so a tab is made without
// one and goes to its page through the page itself, when the page lets ucu run JavaScript.
function chromeGo(tab, url) {
  const previous = urlOf(tab);
  let asked = false;
  try { js(tab, `location.assign(${JSON.stringify(url)})`); asked = true; } catch (e) { /* no JavaScript here */ }
  // A page can refuse, such as one of Chrome's own, and then the address is set after all.
  const until = Date.now() + 1500;
  while (asked && Date.now() < until && urlOf(tab) === previous && !tab.loading()) delay(0.05);
  if (!asked || (urlOf(tab) === previous && !tab.loading() && previous !== url)) tab.url = url;
  return previous;
}

const CHROME_OPS = {
  open_tab(req) {
    const win = windowById(req.window_id);
    const first = win.tabs.length === 1 ? win.tabs[0] : null;
    let tab, index;
    if (req.reuse_blank && first && BLANK.test(urlOf(first) || "about:blank")) {
      tab = first; index = 1;
    } else {
      win.tabs.push(browser.Tab());
      index = win.tabs.length;
      tab = win.tabs[index - 1];
    }
    chromeGo(tab, req.url);
    const done = req.wait === false ? null : loaded(tab, null, req.timeout_seconds);
    return Object.assign(describe(win, tab, index), { loaded: done });
  },

  navigate(req) {
    const { win, tab } = tabOf(req);
    const previous = chromeGo(tab, req.url);
    const done = req.wait === false ? null : loaded(tab, previous === req.url ? null : previous, req.timeout_seconds);
    return Object.assign(describe(win, tab, req.tab_index), { loaded: done });
  },

  create_window(req) {
    const before = new Set(browser.windows().map((w) => w.id()));
    // Given with the window, the place applies before Chrome draws it where the user is.
    let failure = null;
    try { browser.Window(req.bounds ? { bounds: req.bounds } : { visible: false }).make(); } catch (e) { failure = e; }
    let win = null;
    for (let i = 0; i < 200 && !win; i++) {
      const made = browser.windows().filter((w) => !before.has(w.id()));
      win = made.length ? made[0] : null;  // the front one first: the one just made, if another chat made one too
      if (!win) delay(0.01);
    }
    if (!win) throw failure || new Error("Chrome did not create a window");
    if (req.bounds) win.bounds = req.bounds;
    else win.visible = false;
    return { window_id: Number(win.id()) };
  },

  show_window(req) {
    const win = windowById(req.window_id);
    win.bounds = req.bounds;
    win.visible = true;
    return { shown: true };
  },
};
