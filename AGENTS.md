# Instruções para agentes

## Commits

- **Não mencione LLMs, assistentes de IA ou ferramentas de geração** na mensagem
  de commit. Sem trailer `Co-Authored-By` apontando para modelo/assistente, sem
  "gerado com", sem nome de modelo. O histórico é do projeto, não da ferramenta.
- Mensagens em português. O assunto diz **o que mudou**; o corpo, **por quê** —
  o diff já mostra o "como".
- Commit só quando pedido.

## Ambiente

- Use sempre o venv do projeto: `.venv/Scripts/python.exe`. O Python do sistema
  não tem as dependências (`dotenv`, `msal`, `requests`) e falha no import.
- Segredos ficam no `.env` (Azure, chaves de LLM) — nunca commitados.

## Timestamps da Graph — cuidado

Os `createdDateTime`/`lastActivity` da Microsoft Graph vêm com **precisão
fracionária variável** (`.11Z`, `.061Z`, `.7Z`). Comparar as strings ISO
diretamente **está errado**: na ordem lexicográfica
`'2026-07-10T19:51:31.726Z' > '2026-07-10T19:51:31.726000+00:00'`, apesar de
serem o mesmo instante. Isso já causou re-extração diária de centenas de chats.

Sempre normalize antes de comparar, com os helpers de `extractor/teams_client.py`:

- `parse_timestamp(str) -> datetime | None` — string da Graph para datetime UTC.
- `to_utc(datetime) -> datetime | None` — o cursor vem aware; o piso de `--since`
  vem naive.

Semântica dos limites na extração: o **cursor é exclusivo** (a mensagem naquele
instante já foi extraída) e o **piso do `--since` é inclusivo** (senão o backfill
perde mensagens à meia-noite exata). É o que o parâmetro `since_exclusive` de
`get_messages()` distingue.

## Rede

Chamadas à Graph usam a `Session` compartilhada de `teams_client.get_session()`,
que já tem retry com backoff (erros de conexão + 429/5xx). Não use `requests.get`
cru. Erros determinísticos (403/404) **não** são retentados de propósito — no
export de imagens eles são gravados num negative cache (`.failed`) para não
retentarem indefinidamente.
