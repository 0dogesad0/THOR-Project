# Thor Agent

Chat AI de coding **100% offline e portátil** para Windows — estilo opencode/Cursor. Sem internet, sem instalações, sem contas. **Vê imagens** e trabalha em **parceria Thor + OpenCode**.

> **A chegar do GitHub?** Este repo contém só o código (~50 KB). Para funcionar faltam 2 coisas: os **runtimes** (release v1.0) e o **modelo IA** — as instruções completas estão em [Instalação do zero](#instalação-do-zero-vindo-do-github).

---

## Instalação do zero (vindo do GitHub)

Tudo o que falta para o Thor ficar a funcionar — são **2 downloads** além do código:

### 1. Descarregar o código

- Na página do repo: botão verde **Code → Download ZIP**, ou `git clone <url-do-repo>`
- Extrai/entra na pasta do projeto

### 2. Descarregar os runtimes (release v1.0)

Na página do repo, vai a **Releases → v1.0 → Assets** e descarrega os ficheiros:

| Asset (zip) | Contém | Onde extrair |
|---|---|---|
| `runtime…zip` | llama.cpp CPU **+ Python portátil** | pasta `runtime/` (obrigatório — sem ele nada corre) |
| `runtime-gpu…zip` | llama.cpp CUDA (NVIDIA) | pasta `runtime-gpu/` (opcional) |
| `runtime-vulkan…zip` | llama.cpp Vulkan (AMD/Intel) | pasta `runtime-vulkan/` (opcional) |

Como extrair: cria a pasta correspondente dentro do projeto e descompacta **o conteúdo do zip lá dentro** — no fim tens `runtime\llama-server.exe`, `runtime\python.exe`, etc.

- **Mínimo para funcionar**: só o `runtime` (CPU) — corre em qualquer PC Windows.
- **Com GPU**: extrai também os outros dois — o chat escolhe sozinho (CUDA → Vulkan → CPU).
- Sem Python nenhum instalado? O `runtime` traz o Python portátil; se o teu zip não o incluir, instala [Python 3.12+](https://www.python.org/downloads/) que o `start.bat` também o deteta.

### 3. Descarregar o modelo IA (~5.8 GB) — com visão

O modelo não cabe no git nem na release. Duplo-clique em **`install_model.bat`**
(faz os 2 downloads de uma vez e retoma se cair) — ou descarrega à mão:

| Ficheiro | URL | Onde pôr |
|---|---|---|
| `Qwen3VL-8B-Instruct-Q4_K_M.gguf` (5.03 GB) | `https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf` | `model/model.gguf` |
| `mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf` (0.75 GB) | `https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf` | `model/mmproj.gguf` |

- É uma só vez: **sem internet depois disto**
- O `mmproj` é o **projetor de visão** — sem ele o chat funciona mas não vê imagens
- Sem `install_model.bat`? Podes copiar os 2 ficheiros de outra instalação do Thor

### 4. Correr

Duplo-clique em **`start.bat`** — o chat abre sozinho no navegador (`http://127.0.0.1:8090/`).

Se algo falhar: `start.bat` diz em falta qual dos 3 (modelo, runtime, python).

---

## Como usar

1. Copia a pasta `thor-portable` para o destino (ex.: `C:\Users\<user>\Desktop\thor-portable`).
2. Duplo-clique em **`start.bat`**.
3. Espera o modelo carregar (10–30 s) — abre-se sozinho o chat no navegador (`http://127.0.0.1:8090/`).
4. Define a pasta de trabalho no topo do chat, escreve o pedido e pressiona Enter.

Modo terminal (antigo): usa **`start_cli.bat`**.

Dentro do chat podes anexar ficheiros de **qualquer tipo** com o botao **`＋`**
ao lado do campo de texto — ou simplesmente **arrastando-os para a janela**, ou
colando com **Ctrl+V**. Texto/codigo vai inline no pedido; imagens sao **vistas
pelo modelo**; binarios ficam em `.thor/uploads/` e o agente opera-os por caminho.

---

## O que inclui

| Componente | Detalhe |
|---|---|
| **Modelo IA** | `Qwen3-VL-8B-Instruct` (Q4_K_M, GGUF, 5.03 GB) + `mmproj` (0.75 GB) — **vê imagens**, tool calling e código |
| **Runtime LLM** | llama.cpp `b11753`: **CUDA** (`runtime-gpu/`) + **Vulkan** (`runtime-vulkan/`) + **CPU** (`runtime/`) — o chat escolhe o melhor automaticamente (da release v1.0) |
| **Motor de agente** | **Thor + OpenCode** em parceria (ver secção própria) — `opencode/` com o SDK embebido, 100% local |
| **Python** | Python 3.12.10 embeddable (portátil, sem instalar) |
| **Interface** | Chat web no navegador (start.bat) + modo terminal CLI (start_cli.bat) — Python stdlib apenas |
| **Portas locais** | `8080` = llama-server (modelo), `8090` = chat web — so acessiveis na tua maquina |
| **Tamanho total** | ~7 GB (modelo + runtimes CPU+GPU + scripts) |
| **Dependências externas** | Nenhuma em runtime — funciona sem internet e sem software pré-instalado |

O Qwen3-VL suporta **tool calling nativo** (function calling) e **visão**: o
agente decide quando ler, escrever, editar ou executar comandos — e também
quando *olhar* para uma imagem que lhe envias.

---

## Motor de agente: Thor + OpenCode (parceria)

Nao sao dois agentes separados — **trabalham juntos em cada pedido**:

```
tu escreves o pedido
      │
      ▼
 ┌─────────────┐  fase 1   o Thor analisa e DECIDE quem trata
 │     THOR    │──────────► direct  → o Thor responde com as suas ferramentas
 │  (recebe e  │           delegate → distribui a tarefa ao OpenCode
 │ distribui)  │  fase 2   eventos em tempo real: delta / tool_start / tool_result
 └─────────────┘  fase 3   o Thor INTEGRA o resultado e escreve a resposta final
      │
      ▼
 ┌─────────────┐
 │  OpenCode   │  agente de ficheiros/codigo: executa os passos que o Thor
 │  (executa)  │  lhe atribuiu, com as ferramentas dele
 └─────────────┘
```

- **O Thor e o orquestrador**: recebe, decide, distribui e integra. Tu falas
  sempre com o Thor — nunca precisas de escolher "um ou outro".
- Se o OpenCode falhar ou nao estiver instalado, **o Thor assume a tarefa ele
  proprio** (o pedido nunca fica por atender).
- Tudo passa pelos **mesmos eventos SSE de sempre** (`start`, `delta`,
  `tool_start`, `tool_result`, `info`, `error`, `stopped`, `done`) — o ⏹ Parar,
  os separadores e o historico continuam a funcionar igual.
- Se o OpenCode nao estiver instalado, o badge do topo mostra so `· Thor` e a
  parceria fica desligada sem quebrar nada.

### Instalar o motor OpenCode (opcional, 1 vez)

Precisa de internet **so uma vez** (~420 MB); depois corre 100% local:

```
opencode\install.bat
```

Instala o `bun.exe` portatil e os pacotes `@opencode/sdk` + `@opencode/ai`.

### Como se garante que e offline

| Camada | Garantia |
|---|---|
| `opencode/host.ts` | **guarda de fetch** injetada: so deixa `127.0.0.1` / `localhost` / `::1` — tudo o resto devolve `503` e fica registado no log |
| `models: { fetch: false }` | o OpenCode nao vai buscar a lista de modelos a models.dev |
| env `OPENCODE_DISABLE_*` | desliga autoupdate, models fetch e share |
| `opencode.json` | `share: "disabled"`, `update: "disable"`, permissoes `allow` (sem pedidos de aprovacao — quem aprova e o proprio Thor) |
| Modelo | llama-server so escuta em `127.0.0.1:8080` |

No arranque o servidor imprime `Parceria ativa: Thor (recebe e distribui) + OpenCode (executa) — 100% local`.

---

## Motor de inferência: Auto / GPU / CPU

No topo do chat ha um seletor **Motor** (fica ao lado do estado do modelo):

| Modo | Comportamento |
|---|---|
| **Auto** (predefinição) | Tenta **CUDA** (NVIDIA, `runtime-gpu/`), depois **Vulkan** (AMD/Intel/NVIDIA, `runtime-vulkan/`), e por fim a **CPU** (`runtime/`, 16 threads) |
| **GPU** | Força GPU (qualquer vendor); se todas falarem, cai para CPU com aviso |
| **CPU** | Força o modo CPU (util quando queres poupar a GPU para outro programa) |

Velocidades medidas nesta máquina (RTX 3090, resposta ~50 tokens):

| Motor | Geração | Processamento do pedido |
|---|---|---|
| GPU RTX 3090 (24 GB, CUDA) | **~100 tok/s** | **~1100–3100 tok/s** |
| CPU (16 threads) | ~4–5 tok/s | ~9 tok/s |

Ou seja: **~20x mais rapido na GPU** — uma resposta de 150 tokens demora ~1.5 s em vez de ~30 s.

Suporte por fabricante de GPU:

| GPU | Backend usado | Requisitos |
|---|---|---|
| NVIDIA (ex.: RTX 4060) | CUDA (`runtime-gpu/`) | driver NVIDIA recente — sem instalar o CUDA Toolkit |
| AMD (ex.: RX 6000/7000, Radeon 780M) | Vulkan (`runtime-vulkan/`) | driver AMD atualizado |
| Intel (ex.: Arc, Iris Xe) | Vulkan (`runtime-vulkan/`) | driver Intel atualizado |
| Sem GPU / tudo falhar | CPU (`runtime/`) | apenas ~8 GB de RAM |

Detalhes:

- A escolha fica guardada em `.thor/backend` e vale para os proximos arranques.
- Mudar de motor reinicia o modelo (~10 s GPU, ~40 s CPU); **nao** desliga o chat.
- O chat pergunta ao Windows (`nvidia-smi` / WMI) que GPUs existem e escolhe o melhor backend automaticamente.
- O modelo usa ~5 GB (+0.75 GB do projetor de visão) — cabe por completo em GPUs de 8 GB+ VRAM (`-ngl 99`). Com menos VRAM, o arranque GPU falha e o chat cai para CPU sozinho.
- O chat mostra no estado o backend efectivo: `GPU·CUDA`, `GPU·VULKAN` ou `CPU`.
- Se o llama-server morrer, o chat **reinicia-o sozinho** no modo escolhido.
- Podes apagar `runtime-gpu/` ou `runtime-vulkan/` que nao usems — o Auto salta para a proxima opcao.

---

## Funcionalidades do agente

### 1. Manipulação de ficheiros e diretórios

| Ferramenta | Descrição |
|---|---|
| `list_dir` | Lista o conteúdo de um diretório (respeita `.gitignore`, ignora `node_modules`, `.git`, etc.) |
| `read_file` | Lê ficheiros com numeração de linhas, offset e limite |
| `write_file` | Cria ficheiros novos ou substitui o conteúdo total |
| `edit_file` | Edição precisa: substitui **uma** ocorrência exata de texto (`old_string` → `new_string`) |
| `apply_patch` | Aplica patches de bloco (linhas `-` removidas, `+` adicionadas, contexto) — evita reescrever ficheiros inteiros |
| `delete_file` | Elimina ficheiros (com confirmação se o modo estiver ON) |
| `copy_file` | Copia ficheiros ou diretórios |
| `move_file` | Move/renomeia ficheiros ou diretórios |
| `create_directory` | Cria diretórios (e pais) |

### 2. Navegação e pesquisa no código

| Ferramenta | Descrição |
|---|---|
| `glob_files` | Procura ficheiros por padrão (`*.py`, `**/*.tsx`, …) |
| `grep_search` | Procura texto ou regex no conteúdo dos ficheiros |

Ambas **respeitam o `.gitignore`** e ignoram automaticamente `node_modules`, `.git`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `target`, `.cache`, etc.

### 3. Execução de comandos (Windows)

| Ferramenta | Descrição |
|---|---|
| `run_command` | Executa qualquer comando shell (PowerShell/CMD via shell) com timeout e captura de stdout/stderr/exit code |
| `run_command` + `background=true` | Lança processos longos em segundo plano (servidores, builds) |
| `list_background` | Lista processos em background |
| `collect_background` | Obtém a saída de um processo em background |

**Deteção de ambientes** (`detect_environments`): identifica automaticamente venv/conda ativos, `package.json` (npm), `pyproject.toml`/`requirements.txt` (Python), `Cargo.toml` (Rust) e ficheiros Docker (`Dockerfile`, `docker-compose.yml`, …).

Comandos destrutivos (`del`, `Remove-Item`, `format`, `taskkill`, …) **pedem confirmação** quando o modo de confirmação está ON (por omissão).

### 4. Gestão de tarefas

| Ferramenta | Descrição |
|---|---|
| `todowrite` | Grava/actualiza uma lista de tarefas em `.thor/todo.json` (estado: `pending`, `in_progress`, `completed`, `cancelled`; prioridade: `high`/`medium`/`low`) |
| `todo_read` | Lê a lista actual de tarefas |

Útil para tarefas multi-passos: o agente cria a lista, vai actualizando o estado e resume no final.

### 5. Desfazer (undo)

| Ferramenta | Descrição |
|---|---|
| `undo_last` | Reverte a **última** alteração de ficheiro (write, edit, patch, delete, move) para o estado anterior |

Snapshots ficam em `.thor/undo/` (podes apagar essa pasta quando quiseres libertar espaço).

### 6. Web (opcional — requer internet)

| Ferramenta | Descrição |
|---|---|
| `websearch` | Pesquisa via DuckDuckGo (HTML lite) |
| `webfetch` | Descarrega e extrai texto de uma página web (documentação, APIs, erros) |

Estas ferramentas são as **únicas** que usam rede. Todo o resto funciona offline.

---

## Chat web (interface principal)

Abrido pelo `start.bat` em `http://127.0.0.1:8090/`:

- **Separadores como num browser** — botao `+` (ou `Ctrl+T`) cria nova conversa; fecha com `✕` ou `Ctrl+W`; cada separador tem o seu historico, rascunho e anexos; o titulo auto-muda com a primeira mensagem.
- **Barra de pasta de trabalho** no topo — colas o caminho (ex.: `C:\Users\me\projeto`) e todas as acoes do agente acontecem la. O botao **📂 Escolher…** abre o seletor nativo do Windows.
- **Texto em tempo real** — a resposta do modelo aparece token a token (streaming).
- **⏹ Parar** — botao vermelho ao lado de Enviar: aborta o raciocinio do modelo **naquele momento**, mantendo o servidor e o chat vivos. O que ja foi gerado fica na conversa.
- **Streaming das ferramentas** — cada chamada aparece em tempo real (nome, argumentos, resultado), como no opencode.
- **Anexar com `＋`, arrastar ou colar** — 3 formas de levar ficheiros ao chat:
  - **`＋`** (ao lado do campo) → *Ficheiros…* (seleção múltipla), *Uma pasta inteira…* ou *Colar da área de transferência*;
  - **arrastar** qualquer ficheiro (ou vários de uma vez) por cima da janela — aparece uma zona de destaque;
  - **Ctrl+V** com uma imagem na área de transferência.
  Texto/código é lido e injetado no pedido, **imagens são vistas pelo modelo**
  (com miniatura no chip do anexo) e binários ficam em `.thor/uploads/`.
- **Leitura de imagens** — o modelo descreve, lê texto/OCR, compara e raciocina
  sobre as imagens que enviares. Podes anexar várias de uma vez (até 8 por pedido).
- **Estado do modelo** (verde = pronto) + **seletor Motor** (Auto/GPU/CPU — ver seccao acima).
- Se o llama-server morrer a meio, o chat **reinicia-o automaticamente** no modo escolhido.

---

## Comandos do chat

| Comando | Efeito | Web | CLI |
|---|---|---|---|
| `/help` | Mostra a ajuda | sim | sim |
| `/clear` | Limpa o historico da conversa | sim | sim |
| `/undo` | Desfaz a ultima alteracao de ficheiro | sim | sim |
| `/env` | Mostra ambientes detetados (venv, conda, docker, npm, ...) | sim | sim |
| `/todo` | Mostra a lista de tarefas | sim | sim |
| `/confirm on\|off` | Liga/desliga o modo de confirmacao para acoes destrutivas | sim | sim |
| `/cwd <caminho>` | Muda o diretorio de trabalho (na web usa a barra do topo) | nao | sim |
| `/exit` | Sai (o start_cli.bat encerra o servidor do modelo) | nao | sim |

---

## Estrutura da pasta

```
thor-portable/
├── start.bat            ← entrada: chat web (abre no navegador; o modelo e gerido pelo server.py)
├── start_cli.bat        ← modo terminal (CLI)
├── install_model.bat    ← 1x: descarrega o modelo Qwen3-VL + projetor de visao (~5.8 GB)
├── agent_core.py        ← nucleo do agente (streaming, tool calling, visao, parceria)
├── agent.py             ← loop do chat em terminal (usa agent_core)
├── server.py            ← servidor HTTP (API + backends CPU/GPU + uploads + SSE + parceria)
├── chat.html            ← interface (separadores, ⏹ Parar, arrastar/soltar, botao ＋)
├── tools.py             ← todas as ferramentas (ficheiros, terminal, web, undo…)
├── opencode_bridge.py   ← ponte Python <-> host OpenCode (spawna, relays, traduz eventos)
├── model/
│   ├── model.gguf       ← Qwen3-VL-8B-Instruct Q4_K_M (5.03 GB) — codigo + visao
│   ├── mmproj.gguf      ← projetor de visao (0.75 GB) — sem ele nao ha imagens
│   └── textonly.gguf    ← (opcional) modelo antigo so-texto, backup
├── opencode/            ← motor OpenCode embebido (opencode/install.bat 1x)
│   ├── install.bat      ← instala bun + @opencode/sdk + @opencode/ai
│   ├── host.ts          ← host embebido: protocolo stdio + guarda de rede offline
│   ├── opencode.json    ← provider "thor" -> 127.0.0.1:8080, permissoes allow
│   ├── package.json     ← dependencias do host (@opencode/sdk, @opencode/ai)
│   └── bun.lock         ← versoes congeladas (reproduzivel)
├── runtime/             ← CPU: llama-server.exe + Python 3.12 portatil + DLLs
├── runtime-gpu/         ← GPU NVIDIA: llama-server.exe CUDA + cuBLAS (apagar = sem CUDA)
└── runtime-vulkan/      ← GPU AMD/Intel/NVIDIA: llama-server.exe Vulkan (apagar = sem Vulkan)
```

Ficheiros gerados durante o uso (podes apagar quando quiseres):

```
.thor/
├── todo.json          ← lista de tarefas
├── undo/              ← snapshots para /undo
├── uploads/           ← imagens e ficheiros anexados no chat web
├── backend            ← motor escolhido (auto|cpu|gpu)
├── llama_backend      ← ultimo motor com que o llama arrancou
├── opencode.log       ← log do host OpenCode (diagnostico da parceria)
├── llama_spawn.log    ← log de arranque do modelo (diagnostico)
└── server.out         ← saida do servidor (se arrancado por script)
```

---

## Requisitos de máquina

- Windows 10/11 x64 (ou superior)
- ~8 GB de RAM livres (o modelo usa ~6 GB em CPU; na GPU fica na VRAM)
- ~8 GB de disco livres (runtimes + modelo + projetor de visão)
- **Não** precisa de: internet (a não ser para os 2 downloads de instalação), Python instalado, Visual C++, contas ou chaves de API
- **Opcional**: GPU — NVIDIA (CUDA), AMD/Intel (Vulkan) com 8 GB+ VRAM e driver atual — aí corre ~10x mais rapido

---

## Trocar o modelo

1. Descarrega outro `.gguf` **com visão** (tem de vir com o ficheiro `mmproj-*.gguf`)
   — ex.: `Qwen3-VL-4B-Instruct`, `Qwen3-VL-8B-Instruct`, `Qwen2.5-VL-7B-Instruct`.
2. Coloca o modelo em `model/model.gguf` e o projetor em `model/mmproj.gguf`.
3. Corre `start.bat` — o agente deteta-os automaticamente.

So texto (sem visão): podes pôr qualquer `.gguf` com tool calling em
`model/model.gguf` e **apagar** `model/mmproj.gguf` — o chat arranca na mesma,
so nao ve imagens (o badge deixa de dizer `👁 vê imagens`).

Modelos maiores = respostas melhores, mas mais lentos em CPU/GPU.

---

## Licenças

- **Agente Thor (agent_core.py, agent.py, server.py, chat.html, tools.py, opencode_bridge.py, docs)**: MIT (ver `LICENSE`)
- **OpenCode (`@opencode/sdk`, `@opencode/ai`)**: distribuído pelo seu próprio licenciamento (vendored em `opencode/node_modules/`, não vai para o git)
- **llama.cpp**: MIT (incluido nos runtimes)
- **OpenMP (LLVM)**: Apache-2.0
- **Qwen3-VL**: Apache-2.0 (model weights — redistribuir com essa licenca)
- **Python**: PSF License
