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
  const lista = document.getElementById("chat-lista");
  const form = document.getElementById("chat-form");
  const campo = document.getElementById("chat-pergunta");
  const botao = document.getElementById("chat-enviar");
  const rodape = document.getElementById("chat-rodape");
  const contexto = document.getElementById("chat-contexto");
  const btSalvas = document.getElementById("chat-salvas");
  const csrf = painel.querySelector("[name=csrfmiddlewaretoken]");

  const URLS = painel.dataset;
  // A conversa é da TRILHA: toda página dela manda o mesmo id, então o fio
  // continua ao virar de tópico, de nível ou ao ir para a avaliação. O
  // subtópico só diz qual material vai no contexto desta pergunta.
  const TRILHA = document.body.dataset.trilha || "";
  const SUB = document.body.dataset.subtopico || "";
  const INTERVALO_MS = 600;
  let ocupado = false;
  let carregado = false;
  // Conversa aberta pela lista de salvas; nula = a da página atual.
  let conversaId = null;

  const params = () => {
    const p = new URLSearchParams();
    if (conversaId) p.set("conversa", conversaId);
    else {
      if (TRILHA) p.set("trilha", TRILHA);
      if (SUB) p.set("subtopico", SUB);
    }
    return p;
  };
  const alvo = (base) => {
    const p = params().toString();
    return p ? `${base}?${p}` : base;
  };
  const urlStatus = (id) => URLS.status.replace(/\/0\/status\/$/, `/${id}/status/`);
  const aoFim = () => { fluxo.scrollTop = fluxo.scrollHeight; };

  const aviso = (texto, alerta) => {
    rodape.textContent = texto || "";
    rodape.classList.toggle("alerta", !!alerta);
  };

  // No envio o subtópico vai SEMPRE, mesmo numa conversa aberta pela lista: ele
  // não escolhe a conversa, diz de qual página a pergunta saiu.
  const corpoComAlvo = () => {
    const corpo = params();
    if (SUB) corpo.set("subtopico", SUB);
    return corpo;
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

  /* Escrita progressiva: o servidor publica o texto parcial a cada 250 ms e o
     painel busca a cada 600 ms, então o que chega são blocos. Revelar letra a
     letra a partir de um buffer transforma esses saltos numa digitação
     contínua — e a velocidade acompanha o quanto falta, para nunca ficar para
     trás do que o modelo já respondeu. */
  const revelador = (el) => {
    let buffer = "";
    let mostrado = 0;
    let relogio = null;
    const corpo = document.createElement("span");
    const pontos = document.createElement("span");
    pontos.className = "chat-digitando";
    pontos.innerHTML = "<i></i><i></i><i></i>";
    el.replaceChildren(corpo, pontos);

    const passo = () => {
      const falta = buffer.length - mostrado;
      if (falta <= 0) { relogio = null; return; }
      mostrado += Math.max(2, Math.ceil(falta / 12));
      corpo.textContent = buffer.slice(0, mostrado);
      aoFim();
      relogio = setTimeout(passo, 30);
    };

    return {
      alimentar(texto) {
        if (!texto || texto.length <= buffer.length) return;
        buffer = texto;
        if (!relogio) passo();
      },
      // Deixa a digitação alcançar o texto antes de trocar pelo HTML final,
      // senão a resposta "pula" no último instante.
      encerrar(aplicar) {
        const esperar = () => {
          if (relogio && mostrado < buffer.length) { setTimeout(esperar, 30); return; }
          if (relogio) { clearTimeout(relogio); relogio = null; }
          aplicar();
        };
        esperar();
      },
    };
  };

  const pintar = (el, dados) => {
    el.classList.toggle("chat-bolha--erro", dados.status === "erro");
    if (dados.status === "erro") {
      el.textContent = dados.erro || "Não consegui responder agora.";
      return;
    }
    if (dados.status === "gerando") {
      el.classList.add("escrevendo");
      if (!el._revelador) el._revelador = revelador(el);
      el._revelador.alimentar(dados.parcial || "");
      return;
    }
    el.classList.remove("escrevendo");
    // Pronta: o HTML já veio sanitizado pelo nh3 do lado do servidor.
    const aplicar = () => {
      el.innerHTML = dados.html || "";
      el.classList.add("markdown-body");
      if (window.__mermaidRefresh) window.__mermaidRefresh(el);
      if (window.__armarCopiar) window.__armarCopiar(el);
      aoFim();
    };
    if (el._revelador) { el._revelador.encerrar(aplicar); el._revelador = null; } else aplicar();
  };

  /* ---- polling --------------------------------------------------------- */

  const acompanhar = (el, id) => {
    const tick = async () => {
      try {
        const r = await fetch(urlStatus(id), { headers: { "X-Requested-With": "XMLHttpRequest" } });
        const d = await r.json();
        pintar(el, d);
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
    const corpo = corpoComAlvo();
    corpo.set("pergunta", pergunta);
    try {
      const r = await fetch(URLS.enviar, {
        method: "POST",
        headers: { "X-CSRFToken": csrf.value, "X-Requested-With": "XMLHttpRequest" },
        body: corpo,
      });
      const d = await r.json();
      if (!r.ok) { aviso(d.erro || "Não deu para enviar agora.", true); terminar(); return; }
      bolha(d.pergunta);
      acompanhar(bolha(d.resposta), d.resposta.id);
    } catch (e) {
      aviso("Sem conexão com o servidor.", true);
      terminar();
    }
  };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const pergunta = campo.value.trim();
    if (!pergunta || ocupado) return;
    campo.value = "";
    ajustarAltura();
    verLista(false);
    botao.classList.add("voando");
    setTimeout(() => botao.classList.remove("voando"), 500);
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
    fluxo.querySelectorAll(".chat-bolha").forEach((b) => b.remove());
    if (vazio) vazio.hidden = false;
    try {
      const r = await fetch(alvo(URLS.historico), { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const d = await r.json();
      if (conversaId && d.rotulo) contexto.textContent = d.contexto || d.rotulo;
      d.mensagens.forEach((m) => {
        const el = bolha(m);
        if (m.status === "gerando") { ocupado = true; botao.disabled = true; acompanhar(el, m.id); }
      });
    } catch (e) { /* sem histórico é um começo válido de conversa */ }
    carregado = true;
  };

  /* ---- conversas salvas ------------------------------------------------ */

  const verLista = (mostrar) => {
    lista.toggleAttribute("hidden", !mostrar);
    fluxo.toggleAttribute("hidden", mostrar);
    btSalvas.setAttribute("aria-expanded", mostrar ? "true" : "false");
    if (mostrar) carregarLista();
  };

  const carregarLista = async () => {
    lista.replaceChildren();
    try {
      const r = await fetch(URLS.conversas, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const d = await r.json();
      if (!d.conversas.length) {
        const p = document.createElement("p");
        p.className = "chat-lista-vazia";
        p.textContent = "Nenhuma conversa salva ainda. As dúvidas que você tirar ficam guardadas aqui por 90 dias.";
        lista.appendChild(p);
        return;
      }
      d.conversas.forEach((c) => lista.appendChild(itemDaLista(c)));
    } catch (e) {
      aviso("Não deu para carregar as conversas.", true);
    }
  };

  const itemDaLista = (c) => {
    const bt = document.createElement("button");
    bt.type = "button";
    bt.className = "chat-item" + (c.id === conversaId ? " atual" : "");
    bt.setAttribute("role", "listitem");
    const topo = document.createElement("div");
    topo.className = "chat-item-topo";
    const nome = document.createElement("span");
    nome.className = "chat-item-nome";
    nome.textContent = c.rotulo;
    const quando = document.createElement("span");
    quando.className = "chat-item-quando";
    quando.textContent = c.quando;
    topo.append(nome, quando);
    bt.appendChild(topo);
    if (c.contexto) {
      const ctx = document.createElement("p");
      ctx.className = "chat-item-ctx";
      ctx.textContent = c.contexto;
      bt.appendChild(ctx);
    }
    const previa = document.createElement("p");
    previa.className = "chat-item-previa";
    previa.textContent = c.previa;
    bt.appendChild(previa);
    bt.addEventListener("click", () => {
      conversaId = c.id;
      verLista(false);
      carregar();
      campo.focus();
    });
    return bt;
  };

  btSalvas.addEventListener("click", () => verLista(lista.hasAttribute("hidden")));

  document.getElementById("chat-limpar").addEventListener("click", async () => {
    if (!fluxo.querySelector(".chat-bolha")) return;
    if (!window.confirm("Apagar esta conversa?")) return;
    await fetch(URLS.limpar, {
      method: "POST",
      headers: { "X-CSRFToken": csrf.value, "X-Requested-With": "XMLHttpRequest" },
      body: corpoComAlvo(),
    });
    // Apagada a conversa aberta pela lista, o painel volta para a da página.
    conversaId = null;
    contexto.textContent = tituloDaPagina();
    fluxo.querySelectorAll(".chat-bolha").forEach((b) => b.remove());
    if (vazio) vazio.hidden = false;
    terminar();
  });

  /* ---- abrir e fechar -------------------------------------------------- */

  const alternar = (mostrar) => {
    abrir.setAttribute("aria-expanded", mostrar ? "true" : "false");
    if (mostrar) {
      abrir.classList.remove("chama");
      try { localStorage.setItem("trilhas-chat-visto", "1"); } catch (e) {}
      painel.classList.remove("saindo");
      painel.removeAttribute("hidden");
      if (!carregado) carregar();
      campo.focus();
      return;
    }
    // `hidden` é display:none e cortaria a animação de saída pela metade: só
    // depois que ela termina o painel some de fato.
    painel.classList.add("saindo");
    const esconder = () => {
      painel.classList.remove("saindo");
      painel.setAttribute("hidden", "");
      painel.removeEventListener("animationend", esconder);
    };
    painel.addEventListener("animationend", esconder);
    // Sem animação (prefers-reduced-motion) o evento nunca vem.
    setTimeout(esconder, 260);
    abrir.focus();
  };

  abrir.addEventListener("click", () => alternar(painel.hasAttribute("hidden")));
  document.getElementById("chat-fechar").addEventListener("click", () => alternar(false));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || painel.hasAttribute("hidden")) return;
    // Esc fecha a lista primeiro; só depois o painel.
    if (!lista.hasAttribute("hidden")) verLista(false);
    else alternar(false);
  });

  const tituloDaPagina = () => {
    const t = document.body.dataset.subtopicoTitulo;
    if (t) return `Sobre "${t}"`;
    return TRILHA ? "Sobre esta trilha" : "Sobre o que você está estudando";
  };
  contexto.textContent = tituloDaPagina();

  try {
    if (!localStorage.getItem("trilhas-chat-visto")) abrir.classList.add("chama");
  } catch (e) { /* sem localStorage, sem chamada: não é essencial */ }
})();
