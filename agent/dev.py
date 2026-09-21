"""Dev-режим: запуск backend + vite dev server одной командой.

Использование:
    uv run agent-dev            # backend :8321 + vite :5173
    uv run agent-dev --no-vite  # только backend

Аргументы backend передаются через --:
    uv run agent-dev -- --port 9000 --reload
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import NoReturn


async def _main() -> NoReturn:
    backend_cmd = ["uv", "run", "agent-web"]
    frontend_cmd = ["npm", "run", "dev"]
    run_vite = True

    args = sys.argv[1:]
    if "--no-vite" in args:
        run_vite = False
        args.remove("--no-vite")

    if "--" in args:
        sep = args.index("--")
        backend_args = args[sep + 1 :]
        args = args[:sep]
    else:
        backend_args = []
    backend_cmd.extend(backend_args)

    backend_proc: subprocess.Popen[bytes] | None = None
    frontend_proc: subprocess.Popen[bytes] | None = None

    def cleanup() -> None:
        for proc, name in [(frontend_proc, "vite"), (backend_proc, "uvicorn")]:
            if proc and proc.poll() is None:
                print(f"\nОстановка {name}...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    signal.signal(signal.SIGINT, lambda *_a: None)
    signal.signal(signal.SIGTERM, lambda *_a: None)
    try:
        print("→ Бэкенд: http://127.0.0.1:8321")
        backend_proc = subprocess.Popen(backend_cmd)

        if run_vite:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            frontend_cwd = os.path.join(repo_root, "web")

            if not os.path.isdir(os.path.join(frontend_cwd, "node_modules")):
                print("node_modules не найдены, запускаю npm install...")
                subprocess.run(["npm", "install"], cwd=frontend_cwd, check=True)

            print("→ Фронтенд: http://localhost:5173 (прокси /api → 8321)")
            frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_cwd)

        print("\nОба запущены. Открой http://localhost:5173 в браузере. Ctrl+C — остановить.\n")

        while True:
            await asyncio.sleep(1)
            if backend_proc.poll() is not None:
                print("Бэкенд завершился.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()
        sys.exit(0)


def main() -> NoReturn:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
