# TeamsNotes

Ferramenta de linha de comando que extrai suas conversas do **Microsoft Teams**,
gera **briefings** (resumos diários/semanais/mensais) com uma LLM e mantém um
**arquivo HTML navegável** das conversas originais.

- Descoberta dinâmica dos seus chats via Microsoft Graph (sem lista fixa).
- Armazenamento local em SQLite (texto limpo + HTML original das mensagens).
- Classificação/resumo via LLM — **Anthropic** ou **Google Gemini** (configurável).
- Arquivo HTML por chat no estilo Teams: imagens inline, snippets de código,
  divisória por dia, busca no índice e tema claro/escuro.

```
Microsoft Teams
      │  Graph API (/me/chats, /messages)
      ▼
  extração ──▶ SQLite ──┬──▶ classificação (LLM) ──▶ briefings .md (daily/weekly/monthly)
                        └──▶ exportação ──▶ arquivo HTML navegável (index + 1 página/chat)
```

---

## Pré-requisitos

- **Python 3.11+**
- Um **app registrado no Azure AD** com permissões delegadas do Microsoft Graph.
- Uma **chave de API de LLM**: Anthropic **ou** Google Gemini.

---

## Instalação

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Configuração

Copie o template e preencha os valores:

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # Linux/macOS
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `AZURE_CLIENT_ID` | sim | ID do app registrado no Azure AD |
| `AZURE_TENANT_ID` | sim | ID do tenant (diretório) |
| `AZURE_CLIENT_SECRET` | não | não usado no fluxo device code (cliente público) |
| `LLM_PROVIDER` | sim | `anthropic` ou `gemini` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | se `anthropic` | chave e modelo (ex.: `claude-sonnet-4-6`) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | se `gemini` | chave e modelo (ex.: `gemini-2.5-flash`) |
| `OBSIDIAN_VAULT_PATH` | sim | pasta-raiz dos briefings (`daily/`, `weekly/`, `monthly/`) |
| `CHATS_HTML_PATH` | sim | pasta de saída do arquivo HTML das conversas |
| `DB_PATH` | não | caminho do SQLite (default `./storage/messages.db`) |
| `LLM_MIN_INTERVAL` | não | espaçamento (s) entre chamadas LLM; útil em planos gratuitos |

### Registro no Azure AD

1. **Azure AD → App registrations → New registration**. Tipo: contas só do seu diretório.
2. **API permissions → Microsoft Graph → Delegated** — adicione:

   | Permissão | Para quê |
   |---|---|
   | `Chat.Read` | ler mensagens de chats 1:1 e de grupo |
   | `ChannelMessage.Read.All` | ler mensagens de canais de equipes |
   | `User.Read` | identificar o usuário autenticado |
   | `offline_access` | manter o refresh token ativo |

   > Se disponível, clique em **Grant admin consent** para acelerar; senão, o
   > consentimento acontece no primeiro login.
3. Copie o **Application (client) ID** e o **Directory (tenant) ID** para o `.env`.

A autenticação usa o fluxo **device code** (cliente público), então **não** é
necessário client secret.

---

## Uso

### 1. Primeira execução (autenticação)

Na primeira vez, o programa exibe um **código** e uma URL. Acesse a URL, informe o
código e autorize. O token fica em cache (`.msal_cache.json`) e é renovado sozinho.

### 2. Execução diária (processa o dia anterior)

```bash
python scheduler/run_nightly.py
```

Extrai as conversas novas, gera o briefing de ontem e atualiza o arquivo HTML.

### 3. Backfill histórico (intervalo de datas)

```bash
python scheduler/run_nightly.py --since 2025-12-01 --until 2026-06-27
```

### Flags

| Flag | Efeito |
|---|---|
| `--since AAAA-MM-DD` | data inicial do processamento/extração |
| `--until AAAA-MM-DD` | data final (limita extração e classificação) |
| `--force` | re-extrai a janela inteira ignorando os cursores (recupera o HTML/imagens de datas já extraídas) |
| `--no-classify` | só extrai e gera o HTML — **não chama a LLM** (não consome cota) |
| `--no-export` | não regenera o HTML ao final |
| `--reprocess` | reclassifica um período **já processado** com a engine atual: limpa os briefings/itens do intervalo e remarca as mensagens (não re-extrai). Exige `--since` |

> Exemplo — preencher o arquivo HTML de um período passado **sem gastar cota de LLM**:
> ```bash
> python scheduler/run_nightly.py --since 2025-12-01 --until 2026-05-31 --force --no-classify
> ```

### Reprocessar com outra engine

Para refazer os briefings de um período com outro provider (ex.: você gerou com
Gemini e quer a versão com Anthropic):

1. Troque `LLM_PROVIDER` (e o modelo) no `.env`.
2. Rode com `--reprocess` no mesmo intervalo:

```bash
python scheduler/run_nightly.py --since 2026-06-26 --until 2026-06-29 --reprocess
```

Isso **remove os itens/briefings antigos do intervalo** e reclassifica com a engine
nova. Os itens ficam marcados com o `engine` usado (ex.: `anthropic/claude-sonnet-4-6`).
Se quiser **preservar** os resultados da engine anterior para comparação, faça antes
um backup do banco (`python scheduler/reset_db.py` ou copie o `messages.db`).

### Exportar o HTML avulso (sem re-extrair)

```bash
python exporter/export_html.py
```

Regenera as páginas a partir do banco — útil depois de ajustar o CSS/JS.

### Reiniciar (backup do banco)

```bash
python scheduler/reset_db.py
```

Renomeia o banco atual para `messages_backup_<timestamp>.db` (não apaga) e deixa
um banco limpo ser recriado na próxima execução.

---

## Saídas

- **Briefings**: `OBSIDIAN_VAULT_PATH/daily/AAAA-MM-DD.md`,
  `.../weekly/AAAA-Wnn.md`, `.../monthly/AAAA-MM.md`.
- **Arquivo de conversas**: `CHATS_HTML_PATH/index.html` (índice com busca) e
  `CHATS_HTML_PATH/chats/<slug>.html` (uma página por chat). As imagens baixadas
  ficam em `CHATS_HTML_PATH/assets/img/`.

Cada briefing agrupa os itens **por chat** e os classifica em seções: _Alinhamentos
& Decisões_, _Alertas & Problemas_, _Lembretes & Prazos_, _Definições Técnicas_,
_Snippets de Código_ e _Links Relevantes_. Conversa social / ruído é descartado.

As páginas de chat são **regeneradas a partir do histórico completo** do banco,
então as conversas vão **acumulando** entre execuções, sem páginas duplicadas.

---

## Agendamento

Sem flags, `run_nightly.py` processa **o dia anterior inteiro** — basta rodá-lo
logo após a meia-noite.

### Windows (Agendador de Tarefas)

Use o **Agendador de Tarefas** nativo — **não** use NSSM (NSSM é para _serviços_
sempre ligados, não para um job diário). Há um wrapper pronto, `run_daily.bat`, que
ajusta o diretório de trabalho e grava `logs/nightly.log`.

Registre a tarefa via PowerShell (ajuste o caminho do projeto):

```powershell
$proj    = "C:\Sandbox\Pessoal\TeamsNotes"
$action  = New-ScheduledTaskAction -Execute "$proj\run_daily.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At 00:10
# StartWhenAvailable: roda assim que possível se a máquina estava desligada à meia-noite.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "TeamsNotes Diario" -Action $action -Trigger $trigger -Settings $settings
```

> **Primeira autenticação**: rode `python scheduler/run_nightly.py` manualmente uma vez
> para autorizar (device code). O token fica em cache e é renovado sozinho — depois disso
> a tarefa roda sem interação. A máquina precisa estar ligada/acordada (ou use
> `-StartWhenAvailable`/`-WakeToRun`, como acima).

### Linux/macOS (cron)

```cron
10 0 * * * /caminho/.venv/bin/python /caminho/scheduler/run_nightly.py >> /caminho/nightly.log 2>&1
```

---

## Notas sobre LLM e cota

- O provider é trocável a qualquer momento via `LLM_PROVIDER` no `.env`.
- O cliente trata **rate limit (429)** com retry e backoff; em cota **diária**
  esgotada ele falha rápido (não fica esperando). O processamento é **retomável**:
  dias já feitos são pulados, então um backfill grande pode ser feito em bateladas.
- Em planos gratuitos com poucas requisições por minuto, defina `LLM_MIN_INTERVAL`
  (ex.: `7`) para espaçar as chamadas e evitar 429.

---

## Estrutura

```
auth/         autenticação (MSAL, device code, cache de token)
extractor/    cliente do Microsoft Graph (listar chats, buscar mensagens)
processor/    classificação via LLM + montagem dos briefings em Markdown
storage/      camada SQLite (mensagens, cursores, itens classificados)
exporter/     geração do arquivo HTML (templates CSS/JS)
scheduler/    entry point do pipeline e utilitário de reset
config.py     parâmetros e variáveis de ambiente
```

---

## Privacidade

Todo o conteúdo permanece **local**. O `.gitignore` já exclui o `.env`, o cache de
token, os bancos SQLite, os briefings e os dumps de log — nada disso é versionado.
Avalie a política da sua empresa antes de enviar conteúdo de conversas para APIs de
LLM externas.

---

## Referências

- [Microsoft Graph — Chats](https://learn.microsoft.com/graph/api/chat-list-messages)
- [MSAL Python](https://github.com/AzureAD/microsoft-authentication-library-for-python)
- [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer)
