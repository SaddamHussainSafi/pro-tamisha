/* Order Connect customer form. No framework, no third-party code. Every value from the server
   or the customer is inserted with textContent / attributes, never as HTML. */
(function () {
  "use strict";

  var root = document.getElementById("oc-app");
  if (!root) return;
  var mode = root.getAttribute("data-mode") || "live";
  var publicId = root.getAttribute("data-public-id") || "";
  var host = root.getAttribute("data-host") || "";
  var staffOrigin = root.getAttribute("data-staff-origin") || "";
  var UNIT = {
    BOX: ["box", "boxes"],
    PIECE: ["piece", "pieces"],
    LB: ["lb", "lb"],
    KG: ["kg", "kg"],
  };
  var WHOLE = { BOX: true, PIECE: true };

  var cfg = null;
  var state = freshState();
  var storeKey = "oc:" + publicId;

  function freshState() {
    return {
      step: "products",
      cart: {}, // "productId|UNIT" -> qty string
      values: {},
      consent: false,
      errors: {},
      itemErrors: {},
      query: "",
      category: "",
      units: {}, // productId -> chosen unit
      token: randomToken(),
      sending: false,
      done: null,
      notice: "",
    };
  }

  function randomToken() {
    var b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    return Array.prototype.map
      .call(b, function (x) {
        return ("0" + x.toString(16)).slice(-2);
      })
      .join("");
  }

  // ---------------------------------------------------------------- DOM helper

  function h(tag, attrs) {
    var el = document.createElement(tag);
    var a = attrs || {};
    Object.keys(a).forEach(function (k) {
      var v = a[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") el.textContent = v;
      else if (k === "on") {
        Object.keys(v).forEach(function (ev) {
          el.addEventListener(ev, v[ev]);
        });
      } else if (k === "className") el.className = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x) el.appendChild(x); });
      else el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function unitName(code, qty) {
    var u = UNIT[code] || [code, code];
    return Number(qty) === 1 ? u[0] : u[1];
  }

  // ---------------------------------------------------------------- persistence (best effort)

  function save() {
    if (mode !== "live") return;
    try {
      window.sessionStorage.setItem(
        storeKey,
        JSON.stringify({ cart: state.cart, values: state.values, token: state.token })
      );
    } catch (e) { /* storage blocked in some third-party frames: fine */ }
  }

  function restore() {
    try {
      var s = JSON.parse(window.sessionStorage.getItem(storeKey) || "null");
      if (s && typeof s === "object") {
        state.cart = s.cart || {};
        state.values = s.values || {};
        if (typeof s.token === "string" && /^[a-f0-9]{32}$/.test(s.token)) state.token = s.token;
      }
    } catch (e) { /* ignore */ }
  }

  function forget() {
    try { window.sessionStorage.removeItem(storeKey); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- shell states

  function showState(kind, heading, text, retry) {
    root.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
    clear(root);
    root.className = "oc-shell";
    var box = h(
      "div",
      { className: "oc-state" + (kind === "error" ? " oc-state-error" : ""), role: kind === "error" ? "alert" : "status", "aria-live": "polite" },
      kind === "loading" ? h("span", { className: "oc-spinner", "aria-hidden": "true" }) : null,
      heading ? h("h1", { text: heading }) : null,
      text ? h("p", { text: text }) : null,
      retry ? h("button", { type: "button", className: "oc-btn", on: { click: retry } }, "Try again") : null
    );
    root.appendChild(box);
  }

  // ---------------------------------------------------------------- loading

  function load(keepNotice) {
    showState("loading", "", "Loading the order form…");
    var url = "/api/v1/public/order-forms/" + encodeURIComponent(publicId) + "?host=" + encodeURIComponent(host);
    fetch(url, { credentials: "omit", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().then(
          function (body) { return { status: r.status, body: body }; },
          function () { return { status: r.status, body: null }; }
        );
      })
      .then(function (res) {
        if (res.status === 200 && res.body) {
          start(res.body, keepNotice);
        } else if (res.status === 404) {
          showState("error", "This order form is not available", "Please contact us to place your order.");
        } else if (res.status === 409) {
          showState("error", "Orders are paused", (res.body && res.body.error && res.body.error.message) || "Please contact us.");
        } else if (res.status === 429) {
          showState("error", "Please wait a moment", "Too many requests. Try again in a few minutes.", function () { load(); });
        } else {
          showState("error", "Something went wrong", "We couldn't load the order form.", function () { load(); });
        }
      })
      .catch(function () {
        showState("error", "You seem to be offline", "Check your connection and try again.", function () { load(); });
      });
  }

  function start(config, keepNotice) {
    cfg = config;
    var theme = cfg.form.theme || {};
    if (/^#[0-9A-Fa-f]{6}$/.test(theme.accent || "")) document.documentElement.style.setProperty("--oc-accent", theme.accent);
    var radius = { none: "0px", small: "8px", large: "16px" }[theme.radius] || "8px";
    document.documentElement.style.setProperty("--oc-radius", radius);
    if (mode === "live" && !state.done) restore();
    pruneCart();
    if (!hasProducts()) state.step = state.step === "products" ? "details" : state.step;
    if (!keepNotice) state.notice = "";
    render(false);
  }

  function product(id) {
    for (var i = 0; i < cfg.products.length; i++) if (cfg.products[i].id === id) return cfg.products[i];
    return null;
  }

  function pruneCart() {
    Object.keys(state.cart).forEach(function (k) {
      var parts = k.split("|");
      var p = product(parts[0]);
      var ok = p && p.units.some(function (u) { return u.code === parts[1]; });
      if (!ok || !(Number(state.cart[k]) > 0)) delete state.cart[k];
    });
    var keys = {};
    cfg.fields.forEach(function (f) { keys[f.key] = true; });
    Object.keys(state.values).forEach(function (k) { if (!keys[k]) delete state.values[k]; });
  }

  function hasProducts() {
    return cfg.fields.some(function (f) { return f.kind === "PRODUCTS"; });
  }

  function productsField() {
    for (var i = 0; i < cfg.fields.length; i++) if (cfg.fields[i].kind === "PRODUCTS") return cfg.fields[i];
    return null;
  }

  function lines() {
    return Object.keys(state.cart)
      .filter(function (k) { return Number(state.cart[k]) > 0; })
      .map(function (k) {
        var parts = k.split("|");
        return { key: k, product: product(parts[0]), unit: parts[1], qty: state.cart[k] };
      })
      .filter(function (l) { return l.product; });
  }

  // ---------------------------------------------------------------- render

  var steps = [];

  function render(focusHeading) {
    root.setAttribute("aria-busy", "false");
    clear(root);
    root.className = "oc-shell";
    steps = (hasProducts() ? [["products", "Products"]] : []).concat([["details", "Your details"], ["review", "Review"]]);

    var head = h("header", { className: "oc-head" },
      cfg.form.logo_url ? h("img", { className: "oc-logo", src: cfg.form.logo_url, alt: "" }) : null,
      h("div", null,
        h("h1", { className: "oc-title", text: cfg.form.title }),
        cfg.form.intro && state.step !== "done" ? h("p", { className: "oc-intro", text: cfg.form.intro }) : null
      )
    );
    root.appendChild(head);
    if (mode === "preview") {
      root.appendChild(h("p", { className: "oc-banner", role: "note" }, "Preview — nothing you enter here is sent."));
    }
    if (state.done) return renderDone();

    var nav = h("ol", { className: "oc-steps", "aria-label": "Order steps" });
    steps.forEach(function (s, i) {
      var current = s[0] === state.step;
      nav.appendChild(
        h("li", { className: current ? "is-current" : "", "aria-current": current ? "step" : null },
          h("span", { className: "oc-step-n", "aria-hidden": "true", text: String(i + 1) }),
          h("span", { text: s[1] })
        )
      );
    });
    root.appendChild(nav);
    if (state.notice) root.appendChild(h("p", { className: "oc-notice", role: "alert", text: state.notice }));

    var body = h("section", { className: "oc-body" });
    root.appendChild(body);
    if (state.step === "products") renderProducts(body);
    else if (state.step === "details") renderDetails(body);
    else renderReview(body);
    if (focusHeading) {
      var hd = body.querySelector("h2");
      if (hd) { hd.setAttribute("tabindex", "-1"); hd.focus(); }
    }
  }

  function go(step) {
    state.step = step;
    state.notice = "";
    save();
    render(true);
    try { window.scrollTo(0, 0); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- step 1: products

  var listEl = null;
  var barCount = null;
  var barBtn = null;

  function renderProducts(body) {
    var pf = productsField();
    body.appendChild(h("h2", { className: "oc-h2", text: pf ? pf.label : "Products" }));
    if (pf && pf.help_text) body.appendChild(h("p", { className: "oc-help", text: pf.help_text }));

    var tools = h("div", { className: "oc-tools" });
    var search = h("input", {
      type: "search", id: "oc-search", className: "oc-input", placeholder: "Search products",
      autocomplete: "off", value: state.query,
      on: { input: function (e) { state.query = e.target.value; drawList(); } },
    });
    tools.appendChild(h("label", { className: "oc-sr", for: "oc-search", text: "Search products" }));
    tools.appendChild(search);
    if (cfg.categories.length) {
      var chips = h("div", { className: "oc-chips", role: "group", "aria-label": "Categories" });
      [{ id: "", name: "All" }].concat(cfg.categories).forEach(function (c) {
        chips.appendChild(h("button", {
          type: "button", className: "oc-chip", "aria-pressed": String(state.category === c.id),
          on: { click: function () { state.category = c.id; render(false); var s = document.getElementById("oc-search"); if (s) s.focus(); } },
        }, c.name));
      });
      tools.appendChild(chips);
    }
    body.appendChild(tools);
    if (cfg.form.show_prices) {
      body.appendChild(h("p", { className: "oc-help", text: "Prices shown are our standard prices. We confirm the final price with your order." }));
    }
    listEl = h("ul", { className: "oc-products", "aria-label": "Products" });
    body.appendChild(listEl);
    drawList();

    barCount = h("span", { className: "oc-bar-count", "aria-live": "polite" });
    barBtn = h("button", { type: "button", className: "oc-btn", on: { click: function () {
      if (pf && pf.required && !lines().length) { state.notice = "Add at least one product to continue."; render(false); return; }
      go("details");
    } } }, "Continue");
    body.appendChild(h("div", { className: "oc-bar" }, barCount, barBtn));
    updateBar();
  }

  function updateBar() {
    if (!barCount) return;
    var n = lines().length;
    barCount.textContent = n ? n + (n === 1 ? " product" : " products") + " in your order" : "No products yet";
  }

  function matches(p) {
    var q = state.query.trim().toLowerCase();
    if (state.category && p.category_id !== state.category) return false;
    if (!q) return true;
    return p.name.toLowerCase().indexOf(q) >= 0 || p.sku.toLowerCase().indexOf(q) >= 0;
  }

  function drawList() {
    clear(listEl);
    var shown = cfg.products.filter(matches);
    if (!shown.length) {
      listEl.appendChild(h("li", { className: "oc-empty", text: cfg.products.length ? "No products match your search." : "No products are available right now." }));
      return;
    }
    shown.forEach(function (p) { listEl.appendChild(card(p)); });
  }

  var noPhoto = {};
  function thumb(p, initials) {
    var fallback = h("span", { className: "oc-thumb", "aria-hidden": "true", text: initials });
    if (!p.image_url || noPhoto[p.id]) return fallback;
    var img = h("img", { className: "oc-thumb", src: p.image_url, alt: "", loading: "lazy", decoding: "async" });
    img.addEventListener("error", function () {
      noPhoto[p.id] = true;
      if (img.parentNode) img.parentNode.replaceChild(fallback, img);
    });
    return img;
  }

  function card(p) {
    var unit = state.units[p.id] || p.units[0].code;
    if (!p.units.some(function (u) { return u.code === unit; })) unit = p.units[0].code;
    var key = p.id + "|" + unit;
    var qty = state.cart[key] || "0";
    var uinfo = p.units.filter(function (u) { return u.code === unit; })[0];
    var initials = p.name.split(/\s+/).map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase();
    var qid = "oc-q-" + p.id;
    var inOrder = Number(qty) > 0;

    var unitCtl;
    if (p.units.length > 1) {
      unitCtl = h("select", { className: "oc-input oc-unit", "aria-label": "Unit for " + p.name, on: { change: function (e) {
        state.units[p.id] = e.target.value; replace(); } } });
      p.units.forEach(function (u) {
        unitCtl.appendChild(h("option", { value: u.code, selected: u.code === unit, text: "per " + unitName(u.code, 1) }));
      });
    } else {
      unitCtl = h("span", { className: "oc-unit-label", text: "per " + unitName(unit, 1) });
    }

    function set(v) {
      var n = Number(v);
      if (!(n > 0)) delete state.cart[key];
      else state.cart[key] = String(Math.min(n, 9999));
      save();
      replace();
      updateBar();
    }
    function step(d) {
      var n = Number(state.cart[key] || 0) + d;
      set(WHOLE[unit] ? Math.max(0, Math.round(n)) : Math.max(0, n));
      var again = document.getElementById(qid);
      if (again) again.focus();
    }
    function replace() {
      var fresh = card(p);
      li.parentNode && li.parentNode.replaceChild(fresh, li);
    }

    var err = state.itemErrors[key];
    var li = h("li", { className: "oc-card" + (inOrder ? " is-in" : "") },
      thumb(p, initials),
      h("div", { className: "oc-card-main" },
        h("p", { className: "oc-name" }, p.name, h("span", { className: "oc-sku", text: " " + p.sku })),
        uinfo && uinfo.price ? h("p", { className: "oc-price", text: uinfo.price + " " + uinfo.currency + " per " + unitName(unit, 1) }) : null,
        err ? h("p", { className: "oc-err", text: err }) : null
      ),
      h("div", { className: "oc-card-ctl" },
        unitCtl,
        h("div", { className: "oc-stepper" },
          h("button", { type: "button", className: "oc-step", "aria-label": "Fewer " + unitName(unit, 2) + " of " + p.name, disabled: !inOrder, on: { click: function () { step(-1); } } }, "−"),
          h("input", { id: qid, className: "oc-input oc-qty", type: "text", inputmode: WHOLE[unit] ? "numeric" : "decimal",
            "aria-label": "Quantity of " + p.name + " in " + unitName(unit, 2), value: inOrder ? qty : "", placeholder: "0",
            on: { change: function (e) {
              var raw = e.target.value.trim().replace(",", ".");
              if (raw === "") return set(0);
              var n = Number(raw);
              if (!isFinite(n) || n < 0) { e.target.value = qty === "0" ? "" : qty; return; }
              set(WHOLE[unit] ? Math.round(n) : Math.round(n * 10000) / 10000);
            } } }),
          h("button", { type: "button", className: "oc-step", "aria-label": "More " + unitName(unit, 2) + " of " + p.name, on: { click: function () { step(1); } } }, "+")
        )
      )
    );
    return li;
  }

  // ---------------------------------------------------------------- step 2: details

  function fieldControl(f) {
    var id = "oc-f-" + f.key;
    var err = state.errors[f.key];
    var describedBy = [f.help_text ? id + "-help" : "", err ? id + "-err" : ""].filter(Boolean).join(" ") || null;
    var v = state.values[f.key];
    var common = { id: id, name: f.key, required: f.required, "aria-invalid": err ? "true" : null, "aria-describedby": describedBy };
    function onv(e) {
      state.values[f.key] = f.kind === "CHECKBOX" ? e.target.checked : e.target.value;
      if (state.errors[f.key]) { delete state.errors[f.key]; }
      save();
    }
    var ctl;
    if (f.kind === "TEXTAREA") {
      ctl = h("textarea", Object.assign({ className: "oc-input", rows: f.key === "delivery_address" ? 3 : 4, maxlength: 2000, on: { input: onv } }, common));
      ctl.value = v || "";
    } else if (f.kind === "SELECT") {
      ctl = h("select", Object.assign({ className: "oc-input", on: { change: onv } }, common),
        h("option", { value: "", text: "Choose…" }),
        f.options.map(function (o) { return h("option", { value: o, selected: v === o, text: o }); }));
    } else if (f.kind === "CHECKBOX") {
      ctl = h("input", Object.assign({ type: "checkbox", className: "oc-check", checked: !!v, on: { change: onv } }, common));
      return h("div", { className: "oc-field oc-field-check" },
        ctl,
        h("label", { for: id }, f.label, f.required ? h("span", { className: "oc-req", "aria-hidden": "true", text: " *" }) : null),
        f.help_text ? h("p", { id: id + "-help", className: "oc-help", text: f.help_text }) : null,
        err ? h("p", { id: id + "-err", className: "oc-err", text: err }) : null
      );
    } else {
      var type = { EMAIL: "email", PHONE: "tel", DATE: "date" }[f.kind] || "text";
      var auto = { customer_name: "name", business_name: "organization", email: "email", phone: "tel" }[f.key] || null;
      ctl = h("input", Object.assign({ type: type, className: "oc-input", autocomplete: auto, maxlength: f.kind === "PHONE" ? 32 : 200, on: { input: onv } }, common));
      if (f.kind === "DATE") ctl.setAttribute("min", new Date().toISOString().slice(0, 10));
      ctl.value = v || "";
    }
    return h("div", { className: "oc-field" },
      h("label", { for: id, className: "oc-label" }, f.label, f.required ? h("span", { className: "oc-req", "aria-hidden": "true", text: " *" }) : null, f.required ? h("span", { className: "oc-sr", text: " (required)" }) : null),
      f.help_text ? h("p", { id: id + "-help", className: "oc-help", text: f.help_text }) : null,
      ctl,
      err ? h("p", { id: id + "-err", className: "oc-err", text: err }) : null
    );
  }

  function checkDetails() {
    var errs = {};
    cfg.fields.forEach(function (f) {
      if (f.kind === "PRODUCTS") return;
      var v = state.values[f.key];
      var s = typeof v === "string" ? v.trim() : v;
      if (f.required && (f.kind === "CHECKBOX" ? !v : !s)) errs[f.key] = f.kind === "CHECKBOX" ? "Please tick this box" : "This field is required";
      else if (s && f.kind === "EMAIL" && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(s)) errs[f.key] = "Enter a valid email address";
    });
    return errs;
  }

  function renderDetails(body) {
    body.appendChild(h("h2", { className: "oc-h2", text: "Your details" }));
    body.appendChild(h("p", { className: "oc-help", text: "Fields marked * are required." }));
    var form = h("form", { className: "oc-form", novalidate: true, on: { submit: function (e) {
      e.preventDefault();
      state.errors = checkDetails();
      if (Object.keys(state.errors).length) {
        render(false);
        var first = root.querySelector("[aria-invalid='true']");
        if (first) first.focus();
        return;
      }
      go("review");
    } } });
    cfg.fields.forEach(function (f) { if (f.kind !== "PRODUCTS") form.appendChild(fieldControl(f)); });
    form.appendChild(h("div", { className: "oc-actions" },
      hasProducts() ? h("button", { type: "button", className: "oc-btn oc-btn-ghost", on: { click: function () { go("products"); } } }, "Back") : null,
      h("button", { type: "submit", className: "oc-btn" }, "Review order")
    ));
    body.appendChild(form);
  }

  // ---------------------------------------------------------------- step 3: review + send

  function renderReview(body) {
    body.appendChild(h("h2", { className: "oc-h2", text: "Check your order" }));
    var ls = lines();
    if (hasProducts()) {
      var list = h("ul", { className: "oc-review-list" });
      ls.forEach(function (l) {
        list.appendChild(h("li", null,
          h("span", { className: "oc-review-qty", text: l.qty + " " + unitName(l.unit, l.qty) }),
          h("span", { text: l.product.name }),
          state.itemErrors[l.key] ? h("span", { className: "oc-err", text: state.itemErrors[l.key] }) : null
        ));
      });
      if (!ls.length) list.appendChild(h("li", { className: "oc-empty", text: "No products." }));
      body.appendChild(h("div", { className: "oc-review-block" },
        h("div", { className: "oc-review-head" }, h("h3", { text: "Products" }), h("button", { type: "button", className: "oc-link", on: { click: function () { go("products"); } } }, "Change")),
        list));
    }
    var dl = h("dl", { className: "oc-dl" });
    cfg.fields.forEach(function (f) {
      if (f.kind === "PRODUCTS") return;
      var v = state.values[f.key];
      var shown = f.kind === "CHECKBOX" ? (v ? "Yes" : "No") : (v || "").toString().trim();
      if (!shown) return;
      dl.appendChild(h("dt", { text: f.label }));
      dl.appendChild(h("dd", { text: shown }));
    });
    body.appendChild(h("div", { className: "oc-review-block" },
      h("div", { className: "oc-review-head" }, h("h3", { text: "Your details" }), h("button", { type: "button", className: "oc-link", on: { click: function () { go("details"); } } }, "Change")),
      dl));

    var consent = cfg.form.consent || {};
    var consentErr = state.errors.consent;
    if (consent.required) {
      body.appendChild(h("div", { className: "oc-field oc-field-check" },
        h("input", { type: "checkbox", id: "oc-consent", className: "oc-check", checked: state.consent, "aria-invalid": consentErr ? "true" : null, "aria-describedby": consentErr ? "oc-consent-err" : null,
          on: { change: function (e) { state.consent = e.target.checked; } } }),
        h("label", { for: "oc-consent", text: consent.text }),
        consentErr ? h("p", { id: "oc-consent-err", className: "oc-err", text: consentErr }) : null));
    }
    // Honeypot: hidden from people and screen readers; bots that fill every field reveal themselves.
    var trap = h("div", { className: "oc-trap", "aria-hidden": "true" },
      h("label", { for: "oc-website", text: "Website" }),
      h("input", { id: "oc-website", name: "website", type: "text", tabindex: "-1", autocomplete: "off" }));
    body.appendChild(trap);

    var sendBtn = h("button", { type: "button", className: "oc-btn", disabled: state.sending || mode === "preview", on: { click: send } },
      state.sending ? "Sending…" : "Send order");
    body.appendChild(h("p", { className: "oc-help", text: "Sending this is a request, not a purchase. We will contact you to confirm your order." }));
    body.appendChild(h("div", { className: "oc-actions" },
      h("button", { type: "button", className: "oc-btn oc-btn-ghost", on: { click: function () { go("details"); } } }, "Back"),
      sendBtn));
    body.appendChild(h("p", { className: "oc-sr", role: "status", "aria-live": "polite", text: state.sending ? "Sending your order" : "" }));
  }

  function send() {
    if (state.sending || mode !== "live") return;
    var consent = cfg.form.consent || {};
    state.errors = checkDetails();
    if (consent.required && !state.consent) state.errors.consent = "Please agree to continue";
    if (Object.keys(state.errors).length) {
      if (state.errors.consent && Object.keys(state.errors).length === 1) { render(false); return; }
      state.notice = "Some details need attention.";
      state.step = "details";
      render(true);
      return;
    }
    var values = {};
    cfg.fields.forEach(function (f) {
      if (f.kind === "PRODUCTS") return;
      var v = state.values[f.key];
      if (f.kind === "CHECKBOX") values[f.key] = !!v;
      else if (v !== undefined && v !== null && String(v).trim() !== "") values[f.key] = String(v);
    });
    var trap = document.getElementById("oc-website");
    var payload = {
      form_token: cfg.token,
      client_token: state.token,
      values: values,
      items: lines().map(function (l) { return { product_id: l.product.id, unit_code: l.unit, qty: l.qty }; }),
      consent: !!state.consent,
      website: trap ? trap.value : "",
    };
    state.sending = true;
    state.itemErrors = {};
    render(false);
    fetch("/api/v1/public/order-forms/" + encodeURIComponent(publicId) + "/submissions", {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (b) { return { status: r.status, body: b }; }, function () { return { status: r.status, body: null }; });
      })
      .then(function (res) {
        state.sending = false;
        var err = (res.body && res.body.error) || {};
        if (res.status === 200 || res.status === 201) {
          state.done = res.body;
          forget();
          render(true);
          return;
        }
        if (res.status === 422 && err.fields) return showFieldErrors(err.fields, err.message);
        if (res.status === 409 && (err.code === "FORM_UPDATED" || err.code === "FORM_EXPIRED")) {
          state.notice = err.message;
          save();
          return load(true);
        }
        state.notice = err.message || (res.status === 429 ? "Too many orders right now. Please try again later." : "We couldn't send your order. Please try again.");
        render(false);
      })
      .catch(function () {
        state.sending = false;
        state.notice = "You seem to be offline. Your order is kept here; try sending again.";
        render(false);
      });
  }

  function showFieldErrors(fields, message) {
    var detailErr = {};
    var products = false;
    var ls = lines();
    Object.keys(fields).forEach(function (k) {
      var m = /^items\.(\d+)$/.exec(k);
      if (m) {
        var l = ls[Number(m[1]) - 1];
        if (l) state.itemErrors[l.key] = fields[k];
        products = true;
      } else if (k === "products") {
        products = true;
      } else {
        detailErr[k] = fields[k];
      }
    });
    state.errors = detailErr;
    state.notice = message || "Please check the highlighted fields.";
    if (Object.keys(detailErr).length && !(Object.keys(detailErr).length === 1 && detailErr.consent)) state.step = "details";
    else if (products && hasProducts()) state.step = "products";
    render(true);
  }

  function renderDone() {
    var d = state.done;
    root.appendChild(h("section", { className: "oc-body oc-done", role: "status" },
      h("h2", { className: "oc-h2", text: "Order request sent" }),
      h("p", { text: d.message }),
      h("p", { className: "oc-ref" }, "Your reference: ", h("strong", { text: d.reference })),
      h("button", { type: "button", className: "oc-btn oc-btn-ghost", on: { click: function () {
        state = freshState();
        render(true);
      } } }, "Start another order")));
    var hd = root.querySelector(".oc-done h2");
    if (hd) { hd.setAttribute("tabindex", "-1"); hd.focus(); }
  }

  // ---------------------------------------------------------------- boot

  if (mode === "preview") {
    showState("loading", "", "Waiting for the form builder…");
    window.addEventListener("message", function (e) {
      if (e.origin !== staffOrigin || e.source !== window.parent) return;
      var m = e.data;
      if (!m || m.type !== "oc-preview" || !m.config || !m.config.form) return;
      var step = state.step;
      start(m.config, false);
      if (m.step && ["products", "details", "review"].indexOf(m.step) >= 0) { state.step = m.step; render(false); }
      else if (step !== state.step) { state.step = step; render(false); }
    });
    if (window.parent !== window && staffOrigin) window.parent.postMessage({ type: "oc-preview-ready" }, staffOrigin);
  } else {
    load(false);
  }
})();
