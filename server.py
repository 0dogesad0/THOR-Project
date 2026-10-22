"""Servidor do chat web local — interface gráfica estilo opencode, 100% offline."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import agent_core as core
from tools import detect_environments, execute_tool, set_confirm_mode

BASE_DIR = Path(__file__).resolve().parent
CHAT_LOCK = threading.Lock()
SESS_LOCK = threading.Lock()
SESSIONS: dict[str, dict[str, Any]] = {}  # id -> {"title": str, "messages": [...]}
WEB_PORT = 8090
MAX_UPLOAD = 10 * 1024 * 1024
LLAMA_LOCK = threading.Lock()
TEXT_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".php", ".sql", ".json", ".md",
             ".txt", ".csv", ".xml", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".bat", ".ps1",
             ".sh", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".rb", ".env", ".gitignore"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def new_session() -> str:
    sid = uuid.uuid4().hex[:12]
    with SESS_LOCK:
        SESSIONS[sid] = {"title": "Conversa", "messages": []}
    return sid


def get_session(sid: str) -> tuple[str, dict[str, Any]]:
    """Devolve (sid, sess); cria se nao existir."""
    if sid:
        with SESS_LOCK:
            sess = SESSIONS.get(sid)
            if sess is not None:
                return sid, sess
    sid = new_session()
    with SESS_LOCK:
        return sid, SESSIONS[sid]


def close_session(sid: str) -> bool:
    with SESS_LOCK:
        return SESSIONS.pop(sid, None) is not None


def set_title_if_needed(sess: dict[str, Any], message: str) -> str:
    with SESS_LOCK:
        if sess["title"] in ("", "Conversa"):
            t = " ".join(message.split())[:36]
            sess["title"] = t + ("…" if len(t) == 36 else "") or "Conversa"
        return sess["title"]


def browse_folder() -> str | None:
    """Abre o seletor de pastas nativo do Windows (via PowerShell, sem ctypes).
    Usa uma janela-guia TopMost como dono para o seletor aparecer SEMPRE à frente.
    Devolve o caminho escolhido ou None se cancelar."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$f = New-Object System.Windows.Forms.Form; "
        "$f.Text = 'Thor — escolher pasta'; "
        "$f.TopMost = $true; "
        "$f.FormBorderStyle = 'FixedToolWindow'; "
        "$f.StartPosition = 'CenterScreen'; "
        "$f.ShowInTaskbar = $true; "
        "$f.Size = New-Object System.Drawing.Size(420, 90); "
        "$lbl = New-Object System.Windows.Forms.Label; "
        "$lbl.Text = 'A escolher pasta...' + [Environment]::NewLine + 'Na janela seguinte (\"Procurar Pasta\") escolhe a pasta e pressiona OK.'; "
        "$lbl.Dock = 'Fill'; "
        "$f.Controls.Add($lbl); "
        "$f.Show(); "
        "$f.Activate(); "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description = 'Escolhe a pasta de trabalho do agente Thor'; "
        "$d.ShowNewFolderButton = $false; "
        "try { "
        "  if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath } "
        "} finally { $f.Close() }"
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=300,
    )
    out = (r.stdout or "").strip()
    if not out or not re.match(r"^[A-Za-z]:", out):
        return None
    return out.splitlines()[-1].strip()


# ---------- backends (CPU / GPU dedicada / Auto) ----------
BACKEND_FILE = BASE_DIR / ".thor" / "backend"
BACKEND_STATE_FILE = BASE_DIR / ".thor" / "llama_backend"
LLAMA_STATE: dict[str, Any] = {"proc": None, "backend": None}
_GPU_NAME: str | None = None


def read_mode() -> str:
    try:
        v = BACKEND_FILE.read_text(encoding="utf-8").strip()
        if v in ("auto", "cpu", "gpu"):
            return v
    except Exception:
        pass
    return "auto"


def write_mode(mode: str) -> None:
    try:
        BACKEND_FILE.parent.mkdir(parents=True, exist_ok=True)
        BACKEND_FILE.write_text(mode, encoding="utf-8")
    except Exception:
        pass


def _nvidia_present() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


_NVIDIA_OK: bool | None = None
_GPU_NAME: str | None = None


def gpu_name() -> str | None:
    """Nome da GPU dedicada (NVIDIA via nvidia-smi; AMD/Intel via WMI)."""
    global _GPU_NAME
    if _GPU_NAME is not None:
        return _GPU_NAME or None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0 and r.stdout.strip():
            _GPU_NAME = r.stdout.strip().splitlines()[0]
            return _GPU_NAME
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        adapters = [l.strip() for l in (r.stdout or "").splitlines()
                    if l.strip() and "microsoft" not in l.lower()]
        _GPU_NAME = adapters[0] if adapters else ""
    except Exception:
        _GPU_NAME = ""
    return _GPU_NAME or None


def best_gpu_kind() -> str:
    """Melhor backend disponivel nesta maquina: cuda (NVIDIA) | vulkan (AMD/Intel/NVIDIA) | cpu."""
    global _NVIDIA_OK
    if _NVIDIA_OK is None:
        _NVIDIA_OK = _nvidia_present()
    if _NVIDIA_OK:
        return "cuda"
    if gpu_name():
        return "vulkan"
    return "cpu"


def current_backend() -> str | None:
    """Backend efectivo do llama-server: cuda | vulkan | cpu."""
    if not core.server_alive():
        return None
    if LLAMA_STATE.get("backend"):
        return LLAMA_STATE["backend"]
    try:
        v = BACKEND_STATE_FILE.read_text(encoding="utf-8").strip()
        if v in ("cpu", "cuda", "vulkan"):
            return v
    except Exception:
        pass
    return None


def _try_spawn(kind: str, wait: int = 90) -> subprocess.Popen | None:
    model = BASE_DIR / "model" / "model.gguf"
    if kind == "cuda":
        exe = BASE_DIR / "runtime-gpu" / "llama-server.exe"
        extra = ["-ngl", "99", "-t", "8"]
    elif kind == "vulkan":
        exe = BASE_DIR / "runtime-vulkan" / "llama-server.exe"
        extra = ["-ngl", "99", "-t", "8"]
    else:
        exe = BASE_DIR / "runtime" / "llama-server.exe"
        extra = ["-t", "16"]
    if not exe.exists() or not model.exists():
        return None
    log_path = BASE_DIR / ".thor" / "llama_spawn.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(log_path, "ab")
        logf.write(f"\n--- spawn {kind} {time.strftime('%H:%M:%S')} ---\n".encode())
        logf.flush()
        proc = subprocess.Popen(
            [str(exe), "-m", str(model), "--port", "8080", "--host", "127.0.0.1",
             "-c", "8192", "--no-warmup", *extra],
            cwd=str(BASE_DIR),
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:  # processo morreu (sem GPU, DLL em falta, ...)
            return None
        if core.server_alive():
            return proc
        time.sleep(1)
    try:
        proc.kill()
    except Exception:
        pass
    return None


def _spawn_order(mode: str) -> list[str]:
    if mode == "cpu":
        return ["cpu"]
    best = best_gpu_kind()
    if mode == "gpu":
        # prefere a melhor GPU, mas tenta as outras antes de desistir para CPU
        gpus = [k for k in (best, "cuda", "vulkan") if k in ("cuda", "vulkan") and k != best]
        return [best] + gpus + ["cpu"] if best != "cpu" else ["cuda", "vulkan", "cpu"]
    # auto: melhor primeiro, depois as restantes, CPU por fim
    gpus = [k for k in ("cuda", "vulkan") if k != best]
    return ([best] + gpus + ["cpu"]) if best != "cpu" else ["cuda", "vulkan", "cpu"]


def _kill_llama_all() -> None:
    try:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                       capture_output=True, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
    LLAMA_STATE["proc"] = None
    LLAMA_STATE["backend"] = None
    deadline = time.time() + 10
    while time.time() < deadline and core.server_alive():
        time.sleep(0.5)


def ensure_llama(timeout: int = 150) -> bool:
    """Garante o llama-server a correr: CUDA (NVIDIA) > Vulkan (AMD/Intel) > CPU."""
    if core.server_alive():
        return True
    with LLAMA_LOCK:
        if core.server_alive():
            return True
        order = _spawn_order(read_mode())
        for kind in order:
            wait = min(timeout, 90 if kind in ("cuda", "vulkan") else timeout)
            proc = _try_spawn(kind, wait=wait)
            if proc is not None:
                LLAMA_STATE["proc"] = proc
                LLAMA_STATE["backend"] = kind
                try:
                    BACKEND_STATE_FILE.write_text(kind, encoding="utf-8")
                except Exception:
                    pass
                print(f"[{time.strftime('%H:%M:%S')}] llama-server arrancado em {kind.upper()}", flush=True)
                return True
        return False


def switch_backend(mode: str) -> dict[str, Any]:
    """Muda o modo (auto/cpu/gpu); reinicia o modelo se necessario. Caller segura CHAT_LOCK."""
    write_mode(mode)
    if not core.server_alive():
        ok = ensure_llama()
        return {"ok": ok, "mode": mode, "backend": current_backend(),
                "error": None if ok else "nao foi possivel arrancar o modelo"}
    effective = current_backend()
    desired = best_gpu_kind() if mode in ("auto", "gpu") else "cpu"
    if effective == desired:
        return {"ok": True, "mode": mode, "backend": effective, "restarted": False}
    _kill_llama_all()
    ok = ensure_llama()
    return {"ok": ok, "mode": mode, "backend": current_backend(), "restarted": True,
            "error": None if ok else "falha ao reiniciar no modo " + mode}


class Handler(BaseHTTPRequestHandler):
    server_version = "ThorChat/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 50 * 1024 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    # ---------- GET ----------
    def do_GET(self) -> None:
        try:
            self._do_GET()
        except BrokenPipeError:
            self.close_connection = True
        except Exception as e:
            try:
                self._json({"ok": False, "error": f"erro interno: {e}"}, 500)
            except Exception:
                self.close_connection = True

    def _do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = (BASE_DIR / "chat.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/info":
            with SESS_LOCK:
                total = sum(len(s["messages"]) for s in SESSIONS.values())
            kind = current_backend()
            self._json({
                "model": core.MODEL,
                "cwd": str(Path.cwd()),
                "llama": core.server_alive(),
                "mode": read_mode(),
                "backend": "gpu" if kind in ("cuda", "vulkan") else kind,
                "api": kind,
                "gpu": gpu_name(),
                "envs": detect_environments(),
                "messages": total,
                "sessions": len(SESSIONS),
            })
            return
        if path == "/api/sessions":
            with SESS_LOCK:
                self._json([
                    {"id": sid, "title": s["title"], "count": len(s["messages"])}
                    for sid, s in SESSIONS.items()
                ])
            return
        if path == "/api/session":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            sid = ""
            for part in q.split("&"):
                if part.startswith("id="):
                    sid = part[3:]
            with SESS_LOCK:
                sess = SESSIONS.get(sid)
                if sess is None:
                    self._json({"ok": False, "error": "sessao inexistente"}, 404)
                    return
                self._json({"ok": True, "id": sid, "title": sess["title"], "messages": sess["messages"]})
            return
        self._send(404, b"not found", "text/plain")

    # ---------- POST ----------
    def do_POST(self) -> None:
        try:
            self._do_POST()
        except BrokenPipeError:
            self.close_connection = True
        except Exception as e:
            try:
                self._json({"ok": False, "error": f"erro interno: {e}"}, 500)
            except Exception:
                self.close_connection = True

    def _do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        body = self._read_body() or {}

        if path == "/api/cwd":
            p = Path(body.get("path", "")).expanduser()
            if p.is_dir():
                os.chdir(p)
                self._json({"ok": True, "cwd": str(Path.cwd())})
            else:
                self._json({"ok": False, "error": f"Diretorio inexistente: {p}"}, 400)
            return

        if path == "/api/browse":
            try:
                picked = browse_folder()
            except Exception as e:
                self._json({"ok": False, "error": f"Falha no seletor de pastas: {e}"}, 500)
                return
            if not picked:
                self._json({"ok": False, "canceled": True})
                return
            try:
                os.chdir(picked)
                self._json({"ok": True, "path": picked, "cwd": str(Path.cwd())})
            except Exception as e:
                self._json({"ok": False, "error": f"Sem acesso a {picked}: {e}"}, 400)
            return

        if path == "/api/sessions":
            sid = new_session()
            self._json({"ok": True, "id": sid, "title": "Conversa"})
            return

        if path == "/api/stop":
            core.STOP_EVENT.set()
            print(f"[{time.strftime('%H:%M:%S')}] STOP pedido pelo utilizador", flush=True)
            self._json({"ok": True})
            return

        if path == "/api/backend":
            mode = str(body.get("mode", "")).lower()
            if mode not in ("auto", "cpu", "gpu"):
                self._json({"ok": False, "error": "modo invalido (auto|cpu|gpu)"}, 400)
                return
            if not CHAT_LOCK.acquire(blocking=False):
                self._json({"ok": False, "error": "Ha um pedido a correr — espera antes de mudar de motor."}, 409)
                return
            try:
                print(f"[{time.strftime('%H:%M:%S')}] mudanca de motor -> {mode.upper()}", flush=True)
                res = switch_backend(mode)
            finally:
                CHAT_LOCK.release()
            self._json(res, 200 if res.get("ok") else 500)
            return

        if path == "/api/sessions/close":
            ok = close_session(str(body.get("id", "")))
            self._json({"ok": ok})
            return

        if path == "/api/clear":
            sid = str(body.get("session_id", ""))
            with SESS_LOCK:
                sess = SESSIONS.get(sid)
                if sess is not None:
                    sess["messages"].clear()
                    sess["title"] = "Conversa"
            self._json({"ok": True})
            return

        if path == "/api/command":
            cmd = str(body.get("command", ""))
            out = self._local_command(cmd, str(body.get("session_id", "")))
            self._json({"ok": True, "output": out})
            return

        if path == "/api/upload":
            self._handle_upload(body)
            return

        if path == "/api/chat":
            self._handle_chat(body)
            return

        self._send(404, b"not found", "text/plain")

    # ---------- comandos locais (/undo /env /todo ...) ----------
    def _local_command(self, cmd: str, session_id: str = "") -> str:
        cmd = cmd.strip()
        if cmd == "/clear":
            with SESS_LOCK:
                sess = SESSIONS.get(session_id)
                if sess is not None:
                    sess["messages"].clear()
                    sess["title"] = "Conversa"
            return "Conversa limpa."
        if cmd == "/undo":
            return execute_tool("undo_last", {})
        if cmd == "/env":
            return detect_environments()
        if cmd == "/todo":
            return execute_tool("todo_read", {})
        if cmd.startswith("/confirm "):
            mode = cmd.split(" ", 1)[1].strip().lower()
            return set_confirm_mode(mode in ("on", "1", "true", "yes"))
        if cmd == "/help":
            return (
                "/clear  limpa conversa\n/undo  desfaz ultima alteracao\n"
                "/env  ambientes\n/todo  tarefas\n/confirm on|off  modo confirmacao"
            )
        return f"Comando desconhecido: {cmd}"

    # ---------- uploads ----------
    def _handle_upload(self, body: Any) -> None:
        if not body:
            self._json({"ok": False, "error": "body vazio"}, 400)
            return
        name = os.path.basename(str(body.get("name", "ficheiro")))
        data_url = str(body.get("data", ""))
        if not data_url:
            self._json({"ok": False, "error": "sem dados"}, 400)
            return
        if len(data_url) > MAX_UPLOAD * 2:
            self._json({"ok": False, "error": "ficheiro demasiado grande (max 10MB)"}, 400)
            return
        ext = Path(name).suffix.lower()
        try:
            if "," in data_url[:100]:
                data_url = data_url.split(",", 1)[1]
            raw = base64.b64decode(data_url)
        except Exception as e:
            self._json({"ok": False, "error": f"base64 invalido: {e}"}, 400)
            return

        if ext in IMG_EXTS:
            updir = Path.cwd() / ".thor" / "uploads"
            updir.mkdir(parents=True, exist_ok=True)
            dest = updir / name
            dest.write_bytes(raw)
            self._json({
                "ok": True,
                "kind": "image",
                "name": name,
                "path": str(dest),
                "note": "Imagem guardada. O modelo actual e texto-only; descreve o que queres fazer com ela.",
            })
            return

        text = raw.decode("utf-8", errors="replace")
        if len(text) > 60000:
            text = text[:60000] + "\n... [truncado]"
        self._json({"ok": True, "kind": "text", "name": name, "content": text, "chars": len(text)})

    # ---------- chat (SSE) ----------
    def _handle_chat(self, body: Any) -> None:
        message = str(body.get("message", "")).strip()
        if not message:
            self._json({"ok": False, "error": "mensagem vazia"}, 400)
            return

        sid, sess = get_session(str(body.get("session_id", "")))
        title = set_title_if_needed(sess, message)

        if not ensure_llama():
            self._json({"ok": False, "error": "llama-server nao arrancou. Corre start.bat e verifica o modelo."}, 503)
            return

        if not CHAT_LOCK.acquire(blocking=False):
            self._json({"ok": False, "error": "Agente ocupado — espera a terminar o pedido actual."}, 409)
            return

        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] chat [{sid}] {len(message)} chars: {message[:70]!r} ...", flush=True)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def emit(ev: dict[str, Any]) -> None:
                data = json.dumps(ev, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()

            emit({"type": "start", "cwd": str(Path.cwd()), "session_id": sid, "title": title})
            n_delta = 0
            for ev in core.run_turn_events(sess["messages"], message):
                if ev.get("type") == "tool_start":
                    print(f"[{time.strftime('%H:%M:%S')}]   tool: {ev.get('name')} {str(ev.get('args', ''))[:80]}", flush=True)
                elif ev.get("type") == "delta":
                    n_delta += 1
                elif ev.get("type") == "stopped":
                    print(f"[{time.strftime('%H:%M:%S')}]   PARADO pelo utilizador ({n_delta} pedacos de texto)", flush=True)
                emit(ev)
            if n_delta:
                print(f"[{time.strftime('%H:%M:%S')}]   texto do modelo: {n_delta} pedacos (stream)", flush=True)
            emit({"type": "done"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                data = json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        finally:
            self.close_connection = True
            CHAT_LOCK.release()
            print(f"[{time.strftime('%H:%M:%S')}] concluido em {time.time() - t0:.0f}s", flush=True)


def main() -> None:
    os.chdir(BASE_DIR)
    print(f"Thor Chat Web  ·  http://127.0.0.1:{WEB_PORT}  ·  modelo {core.MODEL}", flush=True)
    print("[janela do servidor] pode ficar minimizada, mas NAO FECHAR - aqui aparece o registo de cada pedido.", flush=True)
    print(f"Motor: modo={read_mode().upper()} | GPU: {gpu_name() or 'nenhuma detetada'} | preferencia: {best_gpu_kind().upper()}", flush=True)
    threading.Thread(target=ensure_llama, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}/")).start()
    srv = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler)
    try:
        srv.serve_forever()
    finally:
        if LLAMA_STATE.get("proc") is not None:
            try:
                LLAMA_STATE["proc"].kill()
            except Exception:
                pass
        srv.server_close()


if __name__ == "__main__":
    main()
