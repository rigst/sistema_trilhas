/* Deck de cards ("stories"): barra segmentada, swipe, teclado e modo de
   exibição (cards ↔ lista corrida). Usado pelo leitor de tópico e pela revisão.

   TrilhasDeck(root, { posKey, modeKey, onShow }) — root contém .story-stage,
   .story-bars, .story-count, .story-prev/.story-next e os .story-card. */
window.TrilhasDeck = function (root, opts) {
  opts = opts || {};
  const stage = root.querySelector(".story-stage");
  const bars = root.querySelector(".story-bars");
  const count = root.querySelector(".story-count");
  const prevBtn = root.querySelector(".story-prev");
  const nextBtn = root.querySelector(".story-next");
  const list = Array.from(root.querySelectorAll(".story-card"));
  if (!stage || !bars || !list.length) return null;
  let idx = 0;

  list.forEach((_, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("aria-label", "Ir para o card " + (i + 1));
    b.addEventListener("click", () => show(i, i < idx ? "back" : "fwd"));
    bars.appendChild(b);
  });
  const segs = Array.from(bars.children);
  const movel = window.matchMedia("(max-width: 820px)");
  const semMovimento = window.matchMedia("(prefers-reduced-motion: reduce)");
  let booted = false;

  function show(i, dir) {
    idx = Math.max(0, Math.min(list.length - 1, i));
    list.forEach((c, j) => c.classList.toggle("on", j === idx));
    segs.forEach((b, j) => {
      b.classList.toggle("done", j < idx);
      b.classList.toggle("cur", j === idx);
    });
    if (count) count.textContent = (idx + 1) + " / " + list.length;
    stage.dataset.dir = dir || "fwd";
    list[idx].scrollTop = 0;
    // No mobile o card não rola por dentro: leva a página ao topo do card.
    if (booted && movel.matches) {
      const alvo = stage.getBoundingClientRect().top + window.scrollY - 116;
      window.scrollTo({ top: Math.max(alvo, 0), behavior: semMovimento.matches ? "auto" : "smooth" });
    }
    booted = true;
    if (prevBtn) prevBtn.disabled = idx === 0;
    if (nextBtn) nextBtn.disabled = idx === list.length - 1;
    if (opts.posKey) try { sessionStorage.setItem(opts.posKey, idx); } catch (e) {}
    if (opts.onShow) opts.onShow(idx, list[idx]);
  }
  const emCards = () => root.classList.contains("mode-cards");
  const next = () => { if (emCards()) show(idx + 1, "fwd"); };
  const prev = () => { if (emCards()) show(idx - 1, "back"); };

  if (nextBtn) nextBtn.addEventListener("click", next);
  if (prevBtn) prevBtn.addEventListener("click", prev);
  const tapL = stage.querySelector(".story-tap--l");
  const tapR = stage.querySelector(".story-tap--r");
  if (tapL) tapL.addEventListener("click", prev);
  if (tapR) tapR.addEventListener("click", next);
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input,textarea,select")) return;
    if (e.key === "ArrowRight") next();
    else if (e.key === "ArrowLeft") prev();
  });
  let tx = 0, ty = 0;
  stage.addEventListener("touchstart", (e) => {
    tx = e.touches[0].clientX; ty = e.touches[0].clientY;
  }, { passive: true });
  stage.addEventListener("touchend", (e) => {
    if (!emCards()) return;
    const dx = e.changedTouches[0].clientX - tx;
    const dy = e.changedTouches[0].clientY - ty;
    if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.4) (dx < 0 ? next : prev)();
  }, { passive: true });

  // Modo cards ↔ lista/artigo, persistido por tipo de página.
  const toggles = Array.from(root.querySelectorAll(".mode-toggle button"));
  function setMode(m, persist) {
    root.classList.toggle("mode-cards", m === "cards");
    root.classList.toggle("mode-artigo", m !== "cards");
    toggles.forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === m ? "true" : "false"));
    if (persist && opts.modeKey) try { localStorage.setItem(opts.modeKey, m); } catch (e) {}
  }
  toggles.forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode, true)));
  let mode = "cards";
  if (opts.modeKey) try { mode = localStorage.getItem(opts.modeKey) || "cards"; } catch (e) {}
  setMode(mode, false);

  let pos0 = 0;
  if (opts.posKey) try { pos0 = parseInt(sessionStorage.getItem(opts.posKey), 10) || 0; } catch (e) {}
  show(Math.min(pos0, list.length - 1));
  return { show, next, prev, index: () => idx, cards: list };
};
