"""Read-only Kalshi API client.

RSA-PSS signed, retry/backoff, optional request pacing. Deliberately crippled:
`_request` refuses anything but GET. This package is an accounting tool — it
must never be able to place, amend, or cancel an order, even by accident.

Auth: KALSHI_API_KEY + KALSHI_PRIVATE_KEY_PATH env vars (or pass explicitly).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com"


class KalshiAPIError(Exception):
    pass


def load_env_file(path: Path) -> None:
    """Best-effort .env loader (no dependency on python-dotenv)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class KalshiReadOnlyClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        backoff_factor: float = 0.5,
        min_request_interval: float = 0.0,
    ):
        self.api_key = api_key or os.environ.get("KALSHI_API_KEY") or ""
        key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH") or ""
        if not self.api_key or not key_path:
            raise KalshiAPIError(
                "Missing credentials: set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH"
            )
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        p = Path(key_path)
        if not p.exists():
            raise KalshiAPIError(f"Private key not found: {key_path}")
        self._private_key = serialization.load_pem_private_key(
            p.read_bytes(), password=None
        )

        self._client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        self._min_interval = max(0.0, min_request_interval)
        self._pace_lock = asyncio.Lock()
        self._last_req = 0.0

    # ── plumbing ────────────────────────────────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        message = (timestamp + method.upper() + path).encode("utf-8")
        sig = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("utf-8")

    async def _pace(self) -> None:
        if self._min_interval <= 0.0:
            return
        loop = asyncio.get_event_loop()
        async with self._pace_lock:
            elapsed = loop.time() - self._last_req
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_req = loop.time()

    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        # READ-ONLY BY CONSTRUCTION: this client exposes no other verb.
        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Accept": "application/json",
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": self._sign(timestamp, "GET", endpoint),
        }
        if params:
            url = f"{url}?{urlencode(params)}"

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                await self._pace()
                resp = await self._client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code == 429 or e.response.status_code >= 500:
                    wait = self.backoff_factor * (2 ** attempt)
                    logger.warning("Kalshi GET %s → %d, retry in %.1fs",
                                   endpoint, e.response.status_code, wait)
                    await asyncio.sleep(wait)
                else:
                    raise KalshiAPIError(
                        f"HTTP {e.response.status_code}: {e.response.text}"
                    ) from e
            except Exception as e:  # noqa: BLE001 — network layer, retry everything
                last_exc = e
                await asyncio.sleep(self.backoff_factor * (2 ** attempt))
        raise KalshiAPIError(f"Failed after {self.max_retries} retries: {last_exc}")

    async def _paginate(self, endpoint: str, key: str,
                        params: Optional[Dict] = None,
                        max_pages: int = 500) -> AsyncIterator[Dict[str, Any]]:
        """Cursor-walk an endpoint TO COMPLETION.

        Account-wide pagination silently truncating (a fixed page budget that
        history eventually outgrows) is one of the classic Kalshi accounting
        bugs — the max_pages here is a runaway backstop (500 × 200 = 100k
        rows), not a working limit, and hitting it raises instead of lying.
        """
        cursor: Optional[str] = None
        for page in range(max_pages):
            p = {"limit": 200, **(params or {})}
            if cursor:
                p["cursor"] = cursor
            r = await self._get(endpoint, params=p)
            rows = r.get(key) or []
            for row in rows:
                yield row
            cursor = r.get("cursor")
            if not cursor or not rows:
                return
        raise KalshiAPIError(
            f"{endpoint}: exceeded {max_pages} pages — raise max_pages rather "
            "than silently truncating history"
        )

    # ── endpoints (all read-only) ───────────────────────────────────────────

    async def get_balance(self) -> Dict[str, Any]:
        return await self._get("/trade-api/v2/portfolio/balance")

    async def all_settlements(self) -> List[Dict[str, Any]]:
        return [s async for s in self._paginate(
            "/trade-api/v2/portfolio/settlements", "settlements")]

    async def _live_then_historical_fills(self, params: Optional[Dict] = None
                                          ) -> List[Dict[str, Any]]:
        """Kalshi moves fills older than the archive cutoff out of
        /portfolio/fills and into /historical/fills. Read live first, then the
        archive (rows move in that direction), and drop the overlap by fill_id."""
        seen: Dict[str, Dict[str, Any]] = {}
        for endpoint in ("/trade-api/v2/portfolio/fills",
                         "/trade-api/v2/historical/fills"):
            async for f in self._paginate(endpoint, "fills", params=params):
                key = f.get("fill_id") or f.get("trade_id") or repr(sorted(f.items()))
                seen.setdefault(key, f)
        return list(seen.values())

    async def all_fills(self) -> List[Dict[str, Any]]:
        """Every fill on the account: live and archived, de-duplicated."""
        return await self._live_then_historical_fills()

    async def fills_for_ticker(self, ticker: str) -> List[Dict[str, Any]]:
        return await self._live_then_historical_fills(params={"ticker": ticker})

    async def archived_positions(self) -> List[Dict[str, Any]]:
        """Settled positions archived out of /portfolio/settlements. These carry
        Kalshi's realized P&L (before fees), fees paid, and traded notional —
        there is no historical settlements endpoint."""
        return [p async for p in self._paginate(
            "/trade-api/v2/historical/positions", "market_positions")]

    async def historical_cutoff(self) -> Dict[str, Any]:
        return await self._get("/trade-api/v2/historical/cutoff")

    async def historical_markets(self, tickers: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(0, len(tickers), 100):
            r = await self._get("/trade-api/v2/historical/markets",
                                params={"tickers": ",".join(tickers[i:i + 100]),
                                        "limit": 200})
            out.extend(r.get("markets") or [])
        return out

    async def deposits(self) -> List[Dict[str, Any]]:
        return [d async for d in self._paginate(
            "/trade-api/v2/portfolio/deposits", "deposits")]

    async def withdrawals(self) -> List[Dict[str, Any]]:
        return [w async for w in self._paginate(
            "/trade-api/v2/portfolio/withdrawals", "withdrawals")]

    async def open_positions(self) -> List[Dict[str, Any]]:
        return [p async for p in self._paginate(
            "/trade-api/v2/portfolio/positions", "market_positions")]

    async def get_markets(self, tickers: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(0, len(tickers), 100):
            r = await self._get("/trade-api/v2/markets",
                                params={"tickers": ",".join(tickers[i:i + 100]),
                                        "limit": 200})
            out.extend(r.get("markets") or [])
        return out

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
