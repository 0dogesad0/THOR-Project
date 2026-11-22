"""Núcleo do agente — partilhado pelo CLI (agent.py) e pelo chat web (server.py)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from tools import OLLAMA_TOOLS, execute_tool

# A consola Windows usa cp1252 por omissão: sem isto, qualquer print com ⏹/emoji
# (vindo do utilizador ou do modelo) levanta UnicodeEncodeError e mata o turno.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HOST = "http://127.0.0.1:8080"
MAX_ITERATIONS = 40
STOP_EVENT = threading.Event()


class StopRequested(Exception):
    """Pedido de paragem pelo utilizador — aborta a geracao actual."""


def load_model_name() -> str:
    """Nome do modelo a servir.

    Ha 2 .gguf na pasta model/: model.gguf (o MiMo — modelo de linguagem) e
    mmproj.gguf (projetor de visao — NAO e um modelo de linguagem). Sem a
    preferencia explicita, a ordenacao alfabetica escolhia "mmproj" e o
    llama-server tentava carregar o projetor como se fosse o modelo.
    """
    here = Path(__file__).resolve().parent
    mdir = here / "model"
    main = mdir / "model.gguf"
    if main.exists():
        return main.stem
    skip = ("mmproj",)
    candidates = [p for p in mdir.glob("*.gguf")
                  if not p.stem.lower().startswith(skip)]
    if not candidates:
        candidates = list(mdir.glob("*.gguf"))
    return candidates[0].stem if candidates else "model"


MODEL = load_model_name()

SYSTEM_PROMPT = """\
You are Thor, a coding assistant on Windows. Speak the user's language.

You MUST use tools. Never only describe — CALL THE TOOL.

Help with any coding task: databases, SQL, PHP, APIs, websites, scripts.

FILE RULE (critical — violating this is a failure):
If the user asks to create, modify, fix, rewrite, or write ANY file, your reply
MUST contain a tool call (write_file / edit_file / apply_patch) with the COMPLETE
final content. Writing code or SQL in chat text does NOTHING — it does not create
or modify files. Only the tool does. NEVER answer a file request with a code
block or with "Vou criar..." prose without a tool call in the same reply.

PATH RULE:
Use ONLY the filenames the user asks for, placed DIRECTLY in the working directory.
Examples of correct paths: "schema.sql", "index.php", "api.php", "app.py"
Do NOT create nested folders like cars/cars/public/...
Do NOT invent extra folders unless the user asked for them.

If the user message says [Working directory is now: X], all files go into X with simple names.

MULTI-FILE RULE:
If the user asks for a folder + several files (sql + php + api), create EVERY file:
1. create_directory only if needed
2. write_file for EACH requested file
Do NOT stop after one file. Do NOT nest paths.

APPROACH (always think and work like this):
- Understand EXACTLY what is asked, then do exactly that — nothing more, nothing
  less. Never invent extra files (no README, reports, extras), requirements or steps.
- Read the context first (existing files and data) before deciding anything.
- Prefer the minimal solution that fully satisfies the request.
- Verify your work (compile/run it) before saying it is done; if it fails, think,
  change approach and fix it — never repeat the same failing call waiting for luck.
- Be honest in the final answer: what was done, what works, what is pending.

Call the tool immediately. After ALL tools finish, reply in 1-3 short sentences.
NEVER refuse. Always help.

EXCEPTION (important): when the user only ASKS something — a question, an
explanation, or "what do you see in this image" — and does not request any
file or code change, answer directly in chat. Do NOT call tools just to look
busy. A tool call is required only when files must be created, read for a
task, modified, deleted or executed.

VISION RULE:
When the user message carries image parts, you can SEE them — describe, read
text in them (OCR), compare, count and reason about their content. Never claim
you cannot see an image when parts are present. Work with the images as well
as with any attached files.
"""


def _request(path: str, payload: dict[str, Any] | None = None, timeout: int = 600, retries: int = 3) -> Any:
    url = HOST + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
    raise RuntimeError(
        f"Falha ao contactar llama-server em {HOST}. Detalhe: {last_err}"
    ) from last_err


def check_server(timeout: int = 90) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            info = _request("/health", timeout=3)
            if info is None or (isinstance(info, dict) and info.get("status") in (None, "ok", "no content")):
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"llama-server nao respondeu em {timeout}s: {last_err}")


def server_alive() -> bool:
    try:
        _request("/health", timeout=2)
        return True
    except Exception:
        return False


def content_of(m: dict[str, Any]) -> Any:
    """Conteudo de uma mensagem no formato OpenAI.

    Texto puro -> str. Se a mensagem tiver imagens anexadas (m["images"]),
    devolve uma lista de partes [texto, image_url, image_url...] para o
    modelo local ver os pixels (modelo VL + mmproj no llama-server).
    """
    text = m.get("content") or ""
    imgs = m.get("images") or []
    if not imgs:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for im in imgs:
        data = im.get("data")
        if not data:
            continue
        mime = im.get("mime") or "image/png"
        url = im.get("url") or f"data:{mime};base64,{data}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1:  # so texto: o modelo espera string, nao lista vazia
        return text
    return parts


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: bool = True,
    max_tokens: int = 4096,
) -> Iterator[dict[str, Any]]:
    """Gera {"delta": texto}* seguido de {"message": {...}}. Emite StopRequested se pedirem paragem.

    Usa stream=true no llama-server: permite mostrar texto a aparecer em tempo real
    e abortar logo que o utilizador carregue em Parar (fechar a ligacao faz o
    llama-server parar de gerar do lado dele).
    system/tools/max_tokens: chamadas leves do orquestrador (decisão de distribuição
    e integração final) correm sem ferramentas e com poucos tokens."""
    openai_msgs: list[dict[str, Any]] = [{"role": "system", "content": system or SYSTEM_PROMPT}]
    # imagens SO na mensagem mais recenta que as tem: cada imagem custa ~1k
    # tokens e o contexto e limitado — reenviar as antigas a cada turno enche-o.
    img_owner: dict[str, Any] | None = None
    for m in messages:
        if m.get("images"):
            img_owner = m
    for m in messages:
        if m.get("role") == "system":
            continue
        entry: dict[str, Any] = {"role": m["role"], "content": ""}
        if m is img_owner:
            entry["content"] = content_of(m)
        else:
            entry["content"] = m.get("content") or ""
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            entry["tool_call_id"] = m["tool_call_id"]
        if m.get("name") and m["role"] == "tool":
            entry["name"] = m["name"]
        openai_msgs.append(entry)

    payload = {
        "model": MODEL,
        "messages": openai_msgs,
        "temperature": 0.1,
        "stream": True,
        "max_tokens": max_tokens,
    }
    if tools:
        tools_payload = []
        for t in OLLAMA_TOOLS:
            fn = t["function"]
            tools_payload.append({
                "type": "function",
                "function": {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        payload["tools"] = tools_payload
        payload["tool_choice"] = "auto"
    data_bytes = json.dumps(payload).encode("utf-8")
    url = HOST + "/v1/chat/completions"
    last_err: Exception | None = None
    resp: Any = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=600)
            break
        except urllib.error.HTTPError as e:
            last_err = e
            # 400/413 = pedido invalido (ex.: imagens a estourar o contexto) —
            # repetir 3x so gasta tempo e nao resolve.
            if e.code in (400, 401, 403, 404, 413, 422):
                raise RuntimeError(
                    f"Falha ao contactar llama-server em {HOST}. Detalhe: {e}"
                ) from e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Falha ao contactar llama-server em {HOST}. Detalhe: {last_err}"
            ) from last_err
        except (urllib.error.URLError, ConnectionResetError, ConnectionAbortedError,
                BrokenPipeError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Falha ao contactar llama-server em {HOST}. Detalhe: {last_err}"
            ) from last_err

    content_parts: list[str] = []
    acc_calls: dict[int, dict[str, Any]] = {}
    try:
        assert resp is not None
        for raw in resp:
            if STOP_EVENT.is_set():
                raise StopRequested()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                yield {"delta": piece}
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index") or 0)
                slot = acc_calls.setdefault(idx, {
                    "id": f"call_{idx}_{int(time.time() * 1000)}",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                args_piece = fn.get("arguments")
                if isinstance(args_piece, str) and args_piece:
                    cur = slot["function"]["arguments"]
                    if isinstance(cur, str):
                        slot["function"]["arguments"] = cur + args_piece
                    else:
                        slot["function"]["arguments"] = args_piece
                elif isinstance(args_piece, dict):
                    slot["function"]["arguments"] = args_piece
    finally:
        try:
            resp.close()
        except Exception:
            pass

    content = "".join(content_parts)
    tool_calls = [acc_calls[i] for i in sorted(acc_calls)]
    for tc in tool_calls:
        raw_args = tc["function"].get("arguments") or "{}"
        if isinstance(raw_args, str):
            try:
                tc["function"]["arguments"] = json.loads(raw_args)
            except json.JSONDecodeError:
                tc["function"]["arguments"] = {"_raw": raw_args[:500]}
    if not tool_calls and content:
        tool_calls = _extract_tool_calls_from_text(content)
    yield {"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}


def chat(messages: list[dict[str, Any]], echo: bool = False, **kw: Any) -> dict[str, Any]:
    """Variante nao-streaming (CLI): junta os deltas e devolve a mensagem final.
    **kw segue para chat_stream (system/tools/max_tokens)."""
    reply: dict[str, Any] | None = None
    for item in chat_stream(messages, **kw):
        if "delta" in item and echo:
            sys.stdout.write(item["delta"])
            sys.stdout.flush()
        elif "message" in item:
            reply = item["message"]
    if reply is None:
        return {"role": "assistant", "content": "", "tool_calls": []}
    return reply


def _extract_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for m in re.finditer(r'"name"\s*:\s*"([a-z_]+)"', text):
        name = m.group(1)
        if name not in ("write_file", "read_file", "edit_file", "create_directory", "delete_file",
                        "copy_file", "move_file", "run_command", "list_dir", "glob_files",
                        "grep_search", "apply_patch", "todowrite", "undo_last"):
            continue
        start = text.rfind("{", 0, m.start())
        if start < 0:
            continue
        depth = 0
        end = -1
        for i in range(start, min(start + 8000, len(text))):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            continue
        try:
            obj = json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
        if obj.get("name") != name:
            continue
        args = obj.get("arguments") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        calls.append({
            "id": f"call_{len(calls)}_{int(time.time()*1000)}",
            "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, dict) else {"_raw": str(args)}},
        })
    return calls


def parse_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if not raw_args:
        return {}
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            pass
        m = re.search(r'"path"\s*:\s*"([^"]+)"', raw_args)
        c = re.search(r'"content"\s*:\s*"(.*)"\s*}', raw_args, re.S)
        if m:
            args: dict[str, Any] = {"path": m.group(1)}
            if c:
                args["content"] = c.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            elif '"command"' in raw_args:
                mc = re.search(r'"command"\s*:\s*"([^"]+)"', raw_args)
                if mc:
                    args = {"command": mc.group(1)}
            return args
    return {"_raw": str(raw_args)[:500]}


_WIN_PATH_RE = re.compile(r"([A-Za-z]:\\(?:[^\\\s\"'\n\r]+\\)*[^\\\s\"'\n\r]+)")
_STOPWORDS = {
    "com", "uma", "base", "dados", "carros", "php", "front", "end", "api",
    "sql", "para", "comunicacao", "tudo", "funcional", "cria", "criar",
    "ficheiro", "ficheiros", "pasta", "pastas", "o", "a", "os", "as",
    "de", "do", "da", "em", "no", "na", "e", "ou", "que",
}


def extract_target_dir(user_text: str) -> str | None:
    m = _WIN_PATH_RE.search(user_text)
    if not m:
        return None
    parts = m.group(1).split("\\")
    kept = [parts[0]]
    for seg in parts[1:]:
        low = seg.lower().strip(".,;:")
        if low in _STOPWORDS or (len(low) > 1 and " " in seg):
            break
        kept.append(seg)
    if len(kept) < 2:
        return None
    p = "\\".join(kept).rstrip("\\/.,;:")
    return p if len(p) >= 6 else None


def prepare_user_message(user_text: str, images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Mensagem do utilizador; `images` (base64) fica anexada para o modelo ver."""
    target_dir = extract_target_dir(user_text)
    if target_dir:
        try:
            p = Path(target_dir)
            p.mkdir(parents=True, exist_ok=True)
            os.chdir(p)
            cleaned = user_text.replace(target_dir, "a pasta actual").replace("  ", " ")
            msg = {
                "role": "user",
                "content": (
                    f"[Working directory is now: {target_dir} — created if missing]\n"
                    f"{cleaned}\n"
                    "Use ONLY simple relative filenames (schema.sql, index.php, api.php). "
                    "Create EVERY file the user asked for, one write_file call each."
                ),
            }
            if images:
                msg["images"] = images
            return msg
        except Exception:
            pass
    msg = {
        "role": "user",
        "content": (
            f"{user_text}\n"
            "Create EVERY file requested. Use relative filenames. Do not stop early."
        ),
    }
    if images:
        msg["images"] = images
    return msg


_FILE_INTENT_RE = re.compile(
    r"(modific|alter|corrig|criar|cria\b|crie|escrev|grava|ficheiro|arquivo|schema|"
    r"\.sql|\.py\b|\.js\b|\.html|\.css|\.php|\.json|\.md\b|\.txt|\.bat|\.ps1|"
    r"\.ts\b|\.tsx|\.jsx|\.java|\.go|\.rs\b|\.rb\b|save|novo ficheiro|nova pasta|"
    r"append|adicion|atualiz|actualiz|update|edit|fix|refaz|refa|implement|"
    r"adiciona|renome|move|apaga|elimina|substitui|reescrev)",
    re.I,
)

_NUDGE_MSG = (
    "You did NOT call any tool. That is a failure: writing code or SQL in chat "
    "does NOT create or modify files — only tools do. In your next reply you MUST "
    "call the appropriate tool IMMEDIATELY (write_file / edit_file / apply_patch / "
    "create_directory) with the COMPLETE final content. Do not explain, do not "
    "show code blocks — CALL THE TOOL now."
)

# verbos de CRIACAO/EDICAO de ficheiros — usados para desligar o nudge quando
# o pedido e so de observar imagens (ver _needs_file_action)
_FILE_CREATE_RE = re.compile(
    r"(modific|alter|corrig|criar|cria\b|crie|escrev|grava|gravar|\bsave\b|"
    r"adicion|atualiz|actualiz|update|edit|\bfix\b|refaz|refa|implement|"
    r"renome|move|apaga|elimina|substitui|reescrev|novo ficheiro|nova pasta|"
    r"append|gera\b|gerar|produz|constru|escreve)",
    re.I,
)


def _needs_file_action(user_text: str, has_images: bool = False) -> bool:
    """True se o pedido parece exigir escrita/edicao de ficheiros.

    `has_images`: com imagens anexadas o marcador [Ficheiro: ...] dos anexos
    enganava o detector e forcava uma escrita de ficheiros mesmo quando o
    utilizador so quis SABER o que se ve na imagem — ai a resposta certa e
    texto. So reforca se houver mesmo verbo de criacao/edicao."""
    if not _FILE_INTENT_RE.search(user_text or ""):
        return False
    if has_images:
        return bool(_FILE_CREATE_RE.search(user_text or ""))
    return True


def run_turn_events(messages: list[dict[str, Any]], user_text: str,
                    images: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
    """Executa um turno do agente e produz eventos:
    info | delta (texto em tempo real) | tool_start | tool_result | stopped | error.

    `images` = anexos em base64 que o modelo passa a VER (via mmproj)."""
    STOP_EVENT.clear()
    messages.append(prepare_user_message(user_text, images))
    had_tools = False
    try:
        for _turn in range(MAX_ITERATIONS):
            if STOP_EVENT.is_set():
                yield {"type": "stopped", "content": "⏹ interrompido pelo utilizador."}
                return
            yield {
                "type": "info",
                "content": f"a contactar o modelo (turno {_turn + 1})…",
            }
            if had_tools:
                yield {"type": "delta", "content": "\n\n"}
            try:
                reply: dict[str, Any] | None = None
                for item in chat_stream(messages):
                    if "delta" in item:
                        yield {"type": "delta", "content": item["delta"]}
                    elif "message" in item:
                        reply = item["message"]
            except StopRequested:
                yield {"type": "stopped", "content": "⏹ interrompido pelo utilizador."}
                return
            if reply is None:
                reply = {"role": "assistant", "content": "", "tool_calls": []}
            messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})
            if not reply["tool_calls"]:
                # pedidos de ficheiros SEM ferramentas = o modelo so escreveu texto:
                # reforca uma vez, senao o utilizador fica com "promessas" sem ficheiros
                if (not had_tools and _turn == 0
                        and _needs_file_action(user_text, bool(images))
                        and not STOP_EVENT.is_set()):
                    yield {
                        "type": "info",
                        "content": "o modelo respondeu so com texto — reforcar o uso de ferramentas de ficheiro…",
                    }
                    messages.append({
                        "role": "user",
                        "content": _NUDGE_MSG,
                    })
                    continue
                return
            had_tools = True
            for tc in reply["tool_calls"]:
                fn = tc.get("function") or {}
                name = fn.get("name", "?")
                call_id = tc.get("id", f"call_{int(time.time()*1000)}")
                args = parse_args(fn.get("arguments") or {})
                preview = json.dumps(args, ensure_ascii=False)
                yield {"type": "tool_start", "name": name, "args": preview}
                result = execute_tool(name, args)
                yield {"type": "tool_result", "name": name, "result": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result,
                })
        yield {"type": "info", "content": "Limite de iteracoes de ferramentas atingido."}
    except RuntimeError as e:
        yield {"type": "error", "content": str(e)}


# ============ orquestrador: o Thor recebe e distribui, o OpenCode executa ============

ORCHESTRATOR_SYS = """\
És o Thor, orquestrador de um assistente local com dois agentes:
- tu, o Thor: conversas, explicações, raciocínio e a resposta final ao utilizador;
- o OpenCode: agente especializado em ficheiros e código do projeto (ler, escrever,
  editar, organizar e executar scripts).

Nesta fase decides APENAS quem trata o pedido e, se for o OpenCode, escreves a tarefa.
Responde exatamente neste formato:

DIRETO
<1 frase curta com a tua decisão>

ou

DELEGAR
<tarefa para o OpenCode em 1-3 frases, com contexto suficiente para outro agente>
1. <passo 1>
2. <passo 2>

Regras:
- Saudações, perguntas conceptuais, explicações e conversa = DIRETO.
- Criar/editar/ler/mover/executar ficheiros ou código, debugging e tarefas no projeto = DELEGAR.
- Método: percebe o pedido EXATO primeiro (o utilizador é a especificação — não
  inventes requisitos nem ficheiros extras) e transmite na tarefa só o que é
  pedido, com a lista exata de ficheiros a criar.
- Em exercícios (especialmente vindos de imagens): escreve na tarefa o CONTEXTO —
  linguagem/tecnologia pedida, o que exatamente é pedido e os ficheiros de dados
  envolvidos — para o OpenCode não inventar HTML onde se pede Python nem misturar.
  Inclui sempre: (a) seguir o enunciado item a item / grelha de avaliação;
  (b) LER primeiro os dados existentes (estrutura real, sem assumir);
  (c) os ficheiros já existentes são do utilizador — trabalhar COM eles, nunca
  modificá-los; resultados só em ficheiros novos.
- Entre 1 e 3 passos; se for uma ação única, escreve só a tarefa sem passos.
- Máximo 8 linhas, na língua do pedido.
"""

INTEGRATE_SYS = """\
És o Thor, orquestrador. O teu agente OpenCode acabou de trabalhar num pedido do
utilizador e escreves agora TU a resposta final.
Responde apenas com o texto final para o utilizador:
- na língua do pedido, 2 a 6 frases (ou lista curta);
- o que foi feito, que ficheiros/resultados importam e o estado final;
- sem descrever o processo interno nem citar ferramentas;
- se algo falhou ou ficou pendente, diz-o claramente.
"""


def dispatch(user_text: str, can_delegate: bool = True, context: str = "",
             has_images: bool = False) -> dict[str, Any]:
    """Fase 1 da parceria: o Thor decide se responde ele proprio (direct) ou se
    distribui a tarefa ao OpenCode (delegate -> task + steps).
    Falha em seguranca para "direct": em erro, o Thor trata do pedido ele proprio.

    `has_images`: o Thor VE as imagens. Se o pedido for so descrever, responde
    DIRETO. Se for FAZER o exercicio/codigo a partir das fotos, DELEGA — o Thor
    transcreve o enunciado e o OpenCode executa sobre o TEXTO (nao sobre pixels)."""
    sys_p = ORCHESTRATOR_SYS
    if not can_delegate:
        sys_p += "\nAVISO: o OpenCode esta indisponivel agora — responde sempre DIRETO."
    elif has_images:
        sys_p += (
            "\nAVISO: ha imagens anexadas. Tu (Thor) ves os pixels; o OpenCode nao. "
            "Se o utilizador so pergunta o que se ve / pede explicacao = DIRETO. "
            "Se pede para FAZER exercicios, criar codigo, CSV, HTML, Java, Python "
            "ou qualquer ficheiro a partir das imagens = DELEGAR. Nao pecas ao "
            "OpenCode que leia os JPEG/PNG: o Thor transcreve o enunciado antes."
        )
    user = user_text
    if context:
        user = f"Conversa recente:\n{context}\n\nPedido atual:\n{user_text}"
    try:
        reply = chat([{"role": "user", "content": user}],
                     system=sys_p, tools=False, max_tokens=320)
    except StopRequested:
        raise
    except Exception:
        return {"action": "direct"}
    text = (reply.get("content") or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"action": "direct"}
    head = lines[0].lstrip("#*>-• ").upper()
    if head.startswith("DELEG"):
        brief: list[str] = []
        steps: list[str] = []
        for ln in lines[1:]:
            m = re.match(r"^\d+[.)]\s+(.+)$", ln)
            if m:
                steps.append(m.group(1).strip())
            else:
                brief.append(ln)
        task = " ".join(brief).strip() or user_text
        steps = steps[:3] or [task]
        return {"action": "delegate", "task": task, "steps": steps}
    return {"action": "direct"}


def vision_transcribe(images: list[dict[str, Any]]) -> str:
    """Le as imagens anexadas (OCR + enunciado) e devolve o texto.

    Usado ANTES de delegar ao OpenCode: ele nao ve pixels (so recebe caminhos),
    por isso o Thor le primeiro e passa o TEXTO da tarefa — a parceria mantem-se
    (Thor recebe/distribui, OpenCode executa). Devolve "" em falha/paragem.

    Uma imagem de telemovel a resolucao nativa pode gastar 7k+ tokens; 4 de
    uma vez estouram o contexto do modelo. Por isso le UMA de cada vez.
    """
    if not images:
        return ""
    sys_p = (
        "Transcreve TODO o conteúdo visível nesta imagem: texto, "
        "enunciados, exercícios, instruções, números, tabelas e estrutura. "
        "Transcreve FIELMENTE o que está escrito — NÃO inventes, NÃO completes "
        "e NÃO mistures tecnologias: se o enunciado pede Python, transcreve "
        "Python; se pede HTML, transcreve HTML. "
        "Se for um enunciado de exercício, transcreve-o por completo, item a "
        "item, incluindo o nome das linguagens/ficheiros mencionados. "
        "NÃO resolvas nem comentes. "
        "No fim, acrescenta exatamente 3 linhas de resumo:\n"
        "LINGUAGEM: <linguagem/tecnologia realmente pedida no enunciado>\n"
        "PEDIDOS: <lista curta e concreta do que é pedido, por ordem>\n"
        "DADOS: <ficheiros de dados mencionados/existentes, ou 'nenhum'>"
    )
    n = len(images)
    parts: list[str] = []
    for i, im in enumerate(images, 1):
        if STOP_EVENT.is_set():
            raise StopRequested()
        msgs: list[dict[str, Any]] = [{
            "role": "user",
            "content": f"Transcreve o conteúdo visível da imagem {i} de {n}:",
            "images": [im],
        }]
        out: list[str] = []
        try:
            for item in chat_stream(msgs, system=sys_p, tools=False, max_tokens=1024):
                if "delta" in item:
                    out.append(item["delta"])
        except StopRequested:
            raise
        except Exception as e:
            print(f"[vision] imagem {i}/{n} falhou: {e}", flush=True)
            continue
        text = "".join(out).strip()
        if text:
            parts.append(f"--- imagem {i} de {n} ---\n{text}")
    return "\n\n".join(parts)


def integrate_events(user_text: str, work: str) -> Iterator[dict[str, Any]]:
    """Fase 3 da parceria: o Thor lê o resultado do OpenCode e escreve a resposta
    final ao utilizador (deltas | stopped | error)."""
    prompt = (
        f"Pedido original do utilizador:\n{user_text}\n\n"
        f"Resultado do trabalho do agente OpenCode:\n"
        f"{(work or '(sem texto — só ações)').strip()[:4000]}\n\n"
        "Escreve agora a resposta final ao utilizador."
    )
    try:
        for item in chat_stream([{"role": "user", "content": prompt}],
                                system=INTEGRATE_SYS, tools=False, max_tokens=600):
            if "delta" in item:
                yield {"type": "delta", "content": item["delta"]}
    except StopRequested:
        yield {"type": "stopped", "content": "⏹ interrompido pelo utilizador."}
    except RuntimeError as e:
        yield {"type": "error", "content": str(e)}
