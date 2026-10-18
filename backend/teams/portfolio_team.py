"""
Portfolio Team - Fetches account data from Toss Securities Open API
"""
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from backend.api.toss_client import TossClient, TossApiError, create_toss_client_from_env


@dataclass
class PortfolioResult:
    portfolio: Dict[str, Any]
    data_source: str  # "live" | "sample"
    error: Optional[str] = None


class PortfolioTeam:
    """
    Portfolio Team - Fetches account summary and positions from Toss API,
    or provides simulated data when API keys are missing or in sample mode.
    """

    def __init__(self, toss_client: TossClient = None):
        self.toss_client = toss_client
        self._sample_portfolio = None

    def _create_sample_portfolio(self) -> Dict[str, Any]:
        """Create sample portfolio data (fallback)"""
        return {
            "total_balance": "₩12,450,000",
            "cash": "₩2,000,000",
            "cash_krw": 2000000,
            "cash_usd": 0,
            "holdings": [
                {
                    "code": "005930",
                    "name": "삼성전자",
                    "market": "KR",
                    "currency": "KRW",
                    "qty": 10,
                    "avg_price": 70000.0,
                    "current_price": 72000.0,
                    "unrealized_pnl": 20000.0,
                    "roi": 2.86
                },
                {
                    "code": "000660",
                    "name": "SK하이닉스",
                    "market": "KR",
                    "currency": "KRW",
                    "qty": 5,
                    "avg_price": 180000.0,
                    "current_price": 185000.0,
                    "unrealized_pnl": 25000.0,
                    "roi": 1.39
                },
                {
                    "code": "035420",
                    "name": "NAVER",
                    "market": "KR",
                    "currency": "KRW",
                    "qty": 3,
                    "avg_price": 205000.0,
                    "current_price": 210000.0,
                    "unrealized_pnl": 1500.0,
                    "roi": 0.24
                }
            ]
        }

    async def sync_portfolio(self) -> PortfolioResult:
        """
        Synchronize portfolio data from Toss API.
        Returns PortfolioResult with portfolio data and data_source.
        """
        # TossClient가 없으면 샘플 데이터 반환
        if not self.toss_client:
            return PortfolioResult(
                portfolio=self._create_sample_portfolio(),
                data_source="sample",
                error="TossClient not provided"
            )

        try:
            # 1. 계좌 목록 조회
            accounts = await self.toss_client.get_accounts()
            if not accounts:
                return PortfolioResult(
                    portfolio=self._create_sample_portfolio(),
                    data_source="sample",
                    error="No accounts found"
                )

            # 계좌 선택: TOSS_ACCOUNT_SEQ가 설정된 경우 해당 계좌 사용, 없으면 첫 번째 계좌
            account = None
            if self.toss_client.account_seq is not None:
                # 지정된 account_seq와 일치하는 계좌 찾기
                for acc in accounts:
                    if acc.get("accountSeq") == self.toss_client.account_seq:
                        account = acc
                        break
                if not account:
                    return PortfolioResult(
                        portfolio=self._create_sample_portfolio(),
                        data_source="sample",
                        error=f"Account with accountSeq={self.toss_client.account_seq} not found"
                    )
            else:
                # 첫 번째 계좌 사용 (종합매매 계좌)
                account = accounts[0]

            account_seq = account.get("accountSeq")
            if not account_seq:
                return PortfolioResult(
                    portfolio=self._create_sample_portfolio(),
                    data_source="sample",
                    error="No accountSeq in account"
                )

            # 2. 보유 종목 조회
            holdings_data = await self.toss_client.get_holdings(account_seq)

            # 3. data.json 구조로 변환 (기본 변환)
            portfolio = self.toss_client.transform_holdings_to_portfolio(holdings_data)

            # 4. 예수금(현금) 조회 및 반영
            try:
                cash_data = await self.toss_client.get_cash_balance(account_seq)
                usd_to_krw = await self.toss_client.get_exchange_rate()
                portfolio = self.toss_client.update_portfolio_with_cash(portfolio, cash_data, usd_to_krw)
            except Exception as e:
                print(f"[PortfolioTeam] Cash balance fetch failed: {e}")
                # 현금 조회 실패 시 기본값 유지

            return PortfolioResult(
                portfolio=portfolio,
                data_source="live",
                error=None
            )

        except TossApiError as e:
            # API 에러 시 샘플 데이터로 폴백
            error_msg = str(e)
            if e.status_code == 403:
                error_msg = f"IP 주소 미등록: 토스증권 WTS에서 허용 IP를 등록하세요. ({e})"
            elif e.status_code == 401:
                error_msg = f"인증 실패: client_id/secret 확인 필요 ({e})"

            print(f"[PortfolioTeam] Toss API error: {error_msg}. Falling back to sample data.")
            return PortfolioResult(
                portfolio=self._create_sample_portfolio(),
                data_source="sample",
                error=error_msg
            )

        except Exception as e:
            print(f"[PortfolioTeam] Unexpected error: {e}. Falling back to sample data.")
            return PortfolioResult(
                portfolio=self._create_sample_portfolio(),
                data_source="sample",
                error=str(e)
            )

    async def sync_market_indices(self) -> List[Dict[str, Any]]:
        """
        시장 지수(KOSPI, KOSDAQ)를 Toss API에서 조회.
        실패 시 빈 리스트 반환 (호출자에서 폴백 처리).
        """
        if not self.toss_client:
            return []

        try:
            indicators = await self.toss_client.get_market_indicators()
            return self.toss_client.transform_market_indicators(indicators)
        except TossApiError as e:
            print(f"[PortfolioTeam] Market indicators API error: {e}")
            return []
        except Exception as e:
            print(f"[PortfolioTeam] Unexpected error fetching market indicators: {e}")
            return []

    async def close(self):
        if self.toss_client:
            await self.toss_client.close()