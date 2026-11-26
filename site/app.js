"use strict";

(function () {
  const root = document.documentElement;
  const live = document.getElementById("live");
  const say = (text) => { if (live) { live.textContent = ""; live.textContent = text; } };

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
    button.addEventListener("click", async () => {
      const code = button.parentElement.querySelector("code");
      try {
        await copyText(code.textContent);
        button.textContent = "Скопировано";
        button.classList.add("done");
        say("Команда скопирована");
      } catch (e) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        button.textContent = "Выделено";
        say("Скопировать не вышло, команда выделена: нажмите Ctrl+C или Cmd+C");
      }
      clearTimeout(button._t);
      button._t = setTimeout(() => { button.textContent = "Копировать"; button.classList.remove("done"); }, 2200);
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

  function plural(n, one, few, many) {
    let word = many;
    if (n % 10 === 1 && n % 100 !== 11) word = one;
    else if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) word = few;
    return n + " " + word;
  }
  function counts(messages, reactions) {
    const parts = [];
    if (messages) parts.push(plural(messages, "сообщение", "сообщения", "сообщений"));
    if (reactions) parts.push(plural(reactions, "реакция", "реакции", "реакций"));
    return parts.join(", ") || "ничего";
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
    let done = "done";
    if (chosen.length === 1) done = "[" + chosen[0].label + "]";
    else if (chosen.length > 1) done = "done (" + chosen.length + " selections)";
    instr.innerHTML = '<span class="ans">' + esc(done) + "</span>";
    list.hidden = true;
    if (!chosen.length) {
      after.innerHTML = "Ни один чат не отмечен, ничего не удалено.";
      hint.textContent = "Так программа и поступит: без отмеченных чатов она ничего не трогает.";
    } else {
      const m = chosen.reduce((s, o) => s + o.messages, 0);
      const r = chosen.reduce((s, o) => s + o.reactions, 0);
      after.innerHTML =
        "Будет удалено: " + esc(counts(m, r)) + " в " + plural(chosen.length, "чате", "чатах", "чатах") +
        ". Вернуть их будет нельзя. Это займёт примерно " + plural(minutes(chosen), "минуту", "минуты", "минут") + ".\n" +
        '<span class="q">?</span> <span class="b">Чтобы подтвердить, введите число ' + (m + r) + ":</span> " +
        '<span class="cur demo-blink" aria-hidden="true"></span>';
      hint.textContent = "Дальше в настоящей программе вы бы ввели это число, и она начала бы удалять. Здесь демонстрация заканчивается.";
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
