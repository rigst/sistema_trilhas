# Conformidade legal — LGPD e Marco Civil

Como o Trilhas de Estudo registra o aceite dos termos, por quanto tempo guarda os
registros de acesso e o que fazer para publicar uma versão nova das políticas.

## 1. Registro de aceite

O app `legal` guarda dois modelos.

**`DocumentoLegal`** — uma linha por *versão* de cada documento. Ciclo de vida:
`rascunho` → `publicado` → `arquivado`. Ao publicar, o sistema congela o HTML renderizado
e o `sha256` do texto; a partir daí a versão é imutável.

**`AceiteLegal`** — a prova: qual versão, quem (com identificação congelada), se era
visitante, IP, navegador, data, sessão, origem, o hash do texto **no momento do aceite** e
um JSON de evidência (host, path, método, `Referer`, `X-Forwarded-For` bruto, idioma e as
versões vigentes na hora).

O ponto mais importante do desenho: **o aceite sobrevive à exclusão do usuário.** A task
`accounts.tasks.cleanup_expired_visitors` apaga visitantes expirados por
`queryset.delete()`, e a prova não pode ir junto — daí `usuario` ser `SET_NULL` e existir
o `usuario_label` congelado.

### Onde o aceite é capturado

- **Acesso visitante** (`accounts/views.py:entrar_como_visitante`): o checkbox é validado
  **antes** de criar a conta. Sem aceite, nenhum usuário é criado e a pessoa volta ao
  login com a mensagem de erro.
- **Cadastro** (`accounts/forms.py:CadastroForm` + `accounts/views.py`): o
  `AceiteLegalMixin` acrescenta o campo obrigatório. Aqui o cadastro passa por
  confirmação de e-mail, então o aceite é **gravado em `confirmar_email`**, que é quando
  a conta passa a existir de fato — não no envio do formulário. `SIGNUP_ENABLED` é
  `False` em produção: o caminho existe e é testado, mas não está acessível ao público.
- **Login normal**: sem checkbox. Quem já tem conta já aceitou; versão nova é tratada pelo
  middleware.
- **Versão nova** (`legal/middleware.py`): `AceiteObrigatorioMiddleware` redireciona
  qualquer usuário autenticado com aceite pendente para `/legal/reaceite/`, liberando só
  as rotas da allowlist. Ele entra **antes** do `VisitorExpiryMiddleware`.

O checkbox nasce sempre desmarcado (`initial=False`) e é obrigatório no **servidor**
(`required=True`) — burlar o HTML no navegador não passa pela validação do formulário.

### Extrair evidência

No admin, em *Conformidade legal → Aceites*: filtre por documento, versão, origem ou data
e use **"Exportar seleção em CSV"**. O CSV traz o hash gravado no aceite e o hash atual do
documento lado a lado, mais a coluna `integro` — se divergirem, o texto foi alterado
depois do aceite.

O próprio usuário consulta seus aceites em `/legal/meus-aceites/` (LGPD art. 18).
`AceiteLegal` é somente leitura no admin.

## 2. Publicar uma versão nova das políticas

O **banco é a fonte da verdade**; `legal/documentos/<tipo>/<versao>.md` é o espelho em git.

1. No admin, em *Documentos legais*, selecione a versão vigente e rode
   **"Duplicar como nova versão (rascunho)"**.
2. Edite o rascunho em Markdown; *Pré-visualização* mostra o resultado sanitizado.
3. Marque **mudança material** se todos devem aceitar de novo.
4. Selecione o rascunho e rode **"Publicar rascunhos selecionados"**.
5. Espelhe em git:
   ```bash
   ./venv/bin/python manage.py exportar_documentos_legais
   git add legal/documentos && git commit -m "Publica <documento> vX.Y"
   ```

A publicação só existe como **ação da changelist**, nunca como link: ação de admin já vem
como POST com CSRF.

Versão publicada não é editável nem apagável, nem antes do primeiro aceite — no instante
em que vai ao ar já está sendo exibida. Para mudar o texto, publique outra versão.
`importar_documentos_legais` **recusa** sobrescrever versão existente cujo texto tenha
mudado.

## 3. Guarda dos registros de acesso (6 meses)

O art. 15 do Marco Civil da Internet exige 6 meses. Quem cumpre é o nginx.

Este site já grava em `/var/log/nginx/acesso/trilhas.access.log`, e a rotação de 200 dias
está em `/etc/logrotate.d/stolben-acesso`. Para reinstalar ou replicar:

```bash
sudo install -d -o root -g adm -m 0755 /var/log/nginx/acesso
sudo cp deploy/logrotate/stolben-acesso /etc/logrotate.d/stolben-acesso
sudo python3 deploy/nginx_acesso.py --dry-run
sudo python3 deploy/nginx_acesso.py && sudo nginx -t && sudo systemctl reload nginx
```

O subdiretório `acesso/` evita colidir com o `/etc/logrotate.d/nginx` do sistema, que
rotaciona `/var/log/nginx/*.log` a cada 14 dias — o glob não é recursivo.

O `X-Forwarded-For` é lido pelo **último** item, em `legal/utils.py:ip_do_request()`:
atrás do nginx, esse é o IP que ele observou; os anteriores vieram do cliente e são
forjáveis.

## 4. Checklist de deploy

```bash
./venv/bin/python manage.py migrate
./venv/bin/python manage.py importar_documentos_legais   # cria os rascunhos novos
./venv/bin/python manage.py collectstatic --noinput      # unfold traz estáticos
sudo systemctl restart trilhas trilhas_celery
```

Todos com `DJANGO_SETTINGS_MODULE=config.settings.production` — sem isso o `migrate`
acerta o sqlite de desenvolvimento e a tabela nova continua faltando no Postgres.
O `importar_documentos_legais` cria **rascunho**; publicar é ação de admin (seção 2), que
é onde se decide se a mudança é material. O `--publicar` serve ao seed inicial.

O `trilhas_celery` entra na lista porque as tasks de IA rodam nele: worker prefork já tem
os módulos em memória e segue respondendo com a versão antiga do código.

> O `trilhas.service` (gunicorn) não define `ExecReload`, então use `restart`
> (`reload` responde "Job type reload is not applicable").

`collectstatic` precisa das variáveis de produção: o app usa
`ManifestStaticFilesStorage`, e um estático fora do manifesto derruba a página com 500.

## 5. Retenção das conversas do chat de dúvidas

O chat é a única entrada de texto livre do app, e a política promete prazo: conversa
parada há mais de `CHAT_RETENCAO_DIAS` (90) é apagada. Quem cumpre é
`chat.tasks.purgar_conversas_antigas`, agendada no `CELERY_BEAT_SCHEDULE` para as 4h30 —
ou seja, depende do `trilhas_celery.service` estar de pé (ele roda worker **e** beat).

```bash
# Conferir que o expurgo está no beat e quanto ele apagaria agora
./venv/bin/python manage.py shell -c "from chat.tasks import purgar_conversas_antigas; print(purgar_conversas_antigas())"
```

O aluno também apaga a conversa na hora, pelo botão no próprio painel (LGPD art. 18, no
lugar onde o dado foi criado). E `Conversa.user` é `CASCADE`: o expurgo de visitantes
(`accounts.tasks.cleanup_expired_visitors`, que faz `queryset.delete()` nas contas
expiradas) leva as conversas junto, como a política diz.

Nada do conteúdo das conversas vai para log: não há `LOGGING` configurado, o Sentry sobe
com `send_default_pii=False` e o campo `Mensagem.erro` guarda só a mensagem da exceção.

## 6. PWA e a allowlist do middleware

`/sw.js` e `/offline/` entram em `LEGAL_ALLOWLIST_EXTRA` (`config/settings/base.py`). Sem
isso, o service worker receberia 302 para a tela de re-aceite e o navegador cacharia o
redirecionamento — o app ficaria preso na tela de aceite mesmo depois de aceitar.
