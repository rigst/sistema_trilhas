# Segurança

## Reporte

Envie vulnerabilidades de forma privada para **rodrigo@stolben.com**, incluindo
impacto, passos de reprodução e versão observada. Não abra issue pública com
dados reais, credenciais ou detalhes exploráveis antes da correção.

Retorno esperado em até 7 dias.

## Escopo suportado

Somente a versão implantada mais recente (`main`) recebe correções.

## Verificações automáticas

Todo push e PR passa pelo pipeline de [rigst/ci](https://github.com/rigst/ci):

- `bandit` no código e `pip-audit` nas dependências;
- `gitleaks` sobre **todo o histórico**, não só o diff;
- `django check --deploy` com os settings de produção.

## Fora de escopo

- Ausência de antivírus em upload: arquivos enviados não passam por varredura
  antimalware nesta versão.
- Conteúdo gerado pela IA: o modelo pode produzir texto incorreto. Isso é
  limitação conhecida, não vulnerabilidade.
- Quota de tokens: o consumo é debitado por perfil; abuso de quota é tratado
  como questão de produto.

## Chaves de API

O projeto chama a API da Anthropic. A `ANTHROPIC_API_KEY` fica em `.env`, nunca
no repositório, e não é enviada ao cliente em nenhuma resposta.
