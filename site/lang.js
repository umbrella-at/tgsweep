"use strict";

// Sends a visitor to the page in their language before anything is drawn: / is Russian, /en/ English.
// Loaded in <head> of both pages without defer, ahead of the stylesheet. A choice made with the
// language link in the header is remembered in localStorage (tgsweep-lang); there are no cookies.
// After the switch the browser names this site as the referrer, so the real source of the visit
// travels to GoatCounter as ?ref=<host>, the way its campaigns read it.
(function () {
  const KEY = "tgsweep-lang";
  const PAGES = { ru: "/", en: "/en/" };
  const known = (lang) => lang === "ru" || lang === "en";
  // No "telegram" here: the Telegram in-app browser on Android says Telegram-Android, and it is a
  // person reading. Telegram's link preview says TelegramBot, which "bot" already covers.
  const ROBOTS = /bot|crawl|spider|slurp|facebookexternalhit/i;
  // The query parameters GoatCounter takes the source from; one already in the address wins.
  const SOURCES = ["utm_source", "ref", "src", "source"];

  function saved() {
    try {
      const lang = localStorage.getItem(KEY);
      return known(lang) ? lang : null;
    } catch (e) {
      return null; // storage blocked: no memory, no error
    }
  }

  function save(lang) {
    try { localStorage.setItem(KEY, lang); } catch (e) { /* storage blocked */ }
  }

  function preferred() {
    const list = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
    for (const tag of list) {
      const code = String(tag || "").toLowerCase().split("-")[0];
      if (known(code)) return code;
    }
    return "en";
  }

  function referrer() {
    try { return document.referrer ? new URL(document.referrer) : null; } catch (e) { return null; }
  }

  // The same query and hash, plus ref=<host> of an outside referrer unless the address names a source.
  // Only the host: a shorter address, and nothing more about the visitor than where they came from.
  function query(from) {
    const search = location.search;
    const params = new URLSearchParams(search);
    if (!from || !from.hostname || SOURCES.some((name) => params.has(name))) return search;
    return search + (search ? "&" : "?") + "ref=" + encodeURIComponent(from.hostname);
  }

  // The body does not exist yet, so the listener goes on the document.
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("nav a[hreflang]") : null;
    if (link && known(link.getAttribute("hreflang"))) save(link.getAttribute("hreflang"));
  });

  const here = document.documentElement.lang;
  const wanted = saved() || preferred();
  const from = referrer();
  const fromThisSite = from !== null && from.origin === location.origin;
  if (!known(here) || wanted === here || fromThisSite || ROBOTS.test(navigator.userAgent || "")) return;
  // replace, not assign: Back must not lead to the page the visitor was sent away from.
  location.replace(PAGES[wanted] + query(from) + location.hash);
})();
