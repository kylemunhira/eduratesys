"""
Production WSGI server for the SSMS portal (Waitress).

Usage (from the portal directory, with venv activated):
  set APP_ENV=production
  python run_waitress.py

Prefer installing as a Windows service via scripts\\install_portal_service.ps1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure portal package root is on sys.path when launched as a service.
PORTAL_DIR = Path(__file__).resolve().parent
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))

os.chdir(PORTAL_DIR)
os.environ.setdefault("APP_ENV", "production")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def main() -> None:
    # Load env before Django so .env.production is applied.
    from dotenv import load_dotenv

    load_dotenv(PORTAL_DIR / ".env")
    app_env = os.getenv("APP_ENV", "production").strip().lower()
    env_file = PORTAL_DIR / f".env.{app_env}"
    if env_file.exists():
        load_dotenv(env_file, override=True)

    host = os.getenv("WAITRESS_HOST", "0.0.0.0")
    port = int(os.getenv("WAITRESS_PORT", "2023"))
    threads = int(os.getenv("WAITRESS_THREADS", "6"))

    from waitress import serve
    from config.wsgi import application

    print(f"SSMS portal listening on http://{host}:{port} (threads={threads}, APP_ENV={app_env})")
    serve(application, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
