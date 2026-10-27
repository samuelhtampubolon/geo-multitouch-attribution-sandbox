"""
Zero-friction local live-server runner for GEO & Multi-Touch Attribution Sandbox.
Features:
- Port pre-flight checks on 127.0.0.1
- Automatic database initialization & seeding
- Concurrent execution of FastAPI backend and Streamlit dashboard
- Deterministic --check verification flag
"""

from __future__ import annotations
import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable


def is_port_available(host: str, port: int) -> bool:
    """Verifies whether a specific network port is open for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        result = s.connect_ex((host, port))
        return result != 0


def find_available_port(host: str, starting_port: int, max_attempts: int = 10) -> int:
    """Finds next available port starting from starting_port."""
    for p in range(starting_port, starting_port + max_attempts):
        if is_port_available(host, p):
            return p
    return starting_port


def run_preflight_checks() -> bool:
    """Runs database initialization, seeding, and full pytest validation."""
    print("=" * 70)
    print("[*] RUNNING PRE-FLIGHT VERIFICATION & TESTS")
    print("=" * 70)

    # 1. Initialize and Seed
    print("\n[1/3] Initializing and Seeding Offline Corpus...")
    from src.seed_journeys import seed_database
    seed_database("tenant-alpha", force_reseed=False)
    print("[OK] Database ready.")

    # 2. Run Pytest
    print("\n[2/3] Running Pytest Verification Suite (math, security, API, dashboard, JS parity)...")
    res = subprocess.run([PYTHON_EXE, "-m", "pytest", "tests/", "-v"], cwd=str(BASE_DIR))

    # 3. GitHub Pages build freshness
    print("\n[3/3] Verifying GitHub Pages build (index.html, docs/index.html)...")
    pages = subprocess.run([PYTHON_EXE, "scripts/build_pages.py", "--check"], cwd=str(BASE_DIR))

    if res.returncode == 0 and pages.returncode == 0:
        print("\n" + "=" * 70)
        print("[OK] ALL TESTS & PREFLIGHT CHECKS PASSED DETERMINISTICALLY")
        print("=" * 70)
        return True
    else:
        print("\n" + "=" * 70)
        print("[FAIL] PREFLIGHT VERIFICATION FAILED")
        print("=" * 70)
        return False


def start_servers(api_port: int, streamlit_port: int) -> None:
    """Spawns FastAPI and Streamlit dashboard concurrently."""
    host = "127.0.0.1"

    # Preflight ports
    api_port = find_available_port(host, api_port)
    streamlit_port = find_available_port(host, streamlit_port)

    print("=" * 70)
    print("[*] LAUNCHING GEO & MULTI-TOUCH ATTRIBUTION SANDBOX")
    print("=" * 70)
    print(f"Backend API (FastAPI)   : http://{host}:{api_port}")
    print(f"API Documentation (Docs): http://{host}:{api_port}/docs")
    print(f"Interactive Dashboard   : http://{host}:{streamlit_port}")
    print(f"Health Probe (healthz)  : http://{host}:{api_port}/healthz")
    print("=" * 70)
    print("Press Ctrl+C to stop all services.\n")

    # Launch FastAPI process
    api_cmd = [
        PYTHON_EXE, "-m", "uvicorn", "src.api.main:app",
        "--host", host,
        "--port", str(api_port),
        "--no-server-header",
    ]
    api_proc = subprocess.Popen(api_cmd, cwd=str(BASE_DIR))

    # Launch Streamlit process
    dashboard_file = str(BASE_DIR / "src" / "ui" / "dashboard.py")
    streamlit_cmd = [
        PYTHON_EXE, "-m", "streamlit", "run", dashboard_file,
        "--server.address", host,
        "--server.port", str(streamlit_port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    streamlit_proc = subprocess.Popen(streamlit_cmd, cwd=str(BASE_DIR))

    try:
        while True:
            time.sleep(1)
            if api_proc.poll() is not None:
                print("FastAPI process exited unexpectedly.")
                break
            if streamlit_proc.poll() is not None:
                print("Streamlit process exited unexpectedly.")
                break
    except KeyboardInterrupt:
        print("\nShutting down server processes...")
    finally:
        api_proc.terminate()
        streamlit_proc.terminate()
        api_proc.wait()
        streamlit_proc.wait()
        print("Servers safely terminated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="GEO & MTA Sandbox Live Runner")
    parser.add_argument("--check", action="store_true", help="Run preflight checks and test suite, then exit")
    parser.add_argument("--api-port", type=int, default=8000, help="FastAPI port (default 8000)")
    parser.add_argument("--ui-port", type=int, default=8501, help="Streamlit port (default 8501)")
    args = parser.parse_args()

    if args.check:
        success = run_preflight_checks()
        sys.exit(0 if success else 1)

    # Initialize DB first
    from src.seed_journeys import seed_database
    seed_database("tenant-alpha", force_reseed=False)

    start_servers(args.api_port, args.ui_port)


if __name__ == "__main__":
    main()
