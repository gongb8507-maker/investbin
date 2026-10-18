"""
Toss Securities Open API Client (Official Spec Compliant)

Based on: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
Base URL: https://openapi.tossinvest.com
Auth: OAuth 2.0 Client Credentials (client_id / client_secret)

Endpoints used:
- POST /oauth2/token - Token issuance
- GET /api/v1/accounts - Account list
- GET /api/v1/holdings - Holdings (portfolio)
- GET /api/v1/market-indicators/prices - Market indices (KOSPI, KOSDAQ)
- GET /api/v1/order-info/buyable-amount - Cash balance (deposit)

NO order endpoints (read-only only)
"""
import asyncio
import time
import json
import httpx
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import os
import pathlib


@dataclass
class TokenInfo:
    access_token: str
    expires_at: float  # Unix timestamp

    @property
    def is_expired(self) -> bool:
        # Refresh 60 seconds before actual expiry
        return time.time() >= (self.expires_at - 60)


class TossApiError(Exception):
    """Toss API Exception"""
    def __init__(self, message: str, status_code: int = None, error_code: str = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response = response


class TossClient:
    """
    Toss Securities Open API Client (Read-Only)

    공식 스펙: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
    - Base URL: https://openapi.tossinvest.com
    - 인증: OAuth 2.0 Client Credentials Grant
    - 계좌 헤더: X-Tossinvest-Account (accountSeq)
    - 토큰: 프로세스 재시작 간 재사용을 위해 로컬 파일 캐시 (.token_cache.json)
    """

    BASE_URL = "https://openapi.tossinvest.com"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout: float = 30.0,
        account_seq: Optional[int] = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.account_seq = account_seq

        self._token_info: Optional[TokenInfo] = None
        self._token_lock = asyncio.Lock()

        # 토큰 캐시 파일 경로 (.gitignore에 포함됨)
        self._token_cache_path = pathlib.Path(__file__).parent.parent.parent / ".token_cache.json"

        # HTTP 클라이언트 (연결 풀링)
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(timeout),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "investbin-ai-trading/1.0"
            }
        )

        # Rate limit 헤더 파싱용
        self._rate_limit_remaining: Dict[str, int] = {}

        # 캐시에서 토큰 로드 시도
        self._load_token_cache()

    def _load_token_cache(self) -> None:
        """로컬 캐시 파일에서 토큰 로드 (값은 로그에 출력하지 않음)"""
        try:
            if self._token_cache_path.exists():
                with open(self._token_cache_path, 'r') as f:
                    data = json.load(f)
                    # client_id가 일치하는 경우만 로드 (보안)
                    if data.get("client_id") == self.client_id:
                        self._token_info = TokenInfo(
                            access_token=data["access_token"],
                            expires_at=data["expires_at"]
                        )
        except Exception:
            # 캐시 읽기 실패 시 무시 (새 토큰 발급)
            pass

    def _save_token_cache(self) -> None:
        """토큰을 로컬 캐시 파일에 저장 (값은 로그에 출력하지 않음)"""
        try:
            if self._token_info:
                data = {
                    "client_id": self.client_id,
                    "access_token": self._token_info.access_token,
                    "expires_at": self._token_info.expires_at
                }
                with open(self._token_cache_path, 'w') as f:
                    json.dump(data, f)
        except Exception:
            # 캐시 저장 실패 시 무시
            pass

    async def close(self):
        await self._client.aclose()

    # ==========================================
    # Token Management
    # ==========================================

    async def _get_access_token(self) -> str:
        """액세스 토큰 획득 (캐싱 + 자동 갱신)"""
        async with self._token_lock:
            # 유효한 토큰이 있으면 재사용
            if self._token_info and not self._token_info.is_expired:
                return self._token_info.access_token

            # 새 토큰 발급
            await self._issue_new_token()
            return self._token_info.access_token

    async def _issue_new_token(self) -> None:
        """OAuth 2.0 Client Credentials Grant로 새 토큰 발급"""
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        # 토큰 엔드포인트는 form-urlencoded, Authorization 헤더 없음
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            resp = await self._client.post(
                "/oauth2/token",
                data=data,
                headers=headers
            )

            if resp.status_code == 403:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                raise TossApiError(
                    f"IP 주소가 허용되지 않음: {error_data.get('error_description', 'IP address not allowed')}. "
                    "토스증권 WTS 설정 > Open API > 허용 IP 관리에서 호출 IP를 등록하세요.",
                    status_code=403,
                    error_code="access_denied",
                    response=error_data
                )

            if resp.status_code == 401:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                raise TossApiError(
                    f"클라이언트 인증 실패: {error_data.get('error_description', 'invalid_client')}. "
                    "client_id / client_secret를 확인하세요.",
                    status_code=401,
                    error_code="invalid_client",
                    response=error_data
                )

            if resp.status_code == 429:
                # Rate limit - 잠시 대기 후 재시도 (호출자에서 처리)
                raise TossApiError(
                    "토큰 발급 rate limit 초과",
                    status_code=429,
                    error_code="rate_limit"
                )

            if resp.status_code >= 400:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                raise TossApiError(
                    f"토큰 발급 실패: {error_data.get('error_description', f'HTTP {resp.status_code}')}",
                    status_code=resp.status_code,
                    error_code=error_data.get('error'),
                    response=error_data
                )

            token_data = resp.json()
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 86400)  # 기본 24시간

            self._token_info = TokenInfo(
                access_token=access_token,
                expires_at=time.time() + expires_in
            )
            # 캐시 저장
            self._save_token_cache()

        except httpx.RequestError as e:
            raise TossApiError(f"네트워크 오류: {e}")

    def _build_headers(self, account_seq: Optional[int] = None) -> Dict[str, str]:
        """공통 헤더 구성"""
        headers = {
            "Authorization": f"Bearer {self._token_info.access_token}" if self._token_info else "",
            "Content-Type": "application/json",
        }
        # account_seq 우선순위: 메서드 인자 > 인스턴스 변수
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is not None:
            headers["X-Tossinvest-Account"] = str(seq)
        return headers

    def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """응답 처리 및 에러 체크"""
        # Rate limit 헤더 저장
        for key in ["x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"]:
            if key in response.headers:
                self._rate_limit_remaining[key] = int(response.headers[key])

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except:
                error_data = {"raw": response.text}

            if response.status_code == 401:
                # 토큰 만료 시 강제 갱신을 위해 토큰 무효화
                self._token_info = None
                raise TossApiError("인증 만료, 토큰 재발급 필요", status_code=401, response=error_data)

            if response.status_code == 403:
                raise TossApiError(
                    f"IP 허용 안됨: {error_data.get('error_description', 'IP address not allowed')}",
                    status_code=403,
                    error_code="access_denied",
                    response=error_data
                )

            if response.status_code == 429:
                raise TossApiError(
                    "API rate limit 초과",
                    status_code=429,
                    error_code="rate_limit",
                    response=error_data
                )

            raise TossApiError(
                f"API 오류: {error_data.get('error_description', f'HTTP {response.status_code}')}",
                status_code=response.status_code,
                error_code=error_data.get('error'),
                response=error_data
            )

        # ApiResponse envelope에서 result 추출
        data = response.json()
        return data.get("result", data)

    async def _request_with_retry(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """401 시 1회 재시도, 429 시 대기 후 재시도"""
        max_retries = 1  # 401 시 1회만 재시도
        for attempt in range(max_retries + 1):
            try:
                token = await self._get_access_token()
                headers = self._build_headers()
                if "headers" in kwargs:
                    headers.update(kwargs["headers"])
                kwargs["headers"] = headers

                if method == "GET":
                    resp = await self._client.get(path, **kwargs)
                elif method == "POST":
                    resp = await self._client.post(path, **kwargs)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                return self._handle_response(resp)

            except TossApiError as e:
                if e.status_code == 401 and attempt < max_retries:
                    # 토큰 무효화 후 재시도
                    self._token_info = None
                    await asyncio.sleep(0.5)
                    continue
                elif e.status_code == 429:
                    # 429는 대기 후 1회 재시도
                    if attempt < max_retries:
                        await asyncio.sleep(2)
                        continue
                raise

        # 도달하지 않음
        raise TossApiError("최대 재시도 횟수 초과")

    # ==========================================
    # Account & Holdings API (Read-Only)
    # ==========================================

    async def get_accounts(self) -> List[Dict[str, Any]]:
        """계좌 목록 조회 (GET /api/v1/accounts)"""
        resp = await self._request_with_retry("GET", "/api/v1/accounts")
        return resp if isinstance(resp, list) else []

    async def get_holdings(
        self,
        account_seq: int,
        symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """보유 주식 조회 (GET /api/v1/holdings)"""
        params = {}
        if symbol:
            params["symbol"] = symbol

        resp = await self._request_with_retry("GET", "/api/v1/holdings", params=params, headers={"X-Tossinvest-Account": str(account_seq)})
        return resp

    async def get_market_indicators(self) -> List[Dict[str, Any]]:
        """시장 지표 현재가 조회 (GET /api/v1/market-indicators/prices) - KOSPI, KOSDAQ 등"""
        # KOSPI, KOSDAQ만 요청
        params = {"symbols": "KOSPI,KOSDAQ"}
        resp = await self._request_with_retry("GET", "/api/v1/market-indicators/prices", params=params)
        return resp if isinstance(resp, list) else []

    async def get_cash_balance(self, account_seq: int) -> Dict[str, Any]:
        """예수금/주문가능금액 조회 (여러 엔드포인트 시도)"""
        endpoints = [
            "/api/v1/order-info/buyable-amount",
            "/api/v1/accounts/balance",
            "/api/v1/cash",
        ]
        last_error = None
        for endpoint in endpoints:
            try:
                resp = await self._request_with_retry("GET", endpoint,
                    headers={"X-Tossinvest-Account": str(account_seq)})
                print(f"[TossClient] Cash balance fetched from {endpoint}")
                return resp
            except TossApiError as e:
                last_error = e
                if e.status_code == 404:
                    continue
                raise
        # 모든 엔드포인트 실패
        raise last_error or TossApiError("Cash balance endpoints not found", status_code=404)

    async def get_exchange_rate(self) -> float:
        """실시간 USD/KRW 환율 조회 (캐시 1시간)"""
        # 캐시 확인
        if hasattr(self, '_exchange_rate_cache'):
            cached_rate, cached_time = self._exchange_rate_cache
            if (datetime.now() - cached_time).total_seconds() < 3600:  # 1시간
                return cached_rate

        rate = 1350.0  # 기본값
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 여러 무료 환율 API 시도
                apis = [
                    "https://api.exchangerate-api.com/v4/latest/USD",
                    "https://open.er-api.com/v6/latest/USD",
                    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
                ]
                for api in apis:
                    try:
                        resp = await client.get(api)
                        if resp.status_code == 200:
                            data = resp.json()
                            # 응답 구조가 다를 수 있음
                            if "rates" in data:
                                rate = float(data["rates"].get("KRW", 1350.0))
                            elif "usd" in data:
                                rate = float(data["usd"].get("krw", 1350.0))
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        # 캐시 저장
        self._exchange_rate_cache = (rate, datetime.now())
        return rate

    # Note: 토스증권 Open API는 개별 종목 공공 시세/차트 API를 제공하지 않음
    # 실시간 시세는 GET /api/v1/holdings (보유 종목만) 응답에 포함됨
    # market-indicators/prices는 KOSPI/KOSDAQ 지수 전용

    # ==========================================
    # Order API (주문 기능)
    # ==========================================

    async def place_order(
        self,
        symbol: str,
        side: str,  # "BUY" | "SELL"
        order_type: str,  # "LIMIT" | "MARKET" | "MARKET_ON_CLOSE"
        time_in_force: str,  # "DAY" | "IOC" | "FOK"
        quantity: int,
        price: Optional[float] = None,
        order_amount: Optional[float] = None,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        주문 접수 (POST /api/v1/orders)

        Args:
            symbol: 종목코드 (KR: 6자리, US: 티커)
            side: BUY(매수) | SELL(매도)
            order_type: LIMIT(지정가) | MARKET(시장가) | MARKET_ON_CLOSE(장마감시장가)
            time_in_force: DAY(당일) | IOC(즉시체결취소) | FOK(전량체결취소)
            quantity: 주문 수량
            price: 지정가 주문 시 필수 (KRW/USD)
            order_amount: 시장가 주문 시 금액 지정 가능
            account_seq: 계좌번호 (미지정 시 인스턴스 변수 사용)
        """
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for order")

        body = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "timeInForce": time_in_force,
            "quantity": str(quantity)
        }

        if order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            body["price"] = str(price)
        elif order_type in ("MARKET", "MARKET_ON_CLOSE"):
            if order_amount is not None:
                body["orderAmount"] = str(order_amount)

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("POST", "/api/v1/orders", json=body, headers=headers)
        return resp

    async def cancel_order(
        self,
        order_id: str,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """주문 취소 (POST /api/v1/orders/{orderId}/cancel)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for order cancel")

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("POST", f"/api/v1/orders/{order_id}/cancel", headers=headers)
        return resp

    async def replace_order(
        self,
        order_id: str,
        quantity: int,
        price: Optional[float] = None,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """주문 정정 (POST /api/v1/orders/{orderId}/replace)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for order replace")

        body = {"quantity": str(quantity)}
        if price is not None:
            body["price"] = str(price)

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("POST", f"/api/v1/orders/{order_id}/replace", json=body, headers=headers)
        return resp

    async def get_orders(
        self,
        status: str,  # "OPEN" | "CLOSED"
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """주문 목록 조회 (GET /api/v1/orders)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for order list")

        params = {"status": status}
        if symbol:
            params["symbol"] = symbol
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = str(limit)

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("GET", "/api/v1/orders", params=params, headers=headers)
        return resp

    async def get_order_detail(
        self,
        order_id: str,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """주문 상세 조회 (GET /api/v1/orders/{orderId})"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for order detail")

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("GET", f"/api/v1/orders/{order_id}", headers=headers)
        return resp

    async def get_buying_power(
        self,
        currency: str,  # "KRW" | "USD"
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """매수 가능 금액 조회 (GET /api/v1/buying-power)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for buying power")

        params = {"currency": currency}
        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("GET", "/api/v1/buying-power", params=params, headers=headers)
        return resp

    async def get_sellable_quantity(
        self,
        symbol: str,
        account_seq: Optional[int] = None
    ) -> Dict[str, Any]:
        """판매 가능 수량 조회 (GET /api/v1/sellable-quantity)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for sellable quantity")

        params = {"symbol": symbol}
        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("GET", "/api/v1/sellable-quantity", params=params, headers=headers)
        return resp

    async def get_commissions(
        self,
        account_seq: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """매매 수수료 조회 (GET /api/v1/commissions)"""
        seq = account_seq if account_seq is not None else self.account_seq
        if seq is None:
            raise ValueError("account_seq required for commissions")

        headers = {"X-Tossinvest-Account": str(seq)}
        resp = await self._request_with_retry("GET", "/api/v1/commissions", headers=headers)
        return resp if isinstance(resp, list) else []

    # ==========================================
    # Data Transformation: API Response → data.json Portfolio
    # ==========================================

    def transform_holdings_to_portfolio(self, holdings_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        토스증권 API 응답(HoldingsOverview)을 data.json portfolio 구조로 변환

        변환 매핑:
        ┌─────────────────────────────────────────────────────────────────────────────┐
        │ API 응답 필드                    │ data.json 필드                           │ 비고                        │
        ├─────────────────────────────────────────────────────────────────────────────┤
        │ marketValue.amount.krw/usd       │ total_balance (string, "₩..." or "$...") │ KRW+USD 합계 원화환산        │
        │ marketValue.amount.krw           │ cash_krw (number)                        │                             │
        │ marketValue.amount.usd           │ cash_usd (number)                        │                             │
        │ items[].symbol                   │ holdings[].code                          │                             │
        │ items[].name                     │ holdings[].name                          │                             │
        │ items[].marketCountry            │ holdings[].market ("KR" | "US")          │ KR/US 구분                 │
        │ items[].currency                 │ holdings[].currency ("KRW" | "USD")      │                             │
        │ items[].quantity                 │ holdings[].qty (int)                     │                             │
        │ items[].averagePurchasePrice     │ holdings[].avg_price (float)             │                             │
        │ items[].lastPrice                │ holdings[].current_price (float)         │                             │
        │ items[].profitLoss.amount        │ holdings[].unrealized_pnl (float)        │                             │
        │ items[].profitLoss.rate          │ holdings[].roi (float, %)                │ rate * 100                  │
        │ profitLoss.rate (전체)           │ daily_profit 계산용                       │                             │
        │ dailyProfitLoss.amount           │ daily_profit 계산용                       │                             │
        └─────────────────────────────────────────────────────────────────────────────┘
        """
        if not holdings_data:
            return self._empty_portfolio()

        # 전체 평가금액 (KRW + USD 원화환산)
        market_value = holdings_data.get("marketValue", {}).get("amount", {})
        krw_value = float(market_value.get("krw", 0))
        usd_value = float(market_value.get("usd", 0))

        # 실시간 환율 조회 (비동기 불가하므로 동기 호출 또는 캐시 사용)
        # 여기서는 기존 캐시된 값 사용, 필요시 별도 메서드에서 업데이트
        if not hasattr(self, '_usd_to_krw') or self._usd_to_krw is None:
            self._usd_to_krw = 1350.0
        total_krw = krw_value + (usd_value * self._usd_to_krw)

        # 현금성 자산 (예수금) - 별도 API에서 조회 필요
        # transform 시점에 account_seq가 없으므로 0으로 설정, PortfolioTeam에서 업데이트
        cash_krw = 0
        cash_usd = 0

        # Holdings 변환
        holdings = []
        for item in holdings_data.get("items", []):
            market_country = item.get("marketCountry", "KR")
            currency = item.get("currency", "KRW")
            qty = int(item.get("quantity", 0))
            avg_price = float(item.get("averagePurchasePrice", 0))
            current_price = float(item.get("lastPrice", 0))

            # 손익 계산 - API 응답에서 profitLoss.amount가 문자열일 수 있음 (이미 해당 통화 금액)
            profit_loss = item.get("profitLoss", {})
            pnl_amount_raw = profit_loss.get("amount", 0)
            if isinstance(pnl_amount_raw, dict):
                # amount가 객체인 경우 (KRW/USD 분리)
                pnl_amount = float(pnl_amount_raw.get("krw" if market_country == "KR" else "usd", 0))
            else:
                # amount가 문자열/숫자인 경우 (이미 해당 통화 금액)
                pnl_amount = float(pnl_amount_raw)
            pnl_rate = float(profit_loss.get("rate", 0)) * 100  # % 변환

            holdings.append({
                "code": item.get("symbol"),
                "name": item.get("name"),
                "market": market_country,
                "currency": currency,
                "qty": qty,
                "avg_price": round(avg_price, 2),
                "current_price": round(current_price, 2),
                "unrealized_pnl": round(pnl_amount, 2),
                "roi": round(pnl_rate, 2)
            })

        # 통화별 현금 (예수금은 별도 API 필요, 일단 0)
        # 실제로는 GET /api/v1/order-info/buyable-amount 등으로 조회해야 함

        return {
            "total_balance": self._format_currency(total_krw, "KRW"),
            "cash": self._format_currency(cash_krw, "KRW") + " / " + self._format_currency(cash_usd, "USD") if cash_usd > 0 else self._format_currency(cash_krw, "KRW"),
            "cash_krw": cash_krw,
            "cash_usd": cash_usd,
            "holdings": holdings
        }

    def update_portfolio_with_cash(self, portfolio: Dict[str, Any], cash_data: Dict[str, Any], usd_to_krw: float) -> Dict[str, Any]:
        """예수금 정보를 포트폴리오에 반영하고 환율 업데이트"""
        if not cash_data:
            return portfolio

        self._usd_to_krw = usd_to_krw

        # 예수금 파싱 (API 응답 구조에 따라 조정 필요)
        # 예상: {"buyableAmount": {"krw": "1000000", "usd": "500.00"}, ...}
        buyable = cash_data.get("buyableAmount", cash_data.get("amount", {}))
        cash_krw = float(buyable.get("krw", 0))
        cash_usd = float(buyable.get("usd", 0))

        # Holdings의 USD 평가금액도 새 환율로 재계산
        usd_holdings_value = sum(
            h.get("current_price", 0) * h.get("qty", 0)
            for h in portfolio.get("holdings", [])
            if h.get("currency") == "USD"
        )
        krw_holdings_value = sum(
            h.get("current_price", 0) * h.get("qty", 0)
            for h in portfolio.get("holdings", [])
            if h.get("currency") == "KRW"
        )

        total_krw = krw_holdings_value + (usd_holdings_value * usd_to_krw) + cash_krw + (cash_usd * usd_to_krw)

        portfolio["cash_krw"] = cash_krw
        portfolio["cash_usd"] = cash_usd
        portfolio["total_balance"] = self._format_currency(total_krw, "KRW")
        portfolio["cash"] = self._format_currency(cash_krw, "KRW") + " / " + self._format_currency(cash_usd, "USD") if cash_usd > 0 else self._format_currency(cash_krw, "KRW")

        return portfolio

    def transform_market_indicators(self, indicators_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        토스증권 API 응답(/api/v1/market-indicators/prices)을 data.json indices 구조로 변환

        실제 응답 필드:
        - symbol: "KOSPI" | "KOSDAQ"
        - timestamp: ISO 8601
        - lastPrice: 현재값 (number)

        참고: changeRate, volume은 prices 엔드포인트에서 제공하지 않음.
        candles 엔드포인트로 별도 조회 필요. 여기서는 lastPrice만 사용하고
        change_rate는 0으로, volume은 None으로 설정.
        """
        results = []
        for item in indicators_data:
            symbol = item.get("symbol", "")
            if symbol not in ("KOSPI", "KOSDAQ"):
                continue

            results.append({
                "index_name": symbol,
                "current_value": float(item.get("lastPrice", 0)),
                "change_rate": 0.0,  # prices 엔드포인트에서 제공 안 함
                "volume": None,       # prices 엔드포인트에서 제공 안 함
                "timestamp": item.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            })
        return results

    def _format_currency(self, amount: float, currency: str) -> str:
        """통화 포맷팅"""
        if currency == "KRW":
            return f"₩{int(amount):,}"
        elif currency == "USD":
            return f"${amount:,.2f}"
        return str(amount)

    def _empty_portfolio(self) -> Dict[str, Any]:
        return {
            "total_balance": "₩0",
            "cash": "₩0",
            "cash_krw": 0,
            "cash_usd": 0,
            "holdings": []
        }


def create_toss_client_from_env() -> TossClient:
    """환경변수에서 설정 읽어 TossClient 생성"""
    client_id = os.getenv("TOSS_CLIENT_ID") or os.getenv("TOSS_API_KEY")
    client_secret = os.getenv("TOSS_CLIENT_SECRET") or os.getenv("TOSS_SECRET_KEY")
    account_seq_str = os.getenv("TOSS_ACCOUNT_SEQ")

    if not client_id or not client_secret:
        raise ValueError("TOSS_CLIENT_ID (or TOSS_API_KEY) and TOSS_CLIENT_SECRET (or TOSS_SECRET_KEY) must be set in environment")

    account_seq = int(account_seq_str) if account_seq_str and account_seq_str.isdigit() else None

    return TossClient(client_id=client_id, client_secret=client_secret, account_seq=account_seq)