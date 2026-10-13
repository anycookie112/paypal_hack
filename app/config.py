import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

APP_NAME = "Group Fund"  # working name

DATA_DIR = ROOT / "data"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")

# Any OpenAI-compatible chat completions endpoint.
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.atria-asi.ai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Atria-Dawn-Preview")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "240"))

# PayPal: the company's REST app. Payouts (reimbursements) are sent from this account; Checkout tops it up.
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_ENV = os.environ.get("PAYPAL_ENV", "sandbox")  # sandbox | live
PAYPAL_BASE_URL = ("https://api-m.paypal.com" if PAYPAL_ENV == "live" else "https://api-m.sandbox.paypal.com")

# Where the admin comes back to after a PayPal top-up. In development the Vite server proxies /api to the API.
APP_URL = os.environ.get("APP_URL", "http://localhost:5173").rstrip("/")
