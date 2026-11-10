/* Online Orders customer page: product cards + cart + checkout. No framework, no third-party
   code. Every value from the server or the customer is inserted with textContent/attributes,
   never as HTML. Inside the company website (an iframe) it shows no header or footer. */
(function () {
  "use strict";

  var root = document.getElementById("oc-app");
  if (!root) return;
  var mode = root.getAttribute("data-mode") || "live";
  var publicId = root.getAttribute("data-public-id") || "";
  var host = root.getAttribute("data-host") || "";
  var staffOrigin = root.getAttribute("data-staff-origin") || "";
  var framed = window.parent !== window;
  if (!framed) document.documentElement.classList.add("oc-standalone");
  var UNIT = { BOX: ["box", "boxes", "Box"], PIECE: ["piece", "pieces", "Piece"], LB: ["lb", "lb", "Pound (lb)"], KG: ["kg", "kg", "Kilogram (kg)"] };
  var WHOLE = { BOX: true, PIECE: true };
  var SVG = "http://www.w3.org/2000/svg";
  var ICONS = {
    box: "M21 8l-9-5-9 5v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
    scale: "M12 3v18M5 7h14M5 7l-3 7a3 3 0 006 0zM19 7l-3 7a3 3 0 006 0zM8 21h8",
    tag: "M20 12l-8 8-9-9V3h8zM7.5 7.5h.01",
    cart: "M3 4h2l2.4 11.2a1 1 0 001 .8h9.2a1 1 0 001-.8L20 8H6.2M9 20.5a1 1 0 100-2 1 1 0 000 2zM17 20.5a1 1 0 100-2 1 1 0 000 2z",
    search: "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3",
    x: "M18 6L6 18M6 6l12 12",
    back: "M15 18l-6-6 6-6",
    check: "M5 12l5 5L20 7",
    order: "M9 5h10M9 12h10M9 19h10M4 5h.01M4 12h.01M4 19h.01",
    quote: "M6 2h9l5 5v15H6zM14 2v6h6M9 13h6M9 17h4",
  };

  var cfg = null;
  var state = freshState();
  var storeKey = "oc:" + publicId;
  // Set when the website runs the companion script: which part of this (full-height) frame is
  // on screen, so the cart and the phone cart bar can follow the page scroll.
  var view = null;

  function freshState() {
    return {
      view: "shop", // shop | checkout | done
      cart: {}, // "productId|UNIT" -> qty string
      values: {},
      consent: false,
      requestType: "ORDER",
      errors: {},
      itemErrors: {},
      query: "",
      category: "",
      units: {}, // productId -> chosen unit
      token: randomToken(),
      sending: false,
      done: null,
      notice: "",
      sheet: false,
    };
  }

  function randomToken() {
    var b = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(b);
    return Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
  }

  // ---------------------------------------------------------------- DOM helpers

  function h(tag, attrs) {
    var el = document.createElement(tag);
    var a = attrs || {};
    Object.keys(a).forEach(function (k) {
      var v = a[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") el.textContent = v;
      else if (k === "on") Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); });
      else if (k === "className") el.className = v;
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

  function icon(name, size) {
    var s = document.createElementNS(SVG, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", String(size || 16));
    s.setAttribute("height", String(size || 16));
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    var p = document.createElementNS(SVG, "path");
    p.setAttribute("d", ICONS[name]);
    s.appendChild(p);
    return s;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function byId(id) { return document.getElementById(id); }
  function unitName(code, qty) { var u = UNIT[code] || [code, code]; return Number(qty) === 1 ? u[0] : u[1]; }
  function unitTitle(code) { return (UNIT[code] || [0, 0, code])[2]; }
  function say(text) { var l = byId("oc-live"); if (l) { l.textContent = ""; setTimeout(function () { l.textContent = text; }, 30); } }
  function initials(name) { return name.split(/\s+/).map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase(); }

  // ---------------------------------------------------------------- persistence (best effort)

  function save() {
    if (mode !== "live") return;
    try {
      window.sessionStorage.setItem(storeKey, JSON.stringify({ cart: state.cart, values: state.values, token: state.token, requestType: state.requestType }));
    } catch (e) { /* storage blocked in some third-party frames: fine */ }
  }
  function restore() {
    try {
      var s = JSON.parse(window.sessionStorage.getItem(storeKey) || "null");
      if (s && typeof s === "object") {
        state.cart = s.cart || {};
        state.values = s.values || {};
        if (s.requestType === "QUOTE") state.requestType = "QUOTE";
        if (typeof s.token === "string" && /^[a-f0-9]{32}$/.test(s.token)) state.token = s.token;
      }
    } catch (e) { /* ignore */ }
  }
  function forget() { try { window.sessionStorage.removeItem(storeKey); } catch (e) { /* ignore */ } }

  // ---------------------------------------------------------------- website frame (height + viewport)

  var lastHeight = 0;
  function reportHeight() {
    if (!framed || mode !== "live") return;
    var hgt = Math.ceil(document.documentElement.getBoundingClientRect().height);
    if (Math.abs(hgt - lastHeight) < 2) return;
    lastHeight = hgt;
    window.parent.postMessage({ type: "oc-height", height: hgt }, "*"); // a number only
  }
  function toTop() {
    if (view) window.parent.postMessage({ type: "oc-top" }, "*");
    else try { window.scrollTo(0, 0); } catch (e) { /* ignore */ }
  }
  if (framed && mode === "live") {
    if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(document.documentElement);
    window.addEventListener("load", reportHeight);
    window.addEventListener("message", function (e) {
      if (e.source !== window.parent || !e.data || e.data.type !== "oc-viewport") return;
      var top = Number(e.data.top), height = Number(e.data.height);
      if (!isFinite(top) || !isFinite(height) || height <= 0) return;
      if (!view) document.documentElement.classList.add("oc-follow");
      view = { top: top, height: Math.min(height, 4000) };
      follow();
    });
  }

  /** In a full-height frame CSS "sticky" has nothing to stick to: place the cart by hand. */
  function follow() {
    if (!view) return;
    var docH = document.documentElement.scrollHeight;
    var col = byId("oc-cart-col"), inner = byId("oc-cart-inner");
    if (col && inner && col.offsetParent !== null && getComputedStyle(col).display !== "none") {
      var colTop = col.getBoundingClientRect().top + window.scrollY;
      var max = Math.max(0, col.offsetHeight - inner.offsetHeight);
      var y = Math.min(max, Math.max(0, view.top + 16 - colTop));
      inner.style.transform = "translateY(" + Math.round(y) + "px)";
      inner.style.maxHeight = Math.max(320, view.height - 32) + "px";
    }
    var bar = byId("oc-cartbar");
    if (bar) {
      var bh = bar.offsetHeight || 64;
      var t = Math.min(docH - bh - 8, Math.max(0, view.top + view.height - bh - 12));
      bar.style.top = Math.round(t) + "px";
    }
    var sheet = byId("oc-sheet");
    if (sheet) {
      sheet.style.top = Math.max(0, Math.round(view.top)) + "px";
      sheet.style.height = Math.round(Math.min(view.height, docH)) + "px";
    }
  }

  // ---------------------------------------------------------------- loading / states

  function showState(kind, heading, text, retry) {
    root.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
    clear(root);
    root.className = "oc-shell";
    root.appendChild(h("div", { className: "oc-state" + (kind === "error" ? " oc-state-error" : ""), role: kind === "error" ? "alert" : "status", "aria-live": "polite" },
      kind === "loading" ? h("span", { className: "oc-spinner", "aria-hidden": "true" }) : null,
      heading ? h("h1", { text: heading }) : null,
      text ? h("p", { text: text }) : null,
      retry ? h("button", { type: "button", className: "oc-btn", on: { click: retry } }, "Try again") : null));
    reportHeight();
  }

  function load(keepNotice) {
    showState("loading", "", "Loading products…");
    var url = "/api/v1/public/order-forms/" + encodeURIComponent(publicId) + "?host=" + encodeURIComponent(host);
    fetch(url, { credentials: "omit", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().then(function (b) { return { status: r.status, body: b }; }, function () { return { status: r.status, body: null }; });
      })
      .then(function (res) {
        if (res.status === 200 && res.body) start(res.body, keepNotice);
        else if (res.status === 404) showState("error", "This order form is not available", "Please contact us to place your order.");
        else if (res.status === 409) showState("error", "Orders are paused", (res.body && res.body.error && res.body.error.message) || "Please contact us.");
        else if (res.status === 429) showState("error", "Please wait a moment", "Too many requests. Try again in a few minutes.", function () { load(); });
        else showState("error", "Something went wrong", "We couldn't load the products.", function () { load(); });
      })
      .catch(function () { showState("error", "You seem to be offline", "Check your connection and try again.", function () { load(); }); });
  }

  function hexToRgb(hex) {
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }

  function start(config, keepNotice) {
    cfg = config;
    var theme = cfg.form.theme || {};
    var rgb = hexToRgb(theme.accent);
    var rs = document.documentElement.style;
    if (rgb) {
      rs.setProperty("--oc-accent", theme.accent);
      rs.setProperty("--oc-accent-soft", "rgba(" + rgb.join(",") + ",0.08)");
      rs.setProperty("--oc-accent-line", "rgba(" + rgb.join(",") + ",0.35)");
    }
    var r = { none: ["0px", "0px", "6px"], small: ["14px", "10px", "10px"], large: ["22px", "14px", "999px"] }[theme.radius] || ["22px", "14px", "999px"];
    rs.setProperty("--oc-radius", r[0]);
    rs.setProperty("--oc-radius-sm", r[1]);
    rs.setProperty("--oc-radius-btn", r[2]);
    if (mode === "live" && !state.done) restore();
    pruneCart();
    if (!hasProducts() && state.view === "shop") state.view = "checkout";
    if (!keepNotice) state.notice = "";
    render(false);
  }

  function product(id) {
    for (var i = 0; i < cfg.products.length; i++) if (cfg.products[i].id === id) return cfg.products[i];
    return null;
  }
  function category(id) {
    for (var i = 0; i < cfg.categories.length; i++) if (cfg.categories[i].id === id) return cfg.categories[i].name;
    return "";
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
  function hasProducts() { return cfg.fields.some(function (f) { return f.kind === "PRODUCTS"; }); }
  function productsField() { for (var i = 0; i < cfg.fields.length; i++) if (cfg.fields[i].kind === "PRODUCTS") return cfg.fields[i]; return null; }
  function lines() {
    return Object.keys(state.cart)
      .filter(function (k) { return Number(state.cart[k]) > 0; })
      .map(function (k) { var parts = k.split("|"); return { key: k, product: product(parts[0]), unit: parts[1], qty: state.cart[k] }; })
      .filter(function (l) { return l.product; });
  }
  function cartCount() { return lines().length; }

  // ---------------------------------------------------------------- cart operations

  function setQty(pid, unit, value, announce) {
    var key = pid + "|" + unit;
    var n = Number(value);
    var p = product(pid);
    var before = Number(state.cart[key] || 0);
    if (!(n > 0)) delete state.cart[key];
    else state.cart[key] = String(Math.min(WHOLE[unit] ? Math.round(n) : Math.round(n * 10000) / 10000, 9999));
    delete state.itemErrors[key];
    save();
    refreshCard(pid);
    renderCart();
    if (announce && p) {
      var now = Number(state.cart[key] || 0);
      if (!now) say(p.name + " removed from your order");
      else if (!before) say(p.name + " added: " + now + " " + unitName(unit, now));
      else say(p.name + ": " + now + " " + unitName(unit, now));
    }
  }

  // ---------------------------------------------------------------- render: frame

  function render(focusHeading) {
    root.setAttribute("aria-busy", "false");
    clear(root);
    root.className = "oc-shell" + (framed ? " is-framed" : "");
    // On the company website the page already has its header and footer: none here. The direct
    // link (opened on its own) gets a plain title so people know where they are.
    if (!framed) {
      root.appendChild(h("header", { className: "oc-standalone-head" },
        cfg.form.logo_url ? h("img", { className: "oc-logo", src: cfg.form.logo_url, alt: "" }) : null,
        h("h1", { className: "oc-title", text: cfg.form.title })));
    }
    if (mode === "preview") root.appendChild(h("p", { className: "oc-banner", role: "note" }, "Preview: nothing you enter here is sent."));
    root.appendChild(h("p", { id: "oc-live", className: "oc-sr", "aria-live": "polite" }));
    if (state.view === "done") renderDone();
    else if (state.view === "checkout") renderCheckout();
    else renderShop();
    if (focusHeading) {
      var hd = root.querySelector("[data-focus]");
      if (hd) { hd.setAttribute("tabindex", "-1"); hd.focus({ preventScroll: true }); }
    }
    follow();
    reportHeight();
  }

  function go(viewName) {
    state.view = viewName;
    state.notice = "";
    state.sheet = false;
    save();
    render(true);
    toTop();
  }

  // ---------------------------------------------------------------- shop

  var gridEl = null;
  var countEl = null;

  function renderShop() {
    var pf = productsField();
    var shop = h("div", { className: "oc-shop" });
    var main = h("section", { className: "oc-main", "aria-labelledby": "oc-shop-h" });
    main.appendChild(h("h2", { id: "oc-shop-h", className: "oc-sr", "data-focus": "1", text: pf ? pf.label : "Products" }));
    if (cfg.form.intro) main.appendChild(h("p", { className: "oc-intro", text: cfg.form.intro }));
    if (pf && pf.help_text) main.appendChild(h("p", { className: "oc-help", text: pf.help_text }));
    if (state.notice) main.appendChild(h("p", { className: "oc-notice", role: "alert", text: state.notice }));

    var search = h("input", {
      type: "search", id: "oc-search", className: "oc-input oc-search-input", placeholder: "Search products",
      autocomplete: "off", value: state.query,
      on: { input: function (e) { state.query = e.target.value; drawGrid(); } },
    });
    var catSel = null;
    if (cfg.categories.length) {
      catSel = h("select", { id: "oc-cat", className: "oc-input oc-select", on: { change: function (e) { state.category = e.target.value; drawGrid(); } } },
        h("option", { value: "", text: "All categories" }),
        cfg.categories.map(function (c) { return h("option", { value: c.id, selected: state.category === c.id, text: c.name }); }));
    }
    main.appendChild(h("div", { className: "oc-toolbar" },
      h("label", { className: "oc-search" }, icon("search", 18), h("span", { className: "oc-sr", text: "Search products" }), search),
      catSel ? h("label", { className: "oc-cat" }, h("span", { className: "oc-sr", text: "Category" }), catSel) : null));
    if (cfg.form.show_prices) main.appendChild(h("p", { className: "oc-help", text: "Prices shown are our standard prices. Our team confirms the final price with your order." }));
    countEl = h("p", { className: "oc-count", "aria-live": "polite" });
    main.appendChild(countEl);
    gridEl = h("ul", { className: "oc-grid", "aria-label": "Products" });
    main.appendChild(gridEl);
    shop.appendChild(main);

    shop.appendChild(h("div", { id: "oc-cart-col", className: "oc-cart-col" },
      h("aside", { id: "oc-cart-inner", className: "oc-cart", "aria-label": "Your order" })));
    root.appendChild(shop);
    root.appendChild(h("div", { id: "oc-cartbar", className: "oc-cartbar" }));
    root.appendChild(h("div", { id: "oc-sheet", className: "oc-sheet", hidden: !state.sheet }));
    drawGrid();
    renderCart();
  }

  function matches(p) {
    var q = state.query.trim().toLowerCase();
    if (state.category && p.category_id !== state.category) return false;
    if (!q) return true;
    return p.name.toLowerCase().indexOf(q) >= 0 || p.sku.toLowerCase().indexOf(q) >= 0;
  }

  function drawGrid() {
    if (!gridEl) return;
    clear(gridEl);
    var shown = cfg.products.filter(matches);
    countEl.textContent = shown.length === cfg.products.length ? shown.length + " products" : shown.length + " of " + cfg.products.length + " products";
    if (!shown.length) {
      gridEl.appendChild(h("li", { className: "oc-empty", text: cfg.products.length ? "No products match your search." : "No products are available right now." }));
    }
    shown.forEach(function (p) { gridEl.appendChild(card(p)); });
    reportHeight();
    follow();
  }

  var noPhoto = {};
  function photo(p, cls) {
    var fallback = h("span", { className: cls + " oc-initials", "aria-hidden": "true", text: initials(p.name) });
    if (!p.image_url || noPhoto[p.id]) return fallback;
    var img = h("img", { className: cls, src: p.image_url, alt: "", loading: "lazy", decoding: "async" });
    img.addEventListener("error", function () { noPhoto[p.id] = true; if (img.parentNode) img.parentNode.replaceChild(fallback, img); });
    return img;
  }

  function chosenUnit(p) {
    var u = state.units[p.id];
    if (!p.units.some(function (x) { return x.code === u; })) {
      // Prefer a unit that's already in the cart, else the first one offered.
      var inCart = p.units.filter(function (x) { return Number(state.cart[p.id + "|" + x.code]) > 0; })[0];
      u = (inCart || p.units[0]).code;
    }
    return u;
  }

  function priceText(p) {
    var pr = (p.prices || [])[0];
    return pr ? pr.currency + " " + pr.price + " / " + unitName(pr.unit, 1) : null;
  }

  function row(iconName, label, value) {
    return h("div", { className: "oc-row" }, h("dt", null, icon(iconName, 16), label), h("dd", null, value));
  }

  function stepper(p, unit, small) {
    var key = p.id + "|" + unit;
    var qty = state.cart[key] || "";
    var id = (small ? "oc-cq-" : "oc-q-") + p.id + "-" + unit;
    function bump(d) {
      var n = Number(state.cart[key] || 0) + d;
      setQty(p.id, unit, WHOLE[unit] ? Math.max(0, Math.round(n)) : Math.max(0, n), true);
      var again = byId(id + (d > 0 ? "-more" : "-less")) || byId(id + "-more");
      if (again) again.focus();
    }
    return h("div", { className: "oc-stepper" + (small ? " is-small" : "") },
      h("button", { id: id + "-less", type: "button", className: "oc-step", "aria-label": "Fewer " + unitName(unit, 2) + " of " + p.name, on: { click: function () { bump(-1); } } }, "−"),
      h("input", {
        id: id, className: "oc-qty", type: "text", inputmode: WHOLE[unit] ? "numeric" : "decimal", value: qty,
        "aria-label": "Quantity of " + p.name + " in " + unitName(unit, 2),
        on: { change: function (e) {
          var raw = e.target.value.trim().replace(",", ".");
          var n = raw === "" ? 0 : Number(raw);
          if (!isFinite(n) || n < 0) { e.target.value = qty; return; }
          setQty(p.id, unit, n, true);
        } },
      }),
      h("button", { id: id + "-more", type: "button", className: "oc-step", "aria-label": "More " + unitName(unit, 2) + " of " + p.name, on: { click: function () { bump(1); } } }, "+"));
  }

  function card(p) {
    var unit = chosenUnit(p);
    var key = p.id + "|" + unit;
    var inCart = Number(state.cart[key]) > 0;
    var anyInCart = p.units.some(function (u) { return Number(state.cart[p.id + "|" + u.code]) > 0; });
    var uinfo = p.units.filter(function (u) { return u.code === unit; })[0] || {};
    var cat = category(p.category_id);
    var price = cfg.form.show_prices ? priceText(p) : null;

    var soldBy;
    if (p.units.length > 1) {
      soldBy = h("select", { className: "oc-unit", "aria-label": "Unit for " + p.name, on: { change: function (e) { state.units[p.id] = e.target.value; refreshCard(p.id); } } },
        p.units.map(function (u) { return h("option", { value: u.code, selected: u.code === unit, text: unitTitle(u.code) }); }));
    } else soldBy = unitTitle(unit);

    var err = state.itemErrors[key];
    var action = inCart
      ? h("div", { className: "oc-card-action" }, h("span", { className: "oc-incart" }, icon("check", 16), "In your order"), stepper(p, unit, false))
      : h("div", { className: "oc-card-action" },
          h("button", { type: "button", className: "oc-btn oc-add", "aria-label": "Add " + p.name + " to your order", on: { click: function () {
            setQty(p.id, unit, 1, true);
            var q = byId("oc-q-" + p.id + "-" + unit + "-more");
            if (q) q.focus();
          } } }, icon("cart", 17), "Add to order"));

    return h("li", { id: "oc-card-" + p.id, className: "oc-card" + (anyInCart ? " is-in" : "") },
      h("div", { className: "oc-card-top" },
        h("div", { className: "oc-photo-tile" }, photo(p, "oc-photo")),
        h("div", { className: "oc-info" },
          h("h3", { className: "oc-name", text: p.name }),
          h("p", { className: "oc-meta", text: p.sku + (cat ? " · " + cat : "") }),
          h("dl", { className: "oc-rows" },
            row("box", "Sold by", soldBy),
            uinfo.size ? row("scale", "Case size", uinfo.size) : null,
            price ? row("tag", "Price", price) : null))),
      err ? h("p", { className: "oc-err", text: err }) : null,
      action);
  }

  function refreshCard(pid) {
    var old = byId("oc-card-" + pid);
    var p = product(pid);
    if (old && p && old.parentNode) old.parentNode.replaceChild(card(p), old);
  }

  // ---------------------------------------------------------------- cart (aside, phone bar, sheet)

  function cartBody(inSheet) {
    var ls = lines();
    var n = ls.length;
    var wrap = h("div", { className: "oc-cart-body" });
    wrap.appendChild(h("div", { className: "oc-cart-head" },
      h("h2", { className: "oc-cart-title", id: inSheet ? "oc-sheet-h" : null }, icon("cart", 20), "Your order"),
      n ? h("span", { className: "oc-pill", text: n + (n === 1 ? " item" : " items") }) : null,
      inSheet ? h("button", { type: "button", className: "oc-icon-btn", "aria-label": "Close your order", on: { click: function () { state.sheet = false; renderCart(); var b = byId("oc-cartbar-btn"); if (b) b.focus(); } } }, icon("x", 20)) : null));
    if (!n) {
      wrap.appendChild(h("div", { className: "oc-cart-empty" },
        h("span", { className: "oc-empty-icon", "aria-hidden": "true" }, icon("cart", 26)),
        h("p", { text: "Your order is empty." }),
        h("p", { className: "oc-help", text: "Add products, then send them as an order or ask us for a quote." })));
      return wrap;
    }
    var list = h("ul", { className: "oc-cart-lines" });
    ls.forEach(function (l) {
      var err = state.itemErrors[l.key];
      list.appendChild(h("li", { className: "oc-line" },
        h("div", { className: "oc-line-tile" }, photo(l.product, "oc-line-photo")),
        h("div", { className: "oc-line-main" },
          h("p", { className: "oc-line-name", text: l.product.name }),
          h("div", { className: "oc-line-row" }, stepper(l.product, l.unit, true), h("span", { className: "oc-line-unit", text: unitName(l.unit, l.qty) })),
          err ? h("p", { className: "oc-err", text: err }) : null),
        h("button", { type: "button", className: "oc-icon-btn oc-remove", "aria-label": "Remove " + l.product.name + " (" + unitName(l.unit, 2) + ")", on: { click: function () { setQty(l.product.id, l.unit, 0, true); } } }, icon("x", 16))));
    });
    wrap.appendChild(list);
    wrap.appendChild(h("div", { className: "oc-cart-foot" },
      h("p", { className: "oc-help", text: "No payment now. Our team confirms prices, availability and delivery with you." }),
      h("button", { type: "button", className: "oc-btn oc-block", on: { click: function () { go("checkout"); } } }, "Continue")));
    return wrap;
  }

  function renderCart() {
    var aside = byId("oc-cart-inner");
    if (aside) { clear(aside); aside.appendChild(cartBody(false)); }
    var n = cartCount();
    var bar = byId("oc-cartbar");
    if (bar) {
      clear(bar);
      bar.hidden = !n;
      if (n) bar.appendChild(h("button", { id: "oc-cartbar-btn", type: "button", className: "oc-btn oc-block oc-cartbar-btn", "aria-haspopup": "dialog", on: { click: function () { state.sheet = true; renderCart(); var c = byId("oc-sheet-h"); if (c) { c.setAttribute("tabindex", "-1"); c.focus(); } } } },
        icon("cart", 18), "View order", h("span", { className: "oc-pill is-light", text: String(n) })));
    }
    var sheet = byId("oc-sheet");
    if (sheet) {
      clear(sheet);
      sheet.hidden = !state.sheet;
      if (state.sheet) {
        sheet.appendChild(h("div", { className: "oc-sheet-backdrop", on: { click: function () { state.sheet = false; renderCart(); } } }));
        sheet.appendChild(h("div", { className: "oc-sheet-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": "oc-sheet-h",
          on: { keydown: function (e) { if (e.key === "Escape") { state.sheet = false; renderCart(); } } } }, cartBody(true)));
      }
    }
    follow();
    reportHeight();
  }

  // ---------------------------------------------------------------- checkout

  function fieldControl(f) {
    var id = "oc-f-" + f.key;
    var err = state.errors[f.key];
    var describedBy = [f.help_text ? id + "-help" : "", err ? id + "-err" : ""].filter(Boolean).join(" ") || null;
    var v = state.values[f.key];
    var common = { id: id, name: f.key, required: f.required, "aria-invalid": err ? "true" : null, "aria-describedby": describedBy };
    function onv(e) {
      state.values[f.key] = f.kind === "CHECKBOX" ? e.target.checked : e.target.value;
      if (state.errors[f.key]) delete state.errors[f.key];
      save();
    }
    var label = h("label", { for: id, className: "oc-label" }, f.label,
      f.required ? h("span", { className: "oc-req", "aria-hidden": "true", text: " *" }) : null,
      f.required ? h("span", { className: "oc-sr", text: " (required)" }) : null);
    var help = f.help_text ? h("p", { id: id + "-help", className: "oc-help", text: f.help_text }) : null;
    var errEl = err ? h("p", { id: id + "-err", className: "oc-err", text: err }) : null;
    var ctl;
    if (f.kind === "CHECKBOX") {
      ctl = h("input", Object.assign({ type: "checkbox", className: "oc-check", checked: !!v, on: { change: onv } }, common));
      return h("div", { className: "oc-field oc-field-check oc-wide" }, ctl, label, help, errEl);
    }
    if (f.kind === "TEXTAREA") {
      ctl = h("textarea", Object.assign({ className: "oc-input", rows: f.key === "delivery_address" ? 3 : 4, maxlength: 2000, on: { input: onv } }, common));
      ctl.value = v || "";
    } else if (f.kind === "SELECT") {
      ctl = h("select", Object.assign({ className: "oc-input", on: { change: onv } }, common),
        h("option", { value: "", text: "Choose…" }),
        f.options.map(function (o) { return h("option", { value: o, selected: v === o, text: o }); }));
    } else {
      var type = { EMAIL: "email", PHONE: "tel", DATE: "date" }[f.kind] || "text";
      var auto = { customer_name: "name", business_name: "organization", email: "email", phone: "tel", delivery_address: "street-address" }[f.key] || null;
      ctl = h("input", Object.assign({ type: type, className: "oc-input", autocomplete: auto, maxlength: f.kind === "PHONE" ? 32 : 200, on: { input: onv } }, common));
      if (f.kind === "DATE") ctl.setAttribute("min", new Date().toISOString().slice(0, 10));
      ctl.value = v || "";
    }
    var wide = f.kind === "TEXTAREA" || f.kind === "CHECKBOX";
    return h("div", { className: "oc-field" + (wide ? " oc-wide" : "") }, label, help, ctl, errEl);
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
    var consent = cfg.form.consent || {};
    if (consent.required && !state.consent) errs.consent = "Please agree to continue";
    return errs;
  }

  function choiceCard(value, title, text, iconName) {
    var id = "oc-type-" + value.toLowerCase();
    var on = state.requestType === value;
    return h("label", { className: "oc-choice" + (on ? " is-on" : ""), for: id },
      h("input", { type: "radio", id: id, name: "request_type", value: value, checked: on, className: "oc-radio",
        on: { change: function () { state.requestType = value; save(); render(false); var r = byId(id); if (r) r.focus(); } } }),
      h("span", { className: "oc-choice-icon", "aria-hidden": "true" }, icon(iconName, 20)),
      h("span", { className: "oc-choice-text" }, h("strong", { text: title }), h("span", { text: text })));
  }

  function renderCheckout() {
    var quote = state.requestType === "QUOTE";
    var wrap = h("div", { className: "oc-checkout" });
    var formCol = h("section", { className: "oc-checkout-main", "aria-labelledby": "oc-co-h" });
    if (hasProducts()) {
      formCol.appendChild(h("button", { type: "button", className: "oc-back", on: { click: function () { go("shop"); } } }, icon("back", 18), "Back to products"));
    } else if (cfg.form.intro) {
      formCol.appendChild(h("p", { className: "oc-intro", text: cfg.form.intro }));
    }
    formCol.appendChild(h("h2", { id: "oc-co-h", className: "oc-h2", "data-focus": "1", text: "Send your request" }));
    if (state.notice) formCol.appendChild(h("p", { className: "oc-notice", role: "alert", text: state.notice }));

    formCol.appendChild(h("fieldset", { className: "oc-choices" },
      h("legend", { className: "oc-label", text: "What would you like?" }),
      choiceCard("ORDER", "Place an order", "We prepare it and contact you to confirm.", "order"),
      choiceCard("QUOTE", "Request a quote", "We send you prices first. Nothing is ordered yet.", "quote")));

    var form = h("form", { className: "oc-form", novalidate: true, on: { submit: function (e) { e.preventDefault(); send(); } } });
    var fieldsBox = h("div", { className: "oc-fields" });
    cfg.fields.forEach(function (f) { if (f.kind !== "PRODUCTS") fieldsBox.appendChild(fieldControl(f)); });
    form.appendChild(h("h3", { className: "oc-h3", text: "Your details" }));
    form.appendChild(h("p", { className: "oc-help", text: "Fields marked * are required." }));
    form.appendChild(fieldsBox);

    var consent = cfg.form.consent || {};
    if (consent.required) {
      var cerr = state.errors.consent;
      form.appendChild(h("div", { className: "oc-field oc-field-check" },
        h("input", { type: "checkbox", id: "oc-consent", className: "oc-check", checked: state.consent, "aria-invalid": cerr ? "true" : null, "aria-describedby": cerr ? "oc-consent-err" : null,
          on: { change: function (e) { state.consent = e.target.checked; delete state.errors.consent; } } }),
        h("label", { for: "oc-consent", text: consent.text }),
        cerr ? h("p", { id: "oc-consent-err", className: "oc-err", text: cerr }) : null));
    }
    // Honeypot: hidden from people and screen readers; bots that fill every field reveal themselves.
    form.appendChild(h("div", { className: "oc-trap", "aria-hidden": "true" },
      h("label", { for: "oc-website", text: "Website" }),
      h("input", { id: "oc-website", name: "website", type: "text", tabindex: "-1", autocomplete: "off" })));
    form.appendChild(h("p", { className: "oc-help", text: quote ? "This asks for prices only. We reply with a quote; nothing is ordered until you confirm." : "This is a request, not a purchase. No payment now: we confirm your order with you." }));
    form.appendChild(h("button", { type: "submit", className: "oc-btn oc-send", disabled: state.sending || mode === "preview" },
      state.sending ? "Sending…" : quote ? "Send quote request" : "Send order request"));
    form.appendChild(h("p", { className: "oc-sr", role: "status", "aria-live": "polite", text: state.sending ? "Sending your request" : "" }));
    formCol.appendChild(form);
    wrap.appendChild(formCol);

    if (hasProducts()) {
      var ls = lines();
      var sum = h("aside", { className: "oc-summary", "aria-label": "Order summary" },
        h("div", { className: "oc-cart-head" },
          h("h3", { className: "oc-cart-title" }, icon("cart", 20), quote ? "Products to quote" : "Your order"),
          h("button", { type: "button", className: "oc-link", on: { click: function () { go("shop"); } } }, "Edit")));
      var list = h("ul", { className: "oc-cart-lines" });
      ls.forEach(function (l) {
        var err = state.itemErrors[l.key];
        list.appendChild(h("li", { className: "oc-line" },
          h("div", { className: "oc-line-tile" }, photo(l.product, "oc-line-photo")),
          h("div", { className: "oc-line-main" },
            h("p", { className: "oc-line-name", text: l.product.name }),
            h("p", { className: "oc-line-unit", text: l.qty + " " + unitName(l.unit, l.qty) }),
            err ? h("p", { className: "oc-err", text: err }) : null)));
      });
      if (!ls.length) list.appendChild(h("li", { className: "oc-help", text: "No products yet." }));
      sum.appendChild(list);
      wrap.appendChild(sum);
    }
    root.appendChild(wrap);
  }

  function send() {
    if (state.sending || mode !== "live") return;
    var pf = productsField();
    if (pf && pf.required && !cartCount()) { state.notice = "Add at least one product first."; go("shop"); return; }
    state.errors = checkDetails();
    if (Object.keys(state.errors).length) {
      state.notice = "Some details need attention.";
      render(false);
      var first = root.querySelector("[aria-invalid='true']");
      if (first) first.focus();
      return;
    }
    var values = {};
    cfg.fields.forEach(function (f) {
      if (f.kind === "PRODUCTS") return;
      var v = state.values[f.key];
      if (f.kind === "CHECKBOX") values[f.key] = !!v;
      else if (v !== undefined && v !== null && String(v).trim() !== "") values[f.key] = String(v);
    });
    var trap = byId("oc-website");
    var payload = {
      form_token: cfg.token,
      client_token: state.token,
      values: values,
      items: lines().map(function (l) { return { product_id: l.product.id, unit_code: l.unit, qty: l.qty }; }),
      consent: !!state.consent,
      request_type: state.requestType,
      website: trap ? trap.value : "",
    };
    state.sending = true;
    state.itemErrors = {};
    state.notice = "";
    render(false);
    fetch("/api/v1/public/order-forms/" + encodeURIComponent(publicId) + "/submissions", {
      method: "POST", credentials: "omit",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }, function () { return { status: r.status, body: null }; }); })
      .then(function (res) {
        state.sending = false;
        var err = (res.body && res.body.error) || {};
        if (res.status === 200 || res.status === 201) {
          state.done = { reference: res.body.reference, message: res.body.message, quote: state.requestType === "QUOTE" };
          forget();
          go("done");
          return;
        }
        if (res.status === 422 && err.fields) return showFieldErrors(err.fields, err.message);
        if (res.status === 409 && (err.code === "FORM_UPDATED" || err.code === "FORM_EXPIRED")) {
          state.notice = err.message;
          save();
          return load(true);
        }
        state.notice = err.message || (res.status === 429 ? "Too many requests right now. Please try again later." : "We couldn't send your request. Please try again.");
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
      if (m) { var l = ls[Number(m[1]) - 1]; if (l) state.itemErrors[l.key] = fields[k]; products = true; }
      else if (k === "products") products = true;
      else detailErr[k] = fields[k];
    });
    state.errors = detailErr;
    state.notice = message || "Please check the highlighted fields.";
    if (products && !Object.keys(detailErr).length && hasProducts()) { state.view = "shop"; state.sheet = false; }
    render(true);
    toTop();
  }

  function renderDone() {
    var d = state.done;
    root.appendChild(h("section", { className: "oc-done", role: "status" },
      h("span", { className: "oc-done-icon", "aria-hidden": "true" }, icon("check", 30)),
      h("h2", { className: "oc-h2", "data-focus": "1", text: d.quote ? "Quote request sent" : "Order request sent" }),
      h("p", { text: d.message }),
      h("p", { className: "oc-ref" }, "Your reference: ", h("strong", { text: d.reference })),
      h("button", { type: "button", className: "oc-btn oc-btn-ghost", on: { click: function () { state = freshState(); render(true); toTop(); } } }, "Start a new order")));
  }

  // ---------------------------------------------------------------- boot

  if (mode === "preview") {
    showState("loading", "", "Waiting for the form builder…");
    window.addEventListener("message", function (e) {
      if (e.origin !== staffOrigin || e.source !== window.parent) return;
      var m = e.data;
      if (!m || m.type !== "oc-preview" || !m.config || !m.config.form) return;
      if (m.step === "checkout" || m.step === "details" || m.step === "review") state.view = "checkout";
      else if (m.step === "shop" || m.step === "products") state.view = "shop";
      start(m.config, false);
    });
    if (window.parent !== window && staffOrigin) window.parent.postMessage({ type: "oc-preview-ready" }, staffOrigin);
  } else {
    load(false);
  }
})();
