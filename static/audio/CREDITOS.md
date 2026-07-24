# Créditos das músicas de fundo (vídeo do tópico)

Faixas instrumentais suaves usadas como fundo (volume baixo) nos vídeos gerados
a partir dos tópicos. A faixa é escolhida automaticamente pela **categoria** da
trilha (ver `trilhas/video_pipeline.py` → `MUSICAS` / `_CATEGORIA_CLIMA`). O
crédito também aparece no slide final de cada vídeo.

Todas por **MusicLFiles**, via Wikimedia Commons, licença **CC BY 4.0**
(https://creativecommons.org/licenses/by/4.0/):

| Arquivo | Faixa | Clima → categorias |
|---|---|---|
| `fundo_corporativo.ogg` | Soft Corporate | Tecnologia, Programação, Negócios |
| `fundo_ambiente.ogg`    | Placid Ambient | Ciências, Matemática (e padrão) |
| `fundo_espiritual.ogg`  | Spiritual Ambient | História, Direito, Saúde |
| `fundo_ameno.ogg`       | Warm Sunset | Idiomas, Música, Artes |

Fonte: https://commons.wikimedia.org/wiki/User:MusicLFiles

Para trocar/adicionar faixas: coloque o arquivo aqui e ajuste o mapa `MUSICAS`
em `trilhas/video_pipeline.py`. Para forçar uma única faixa em todos os vídeos,
defina `VIDEO_MUSICA_PATH` no `.env`.
