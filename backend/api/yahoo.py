"""
Yahoo Finance Client (via yfinance / unofficial API)
미국 지수/종목 시세, 한국 지수 수집용 (429 rate limit 대응)
"""
import asyncio
import httpx
import yfinance as yf
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json
from pathlib import Path


class YahooApiError(Exception):
    """Yahoo Finance API 예외"""
    def __init__(self, message: str, status_code: int = None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class YahooFinanceClient:
    """
    Yahoo Finance 클라이언트
    - yfinance 라이브러리 사용 (주요 기능)
    - 비공식 REST API 폴백 (chart/query1.finance.yahoo.com)
    - Rate limit(429) 대응: 지수별 순차 호출 + 지연 + 즉시 폴백
    """

    def __init__(self, config: dict = None, timeout: int = 15):
        self.config = config or {}
        self.timeout = timeout
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

        # 캐시 디렉토리
        self._cache_dir = Path(__file__).parent.parent.parent / ".yahoo_cache"
        self._cache_dir.mkdir(exist_ok=True)
        self._cache_ttl = timedelta(minutes=5)

        # Rate limit 상태
        self._rate_limited = False
        self._rate_limit_reset = datetime.now()

    async def close(self):
        await self._http_client.aclose()

    def _get_cache_path(self, key: str) -> Path:
        safe_key = "".join(c for c in key if c.isalnum() or c in "_-")
        return self._cache_dir / f"{safe_key}.json"

    def _is_cache_valid(self, path: Path) -> bool:
        if not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < self._cache_ttl

    # ==========================================
    # yfinance 기반 조회 (주요 기능)
    # ==========================================

    def _get_ticker_info(self, symbol: str) -> Dict[str, Any]:
        """yfinance로 티커 정보 조회 (동기 함수, 스레드에서 실행)"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            hist = ticker.history(period="1d", interval="1m")
            return {
                "info": info,
                "history": hist
            }
        except Exception as e:
            return {"error": str(e)}

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        단일 종목/지수 실시간 시세 조회
        yfinance 사용 (비동기 래퍼)
        """
        cache_key = f"quote_{symbol}"
        cache_path = self._get_cache_path(cache_key)

        if self._is_cache_valid(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        # yfinance는 동기이므로 스레드 풀에서 실행
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._get_ticker_info, symbol)

        if "error" in result:
            raise YahooApiError(result["error"])

        info = result.get("info", {})
        hist = result.get("history")

        current_price = info.get("regularMarketPrice", 0)
        previous_close = info.get("regularMarketPreviousClose", current_price)
        change = current_price - previous_close
        change_pct = (change / previous_close * 100) if previous_close != 0 else 0
        volume = info.get("regularMarketVolume", 0)

        # 히스토리에서 최신 값 보정
        if hist is not None and not hist.empty:
            latest = hist.iloc[-1]
            current_price = float(latest['Close'])
            volume = int(latest['Volume']) if not latest['Volume'] != latest['Volume'] else volume  # NaN 체크

        data = {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "previous_close": round(previous_close, 2),
            "change": round(change, 2),
            "change_percent": round(change_pct, 2),
            "volume": volume,
            "high": float(info.get("regularMarketDayHigh", 0)),
            "low": float(info.get("regularMarketDayLow", 0)),
            "open": float(info.get("regularMarketOpen", 0)),
            "currency": info.get("currency", "USD"),
            "market_state": info.get("marketState", "UNKNOWN"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "name": info.get("longName", info.get("shortName", symbol))
        }

        # 캐시 저장
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return data

    async def get_quotes_batch(self, symbols: List[str], delay: float = 1.5) -> Dict[str, Dict[str, Any]]:
        """
        여러 종목 일괄 조회 (순차 호출 + 지연으로 429 방지)
        delay: 요청 간 지연(초), 기본 1.5초
        """
        results = {}
        for i, symbol in enumerate(symbols):
            try:
                results[symbol] = await self.get_quote(symbol)
            except YahooApiError as e:
                results[symbol] = {"error": str(e), "symbol": symbol}
            except Exception as e:
                results[symbol] = {"error": f"Unexpected: {e}", "symbol": symbol}

            # 마지막 요청이 아니면 지연
            if i < len(symbols) - 1:
                await asyncio.sleep(delay)

        return results

    # ==========================================
    # 비공식 REST API 폴백 (429 시 즉시 사용)
    # ==========================================

    async def _fetch_via_rest(self, symbol: str) -> Dict[str, Any]:
        """
        query1.finance.yahoo.com REST API 직접 호출 (폴백용)
        """
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": "1m", "range": "1d"}

        try:
            resp = await self._http_client.get(url, params=params)

            if resp.status_code == 429:
                raise YahooApiError("Rate limited", status_code=429, error_code="rate_limit")

            if resp.status_code != 200:
                raise YahooApiError(f"HTTP {resp.status_code}", status_code=resp.status_code)

            data = resp.json()
            chart = data.get("chart", {}).get("result", [])
            if not chart:
                raise YahooApiError("No chart data")

            meta = chart[0].get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            previous_close = meta.get("previousClose", price)
            change = price - previous_close
            change_pct = (change / previous_close * 100) if previous_close != 0 else 0
            volume = meta.get("regularMarketVolume", 0)

            return {
                "symbol": symbol,
                "current_price": round(price, 2),
                "previous_close": round(previous_close, 2),
                "change": round(change, 2),
                "change_percent": round(change_pct, 2),
                "volume": int(volume) if volume else 0,
                "high": meta.get("regularMarketDayHigh", 0),
                "low": meta.get("regularMarketDayLow", 0),
                "open": meta.get("regularMarketOpen", 0),
                "currency": meta.get("currency", "USD"),
                "market_state": meta.get("marketState", "UNKNOWN"),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "name": meta.get("symbol", symbol)
            }

        except httpx.RequestError as e:
            raise YahooApiError(f"네트워크 오류: {e}")

    async def get_quote_with_fallback(self, symbol: str) -> Dict[str, Any]:
        """
        yfinance 시도 → 429/실패 시 REST API 폴백
        """
        try:
            return await self.get_quote(symbol)
        except YahooApiError as e:
            if e.status_code == 429 or e.error_code == "rate_limit":
                print(f"[YahooFinance] {symbol} rate limited, trying REST fallback...")
                return await self._fetch_via_rest(symbol)
            raise
        except Exception as e:
            print(f"[YahooFinance] {symbol} yfinance error: {e}, trying REST fallback...")
            return await self._fetch_via_rest(symbol)

    # ==========================================
    # 주요 지수 프리셋
    # ==========================================

    US_INDICES = {
        "S&P500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "RUSSELL2000": "^RUT",
        "VIX": "^VIX"
    }

    KR_INDICES = {
        "KOSPI": "^KS11",
        "KOSDAQ": "^KQ11",
        "KOSPI200": "^KS50",
        "KOSDAQ150": "^KQ100"
    }

    async def get_us_indices(self) -> List[Dict[str, Any]]:
        """미국 주요 지수 일괄 조회 (폴백 포함)"""
        results = []
        for name, symbol in self.US_INDICES.items():
            try:
                quote = await self.get_quote_with_fallback(symbol)
                quote["index_name"] = name
                results.append(quote)
            except Exception as e:
                print(f"[YahooFinance] Failed to fetch {name}({symbol}): {e}")
        return results

    async def get_kr_indices(self) -> List[Dict[str, Any]]:
        """한국 주요 지수 일괄 조회 (폴백 포함)"""
        results = []
        for name, symbol in self.KR_INDICES.items():
            try:
                quote = await self.get_quote_with_fallback(symbol)
                quote["index_name"] = name
                results.append(quote)
            except Exception as e:
                print(f"[YahooFinance] Failed to fetch {name}({symbol}): {e}")
        return results

    async def get_all_indices(self) -> List[Dict[str, Any]]:
        """한국 + 미국 지수 모두 조회"""
        kr_task = asyncio.create_task(self.get_kr_indices())
        us_task = asyncio.create_task(self.get_us_indices())

        kr_results = await kr_task
        us_results = await us_task

        return kr_results + us_results

    # ==========================================
    # 종목 시세 (한국/미국)
    # ==========================================

    async def get_kr_stock_price(self, stock_code: str) -> Dict[str, Any]:
        """한국 종목 시세 (6자리 코드 → .KS 심볼)"""
        symbol = f"{stock_code}.KS"
        return await self.get_quote_with_fallback(symbol)

    async def get_us_stock_price(self, ticker: str) -> Dict[str, Any]:
        """미국 종목 시세"""
        return await self.get_quote_with_fallback(ticker.upper())

    async def get_stock_prices(self, codes: List[str], market: str = "KR") -> Dict[str, Dict[str, Any]]:
        """여러 종목 시세 일괄 조회"""
        if market == "KR":
            symbols = [f"{c}.KS" for c in codes]
        else:
            symbols = [c.upper() for c in codes]

        return await self.get_quotes_batch(symbols)


def create_yahoo_client_from_env() -> YahooFinanceClient:
    """환경변수에서 설정 읽어 YahooFinanceClient 생성"""
    return YahooFinanceClient({})