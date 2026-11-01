# Точка входу: веб-апка (FastAPI)

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    reload = os.getenv("WEB_RELOAD", "0").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        "web.main:app",
        host=host,
        port=port,
        reload=reload,
    )
