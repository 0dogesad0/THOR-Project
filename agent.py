"""Thor Agent — modo terminal (CLI). Para chat gráfico usa start.bat (server.py)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import agent_core as core
from tools import detect_environments, execute_tool, set_confirm_mode

HELP = """\
Comandos do chat:
  /help           mostra esta ajuda
  /clear          limpa a conversa
  /undo           desfaz a ultima alteracao de ficheiro
  /env            mostra ambientes detetados (venv, docker, ...)
  /confirm on|off modo de confirmacao para acoes destrutivas
  /cwd <path>     muda o diretorio de trabalho
  /todo           mostra a lista de tarefas
  /exit           sai

Tudo offline. Ferramentas: ficheiros, edit/patch, glob/grep (.gitignore), terminal (+background),
tarefas, undo, web (opcional), ambientes (venv/conda/docker).
"""


def main() -> int:
    print("Thor Agent CLI  ·  100% offline  ·  llama.cpp + " + core.MODEL)
    try:
        core.check_server()
        print(f"  llama-server pronto em {core.HOST}")
    except RuntimeError as e:
        print(f"\x1b[91m{e}\x1b[0m")
        print("Arranca primeiro com: start.bat")
        return 1

    print(HELP)
    envs = detect_environments()
    if envs and "Nenhum" not in envs:
        print(f"Ambientes detetados:\n{envs}")

    messages: list[dict[str, Any]] = []

    while True:
        try:
            user_text = input("\n\x1b[96myou>\x1b[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nadeus.")
            return 0
        if not user_text:
            continue
        if user_text in ("/exit", "/quit", "/q"):
            print("adeus.")
            return 0
        if user_text == "/clear":
            messages = []
            print("  [conversa limpa]")
            continue
        if user_text == "/help":
            print(HELP)
            continue
        if user_text == "/undo":
            print("  " + execute_tool("undo_last", {}))
            continue
        if user_text == "/env":
            print("  " + detect_environments())
            continue
        if user_text == "/todo":
            print("  " + execute_tool("todo_read", {}))
            continue
        if user_text.startswith("/confirm "):
            mode = user_text.split(" ", 1)[1].strip().lower()
            print("  " + set_confirm_mode(mode in ("on", "1", "true", "yes")))
            continue
        if user_text.startswith("/cwd "):
            p = Path(user_text.split(" ", 1)[1].strip()).expanduser()
            if p.is_dir():
                os.chdir(p)
                print(f"  [cwd: {Path.cwd()}]")
            else:
                print(f"  [erro] diretorio inexistente: {p}")
            continue

        print("\x1b[90m", end="")
        try:
            for ev in core.run_turn_events(messages, user_text):
                if ev["type"] == "text":
                    print(ev["content"])
                elif ev["type"] == "tool_start":
                    print(f"\n  \x1b[90m[tool] {ev['name']}({ev['args'][:200]})\x1b[0m")
                elif ev["type"] == "tool_result":
                    r = ev["result"]
                    if len(r) > 600:
                        r = r[:600] + " ...[truncado]"
                    print(f"  \x1b[90m{r}\x1b[0m")
                elif ev["type"] == "error":
                    print(f"\x1b[91m{ev['content']}\x1b[0m")
                elif ev["type"] == "info":
                    print(f"\x1b[93m{ev['content']}\x1b[0m")
        except KeyboardInterrupt:
            print("\n\x1b[93m[interrumpido]\x1b[0m")
        finally:
            print("\x1b[0m", end="")


if __name__ == "__main__":
    raise SystemExit(main())
