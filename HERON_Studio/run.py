#!/usr/bin/env python3
"""Start HERON Studio.

    python run.py            # opens http://127.0.0.1:5056 in your browser
    python run.py --no-browser --port 5056

The desktop app (electron/) starts this same server and shows it in its own window. A frozen
build re-uses this entry point for engine steps: `--engine <heron.py args>` / `--task ...`.
"""
import sys, threading, webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

if len(sys.argv) > 1 and sys.argv[1] == "--engine":
    from studio.config import ENGINE_DIR
    sys.path.insert(0, str(ENGINE_DIR)); sys.argv = ["heron.py"] + sys.argv[2:]
    import heron; heron.main(); sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "--task":
    from studio import engine_tasks
    {"reflections": engine_tasks.reflections, "model3d": engine_tasks.model3d}[sys.argv[2]](sys.argv[3]); sys.exit(0)

from studio import app
from studio.config import PORT

if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else PORT
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.main()
