"use strict";

// Sends a visitor to the page in their language before anything is drawn: / is Russian, /en/ English.
// Loaded in <head> of both pages without defer, ahead of the stylesheet. A choice made with the
// language link in the header is remembered in localStorage (tgsweep-lang); there are no cookies.
// After the switch the browser names this site as the referrer, so the real one is handed to the new
// page in sessionStorage (tgsweep-ref) and from there to GoatCounter's own referrer setting. The
// address stays clean: a link copied after the switch carries no source of its own.
(function () {
  const KEY = "tgsweep-lang";
  const REF_KEY = "tgsweep-ref";
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

  // The referrer as the browser gave it, whole: GoatCounter then reads it as an ordinary link
  // (names like Hacker News included). An empty string is kept too, so the next page does not
  // count this site as the source.
  function keepReferrer(value) {
    try { sessionStorage.setItem(REF_KEY, value); } catch (e) { /* storage blocked: no source, still switch */ }
  }

  // Read once: a reload or a later page of the same tab must not count the old source again.
  function takeReferrer() {
    try {
      const value = sessionStorage.getItem(REF_KEY);
      sessionStorage.removeItem(REF_KEY);
      return value;
    } catch (e) {
      return null;
    }
  }

  // The body does not exist yet, so the listener goes on the document.
  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element ? event.target.closest("nav a[hreflang]") : null;
    if (link && known(link.getAttribute("hreflang"))) save(link.getAttribute("hreflang"));
  });

  const here = document.documentElement.lang;
  const wanted = saved() || preferred();
  if (known(here) && wanted !== here && !fromThisSite() && !ROBOTS.test(navigator.userAgent || "")) {
    keepReferrer(document.referrer); // not this site here: a visitor from inside the site is never switched
    // replace, not assign: Back must not lead to the page the visitor was sent away from.
    location.replace(PAGES[wanted] + location.search + location.hash);
    return;
  }

  // Set before count.js loads (it is at the end of <body>), as https://www.goatcounter.com/help/js asks.
  const kept = takeReferrer();
  if (kept !== null) window.goatcounter = Object.assign(window.goatcounter || {}, { referrer: kept });
})();
