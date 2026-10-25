/* dealbar – kleines Skript fuer Website und Deal-Seiten (keine Cookies, kein Tracking).
   Amazon-Regel: Rabatt und Preis erscheinen nur, solange die Pruefung juenger als MAX_H Stunden ist. */
(function () {
  "use strict";
  var cfg = window.DEALBAR || {};
  var H = (cfg.maxh || 24) * 36e5, now = Date.now();
  function $(s, el) { return (el || document).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); }
  function ago(t) {
    var m = Math.round((now - t) / 6e4);
    if (m < 2) return "gerade eben";
    if (m < 60) return "vor " + m + " Min.";
    var h = Math.round(m / 60);
    if (h < 24) return "vor " + h + " Std.";
    var d = Math.round(h / 24);
    return d === 1 ? "gestern" : "vor " + d + " Tagen";
  }
  function stamp(t) {
    var d = new Date(t);
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) + ", " +
      d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) + " Uhr";
  }

  var cards = $$("[data-deal]");
  cards.forEach(function (c) {
    var checked = Date.parse(c.getAttribute("data-checked") || "") || 0;
    var date = Date.parse(c.getAttribute("data-date") || "") || checked;
    var fresh = checked && now - checked < H && checked - now < 36e5;
    c._off = fresh ? +(c.getAttribute("data-off") || 0) : 0;
    c._date = date;
    var badge = $(".off", c), price = $(".price", c), st = $("[data-stamp]", c), a = $("[data-ago]", c), n = $(".new", c);
    if (badge && c._off) badge.hidden = false;
    if (price && fresh) price.hidden = false;
    if (st) {
      st.textContent = fresh && (c._off || price) ? (price ? "Preis" : "Rabatt") + " vom " + stamp(checked) +
        ", kann sich jederzeit ändern." : "Aktuellen Preis bei Amazon ansehen.";
    }
    if (a && date) a.textContent = ago(date);
    if (n && date && now - date < 864e5) n.hidden = false;
  });

  var live = $("#live");
  if (live && cards.length) {
    var newest = Math.max.apply(null, cards.map(function (c) { return c._date || 0; }));
    var count = $$("[data-list] [data-deal]").length;
    live.textContent = count + (count === 1 ? " Deal" : " Deals") + " online" + (newest ? " · neuester " + ago(newest) : "");
  }

  /* Filtern, Suchen, Sortieren (nur Startseite) */
  var lists = $$("[data-list]");
  var state = { cat: "", q: "", sort: "new" };
  var q = $("#q"), sort = $("#sort"), chips = $("#chips"), empty = $("#empty"), older = $("#older");
  function matches(c) {
    if (state.cat && c.getAttribute("data-cat") !== state.cat) return false;
    if (!state.q) return true;
    var n = state.q.replace(/^#/, "");
    if (/^\d+$/.test(n) && $$("[data-nr='" + n + "']").length) return c.getAttribute("data-nr") === n;
    return (c.getAttribute("data-q") || "").indexOf(state.q) >= 0;
  }
  function apply() {
    var shown = 0, shownOlder = 0;
    lists.forEach(function (g) {
      var items = $$("[data-deal]", g);
      items.sort(state.sort === "off" ? function (a, b) { return b._off - a._off || b._date - a._date; }
        : function (a, b) { return b._date - a._date; });
      items.forEach(function (c) {
        var ok = matches(c);
        c.hidden = !ok;
        c.classList.remove("lead");
        g.appendChild(c);
        if (ok) { if (g.getAttribute("data-list") === "main") shown++; else shownOlder++; }
      });
    });
    var first = $("[data-list=main] [data-deal]:not([hidden])");
    if (first) first.classList.add("lead");
    $$("img[data-sizes]").forEach(function (im) {  /* grosse Karte -> groesseres Bild laden */
      var s = im.closest(".lead") ? im.getAttribute("data-lead-sizes") : im.getAttribute("data-sizes");
      if (im.getAttribute("sizes") !== s) im.setAttribute("sizes", s);
    });
    if (older) {
      older.hidden = !shownOlder;
      if ((state.q || state.cat) && shownOlder && !shown) older.open = true;
    }
    if (empty) empty.hidden = shown + shownOlder > 0;
  }
  if (lists.length) {
    if (chips) chips.addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      state.cat = b.getAttribute("data-cat") || "";
      $$(".chip", chips).forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      apply();
    });
    if (q) {
      q.addEventListener("input", function () { state.q = q.value.trim().toLowerCase(); apply(); });
      q.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); q.blur(); } });
      var p = new URLSearchParams(location.search).get("q");
      if (p) { q.value = p; state.q = p.trim().toLowerCase(); }
    }
    if (sort) sort.addEventListener("change", function () { state.sort = sort.value; apply(); });
    var reset = $("#reset");
    if (reset) reset.addEventListener("click", function () {
      state.cat = ""; state.q = ""; if (q) q.value = "";
      $$(".chip", chips || document).forEach(function (x) { x.setAttribute("aria-pressed", x.getAttribute("data-cat") ? "false" : "true"); });
      apply();
    });
    apply();
  }

  /* Direktlinks: #12, #nr-12 oder #ordnername */
  function jump() {
    var h = decodeURIComponent(location.hash.slice(1));
    if (!h) return;
    var el = document.getElementById(h) || $("[data-nr='" + h.replace(/^nr-/, "") + "']");
    if (!el || !el.hasAttribute("data-deal")) return;
    var d = el.closest("details");
    if (d) d.open = true;
    el.hidden = false;
    el.scrollIntoView({ block: "center" });
    el.classList.add("hl");
  }
  jump();
  window.addEventListener("hashchange", jump);

  /* Teilen */
  var toast;
  function say(t) {
    if (!toast) { toast = document.createElement("div"); toast.className = "toast"; toast.setAttribute("role", "status"); document.body.appendChild(toast); }
    toast.textContent = t; toast.classList.add("on");
    clearTimeout(say.t); say.t = setTimeout(function () { toast.classList.remove("on"); }, 1800);
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-share]");
    if (!b) return;
    e.preventDefault();
    var url = new URL(b.getAttribute("data-share"), location.href).href;
    var title = b.getAttribute("data-title") || document.title;
    if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () {}); return; }
    if (navigator.clipboard) { navigator.clipboard.writeText(url).then(function () { say("Link kopiert"); }, function () { window.prompt("Link kopieren:", url); }); return; }
    window.prompt("Link kopieren:", url);
  });
})();
