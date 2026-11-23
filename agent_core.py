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

from tools import OLLAMA_TOOLS, execute_tool, load_image

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

BIG FILE RULE (critical — violating this truncates your call):
NEVER send a whole large file in ONE write_file: the generation gets CUT and the
call arrives as truncated/invalid arguments (the tool will answer "Argumentos
TRUNCADOS"). Max ~250 lines per write_file. For a bigger file: write the first
part, then append the rest in small blocks with edit_file.

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
NEVER call read_file on an image or binary file (.jpg .jpeg .png .gif .webp
.pdf .zip .gguf ...) — it is not text and read_file will only return a warning.
If image parts are present you already see the pixels; use them directly.
IMAGE ON DISK: to SEE an image that exists in the working folder (not attached
to the message), call view_image {"path": "nome.jpg"}. The pixels are attached
to the conversation right after the tool result and from then on you see them —
then read the text (OCR), describe and use the content. Never try to read an
image as text and never run OCR tools when view_image exists.

WORKSPACE RULE:
Work ONLY inside the current working directory: create/edit/delete files there
using simple relative names. Reads outside are allowed, writes are NOT — any
write outside the workspace is rejected. Existing data files (CSV, JSON, images,
etc.) belong to the user: READ them, never modify or delete them; put results
in NEW files only.

COMMAND RULE (Windows):
The shell cuts multi-line commands. NEVER send python -c with the code spanning
several lines — it returns exit_code 0 with EMPTY output and you will be stuck
repeating it. To run multi-line Python: 1) write_file "script.py" with the
full code, then 2) run_command "python script.py". Then read the stdout.
If a tool result says LOOP DETETADO, you MUST change approach — repeating the
same call can never produce a different result.
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
    max_tokens: int = 8192,
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
                # JSON incompleto/ilegivel (quebras de linha literais dentro do
                # content, ou geracao cortada a meio). Guarda TUDO — o antigo
                # [:500] destruia o conteudo do ficheiro — e deixa parse_args
                # reconstruir os argumentos.
                tc["function"]["arguments"] = {"_raw": raw_args[:400_000]}
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
        if name not in ("write_file", "read_file", "view_image", "edit_file", "create_directory",
                        "delete_file", "copy_file", "move_file", "run_command", "list_dir",
                        "glob_files", "grep_search", "apply_patch", "todowrite", "undo_last"):
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


# ---------------------------------------------------------------------------
# Reconstrucao de argumentos de ferramenta
#
# O modelo local emite JSON de argumentos com defeitos recorrentes:
#   1. quebras de linha LITERAIS dentro do "content" (json.loads recusa);
#   2. geracao cortada a meio por max_tokens (JSON sem fechar);
#   3. argumentos a chegar como string solta (extraidos do texto).
# Todos acabavam em {"_raw": ...} e da para fn(**{"_raw": ...}) nascia o
# "unexpected keyword argument '_raw'" que o modelo repetia 5x = loop.
# ---------------------------------------------------------------------------

_ARG_KEYS = (
    "path", "content", "old_string", "new_string", "patch", "command",
    "pattern", "query", "url", "source", "destination", "file_glob",
    "offset", "limit", "timeout", "background", "max_results", "max_chars",
    "index", "todos",
)


def _escape_control_chars(s: str) -> str:
    """Escapa quebras de linha/tabs LITERAIS dentro de strings JSON.

    Escrever um ficheiro .py significa meter \\n reais no JSON — o modelo
    local emite-os como saltos de linha proprios e o json.loads falha."""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            continue
        if esc:
            out.append(ch)
            esc = False
        elif ch == "\\":
            out.append(ch)
            esc = True
        elif ch == '"':
            out.append(ch)
            in_str = False
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _repair_json(raw: str) -> dict[str, Any] | None:
    """JSON direto -> reparado (quebras literais, virgulas finais) -> prefixo."""
    s = (raw or "").strip()
    if not s:
        return None
    if not s.startswith("{"):
        i = s.find("{")
        if i < 0:
            return None
        s = s[i:]
    for cand in (s, _escape_control_chars(s)):
        cand = re.sub(r",\s*([}\]])", r"\1", cand)  # virgulas finais
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        try:  # primeiro objeto completo, com lixo a seguir
            obj, _ = json.JSONDecoder().raw_decode(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _extract_field(raw: str, key: str) -> Any:
    """Le um campo do JSON partido: string (com escapes, ou cortada ao meio),
    objeto/array (varredura de profundidade) ou escalar."""
    m = re.search(r"""['"]%s['"]\s*:\s*""" % re.escape(key), raw)
    if not m:
        return None
    i = m.end()
    if i >= len(raw):
        return None
    ch = raw[i]
    if ch in "\"'":
        out: list[str] = []
        j = i + 1
        while j < len(raw):
            c = raw[j]
            if c == "\\" and j + 1 < len(raw):
                nxt = raw[j + 1]
                out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
                j += 2
                continue
            if c == ch:
                return "".join(out)
            out.append(c)
            j += 1
        return "".join(out)  # string sem fechar: o conteudo vai ate ao fim
    if ch in "{[":
        depth = 0
        for j in range(i, len(raw)):
            if raw[j] in "{[":
                depth += 1
            elif raw[j] in "}]":
                depth -= 1
                if depth == 0:
                    frag = raw[i:j + 1]
                    try:
                        return json.loads(frag)
                    except json.JSONDecodeError:
                        return None
        return None
    m2 = re.match(r"[^,}\]\s]+", raw[i:])
    if m2:
        frag = m2.group(0)
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            return frag.rstrip(",")
    return None


def _recover_args(raw: Any) -> dict[str, Any] | None:
    """Reconstroi os argumentos de uma chamada a partir de JSON partido."""
    text = str(raw or "")
    if not text.strip():
        return None
    obj = _repair_json(text)
    if obj:
        return obj
    fields: dict[str, Any] = {}
    for key in _ARG_KEYS:
        val = _extract_field(text, key)
        if val is not None and val != "":
            fields[key] = val
    if not fields:
        return None
    if not text.strip().endswith(("}", "]")):
        # JSON sem fechar = geracao cortada: o conteudo esta incompleto e
        # escreve-lo em silencio criava ficheiros/edicoes a meio.
        fields["_truncated"] = True
    return fields


def parse_args(raw_args: Any) -> dict[str, Any]:
    """Argumentos de uma chamada de ferramenta, mesmo quando o JSON veio
    partido. Devolve sempre um dict utilizavel; "_truncated": True marca
    conteudo cortado (o chamador decide — nunca executa pela metade)."""
    if isinstance(raw_args, dict):
        if "_raw" in raw_args:
            rec = _recover_args(raw_args.get("_raw"))
            if rec:
                extras = {k: v for k, v in raw_args.items() if k != "_raw"}
                extras.update(rec)
                return extras
            return {"_truncated": True}
        return dict(raw_args)
    if raw_args is None:
        return {}
    if isinstance(raw_args, str):
        s = raw_args.strip()
        if not s:
            return {}
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        rec = _recover_args(s)
        if rec:
            return rec
        return {"_truncated": True}
    return {"_truncated": True}


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


def _looks_like_error(result: str) -> bool:
    """True se o resultado de uma ferramenta e um ERRO (nao uma saida normal).

    Usado pelo anti-loop de resultados: so repeticoes de ERRO contam — uma
    leitura repetida com sucesso e uso normal das ferramentas."""
    r = result or ""
    return (
        r.startswith("[erro]")
        or "exit_code: 1" in r
        or "Traceback (most recent call last)" in r
        or "[LOOP DETETADO]" in r
    )


def _error_hint(result: str) -> str:
    """Linha-chave de um traceback (ex.: \"KeyError: 'Mes'\") para o aviso de
    loop trazer a CAUSA concreta em vez de so 'nao repitas'."""
    m = re.findall(r"^[\w.]*(?:Error|Exception|Interrupted):.*$", result or "", re.M)
    if m:
        return m[-1].strip()[:200]
    for ln in reversed((result or "").splitlines()):
        if ln.strip():
            return ln.strip()[:200]
    return ""


def run_turn_events(messages: list[dict[str, Any]], user_text: str,
                    images: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
    """Executa um turno do agente e produz eventos:
    info | delta (texto em tempo real) | tool_start | tool_result | stopped | error.

    `images` = anexos em base64 que o modelo passa a VER (via mmproj)."""
    STOP_EVENT.clear()
    messages.append(prepare_user_message(user_text, images))
    had_tools = False
    # guarda anti-loop:
    #  - last_sig/same_count : a MESMA chamada 2x seguidas (loop direto);
    #  - sig_history         : a MESMA chamada a repetir no meio de OUTRAS
    #    (padrao alternado write_file <-> run_command que passava ao lado do
    #    detector de "2x seguidas" e gastava os 40 turnos todos iguais);
    #  - block_count         : quantas vezes ja foi bloqueada uma repeticao;
    #    na 4a o turno fecha em vez de so avisar.
    last_sig: str | None = None
    same_count = 0
    sig_history: list[str] = []
    res_history: list[str] = []
    block_count = 0
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
            pending_images: list[dict[str, Any]] = []
            for tc in reply["tool_calls"]:
                fn = tc.get("function") or {}
                name = fn.get("name", "?")
                call_id = tc.get("id", f"call_{int(time.time()*1000)}")
                args = parse_args(fn.get("arguments") or {})
                # "_truncated" = o modelo nao chegou a fechar o JSON (geracao
                # cortada). NUNCA se executa pela metade: escrever um content
                # incompleto corrompia o ficheiro e o modelo seguia como se
                # estivesse tudo feito.
                truncated = bool(args.pop("_truncated", False))
                preview = (
                    "<argumentos truncados/incompletos>" if truncated
                    else json.dumps(args, ensure_ascii=False)
                )
                sig = name + " " + preview
                sig_history.append(sig)
                if sig == last_sig:
                    same_count += 1
                else:
                    last_sig, same_count = sig, 0
                # 3a vez da MESMA chamada — seja 2x seguidas, seja a repetir no
                # meio de outras (padrao alternado A,B,A,B que o detector antigo
                # nao apanhava). Nao executa: o resultado ja e conhecido.
                repeats = sig_history[-6:].count(sig)  # inclui a atual
                if repeats >= 3:
                    block_count += 1
                    yield {"type": "tool_start", "name": name, "args": preview}
                    if block_count >= 4 or repeats >= 5:
                        result = (
                            "[LOOP DETETADO — TURNO ENCERRADO] Esta chamada ja "
                            f"apareceu {repeats} vezes (bloqueio n.º {block_count}) "
                            "e o resultado e sempre identico, por isso paro aqui. "
                            "Na resposta final, diz ao utilizador o que conseguiste "
                            "fazer e o que ficou por fazer."
                        )
                        yield {"type": "tool_result", "name": name, "result": result}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": result,
                        })
                        yield {
                            "type": "info",
                            "content": (
                                "o modelo repetiu a mesma chamada 4x — a parar o "
                                "turno para nao gastar iteracoes."
                            ),
                        }
                        return
                    onde = ("3x seguidas" if same_count >= 2
                            else f"ja e a {repeats}a vez nos últimos {len(sig_history[-6:])} chamadas, "
                                 "mesmo alternando com outras")
                    result = (
                        "[LOOP DETETADO] Esta chamada repete-se: "
                        f"{onde} — o resultado sera SEMPRE identico, por isso "
                        "NAO a repitas. Muda a CAUSA do problema: se uma "
                        "execucao falhou, le o erro/traceback e corrige o CODIGO "
                        "(coluna, separador, caminho) antes de voltar a correr; "
                        "se uma escrita falhou, corrige os argumentos. Se ja tens "
                        "informacao suficiente, responde ao utilizador."
                    )
                    yield {"type": "tool_result", "name": name, "result": result}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result,
                    })
                    # mantem last_sig: qualquer repeticao seguinte fica bloqueada
                    continue
                yield {"type": "tool_start", "name": name, "args": preview}
                if truncated:
                    result = (
                        "[erro] Argumentos TRUNCADOS: a chamada chegou cortada "
                        "a meio (JSON incompleto) — o conteudo nao chegou todo. "
                        "NAO reenvies esta mesma chamada grande. Divide o "
                        "trabalho: write_file com a primeira parte (max ~250 "
                        "linhas) e acrescenta o resto em blocos com edit_file; "
                        "em leituras usa read_file com offset/limit."
                    )
                elif name == "view_image":
                    # o agente pede para VER uma imagem do disco: carrega os
                    # pixels e anexa-os a conversa (ver pending_images abaixo)
                    img = load_image(str(args.get("path") or ""))
                    if isinstance(img, str):
                        result = img  # erro (nao existe / nao e imagem)
                    else:
                        pending_images.append(img)
                        result = (
                            f"[imagem carregada] {img['name']} "
                            f"({img['mime']}) — os pixels seguem ANEXADOS a "
                            "conversa depois destas ferramentas. A partir de "
                            "agora ves a imagem: le o texto (OCR), descreve e "
                            "usa o conteudo para continuar o trabalho."
                        )
                else:
                    result = execute_tool(name, args)
                # MESMO resultado de ERRO3x seguidas, mesmo com argumentos
                # diferentes: o modelo esta a voltar ao mesmo sem corrigir a
                # causa (ex.: sempre o mesmo KeyError). So entao e que este
                # ramo conta — escritas/leituras com sucesso nao sao loop.
                repeated_error = (
                    _looks_like_error(result)
                    and len(res_history) >= 2
                    and res_history[-2:] == [result, result]
                )
                res_history.append(result)
                if repeated_error:
                    block_count += 1
                    hint = _error_hint(result)
                    loop_msg = (
                        "[LOOP DETETADO] O resultado desta chamada e EXATAMENTE "
                        "igual aos 2 anteriores — o mesmo erro outra vez"
                        + (f" (causa: {hint})" if hint else "")
                        + ". NAO corras o codigo outra vez sem o alterar: corrige "
                        "a causa concreta (coluna em falta, separador ';' do CSV, "
                        "caminho) e SÓ DEPOIS volta a executar."
                    )
                    if block_count >= 4:
                        loop_msg = (
                            "[LOOP DETETADO — TURNO ENCERRADO] O mesmo erro "
                            f"repetiu-se {block_count} vezes sem correcao — "
                            "paro aqui para nao gastar iteracoes. Na resposta "
                            "final, diz ao utilizador o que conseguiste fazer e "
                            "o que ficou por fazer."
                        )
                    yield {"type": "tool_result", "name": name, "result": loop_msg}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": loop_msg,
                    })
                    if block_count >= 4:
                        yield {
                            "type": "info",
                            "content": "o modelo repetiu o mesmo erro 4x — a parar o turno.",
                        }
                        return
                else:
                    yield {"type": "tool_result", "name": name, "result": result}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result,
                    })
            # IMAGENS DEPOIS de todos os tool results: o chat_stream so manda
            # os pixels da mensagem mais recente que os tem (img_owner), por
            # isso esta mensagem tem de ser a ULTIMA da ronda.
            if pending_images:
                messages.append({
                    "role": "user",
                    "content": (
                        "[view_image: os pixels das imagens pedidas seguem nesta "
                        "mensagem — ves-as agora. Faz OCR/descricao e continua "
                        "o trabalho com o que la esta escrito.]"
                    ),
                    "images": pending_images,
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
