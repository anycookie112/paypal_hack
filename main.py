import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    print(f"API on http://localhost:{port}  (frontend dev server: http://localhost:5173)")
    uvicorn.run("app.main:app", host=host, port=port, reload=os.environ.get("RELOAD", "1") == "1")
