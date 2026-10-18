"""
Finnhub API Client (Real-time Market Data & News)
실시간 뉴스, 센티먼트, 시세 수집용
"""
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import os
import json
from pathlib import Path


class FinnhubApiError(Exception):
    """Finnhub API 예외"""
    def __init__(self, message: str, status_code: int = None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class FinnhubClient:
    """
    Finnhub API 클라이언트
    - 실시간 시세 (Quote)
    - 뉴스 수집 (Company News, General News)
    - 센티먼트 분석
    - 경제 캘린더
    """

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, config: dict, timeout: int = 30):
        self.api_key = config.get("api_key") or os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY가 필요합니다 (config 또는 환경변수)")

        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "investbin-ai-trading/1.0"}
        )

        # 캐시 디렉토리
        self._cache_dir = Path(__file__).parent.parent.parent / ".finnhub_cache"
        self._cache_dir.mkdir(exist_ok=True)
        self._cache_ttl = timedelta(minutes=15)  # 실시간 데이터는 짧은 캐시

        # Rate limit 추적
        self._rate_limit_remaining = 60  # 분당 60콜 (무료 플랜)
        self._rate_limit_reset = datetime.now()

    async def close(self):
        await self._client.aclose()

    def _get_cache_path(self, endpoint: str, params: dict) -> Path:
        """캐시 파일 경로 생성"""
        key = f"{endpoint}_{json.dumps(params, sort_keys=True)}"
        safe_key = "".join(c for c in key if c.isalnum() or c in "_-")
        return self._cache_dir / f"{safe_key}.json"

    def _is_cache_valid(self, path: Path) -> bool:
        """캐시 유효성 검사"""
        if not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < self._cache_ttl

    async def _request(self, method: str, endpoint: str, params: dict = None, use_cache: bool = True) -> Dict[str, Any]:
        """공통 요청 처리 (rate limit + 캐시)"""
        if params is None:
            params = {}

        params["token"] = self.api_key

        cache_path = self._get_cache_path(endpoint, params)

        # 캐시 확인 (GET만)
        if use_cache and method == "GET" and self._is_cache_valid(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        # Rate limit 체크
        if self._rate_limit_remaining <= 0:
            wait_seconds = (self._rate_limit_reset - datetime.now()).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(min(wait_seconds, 60))

        try:
            if method == "GET":
                resp = await self._client.get(endpoint, params=params)
            else:
                resp = await self._client.post(endpoint, json=params)

            # Rate limit 헤더 파싱
            if "x-ratelimit-remaining" in resp.headers:
                self._rate_limit_remaining = int(resp.headers["x-ratelimit-remaining"])
            if "x-ratelimit-reset" in resp.headers:
                self._rate_limit_reset = datetime.fromtimestamp(int(resp.headers["x-ratelimit-reset"]))

            if resp.status_code == 429:
                self._rate_limit_remaining = 0
                self._rate_limit_reset = datetime.now() + timedelta(minutes=1)
                raise FinnhubApiError("Rate limit exceeded", status_code=429, error_code="rate_limit")

            if resp.status_code != 200:
                raise FinnhubApiError(f"HTTP {resp.status_code}: {resp.text}", status_code=resp.status_code)

            data = resp.json()

            # 에러 체크
            if isinstance(data, dict) and "error" in data:
                raise FinnhubApiError(data["error"], error_code="api_error")

            # 캐시 저장 (GET만)
            if use_cache and method == "GET":
                try:
                    with open(cache_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            return data

        except httpx.RequestError as e:
            raise FinnhubApiError(f"네트워크 오류: {e}")

    # ==========================================
    # 실시간 시세
    # ==========================================

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        실시간 시세 조회 (quote)
        symbol 예: "AAPL", "005930.KS" (한국 종목은 .KS/.KQ 접미사)
        """
        return await self._request("GET", "/quote", {"symbol": symbol})

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """여러 종목 시세 일괄 조회 (순차 호출)"""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = await self.get_quote(symbol)
                await asyncio.sleep(0.1)  # rate limit 방지
            except FinnhubApiError as e:
                results[symbol] = {"error": str(e)}
        return results

    # ==========================================
    # 뉴스 수집
    # ==========================================

    async def get_company_news(
        self,
        symbol: str,
        from_date: str = None,
        to_date: str = None
    ) -> List[Dict[str, Any]]:
        """
        기업 뉴스 조회 (company-news)
        symbol: "AAPL" 또는 "005930.KS"
        """
        if not from_date:
            from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        if not to_date:
            to_date = datetime.now().strftime("%Y-%m-%d")

        return await self._request("GET", "/company-news", {
            "symbol": symbol,
            "from": from_date,
            "to": to_date
        })

    async def get_general_news(
        self,
        category: str = "general",
        min_id: int = 0
    ) -> List[Dict[str, Any]]:
        """
        일반 뉴스 조회 (news)
        category: general, forex, crypto, merger
        """
        return await self._request("GET", "/news", {
            "category": category,
            "minId": str(min_id)
        })

    async def get_news_sentiment(self, symbol: str) -> Dict[str, Any]:
        """
        뉴스 센티먼트 조회 (news-sentiment)
        """
        return await self._request("GET", "/news-sentiment", {"symbol": symbol})

    # ==========================================
    # 경제 지표/캘린더
    # ==========================================

    async def get_economic_calendar(
        self,
        from_date: str = None,
        to_date: str = None
    ) -> List[Dict[str, Any]]:
        """경제 캘린더 조회"""
        if not from_date:
            from_date = datetime.now().strftime("%Y-%m-%d")
        if not to_date:
            to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        return await self._request("GET", "/calendar/economic", {
            "from": from_date,
            "to": to_date
        })

    # ==========================================
    # 종목 심볼 검색
    # ==========================================

    async def search_symbol(self, query: str) -> List[Dict[str, Any]]:
        """종목 심볼 검색 (symbol/search)"""
        return await self._request("GET", "/search", {"q": query}, use_cache=False)

    # ==========================================
    # 편의 메서드: 한국 종목 심볼 변환
    # ==========================================

    @staticmethod
    def to_kr_symbol(stock_code: str) -> str:
        """한국 종목코드(6자리) → Finnhub 심볼 (.KS 접미사)"""
        return f"{stock_code}.KS"

    @staticmethod
    def to_us_symbol(ticker: str) -> str:
        """미국 티커 → Finnhub 심볼 (그대로)"""
        return ticker.upper()

    # ==========================================
    # 통합 뉴스 수집 (MarketTeam 호환용)
    # ==========================================

    async def get_news(self, keywords: List[str] = None) -> List[Dict[str, Any]]:
        """
        키워드 기반 뉴스 수집 (MarketTeam 호환용 래퍼)
        일반 뉴스 + 기업 뉴스 병합
        """
        if keywords is None:
            keywords = ["stock", "market", "earnings", "economy"]

        all_news = []

        # 1. 일반 뉴스 (카테고리별)
        try:
            general_news = await self.get_general_news("general")
            all_news.extend(general_news[:20])
        except Exception as e:
            print(f"[Finnhub] 일반 뉴스 수집 실패: {e}")

        # 2. 각 키워드별 기업 뉴스 (주요 종목 중심)
        major_symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "005930.KS", "000660.KS"]
        for symbol in major_symbols:
            try:
                company_news = await self.get_company_news(symbol)
                all_news.extend(company_news[:5])
                await asyncio.sleep(0.05)  # rate limit 방지
            except Exception:
                pass  # 개별 종목 실패는 무시

        # 키워드 필터링 (제목/요약에 키워드 포함된 것만)
        if keywords:
            filtered = []
            for news in all_news:
                title = news.get('headline', '').lower()
                summary = news.get('summary', '').lower()
                if any(kw.lower() in title or kw.lower() in summary for kw in keywords):
                    filtered.append(news)
            return filtered[:50]

        return all_news[:50]


def create_finnhub_client_from_env() -> FinnhubClient:
    """환경변수에서 설정 읽어 FinnhubClient 생성"""
    config = {"api_key": os.getenv("FINNHUB_API_KEY")}
    return FinnhubClient(config)