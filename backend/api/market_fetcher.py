"""
Market data fetcher for pulling real-time indices and stock prices.
- KOSPI/KOSDAQ: Toss Securities Open API (Market Indicators) via PortfolioTeam
- S&P500/NASDAQ: Yahoo Finance (fallback with rate limit handling)
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import httpx

from backend.models.schemas import MarketTrendData, MarketType
from backend.teams.portfolio_team import PortfolioTeam
from backend.api.toss_client import create_toss_client_from_env, TossApiError

# Cache to avoid excessive API calls
_index_cache: Dict[str, tuple[datetime, List[MarketTrendData]]] = {}
CACHE_TTL_SECONDS = 300  # 5분 캐시 (장중 지수는 1분 갱신도 과함)


def _is_cache_valid(cache_time: datetime) -> bool:
    return (datetime.now() - cache_time).total_seconds() < CACHE_TTL_SECONDS


async def _fetch_kr_indices_via_toss() -> List[MarketTrendData]:
    """Toss API로 KOSPI/KOSDAQ 지수 조회"""
    try:
        toss_client = create_toss_client_from_env()
        portfolio_team = PortfolioTeam(toss_client)
        indicators = await portfolio_team.sync_market_indices()
        await toss_client.close()

        results = []
        for item in indicators:
            results.append(
                MarketTrendData(
                    index_name=item["index_name"],
                    current_value=item["current_value"],
                    change_rate=item["change_rate"],
                    volume=item.get("volume"),
                    timestamp=item.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
            )
        return results
    except ValueError:
        # 환경변수 미설정 등
        return []
    except TossApiError as e:
        print(f"[MarketFetcher] Toss API error for KR indices: {e}")
        return []
    except Exception as e:
        print(f"[MarketFetcher] Unexpected error fetching KR indices via Toss: {e}")
        return []


async def _fetch_us_indices_via_yahoo() -> List[MarketTrendData]:
    """Yahoo Finance로 S&P500/NASDAQ 지수 조회 (rate limit 대응)"""
    symbols = {
        "S&P500": "^GSPC",
        "NASDAQ": "^IXIC",
    }

    results: List[MarketTrendData] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, symbol in symbols.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                params = {"interval": "1m", "range": "1d"}
                resp = await client.get(url, params=params)

                if resp.status_code == 429:
                    # 429면 즉시 폴백 (대기 없이)
                    print(f"[MarketFetcher] Rate limited for {name}, using fallback")
                    raise Exception("rate limited")

                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")

                resp_json = resp.json()
                chart = resp_json.get("chart", {}).get("result", [])
                if not chart:
                    raise Exception("no chart data")

                meta = chart[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0.0)
                previous_close = meta.get("previousClose", price)
                change = price - previous_close
                change_pct = (change / previous_close * 100) if previous_close != 0 else 0.0
                volume = meta.get("regularMarketVolume", 0)

                results.append(
                    MarketTrendData(
                        index_name=name,
                        current_value=round(price, 2),
                        change_rate=round(change_pct, 2),
                        volume=int(volume) if volume else None,
                        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                )

                # 요청 사이 지연 (rate limit 방지)
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"[MarketFetcher] Warning: could not fetch {name}: {e}")
                continue

    return results


async def fetch_market_indices() -> List[MarketTrendData]:
    """
    Fetch real-time values for major market indices:
    - KOSPI, KOSDAQ: Toss API (공식)
    - S&P500, NASDAQ: Yahoo Finance (비공식, rate limit 대응)

    Returns list of MarketTrendData. If all APIs fail, a small
    simulated set is returned so the UI never crashes.
    """
    cache_key = "major_indices"
    now = datetime.now()
    if cache_key in _index_cache:
        cache_time, cached_data = _index_cache[cache_key]
        if _is_cache_valid(cache_time):
            return cached_data

    # 병렬로 조회 (Toss와 Yahoo는 독립적)
    kr_task = asyncio.create_task(_fetch_kr_indices_via_toss())
    us_task = asyncio.create_task(_fetch_us_indices_via_yahoo())

    kr_results = await kr_task
    us_results = await us_task

    results = kr_results + us_results

    # Fallback: if no index could be fetched, return a small simulated set
    # so the UI never shows an empty ticker bar.
    if not results:
        results = [
            MarketTrendData(
                index_name="KOSPI",
                current_value=2500,
                change_rate=0.4,
                volume=1_200_000,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            MarketTrendData(
                index_name="KOSDAQ",
                current_value=750,
                change_rate=0.2,
                volume=800_000,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            MarketTrendData(
                index_name="S&P500",
                current_value=4500,
                change_rate=0.5,
                volume=2_000_000,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            MarketTrendData(
                index_name="NASDAQ",
                current_value=13200,
                change_rate=0.9,
                volume=3_000_000,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ]

    # Update cache
    _index_cache[cache_key] = (now, results)
    return results