/* Chat de dúvidas: painel flutuante que conversa com a IA sobre a página aberta.
   A resposta é gerada numa task Celery, então o painel acompanha por polling —
   diferente do partials/poll.html, que recarrega a página inteira: aqui o DOM é
   remendado no lugar, senão a conversa se perderia a cada resposta. */
(() => {
  const painel = document.getElementById("chat-painel");
  const abrir = document.getElementById("chat-abrir");
  if (!painel || !abrir) return;

  const fluxo = document.getElementById("chat-fluxo");
  const vazio = document.getElementById("chat-vazio");
  const form = document.getElementById("chat-form");
  const campo = document.getElementById("chat-pergunta");
  const botao = document.getElementById("chat-enviar");
  const rodape = document.getElementById("chat-rodape");
  const contexto = document.getElementById("chat-contexto");
  const csrf = painel.querySelector("[name=csrfmiddlewaretoken]");

  const URLS = painel.dataset;
  // O subtópico da página vem do leitor; fora dele, a conversa é a geral.
  const SUB = document.body.dataset.subtopico || "";
  const INTERVALO_MS = 1200;
  let ocupado = false;
  let carregado = false;

  const url = (base) => (SUB ? `${base}?subtopico=${encodeURIComponent(SUB)}` : base);
  const urlStatus = (id) => URLS.status.replace(/\/0\/status\/$/, `/${id}/status/`);

  const aoFim = () => { fluxo.scrollTop = fluxo.scrollHeight; };

  const aviso = (texto, alerta) => {
    rodape.textContent = texto || "";
    rodape.classList.toggle("alerta", !!alerta);
  };

  /* ---- bolhas ---------------------------------------------------------- */

  const bolha = (dados) => {
    const el = document.createElement("div");
    el.className = `chat-bolha chat-bolha--${dados.papel === "aluno" ? "aluno" : "ia"}`;
    el.dataset.id = dados.id;
    pintar(el, dados);
    fluxo.appendChild(el);
    if (vazio) vazio.hidden = true;
    aoFim();
    return el;
  };

  const pintar = (el, dados) => {
    el.classList.toggle("chat-bolha--erro", dados.status === "erro");
    if (dados.status === "erro") {
      el.textContent = dados.erro || "Não consegui responder agora.";
      return;
    }
    if (dados.status === "gerando") {
      // Texto ainda chegando: entra como texto puro (um bloco de código pela
      // metade não dá para renderizar), com os três pontinhos no fim.
      el.textContent = dados.parcial || "";
      const pontos = document.createElement("span");
      pontos.className = "chat-digitando";
      pontos.innerHTML = "<i></i><i></i><i></i>";
      el.appendChild(pontos);
      return;
    }
    // Pronta: o HTML já veio sanitizado pelo nh3 do lado do servidor.
    el.innerHTML = dados.html || "";
    el.classList.add("markdown-body");
    if (window.__mermaidRefresh) window.__mermaidRefresh(el);
    if (window.__armarCopiar) window.__armarCopiar(el);
  };

  /* ---- polling --------------------------------------------------------- */

  const acompanhar = (el, id) => {
    const tick = async () => {
      try {
        const r = await fetch(urlStatus(id), { headers: { "X-Requested-With": "XMLHttpRequest" } });
        const d = await r.json();
        pintar(el, d);
        aoFim();
        if (d.status !== "gerando") { terminar(); return; }
      } catch (e) { /* rede instável: tenta de novo no próximo tick */ }
      setTimeout(tick, INTERVALO_MS);
    };
    setTimeout(tick, INTERVALO_MS);
  };

  const terminar = () => {
    ocupado = false;
    botao.disabled = false;
    aviso("");
  };

  /* ---- envio ----------------------------------------------------------- */

  const enviar = async (pergunta) => {
    ocupado = true;
    botao.disabled = true;
    aviso("Pensando…");
    const corpo = new URLSearchParams();
    corpo.set("pergunta", pergunta);
    if (SUB) corpo.set("subtopico", SUB);
    try {
      const r = await fetch(URLS.enviar, {
        method: "POST",
        headers: { "X-CSRFToken": csrf.value, "X-Requested-With": "XMLHttpRequest" },
        body: corpo,
      });
      const d = await r.json();
      if (!r.ok) { aviso(d.erro || "Não deu para enviar agora.", true); ocupado = false; botao.disabled = false; return; }
      bolha(d.pergunta);
      acompanhar(bolha(d.resposta), d.resposta.id);
    } catch (e) {
      aviso("Sem conexão com o servidor.", true);
      ocupado = false;
      botao.disabled = false;
    }
  };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const pergunta = campo.value.trim();
    if (!pergunta || ocupado) return;
    campo.value = "";
    ajustarAltura();
    enviar(pergunta);
  });

  // Enter envia, Shift+Enter quebra linha (o esperado num chat).
  campo.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  const ajustarAltura = () => {
    campo.style.height = "auto";
    campo.style.height = `${Math.min(campo.scrollHeight, 112)}px`;
  };
  campo.addEventListener("input", ajustarAltura);

  /* ---- histórico ------------------------------------------------------- */

  const carregar = async () => {
    if (carregado) return;
    carregado = true;
    try {
      const r = await fetch(url(URLS.historico), { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const d = await r.json();
      d.mensagens.forEach((m) => {
        const el = bolha(m);
        if (m.status === "gerando") { ocupado = true; botao.disabled = true; acompanhar(el, m.id); }
      });
    } catch (e) { /* sem histórico é um começo válido de conversa */ }
  };

  document.getElementById("chat-limpar").addEventListener("click", async () => {
    if (!fluxo.querySelector(".chat-bolha")) return;
    if (!window.confirm("Apagar esta conversa?")) return;
    const corpo = new URLSearchParams();
    if (SUB) corpo.set("subtopico", SUB);
    await fetch(URLS.limpar, {
      method: "POST",
      headers: { "X-CSRFToken": csrf.value, "X-Requested-With": "XMLHttpRequest" },
      body: corpo,
    });
    fluxo.querySelectorAll(".chat-bolha").forEach((b) => b.remove());
    if (vazio) vazio.hidden = false;
    terminar();
  });

  /* ---- abrir e fechar -------------------------------------------------- */

  const alternar = (mostrar) => {
    painel.toggleAttribute("hidden", !mostrar);
    abrir.setAttribute("aria-expanded", mostrar ? "true" : "false");
    if (mostrar) { carregar(); campo.focus(); } else { abrir.focus(); }
  };

  abrir.addEventListener("click", () => alternar(painel.hasAttribute("hidden")));
  document.getElementById("chat-fechar").addEventListener("click", () => alternar(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !painel.hasAttribute("hidden")) alternar(false);
  });

  const titulo = document.querySelector("[data-subtopico-titulo]");
  if (titulo && contexto) contexto.textContent = `Sobre "${titulo.dataset.subtopicoTitulo}"`;
})();
