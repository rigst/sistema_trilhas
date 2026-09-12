# Configuração do gunicorn, lida por descoberta: a unidade não passa
# `--config`, e sem ela o gunicorn procura `./gunicorn.conf.py` a partir do
# WorkingDirectory, que é /var/www/sistema_trilhas.
#
# Existe por um motivo só. O gunicorn 26 abre um socket de controle, para o
# `gunicornc`, cujo caminho padrão é `$XDG_RUNTIME_DIR/gunicorn.ctl` e, sem
# essa variável — o caso sob systemd —, cai em `~/.gunicorn/gunicorn.ctl`.
#
# Cinco serviços deste servidor rodam como `rod` e resolviam todos para o
# MESMO arquivo. Socket unix tem um dono só: quem sobe por último fica com
# ele, e a partir daí o `gunicornc` fala com o app errado sem avisar nada.
# Medido numa auditoria em 12/09/2026.
#
# O nome do projeto no caminho resolve a colisão sem precisar mexer na
# unidade, que é root e não está sob controle deste repositório. O certo
# mesmo seria `/run/sistema_trilhas/gunicorn.ctl` com `RuntimeDirectory=` na unidade,
# como o dojo faz — fica para quando a unidade for tocada.
#
# Exige RESTART, não reload: o SIGHUP relê este arquivo, mas o arbiter não
# reinicia o servidor de controle junto, então o caminho continua sendo o
# resolvido na subida.
control_socket = "/home/rod/.gunicorn/sistema_trilhas.ctl"
