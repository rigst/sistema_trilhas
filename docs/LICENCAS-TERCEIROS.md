# Licenças de terceiros — Trilhas de Estudo com IA

Gerado por `scripts/licencas_terceiros.py` em 2026-07-24 a partir dos pacotes instalados no venv de produção.
Para regenerar: `./venv/bin/python scripts/licencas_terceiros.py`.

O código deste projeto é licenciado sob **licença proprietária** (ver `LICENSE`). As bibliotecas abaixo permanecem sob suas licenças originais.

## Dependências diretas

| Pacote | Versão | Licença |
|---|---|---|
| anthropic | 0.116.0 | MIT License |
| celery | 5.6.3 | BSD-3-Clause |
| Django | 6.0.6 | BSD-3-Clause |
| django-redis | 6.0.0 | BSD License |
| edge-tts | 7.2.8 | GNU Lesser General Public License v3 (LGPLv3) |
| gunicorn | 26.0.0 | MIT |
| Markdown | 3.10.2 | BSD-3-Clause |
| nh3 | 0.3.6 | MIT |
| playwright | 1.61.0 | Apache-2.0 |
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| Pygments | 2.20.0 | BSD-2-Clause |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| redis | 8.0.1 | MIT |
| sentry-sdk | 2.64.0 | MIT |
| weasyprint | 69.0 | BSD License |

## Dependências transitivas

| Pacote | Versão | Licença |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| amqp | 5.3.1 | BSD License |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.14.1 | MIT |
| asgiref | 3.11.1 | BSD License |
| attrs | 26.1.0 | MIT |
| billiard | 4.2.4 | BSD License |
| brotli | 1.2.0 | MIT |
| certifi | 2026.6.17 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.0.0 | MIT |
| charset-normalizer | 3.4.9 | MIT |
| click | 8.4.2 | BSD-3-Clause |
| click-didyoumean | 0.3.1 | MIT License |
| click-plugins | 1.1.1.2 | BSD License |
| click-repl | 0.3.0 | MIT |
| cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause |
| cssselect2 | 0.9.0 | BSD License |
| distro | 1.9.0 | Apache Software License |
| docstring_parser | 0.18.0 | MIT License |
| docutils | 0.23 | Public Domain / BSD License / GNU General Public License (GPL) |
| filelock | 3.32.0 | MIT |
| fonttools | 4.63.0 | MIT |
| frozenlist | 1.8.0 | Apache-2.0 |
| greenlet | 3.5.4 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| id | 1.6.1 | Apache Software License |
| idna | 3.18 | BSD-3-Clause |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| jeepney | 0.9.0 | MIT |
| jiter | 0.16.0 | MIT |
| keyring | 25.7.0 | MIT |
| kombu | 5.6.2 | BSD-3-Clause |
| markdown-it-py | 4.2.0 | MIT License |
| mdurl | 0.1.2 | MIT License |
| more-itertools | 11.1.0 | MIT |
| multidict | 6.7.1 | Apache License 2.0 |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU |
| progress | 1.6.1 | ISC |
| prompt_toolkit | 3.0.52 | BSD License |
| propcache | 0.5.2 | Apache Software License |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| pydyf | 0.12.1 | BSD License |
| pyee | 13.0.1 | MIT License |
| pyphen | 0.17.2 | GNU General Public License v2 or later (GPLv2+) / GNU Lesser General Public License v2 or later (LGPLv2+) / Mozilla Public License 1.1 (MPL 1.1) |
| python-dateutil | 2.9.0.post0 | BSD License / Apache Software License |
| readme_renderer | 45.0 | Apache-2.0 |
| requests | 2.34.2 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| rfc3986 | 2.0.0 | Apache Software License |
| rich | 15.0.0 | MIT License |
| SecretStorage | 3.5.0 | BSD-3-Clause |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| sqlparse | 0.5.5 | BSD License |
| tabulate | 0.10.0 | MIT |
| tinycss2 | 1.5.1 | BSD License |
| tinyhtml5 | 2.1.0 | MIT License |
| twine | 6.2.0 | Apache-2.0 |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.2 | MIT |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.4.4 | MIT |
| urllib3 | 2.7.0 | MIT |
| vine | 5.1.0 | BSD License |
| wcwidth | 0.8.2 | MIT |
| webencodings | 0.5.1 | BSD License |
| yarl | 1.24.5 | Apache-2.0 |
| zopfli | 0.4.3 | Apache Software License |

## Programas externos

Invocados por `subprocess` como processos separados — não são linkados ao código deste projeto.

| Programa | Versão | Licença | Observação |
|---|---|---|---|
| FFmpeg | 6.1.1 (Ubuntu, `--enable-gpl`) | GPL-2.0-or-later | Montagem de vídeo, chamado em `trilhas/video_montagem.py` |
| Chromium (via Playwright) | 1228 | BSD-3-Clause e outras | Renderização de slides em `trilhas/video_slides.py` |

## Componentes com licença recíproca (copyleft)

Listados para conferência ao redistribuir o código ou ao combinar com componentes fechados. O uso como biblioteca, sem modificação e sem distribuição do binário, não propaga obrigações de abertura.

| Pacote | Versão | Licença |
|---|---|---|
| edge-tts | 7.2.8 | GNU Lesser General Public License v3 (LGPLv3) |
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| certifi | 2026.6.17 | Mozilla Public License 2.0 (MPL 2.0) |
| docutils | 0.23 | Public Domain / BSD License / GNU General Public License (GPL) |
| pyphen | 0.17.2 | GNU General Public License v2 or later (GPLv2+) / GNU Lesser General Public License v2 or later (LGPLv2+) / Mozilla Public License 1.1 (MPL 1.1) |

## Notas de manutenção

- **Redis**: o servidor em uso é a série 7.0 (BSD-3-Clause). As versões 7.4 a 7.9 passaram a ser RSALv2/SSPL, que não são licenças livres segundo a OSI. Ao atualizar o servidor, reveja esta seção e a página de licenças do site.
- Os programas externos acima rodam como processos separados, invocados por linha de comando. Não há linkagem com o código deste projeto, e o serviço não distribui os binários — por isso as obrigações de reciprocidade da GPL não se estendem a este código.
- **WeasyPrint** usa Pango, cairo e HarfBuzz do sistema (LGPL) por ligação dinâmica via cffi, forma de uso compatível com a LGPL sem obrigação de abertura.
