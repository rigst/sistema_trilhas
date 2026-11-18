/* Ações de formulário que antes eram atributos inline (onclick/onsubmit).
   A CSP do app só executa script do próprio domínio ou com nonce, e atributo
   de evento não aceita nonce: por isso o comportamento vem daqui, por delegação.

   - form[data-confirmar]: pede confirmação antes de enviar.
   - button[data-renomear]: pergunta o novo nome e envia o form do botão,
     gravando o valor no campo `titulo`.
   - [data-recarregar]: recarrega a página (tela offline). */
(() => {
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("form[data-confirmar]");
    if (form && !globalThis.confirm(form.dataset.confirmar)) e.preventDefault();
  });

  document.addEventListener("click", (e) => {
    const renomear = e.target.closest("button[data-renomear]");
    if (renomear) {
      const nome = globalThis.prompt("Novo nome da trilha:", renomear.dataset.renomear);
      if (nome?.trim()) {
        const form = renomear.closest("form");
        form.querySelector("input[name=titulo]").value = nome.trim();
        form.submit();
      }
      return;
    }
    if (e.target.closest("[data-recarregar]")) globalThis.location.reload();
  });
})();
