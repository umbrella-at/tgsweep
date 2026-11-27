"use strict";

(function () {
  const root = document.documentElement;
  const live = document.getElementById("live");
  const say = (text) => { if (live) { live.textContent = ""; live.textContent = text; } };

  // The page's own words for the demo and the copy buttons: a JSON block, so one script serves every language.
  const source = document.getElementById("page-text");
  const T = source ? JSON.parse(source.textContent) : {};
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (all, key) => (key in values ? String(values[key]) : all));

  // Which computer the steps are for: the visitor's own by default, remembered once chosen.
  const OS_KEY = "tgsweep-os";
  function guessOs() {
    const ua = navigator.userAgent || "";
    if (/iPhone|iPad|Android/i.test(ua)) return "win";
    if (/Mac/i.test(ua)) return "mac";
    if (/Linux|X11|CrOS/i.test(ua)) return "linux";
    return "win";
  }
  function savedOs() {
    try { return localStorage.getItem(OS_KEY); } catch (e) { return null; }
  }
  function setOs(os, remember) {
    root.dataset.os = os;
    document.querySelectorAll('#os-switch input[name="os"]').forEach((input) => { input.checked = input.value === os; });
    if (remember) { try { localStorage.setItem(OS_KEY, os); } catch (e) { /* private mode */ } }
  }
  const initial = savedOs();
  setOs(["win", "mac", "linux"].includes(initial) ? initial : guessOs(), false);
  document.querySelectorAll('#os-switch input[name="os"]').forEach((input) => {
    input.addEventListener("change", () => setOs(input.value, true));
  });

  // Copy buttons next to commands.
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) throw new Error("copy refused");
  }
  document.querySelectorAll(".cmd .copy").forEach((button) => {
    const label = button.textContent;
    button.addEventListener("click", async () => {
      const code = button.parentElement.querySelector("code");
      try {
        await copyText(code.textContent);
        button.textContent = T.copied;
        button.classList.add("done");
        say(T.copied_say);
      } catch (e) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        button.textContent = T.selected;
        say(T.copy_failed_say);
      }
      clearTimeout(button._t);
      button._t = setTimeout(() => { button.textContent = label; button.classList.remove("done"); }, 2200);
    });
  });

  // The hero: the program's chat picker, with the program's own wording and plurals.
  const list = document.getElementById("demo-list");
  if (!list) return;
  const instr = document.getElementById("demo-instr");
  const after = document.getElementById("demo-after");
  const hint = document.getElementById("demo-hint");
  const reset = document.getElementById("demo-reset");
  const options = Array.from(list.querySelectorAll(".opt"));
  const instruction = instr.textContent;
  const hintText = hint.textContent;
  let pointer = 0;
  let answered = false;

  // Word forms after a number, as in the program: ru one, few, many; en one, other.
  function plural(n, forms) {
    let word;
    if (root.lang === "en") word = n === 1 ? forms[0] : forms[1];
    else if (n % 10 === 1 && n % 100 !== 11) word = forms[0];
    else if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) word = forms[1];
    else word = forms[2];
    return n + " " + word;
  }
  function counts(messages, reactions) {
    const parts = [];
    if (messages) parts.push(plural(messages, T.message));
    if (reactions) parts.push(plural(reactions, T.reaction));
    return parts.join(", ") || T.nothing;
  }
  const PAUSE_S = 4;
  const CHUNK = 100;
  function minutes(chosen) {
    const requests = chosen.reduce((sum, o) => sum + Math.ceil(o.messages / CHUNK) + o.reactions, 0);
    return Math.max(1, Math.ceil(requests * PAUSE_S / 60));
  }
  const data = options.map((el) => ({
    el,
    label: el.querySelector(".lbl").textContent.slice(2),
    messages: Number(el.dataset.messages),
    reactions: Number(el.dataset.reactions),
  }));
  function isOn(o) { return o.el.getAttribute("aria-selected") === "true"; }

  function draw() {
    data.forEach((o, i) => {
      o.el.querySelector(".ptr").textContent = i === pointer ? "»" : " ";
      const lbl = o.el.querySelector(".lbl");
      lbl.textContent = (isOn(o) ? "● " : "○ ") + lbl.textContent.slice(2);
    });
    list.setAttribute("aria-activedescendant", data[pointer].el.id);
  }
  function toggle(i) {
    const o = data[i];
    o.el.setAttribute("aria-selected", isOn(o) ? "false" : "true");
  }
  function toggleAll() {
    const all = data.every(isOn);
    data.forEach((o) => o.el.setAttribute("aria-selected", all ? "false" : "true"));
  }
  function esc(s) {
    return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function answer() {
    const chosen = data.filter(isOn);
    answered = true;
    // What the program shows after the answer: nothing, [the one chat], or how many.
    let done = T.picked_nothing;
    if (chosen.length === 1) done = "[" + chosen[0].label + "]";
    else if (chosen.length > 1) done = fill(T.picked_many, { count: chosen.length });
    instr.innerHTML = '<span class="ans">' + esc(done) + "</span>";
    list.hidden = true;
    if (!chosen.length) {
      after.textContent = T.none_picked;
      hint.textContent = T.none_picked_hint;
    } else {
      const m = chosen.reduce((s, o) => s + o.messages, 0);
      const r = chosen.reduce((s, o) => s + o.reactions, 0);
      const plan = fill(T.will_delete, {
        counts: counts(m, r),
        chats: plural(chosen.length, T.chat_in),
        minutes: plural(minutes(chosen), T.minute),
      });
      after.innerHTML =
        esc(plan) + "\n" +
        '<span class="q">?</span> <span class="b">' + esc(fill(T.confirm_number, { total: m + r })) + "</span> " +
        '<span class="cur demo-blink" aria-hidden="true"></span>';
      hint.textContent = T.demo_end_hint;
    }
    after.hidden = false;
    reset.hidden = false;
    say(after.textContent);
  }
  function restart() {
    answered = false;
    pointer = 0;
    data.forEach((o) => o.el.setAttribute("aria-selected", "false"));
    instr.textContent = instruction;
    after.hidden = true;
    after.innerHTML = "";
    list.hidden = false;
    reset.hidden = true;
    hint.textContent = hintText;
    draw();
    list.focus();
  }

  list.addEventListener("keydown", (e) => {
    if (answered) return;
    const key = e.key;  // the program also takes the Russian letters on the same keys: о, л, ф
    if (key === "ArrowDown" || key === "j" || key === "о") { pointer = (pointer + 1) % data.length; }
    else if (key === "ArrowUp" || key === "k" || key === "л") { pointer = (pointer - 1 + data.length) % data.length; }
    else if (key === " " || key === "Spacebar") { toggle(pointer); }
    else if (key === "a" || key === "ф") { toggleAll(); }
    else if (key === "Enter") { e.preventDefault(); answer(); return; }
    else return;
    e.preventDefault();
    draw();
  });
  data.forEach((o, i) => {
    o.el.addEventListener("click", () => {
      if (answered) return;
      pointer = i;
      toggle(i);
      draw();
    });
  });
  reset.addEventListener("click", restart);
  draw();
})();
