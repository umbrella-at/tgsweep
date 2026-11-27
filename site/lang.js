"use strict";

// Sends a visitor to the page in their language before anything is drawn: / is Russian, /en/ English.
// Loaded in <head> of both pages without defer, ahead of the stylesheet. A choice made with the
// language link in the header is remembered in localStorage (tgsweep-lang); there are no cookies.
(function () {
  const KEY = "tgsweep-lang";
  const PAGES = { ru: "/", en: "/en/" };
  const known = (lang) => lang === "ru" || lang === "en";
  // No "telegram" here: the Telegram in-app browser on Android says Telegram-Android, and it is a
  // person reading. Telegram's link preview says TelegramBot, which "bot" already covers.
  const ROBOTS = /bot|crawl|spider|slurp|facebookexternalhit/i;

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

  function fromThisSite() {
    try { return document.referrer !== "" && new URL(document.referrer).origin === location.origin; } catch (e) { return false; }
  }

  // The body does not exist yet, so the listener goes on the document.
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("nav a[hreflang]") : null;
    if (link && known(link.getAttribute("hreflang"))) save(link.getAttribute("hreflang"));
  });

  const here = document.documentElement.lang;
  const wanted = saved() || preferred();
  if (!known(here) || wanted === here || fromThisSite() || ROBOTS.test(navigator.userAgent || "")) return;
  // replace, not assign: Back must not lead to the page the visitor was sent away from.
  location.replace(PAGES[wanted] + location.search + location.hash);
})();
