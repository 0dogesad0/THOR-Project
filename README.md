# Thor Agent

Chat AI de coding **100% offline e portátil** para Windows — estilo opencode/Cursor. Sem internet, sem instalações, sem contas.

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

### 3. Descarregar o modelo IA (~4.7 GB)

O modelo não cabe no git nem na release — download directo do HuggingFace:

```
https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf
```

- Cria a pasta `model/` (se não existir) e coloca lá o ficheiro **com o nome `model.gguf`**
- É uma só vez: ~4.7 GB, demora conforme a internet (~5 min a 150 Mbps)

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

Dentro do chat podes anexar ficheiros com o botao 📎 (texto e codigo sao lidos e enviados ao modelo; imagens sao guardadas em `.thor/uploads/` — o modelo actual so processa texto).

---

## O que inclui

| Componente | Detalhe |
|---|---|
| **Modelo IA** | `Qwen2.5-Coder-7B-Instruct` (quantização Q4_K_M, GGUF, ~4.7 GB) — especializado em código e tool calling |
| **Runtime LLM** | llama.cpp `b11753`: **CUDA** (`runtime-gpu/`) + **Vulkan** (`runtime-vulkan/`) + **CPU** (`runtime/`) — o chat escolhe o melhor automaticamente (da release v1.0) |
| **Python** | Python 3.12.10 embeddable (portátil, sem instalar) |
| **Interface** | Chat web no navegador (start.bat) + modo terminal CLI (start_cli.bat) — Python stdlib apenas |
| **Portas locais** | `8080` = llama-server (modelo), `8090` = chat web — so acessiveis na tua maquina |
| **Tamanho total** | ~5.5 GB (modelo + runtimes CPU+GPU + scripts) |
| **Dependências externas** | Nenhuma — funciona sem internet e sem software pré-instalado |

O modelo Qwen2.5 suporta **tool calling nativo** (function calling), o que permite ao agente decidir quando ler, escrever, editar ou executar comandos.

---

## Motor de inferência: Auto / GPU / CPU

No topo do chat ha um seletor **Motor** (fica ao lado do estado do modelo):

| Modo | Comportamento |
|---|---|
| **Auto** (predefinição) | Tenta **CUDA** (NVIDIA, `runtime-gpu/`), depois **Vulkan** (AMD/Intel/NVIDIA, `runtime-vulkan/`), e por fim a **CPU** (`runtime/`, 16 threads) |
| **GPU** | Força GPU (qualquer vendor); se todas falarem, cai para CPU com aviso |
| **CPU** | Força o modo CPU (util quando queres poupar a GPU para outro programa) |

Velocidades medidas nesta maquina (resposta ~150 tokens):

| Motor | Geração | Processamento do pedido |
|---|---|---|
| GPU RTX 4060 (8 GB, CUDA) | **~44 tok/s** | **~105 tok/s** |
| CPU (16 threads) | ~4.2 tok/s | ~9 tok/s |

Ou seja: **~10x mais rapido na GPU** — uma resposta de 150 tokens demora ~4 s em vez de ~40 s.

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
- O modelo usa ~4.7 GB — cabe por completo em GPUs de 8 GB+ VRAM (`-ngl 99`). Com menos VRAM, o arranque GPU falha e o chat cai para CPU sozinho.
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
- **Anexar ficheiros** (📎) — texto/codigo e lido e injetado no pedido; imagens sao guardadas em `.thor/uploads/`.
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
├── start.bat          ← entrada: chat web (abre no navegador; o modelo e gerido pelo server.py)
├── start_cli.bat      ← modo terminal (CLI)
├── agent_core.py      ← nucleo do agente (streaming, tool calling, paragem, caminhos Windows)
├── agent.py           ← loop do chat em terminal (usa agent_core)
├── server.py          ← servidor HTTP do chat web (API + backends CPU/GPU + uploads + SSE)
├── chat.html          ← interface do chat (separadores, ⏹ Parar, seletor Motor)
├── tools.py           ← todas as ferramentas (ficheiros, terminal, web, undo…)
├── model/
│   └── model.gguf     ← Qwen2.5-Coder-7B-Instruct Q4_K_M (~4.7 GB)
├── runtime/           ← CPU: llama-server.exe + Python 3.12 portatil + DLLs
├── runtime-gpu/       ← GPU NVIDIA: llama-server.exe CUDA 13.4 + cuBLAS (apagar = sem CUDA)
└── runtime-vulkan/    ← GPU AMD/Intel/NVIDIA: llama-server.exe Vulkan (apagar = sem Vulkan)
```

Ficheiros gerados durante o uso (podes apagar quando quiseres):

```
.thor/
├── todo.json          ← lista de tarefas
├── undo/              ← snapshots para /undo
├── uploads/           ← imagens anexadas no chat web
├── backend            ← motor escolhido (auto|cpu|gpu)
├── llama_backend      ← ultimo motor com que o llama arrancou
└── llama_spawn.log    ← log de arranque do modelo (diagnostico)
```

---

## Requisitos de máquina

- Windows 10/11 x64 (ou superior)
- ~8 GB de RAM livres (o modelo usa ~5–6 GB em CPU; na GPU fica na VRAM)
- ~6 GB de disco livres
- **Não** precisa de: internet, Python instalado, Visual C++, contas ou chaves de API
- **Opcional**: GPU — NVIDIA (CUDA), AMD/Intel (Vulkan) com 8 GB+ VRAM e driver atual — aí corre ~10x mais rapido

---

## Trocar o modelo

1. Descarrega outro `.gguf` com suporte a tool calling (ex.: `Qwen2.5-3B-Instruct`, `Qwen2.5-Coder-7B`).
2. Coloca-o em `model/` (podes apagar o `model.gguf` actual ou renomear).
3. Corre `start.bat` — o agente deteta automaticamente o `.gguf` nessa pasta.

Modelos maiores = respostas melhores, mas mais lentos em CPU.

---

## Publicar no GitHub

O `.gitignore` mantem o repo leve (~50 KB de codigo) — **nao** entra no git:

- `model/*.gguf` (~4.7 GB — o GitHub recusa ficheiros >100 MB)
- `runtime/`, `runtime-gpu/`, `runtime-vulkan/` (~2 GB de binarios)
- `.thor/` (estado local: tarefas, undo, uploads, logs)

Como esta distribuido (repo + release v1.0):

1. **Repo (git)**: so o codigo + `LICENSE` + `README.md` — quem clona segue a seccao *Instalação do zero*.
2. **Release v1.0 (Assets)**: os 3 zips dos runtimes (`runtime`, `runtime-gpu`, `runtime-vulkan`).
3. **Modelo**: link directo do HuggingFace no README (4.7 GB, nao passa pelo GitHub).

Actualizacoes de codigo: commit + push no GitHub Desktop; se mudarem os runtimes, criar uma nova release (v1.1...) com os zips novos.

---

## Licenças

- **Agente Thor (agent_core.py, agent.py, server.py, chat.html, tools.py, docs)**: MIT (ver `LICENSE`)
- **llama.cpp**: MIT (incluido nos runtimes)
- **OpenMP (LLVM)**: Apache-2.0
- **Qwen2.5**: Apache-2.0 (model weights — redistribuir com essa licenca)
- **Python**: PSF License
