"""Ponte entre o servidor Thor e o host OpenCode embebido (opencode/host.ts).

Arranca o host com opencode/bun.exe, mantem o pipe stdin/stdout aberto e
traduz os eventos do OpenCode para o formato de eventos do Thor:
info | delta | tool_start | tool_result | stopped | error (e termina em done).

100% local: o host so permite ligacoes a 127.0.0.1 (guarda de fetch no host.ts),
por isso corre sem internet — o modelo e o llama-server na maquina.

Teste rapido (com o llama-server a correr):
    runtime\\python.exe opencode_bridge.py prompt "Responde apenas: OK"
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent
OC_DIR = BASE_DIR / "opencode"
BUN = OC_DIR / "bun.exe"
HOST_TS = OC_DIR / "host.ts"
LOG_FILE = BASE_DIR / ".thor" / "opencode.log"

# knobs de rede do opencode (inofensivos se a versao nao os conhecer);
# a garantia real e a guarda de fetch do proprio host.ts
OFFLINE_ENV = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "NO_COLOR": "1",
}


def available() -> bool:
    """True se a instalacao local do OpenCode existe (bun + pacotes + host)."""
    return BUN.exists() and HOST_TS.exists() and (OC_DIR / "node_modules").is_dir()


class OpenCodeBridge:
    """Gerencia o processo host.ts e o protocolo JSON-lines por stdin/stdout."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lock = threading.Lock()
        self._dead = True
        self._ready = threading.Event()

    # ---------- processo ----------
    def start(self, timeout: float = 90.0) -> None:
        if not available():
            raise RuntimeError(
                "Instalacao do OpenCode em falta: falta opencode/bun.exe ou "
                "opencode/node_modules (corre opencode\\install.bat — precisa internet 1x)."
            )
        with self._lock:
            if self._proc is not None and not self._dead:
                return
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            logf = open(LOG_FILE, "ab")
            logf.write(f"\n--- bridge {time.strftime('%H:%M:%S')} ---\n".encode())
            env = dict(os.environ)
            env.update(OFFLINE_ENV)
            try:
                self._proc = subprocess.Popen(
                    [str(BUN), "run", "host.ts"],
                    cwd=str(OC_DIR),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=logf,
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as e:
                logf.close()
                raise RuntimeError(f"Nao foi possivel arrancar o host OpenCode: {e}") from e
            finally:
                try:
                    logf.close()
                except Exception:
                    pass
            self._dead = False
            self._ready.clear()
            proc = self._proc
            threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
        if not self._ready.wait(timeout) and self._dead:
            raise RuntimeError(
                f"o host OpenCode nao ficou pronto em {int(timeout)}s (ver .thor/opencode.log)"
            )
        if self._dead:
            raise RuntimeError("o host OpenCode terminou ao arrancar (ver .thor/opencode.log)")

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("ev") == "ready":
                    self._ready.set()
                self._q.put(ev)
        except Exception:
            pass
        finally:
            # so marca morte se ainda e o processo actual (pode ter havido respawn)
            if self._proc is proc:
                self._dead = True
                self._ready.set()
                self._q.put(
                    {"ev": "fatal", "content": "o host OpenCode terminou (ver .thor/opencode.log)"}
                )

    def _send(self, obj: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or self._dead:
            raise RuntimeError("o host OpenCode nao esta a correr")
        proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def alive(self) -> bool:
        return self._proc is not None and not self._dead

    # ---------- turno do agente ----------
    def prompt(self, sid: str, text: str, directory: str) -> Iterator[dict[str, Any]]:
        """Envia um pedido e produz eventos do Thor ate ao fim do turno (done)."""
        self.start()

        # drena eventos de turnos anteriores
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

        self._send({"cmd": "prompt", "sid": sid, "text": text, "directory": directory})

        # espera do aceite (com prazo) --------------------------------------
        turn: int | None = None
        deadline = time.time() + 60
        while time.time() < deadline:
            ev = self._get_poll()
            if ev is None:
                continue
            t = ev.get("ev")
            if t == "fatal":
                raise RuntimeError(ev.get("content", "host OpenCode morreu"))
            if t == "accepted" and ev.get("sid") == sid:
                turn = ev.get("turn")
                break
        if turn is None:
            if self._dead:
                raise RuntimeError("o host OpenCode terminou (ver .thor/opencode.log)")
            raise RuntimeError("o host OpenCode nao aceitou o pedido em 60s")

        # eventos do turno ---------------------------------------------------
        # watchdog: o host nunca pode ficar mudo para sempre (ja houve turnos
        # presos sem nenhum evento — CHAT_LOCK bloqueado ate o cliente morrer).
        # sem eventos por IDLE_TIMEOUT (ou alem de TURN_TIMEOUT) -> erro + stop.
        IDLE_TIMEOUT = 180.0
        TURN_TIMEOUT = 900.0
        t_start = time.time()
        t_last = t_start
        while True:
            ev = self._get_poll()
            if ev is None:
                if self._dead:
                    yield {"type": "error", "content": "o host OpenCode terminou (ver .thor/opencode.log)"}
                    return
                now = time.time()
                if now - t_last > IDLE_TIMEOUT or now - t_start > TURN_TIMEOUT:
                    self.stop(sid)
                    yield {
                        "type": "error",
                        "content": (
                            f"OpenCode sem resposta ha {int(now - t_last)}s — turno abortado "
                            "(watchdog; ver .thor/opencode.log)"
                        ),
                    }
                    return
                time.sleep(0.05)
                continue
            t_last = time.time()
            t = ev.get("ev")
            if t == "fatal":
                yield {"type": "error", "content": ev.get("content", "host OpenCode morreu")}
                return
            if ev.get("sid") != sid or ev.get("turn") != turn:
                continue  # evento de outro separador/turno
            if t == "done":
                return
            if t in ("delta", "info", "error", "stopped", "tool_start", "tool_result"):
                out: dict[str, Any] = {"type": t}
                for k in ("content", "name", "args", "result"):
                    if k in ev:
                        out[k] = ev[k]
                yield out

    def _get_poll(self) -> dict[str, Any] | None:
        try:
            return self._q.get(timeout=0.2)
        except queue.Empty:
            return None

    # ---------- controlo ----------
    def stop(self, sid: str) -> bool:
        """Pede interrupcao do turno (botao ⏹ Parar)."""
        try:
            self._send({"cmd": "stop", "sid": sid})
            return True
        except Exception:
            return False

    def forget(self, sid: str) -> None:
        """Esquece a sessao OpenCode de um separador (/clear)."""
        try:
            self._send({"cmd": "forget", "sid": sid})
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._dead = True
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.write(b'{"cmd":"quit"}\n')
                proc.stdin.flush()
                proc.wait(timeout=10)
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


BRIDGE = OpenCodeBridge()


if __name__ == "__main__":
    # teste manual: runtime\python.exe opencode_bridge.py prompt "texto"
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "prompt":
        texto = sys.argv[2] if len(sys.argv) > 2 else "Responde apenas: OK"
        try:
            for ev in BRIDGE.prompt("test1", texto, str(BASE_DIR)):
                print(ev, flush=True)
            print("[fim do turno]", flush=True)
        finally:
            BRIDGE.close()
    else:
        print('uso: runtime\\python.exe opencode_bridge.py prompt "texto"')
