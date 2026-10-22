"""Ferramentas do agente Thor: ficheiros, diretórios, comandos, web e undo."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse

THOR_DIR = ".thor"
UNDO_DIR = THOR_DIR + "/undo"
TODO_FILE = THOR_DIR + "/todo.json"
CONFIRM_MODE = True
_BACKGROUND_PROCS: list[dict[str, Any]] = []

_DEFAULT_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".thor", ".idea", ".vscode", "dist", "build", ".next", "target", ".cache"}


def set_confirm_mode(on: bool) -> str:
    global CONFIRM_MODE
    CONFIRM_MODE = bool(on)
    return f"Modo de confirmacao: {'ON' if on else 'OFF'}"


def _root() -> Path:
    return Path.cwd()


def _resolve(path: str, root: Path | None = None) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = (root or _root()) / p
    return Path(os.path.normpath(str(p)))


def _thor_dir() -> Path:
    d = _root() / THOR_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_gitignore(root: Path) -> set[str]:
    patterns: set[str] = set()
    gi = root / ".gitignore"
    if gi.is_file():
        try:
            for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!"):
                    patterns.add(line.rstrip("/"))
        except Exception:
            pass
    return patterns


def _is_ignored(rel: str, patterns: set[str]) -> bool:
    parts = Path(rel).parts
    if any(p in _DEFAULT_IGNORE for p in parts):
        return True
    name = Path(rel).name
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
        if not pat.startswith("/") and fnmatch.fnmatch(rel, f"*/{pat}"):
            return True
    return False


def _snapshot(path: Path, action: str) -> str | None:
    try:
        undo_root = _root() / UNDO_DIR
        undo_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        digest = hashlib.md5(str(path).encode()).hexdigest()[:8]
        entry_dir = undo_root / f"{stamp}-{digest}"
        entry_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "action": action,
            "path": str(path),
            "timestamp": time.time(),
        }
        if path.is_file():
            shutil.copy2(path, entry_dir / "backup")
            meta["type"] = "file"
        else:
            meta["type"] = "missing"
        (entry_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return str(entry_dir)
    except Exception:
        return None


def _confirm(prompt: str) -> bool:
    if not CONFIRM_MODE:
        return True
    try:
        ans = input(f"  \x1b[93m[confirmar] {prompt} (s/N):\x1b[0m ").strip().lower()
        return ans in ("s", "sim", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def detect_environments() -> str:
    envs: list[str] = []
    cwd = _root()
    if (cwd / ".venv").is_dir() or (cwd / "venv").is_dir():
        envs.append("venv local (.venv/venv)")
    conda = os.environ.get("CONDA_DEFAULT_ENV")
    if conda:
        envs.append(f"conda env: {conda}")
    if os.environ.get("VIRTUAL_ENV"):
        envs.append(f"VIRTUAL_ENV: {os.environ['VIRTUAL_ENV']}")
    for docker in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "Dockerfile"):
        if (cwd / docker).is_file():
            envs.append(f"docker: {docker}")
            break
    if (cwd / "package.json").is_file():
        envs.append("node/npm (package.json)")
    if (cwd / "pyproject.toml").is_file() or (cwd / "requirements.txt").is_file():
        envs.append("python (pyproject/requirements)")
    if (cwd / "Cargo.toml").is_file():
        envs.append("rust (Cargo.toml)")
    return "\n".join(envs) if envs else "Nenhum ambiente especial detetado."


def list_dir(path: str = ".") -> str:
    target = _resolve(path)
    if not target.exists():
        return f"[erro] Nao existe: {target}"
    if target.is_file():
        return f"{target.name}  ({target.stat().st_size} bytes)"
    patterns = _load_gitignore(target if target.is_dir() else target.parent)
    lines = []
    for entry in sorted(target.iterdir(), key=lambda e: (e.is_file(), e.name.lower())):
        if entry.name == THOR_DIR:
            continue
        rel = str(entry.relative_to(_root())) if entry.is_relative_to(_root()) else entry.name
        if entry.is_dir() and entry.name in _DEFAULT_IGNORE:
            continue
        if entry.is_file() and _is_ignored(rel, patterns):
            continue
        marker = "/" if entry.is_dir() else ""
        lines.append(f"{entry.name}{marker}")
    return "\n".join(lines) if lines else f"[vazio] {target}"


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    target = _resolve(path)
    if not target.exists():
        return f"[erro] Nao existe: {target}"
    if target.is_dir():
        return f"[erro] E um diretorio: {target}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[erro] {e}"
    lines = text.splitlines()
    total = len(lines)
    chunk = lines[offset : offset + limit]
    if not chunk:
        return f"[vazio] {target} (total {total} linhas)"
    body = "\n".join(f"{i:5d}| {line}" for i, line in enumerate(chunk, start=offset + 1))
    return f"{target}  (linhas {offset + 1}-{offset + len(chunk)} de {total})\n{body}"


def write_file(path: str, content: str) -> str:
    target = _resolve(path)
    existed = target.is_file()
    if existed:
        _snapshot(target, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    nlines = content.count("\n") + 1 if content else 0
    return f"{'Atualizado' if existed else 'Criado'}: {target} ({target.stat().st_size} bytes, {nlines} linhas)"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    target = _resolve(path)
    if not target.exists():
        return f"[erro] Nao existe: {target}"
    text = target.read_text(encoding="utf-8", errors="replace")
    count = text.count(old_string)
    if count == 0:
        return "[erro] old_string nao encontrada"
    if count > 1:
        return f"[erro] old_string aparece {count} vezes; torna-a unica"
    _snapshot(target, "edit")
    target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8", newline="\n")
    return f"Editado: {target} (1 ocorrencia substituida)"


def apply_patch(path: str, patch: str) -> str:
    """Aplica um patch de bloco: linhas de contexto, -removidas e +adicionadas.
    Encontra o bloco (contexto + remocoes) no ficheiro e substitui-o por (contexto + adicoes)."""
    target = _resolve(path)
    if not target.exists():
        return f"[erro] Nao existe: {target}"
    original = target.read_text(encoding="utf-8", errors="replace").splitlines()
    old_block: list[str] = []
    new_block: list[str] = []
    removed = added = 0
    for pl in patch.splitlines():
        if pl.startswith("---") or pl.startswith("+++") or pl.startswith("@@"):
            continue
        if pl.startswith("-"):
            old_block.append(pl[1:])
            removed += 1
        elif pl.startswith("+"):
            new_block.append(pl[1:])
            added += 1
        elif pl.startswith(" "):
            old_block.append(pl[1:])
            new_block.append(pl[1:])
        else:
            old_block.append(pl)
            new_block.append(pl)
    if not old_block and not new_block:
        return "[erro] Patch vazio."
    if old_block:
        for start in range(len(original) - len(old_block) + 1):
            if original[start : start + len(old_block)] == old_block:
                result = original[:start] + new_block + original[start + len(old_block) :]
                _snapshot(target, "patch")
                target.write_text("\n".join(result) + ("\n" if result else ""), encoding="utf-8", newline="\n")
                return f"Patch aplicado em {target}: -{removed} +{added} linhas"
        preview = old_block[0][:80] if old_block else "?"
        return f"[erro] Bloco do patch nao encontrado (comeca com: {preview!r})"
    result = original + new_block
    _snapshot(target, "patch")
    target.write_text("\n".join(result) + "\n", encoding="utf-8", newline="\n")
    return f"Patch aplicado em {target}: +{added} linhas (append)"


def delete_file(path: str) -> str:
    target = _resolve(path)
    if not target.exists():
        return f"[erro] Nao existe: {target}"
    if target.is_dir():
        return f"[erro] E um diretorio (nao apago): {target}"
    if not _confirm(f"Eliminar {target}?"):
        return "Cancelado pelo utilizador."
    _snapshot(target, "delete")
    size = target.stat().st_size
    target.unlink()
    return f"Eliminado: {target} ({size} bytes)"


def copy_file(source: str, destination: str) -> str:
    src = _resolve(source)
    dst = _resolve(destination)
    if not src.exists():
        return f"[erro] Origem nao existe: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return f"Diretorio copiado: {src} -> {dst}"
    shutil.copy2(src, dst)
    return f"Ficheiro copiado: {src} -> {dst}"


def move_file(source: str, destination: str) -> str:
    src = _resolve(source)
    dst = _resolve(destination)
    if not src.exists():
        return f"[erro] Origem nao existe: {src}"
    if src.is_file():
        _snapshot(src, "move")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Movido: {src} -> {dst}"


def run_command(command: str, timeout: int = 120, background: bool = False) -> str:
    dangerous = bool(re.search(r"\b(remove|rmdir|rd|del|format|Remove-Item|Clear-Content|Stop-Process|taskkill)\b", command, re.I))
    if dangerous and not _confirm(f"Executar comando potencialmente destrutivo: {command}?"):
        return "Cancelado pelo utilizador."
    if background:
        try:
            proc = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", cwd=str(_root()),
            )
        except Exception as e:
            return f"[erro] {e}"
        info = {"pid": proc.pid, "cmd": command, "started": time.time(), "proc": proc}
        _BACKGROUND_PROCS.append(info)
        return f"Lancado em background (pid {proc.pid}): {command}\nUsa list_background/collect_background para ver o estado."
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=str(_root()),
        )
    except subprocess.TimeoutExpired:
        return f"[erro] Timeout {timeout}s: {command}"
    parts = [f"exit_code: {proc.returncode}"]
    if proc.stdout:
        out = proc.stdout if len(proc.stdout) <= 8000 else proc.stdout[:8000] + "\n... [truncado]"
        parts.append("stdout:\n" + out)
    if proc.stderr:
        err = proc.stderr if len(proc.stderr) <= 4000 else proc.stderr[:4000] + "\n... [truncado]"
        parts.append("stderr:\n" + err)
    return "\n".join(parts)


def list_background() -> str:
    if not _BACKGROUND_PROCS:
        return "Nenhum processo em background."
    lines = []
    for i, info in enumerate(_BACKGROUND_PROCS):
        running = info["proc"].poll() is None
        state = "a correr" if running else f"terminou (rc={info['proc'].poll()})"
        lines.append(f"[{i}] pid={info['pid']} {state}  {info['cmd'][:80]}")
    return "\n".join(lines)


def collect_background(index: int = 0) -> str:
    if index < 0 or index >= len(_BACKGROUND_PROCS):
        return f"[erro] Indice invalido {index}. Ha {len(_BACKGROUND_PROCS)} processos."
    info = _BACKGROUND_PROCS[index]
    proc = info["proc"]
    try:
        out, _ = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return f"[ainda a correr] pid={info['pid']}  {info['cmd'][:80]}"
    if out and len(out) > 8000:
        out = out[:8000] + "\n... [truncado]"
    return f"pid={info['pid']} exit={proc.returncode}\n{out or '(sem saida)'}"


def glob_files(pattern: str, path: str = ".") -> str:
    root = _resolve(path)
    if not root.exists():
        return f"[erro] Nao existe: {root}"
    patterns = _load_gitignore(root)
    matches = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        if _is_ignored(rel, patterns):
            continue
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
            matches.append(rel)
            if len(matches) >= 200:
                matches.append("... [limite 200]")
                break
    return "\n".join(matches) if matches else f"Nenhum ficheiro para: {pattern}"


def grep_search(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    root = _resolve(path)
    if not root.exists():
        return f"[erro] Nao existe: {root}"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"[erro] Regex invalida: {e}"
    gi_patterns = _load_gitignore(root)
    results: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file() or not fnmatch.fnmatch(p.name, file_glob):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        if _is_ignored(rel, gi_patterns):
            continue
        try:
            if p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                results.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(results) >= 100:
                    results.append("... [limite 100]")
                    return "\n".join(results)
    return "\n".join(results) if results else f"Nada encontrado para: {pattern}"


def create_directory(path: str) -> str:
    target = _resolve(path)
    target.mkdir(parents=True, exist_ok=True)
    return f"Diretorio criado/ok: {target}"


def todowrite(todos: list[dict[str, Any]]) -> str:
    """Substitui a lista de tarefas. Cada item: {content, status: pending|in_progress|completed|cancelled, priority}."""
    valid = {"pending", "in_progress", "completed", "cancelled"}
    cleaned = []
    for t in todos:
        status = t.get("status", "pending")
        if status not in valid:
            status = "pending"
        cleaned.append({
            "content": str(t.get("content", ""))[:200],
            "status": status,
            "priority": str(t.get("priority", "medium")),
        })
    path = _root() / TODO_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"  [{t['status'][:4]}] {t['content']}" for t in cleaned]
    return f"Todo list atualizada ({len(cleaned)} itens):\n" + ("\n".join(lines) if lines else "  (vazia)")


def todo_read() -> str:
    path = _root() / TODO_FILE
    if not path.is_file():
        return "Sem lista de tarefas."
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"[erro] {e}"
    if not items:
        return "Lista vazia."
    lines = [f"  [{t.get('status', '?')[:4]}] {t.get('content', '')}" for t in items]
    return "\n".join(lines)


def undo_last() -> str:
    undo_root = _root() / UNDO_DIR
    if not undo_root.is_dir():
        return "Nada para desfazer."
    entries = sorted([d for d in undo_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    if not entries:
        return "Nada para desfazer."
    entry = entries[0]
    meta_path = entry / "meta.json"
    if not meta_path.is_file():
        return f"[erro] meta.json em falta em {entry}"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    target = Path(meta["path"])
    action = meta.get("action", "?")
    backup = entry / "backup"
    if action == "delete" or not backup.exists():
        if target.exists():
            target.unlink()
        shutil.rmtree(entry, ignore_errors=True)
        return f"Desfeito ({action}): {target} eliminado novamente."
    if backup.is_file():
        shutil.copy2(backup, target)
    shutil.rmtree(entry, ignore_errors=True)
    return f"Desfeito ({action}): {target} restaurado ao estado anterior."


def websearch(query: str, max_results: int = 5) -> str:
    """Pesquisa na web via DuckDuckGo (HTML lite)."""
    url = f"https://lite.duckduckgo.com/lite/?q={quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[erro] Websearch falhou: {e}"
    results: list[str] = []
    for m in re.finditer(r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if "duckduckgo.com" in href:
            continue
        results.append(f"- {title}\n  {href}")
        if len(results) >= max_results:
            break
    if not results:
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not title or "duckduckgo" in href:
                continue
            results.append(f"- {title}\n  {href}")
            if len(results) >= max_results:
                break
    return f"Resultados para '{query}':\n" + ("\n".join(results) if results else "(sem resultados)")


def webfetch(url: str, max_chars: int = 15000) -> str:
    """Descarrega uma pagina web e extrai texto legivel."""
    if not urlparse(url).scheme:
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read()
    except Exception as e:
        return f"[erro] webfetch falhou: {e}"
    if "pdf" in ctype:
        return f"[info] PDF ({len(raw)} bytes) nao suportado diretamente. Usa run_command com uma ferramenta local se necessario."
    html = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncado]"
    return f"URL: {url}\nConteudo:\n{text}"


TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "list_dir": list_dir,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "apply_patch": apply_patch,
    "delete_file": delete_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "run_command": run_command,
    "glob_files": glob_files,
    "grep_search": grep_search,
    "create_directory": create_directory,
    "todowrite": todowrite,
    "todo_read": todo_read,
    "undo_last": undo_last,
    "websearch": websearch,
    "webfetch": webfetch,
    "detect_environments": detect_environments,
    "list_background": list_background,
    "collect_background": collect_background,
}


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

OLLAMA_TOOLS: list[dict[str, Any]] = [
    _fn("list_dir", "MUST use to list folder contents. Shows files and subfolders.", {"path": S}),
    _fn("read_file", "MUST use to read a file's content with line numbers.", {"path": S, "offset": I, "limit": I}, ["path"]),
    _fn("write_file", "MUST use to CREATE a new file or OVERWRITE an existing one. Takes full file content.", {"path": S, "content": S}, ["path", "content"]),
    _fn("edit_file", "MUST use to MODIFY an existing file. Replaces one unique occurrence of old_string with new_string.", {"path": S, "old_string": S, "new_string": S}, ["path", "old_string", "new_string"]),
    _fn("apply_patch", "Apply a multi-line patch: lines with - removed, + added, space = context.", {"path": S, "patch": S}, ["path", "patch"]),
    _fn("delete_file", "MUST use to DELETE a file.", {"path": S}, ["path"]),
    _fn("copy_file", "Copy a file or folder to another path.", {"source": S, "destination": S}, ["source", "destination"]),
    _fn("move_file", "Move or rename a file/folder.", {"source": S, "destination": S}, ["source", "destination"]),
    _fn("run_command", "MUST use to run a Windows shell command (install, test, build, run scripts).", {"command": S, "timeout": I, "background": B}, ["command"]),
    _fn("glob_files", "MUST use to find files by pattern like *.py or **/*.js.", {"pattern": S, "path": S}, ["pattern"]),
    _fn("grep_search", "MUST use to search text/regex inside files.", {"pattern": S, "path": S, "file_glob": S}, ["pattern"]),
    _fn("create_directory", "MUST use to CREATE a folder (and parents).", {"path": S}, ["path"]),
    _fn("todowrite", "Save a task list. Items: {content, status: pending|in_progress|completed|cancelled, priority}.", {"todos": {"type": "array", "items": {"type": "object", "properties": {"content": S, "status": S, "priority": S}, "required": ["content", "status"]}}}, ["todos"]),
    _fn("todo_read", "Read the current task list.", {}, []),
    _fn("undo_last", "MUST use to undo the last file change (write/edit/delete/patch/move).", {}, []),
    _fn("websearch", "Search the web (DuckDuckGo). Needs internet.", {"query": S, "max_results": I}, ["query"]),
    _fn("webfetch", "Download and extract text from a URL. Needs internet.", {"url": S, "max_chars": I}, ["url"]),
    _fn("detect_environments", "Detect venv, conda, docker, npm, python, rust in cwd.", {}, []),
    _fn("list_background", "List background processes.", {}, []),
    _fn("collect_background", "Get output of a background process by index.", {"index": I}, []),
]


def execute_tool(name: str, args: dict[str, Any]) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"[erro] Ferramenta desconhecida: {name}"
    try:
        return str(fn(**args))
    except TypeError as e:
        return f"[erro] Argumentos invalidos para {name}: {e}"
    except Exception as e:
        return f"[erro] {type(e).__name__}: {e}"
