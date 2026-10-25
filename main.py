"""Start the API: python main.py  (equivalent to `uvicorn qc_rca.api.main:app`)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "qc_rca.api.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT") or os.getenv("API_PORT", "8000")),
        # Behind a hosting proxy (Render), trust X-Forwarded-For so rate limits see real client IPs.
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
