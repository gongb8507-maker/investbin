"""
시장 분석팀 (Market Trends Team)
실시간 시장 흐름 및 종목 뉴스 수집
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import httpx
import yfinance as yf

from backend.models.schemas import (
    NewsData, MarketTrendData, MarketType
)
from backend.api.finnhub import FinnhubClient, create_finnhub_client_from_env  # Finnhub API 클라이언트
from backend.api.yahoo import YahooFinanceClient, create_yahoo_client_from_env  # Yahoo Finance 클라이언트


class MarketTeamError(Exception):
    """시장 분석팀 에러"""
    pass


class MarketTeam:
    """
시장 분석팀 - 실시간 시장 데이터 수집

    역할: Finnhub/Yahoo를 통한 실시간 시세, 뉴스 수집
    """

    def __init__(self, finnhub_config: dict, yahoo_config: dict, httpx_timeout: int = 30):
        self.finnhub = FinnhubClient(finnhub_config, httpx_timeout)
        self.yahoo = YahooFinanceClient(yahoo_config, httpx_timeout)
        self.cache = {}
        self.cache_ttl = timedelta(minutes=10)  # 실시간 데이터는 캐시 시간 짧게

    async def _is_cache_valid(self, key: str) -> bool:
        if key not in self.cache:
            return False
        cached_time, _ = self.cache[key]
        return datetime.now() - cached_time < self.cache_ttl

    async def _get_from_cache(self, key: str) -> Optional[Dict[str, Any]]:
        if await self._is_cache_valid(key):
            _, data = self.cache[key]
            return data
        return None

    async def _set_cache(self, key: str, data: Dict[str, Any]):
        self.cache[key] = (datetime.now(), data)

    def _calc_change_percent(self, open_price: float, close_price: float) -> float:
        """시가/종가로부터 변동률 계산"""
        if open_price == 0:
            return 0.0
        return ((close_price - open_price) / open_price) * 100

    async def get_krx_indices(self) -> List[MarketTrendData]:
        """
        한국 주요 지수 (KOSPI, KOSDAQ, 코스닥150, KRX100)
        """
        indices = [
            "^KS11",  # 코스피
            "^KQ11",  # 코스닥
            "^KS50",  # 코스피50
            "^KQ100"  # 코스닥100
        ]
        trends = []
        for symbol in indices:
            cache_key = f"krx_index_{symbol}"
            cached = await self._get_from_cache(cache_key)
            if cached:
                trends.append(MarketTrendData(**cached))
                continue

            try:
                # Yahoo Finance로 한국 지수 조회
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d")
                if not hist.empty:
                    latest = hist.iloc[-1]
                    open_price = float(latest['Open'])
                    close_price = float(latest['Close'])
                    change_rate = self._calc_change_percent(open_price, close_price)
                    trend = MarketTrendData(
                        index_name=symbol,
                        current_value=close_price,
                        change_rate=change_rate,
                        volume=int(latest['Volume']) if latest['Volume'] is not None and not (hasattr(latest['Volume'], '__float__') and latest['Volume'] != latest['Volume']) else None
                    )
                    await self._set_cache(cache_key, trend.dict())
                    trends.append(trend)
            except Exception as e:
                print(f"[Market Team] {symbol} 지수 조회 실패: {e}")
        return trends

    async def get_us_indices(self) -> List[MarketTrendData]:
        """
        미국 주요 지수 (S&P 500, NASDAQ, DOW, Russell 2000)
        """
        indices = [
            "^GSPC",  # S&P 500
            "^IXIC",  # NASDAQ
            "^DJI",   # DOW Jones
            "^RUT"    # Russell 2000
        ]
        trends = []
        for symbol in indices:
            cache_key = f"us_index_{symbol}"
            cached = await self._get_from_cache(cache_key)
            if cached:
                trends.append(MarketTrendData(**cached))
                continue

            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d")
                if not hist.empty:
                    latest = hist.iloc[-1]
                    open_price = float(latest['Open'])
                    close_price = float(latest['Close'])
                    change_rate = self._calc_change_percent(open_price, close_price)
                    trend = MarketTrendData(
                        index_name=symbol,
                        current_value=close_price,
                        change_rate=change_rate,
                        volume=int(latest['Volume']) if latest['Volume'] is not None and not (hasattr(latest['Volume'], '__float__') and latest['Volume'] != latest['Volume']) else None
                    )
                    await self._set_cache(cache_key, trend.dict())
                    trends.append(trend)
            except Exception as e:
                print(f"[Market Team] {symbol} 지수 조회 실패: {e}")
        return trends

    async def get_stock_prices(self, codes: List[str], market: MarketType = MarketType.KR) -> List[MarketTrendData]:
        """
        개별 종목 시세 조회
        """
        trends = []
        for code in codes:
            cache_key = f"stock_price_{code}"
            cached = await self._get_from_cache(cache_key)
            if cached:
                trends.append(MarketTrendData(**cached))
                continue

            try:
                # 한국 종목 (Yahoo Finance 호환 심볼 사용)
                if market == MarketType.KR:
                    symbol = f"{code}.KS"  # 예: 005930.KS
                else:
                    symbol = f"{code}.NS"  # 미국 주식

                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d")
                if not hist.empty:
                    latest = hist.iloc[-1]
                    open_price = float(latest['Open'])
                    close_price = float(latest['Close'])
                    change_rate = self._calc_change_percent(open_price, close_price)
                    trend = MarketTrendData(
                        index_name=code,
                        current_value=close_price,
                        change_rate=change_rate,
                        volume=int(latest['Volume']) if latest['Volume'] is not None and not (hasattr(latest['Volume'], '__float__') and latest['Volume'] != latest['Volume']) else None
                    )
                    await self._set_cache(cache_key, trend.dict())
                    trends.append(trend)
            except Exception as e:
                print(f"[Market Team] {code} 시세 조회 실패: {e}")
        return trends

    async def collect_news(self, keywords: List[str]) -> List[NewsData]:
        """
        Finnhub를 통한 실시간 뉴스 수집
        """
        try:
            news = await self.finnhub.get_news(keywords)
            news_data = []
            for item in news:
                news_data.append(NewsData(
                    title=item.get('headline', ''),
                    summary=item.get('summary', '')[:300],
                    url=item.get('url', ''),
                    published_at=item.get('datetime', '')[:19] if item.get('datetime') else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source=item.get('source', 'Unknown'),
                    sentiment_score=item.get('sentiment', {}).get('positive', 0.5) if item.get('sentiment') else 0.5
                ))
            return news_data
        except Exception as e:
            print(f"[Market Team] 뉴스 수집 실패: {e}")
            return []

    async def get_market_sentiment(self) -> Dict[str, Any]:
        """
        시장 센티먼트 분석 (Finnhub 뉴스 기반)
        """
        try:
            # 최근 뉴스에서 센티먼트 분석
            news = await self.collect_news(["earnings", "inflation", "interest rates", "economy"])
            positive = sum(1 for n in news if n.sentiment_score > 0.7)
            negative = sum(1 for n in news if n.sentiment_score < 0.3)

            sentiment_score = 0.5
            if news:
                sentiment_score = sum(n.sentiment_score for n in news) / len(news)

            return {
                "score": int(sentiment_score * 100),
                "status": "강한 상승세" if sentiment_score > 0.7 else "상승세" if sentiment_score > 0.5 else "중립" if sentiment_score > 0.3 else "하락세",
                "summary": f"최근 {len(news)}개 뉴스 분석 결과 센티먼트 점수: {sentiment_score:.2f}"
            }
        except Exception as e:
            print(f"[Market Team] 센티먼트 분석 실패: {e}")
            return {
                "score": 50,
                "status": "중립",
                "summary": "시장 센티먼트 데이터 수집 중"
            }

    async def generate_market_report(self) -> Dict[str, Any]:
        """
        전체 시장 리포트 생성
        """
        krx_trends = await self.get_krx_indices()
        us_trends = await self.get_us_indices()
        sentiment = await self.get_market_sentiment()
        news = await self.collect_news(["AI", "semiconductor", "earnings", "inflation"])

        return {
            "market_report": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "krx_trends": [t.dict() for t in krx_trends],
                "us_trends": [t.dict() for t in us_trends],
                "sentiment": sentiment,
                "news": [n.dict() for n in news[:10]]
            }
        }