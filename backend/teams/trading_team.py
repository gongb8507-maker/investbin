"""
매수/매도 승인팀 (Trading Strategy Team)
시그널 생성 및 최종 결정 로직
"""
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from backend.models.schemas import (
    StockAnalysis, TradingSignal, ActionType, SignalStrength, OrderStatus, MarketType
)
from backend.api.toss_client import TossClient, create_toss_client_from_env, TossApiError


class TradingTeamError(Exception):
    """트레이딩 팀 에러"""
    pass


class TradingTeam:
    """
    트레이딩 팀 - 시그널 생성 및 결정

    역할: Expert/Market 데이터를 기반으로 매수/매도 시그널 생성, 2차 검토
    실제 주문 실행은 TossClient의 place_order 등 메서드 사용
    """

    def __init__(self, toss_config: dict, paper_trading: bool = True):
        self.toss_config = toss_config
        self.paper_trading = paper_trading
        self.toss_client: Optional[TossClient] = None
        self.account_seq: Optional[int] = None
        self.signal_history = []  # 시그널 이력

    async def _ensure_client(self):
        """TossClient 지연 초기화"""
        if self.toss_client is None:
            try:
                self.toss_client = create_toss_client_from_env()
                # 계좌 확보
                accounts = await self.toss_client.get_accounts()
                if accounts:
                    if self.toss_client.account_seq:
                        for acc in accounts:
                            if acc.get("accountSeq") == self.toss_client.account_seq:
                                self.account_seq = acc.get("accountSeq")
                                break
                    if not self.account_seq:
                        self.account_seq = accounts[0].get("accountSeq")
            except ValueError:
                # Mock 모드
                pass

    async def close(self):
        if self.toss_client:
            await self.toss_client.close()

    async def generate_trading_signals(self, analyses: List[StockAnalysis], market_sentiment: Dict[str, Any]) -> List[TradingSignal]:
        """
        분석 결과를 바탕으로 거래 시그널 생성
        """
        signals = []
        sentiment_score = market_sentiment.get('score', 50)

        for analysis in analyses:
            # 기본 시그널 설정
            base_score = analysis.score
            sentiment_factor = 1.0

            # 시장 센티먼트에 따라 가중치 조정
            if sentiment_score > 70:
                sentiment_factor = 1.2  # 강세장에서는 더 공격적
            elif sentiment_score < 30:
                sentiment_factor = 0.8  # 약세장에서는 보수적

            adjusted_score = min(100, int(base_score * sentiment_factor))

            # 시그널 강도 결정
            if adjusted_score >= 85:
                signal_strength = SignalStrength.STRONG_BUY
                action = ActionType.BUY
                quantity = 10  # 기본 수량
            elif adjusted_score >= 70:
                signal_strength = SignalStrength.BUY
                action = ActionType.BUY
                quantity = 5
            elif adjusted_score >= 55:
                signal_strength = SignalStrength.WEAK_BUY
                action = ActionType.BUY
                quantity = 1
            elif adjusted_score >= 45:
                signal_strength = SignalStrength.HOLD
                action = ActionType.HOLD
                quantity = 0
            elif adjusted_score >= 30:
                signal_strength = SignalStrength.WEAK_SELL
                action = ActionType.REJECT
                quantity = 0
            else:
                signal_strength = SignalStrength.STRONG_SELL
                action = ActionType.REJECT
                quantity = 0

            # 현재가 조회
            current_price = analysis.current_price if analysis.current_price > 0 else 72000

            signal = TradingSignal(
                signal_id=f"SIG_{datetime.now().strftime('%Y%m%d%H%M%S')}_{analysis.code}",
                code=analysis.code,
                name=analysis.name,
                market=analysis.market,
                action=action,
                current_price=current_price,
                confidence=adjusted_score,
                reason=analysis.reason,
                strategy="AI_Quant_v2",
                quantity=quantity
            )
            signals.append(signal)

        return signals

    async def filter_and_rank_signals(self, signals: List[TradingSignal]) -> List[TradingSignal]:
        """
        시그널 필터링 및 우선순위 부여
        """
        # 매수 신호만 필터링
        buy_signals = [s for s in signals if s.action == ActionType.BUY]
        if not buy_signals:
            return []

        # 신뢰도 순으로 정렬
        ranked = sorted(buy_signals, key=lambda x: x.confidence, reverse=True)
        return ranked[:3]  # 상위 3개만 추천

    async def create_approval_requests(self, signals: List[TradingSignal]) -> List[Dict[str, Any]]:
        """
        텔레그램 승인 요청 데이터 생성
        """
        approvals = []
        for signal in signals:
            approval = {
                "approval_id": f"APP_{datetime.now().strftime('%Y%m%d%H%M%S')}_{signal.code}",
                "signal_id": signal.signal_id,
                "chat_id": 1234567890,  # CEO 텔레그램 ID (설정 필요)
                "status": "PENDING",
                "action": signal.action.value,
                "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "signal_data": signal.model_dump()
            }
            approvals.append(approval)
        return approvals

    async def execute_order(self, signal: TradingSignal) -> Dict[str, Any]:
        """
        실제 주문 실행 (Toss API 연동)
        매수가능금액/매도가능수량 선체크 포함
        """
        await self._ensure_client()

        if not self.toss_client or not self.account_seq:
            return {
                "success": False,
                "error": "TossClient not initialized or no account",
                "signal": signal.model_dump()
            }

        try:
            # 매수 가능 금액 확인 (매수 시)
            if signal.action == ActionType.BUY:
                currency = "KRW" if signal.code.isdigit() else "USD"
                buying_power = await self.toss_client.get_buying_power(currency, self.account_seq)
                cash_power = float(buying_power.get("cashBuyingPower", 0))
                estimated_cost = (signal.current_price or 0) * (signal.quantity or 1) * 1.005  # 수수료 감안 0.5% 여유

                if cash_power < estimated_cost:
                    return {
                        "success": False,
                        "error": f"매수 가능 금액 부족: 보유 {cash_power:,.0f}{currency}, 필요 ~{estimated_cost:,.0f}{currency}",
                        "signal": signal.model_dump()
                    }

            # 매도 가능 수량 확인 (매도 시)
            if signal.action == ActionType.SELL:
                sellable = await self.toss_client.get_sellable_quantity(signal.code, self.account_seq)
                sellable_qty = float(sellable.get("sellableQuantity", 0))

                if sellable_qty < (signal.quantity or 0):
                    return {
                        "success": False,
                        "error": f"매도 가능 수량 부족: 가능 {sellable_qty}주, 요청 {signal.quantity}주",
                        "signal": signal.model_dump()
                    }

            # 주문 실행
            if signal.action == ActionType.BUY and signal.current_price is None:
                return {"success": False, "error": "BUY order requires price", "signal": signal.model_dump()}

            order_type = "LIMIT" if signal.action == ActionType.BUY else "MARKET"
            order_result = await self.toss_client.place_order(
                symbol=signal.code,
                side=signal.action.value,
                order_type=order_type,
                time_in_force="DAY",
                quantity=signal.quantity or 1,
                price=signal.current_price if signal.action == ActionType.BUY else None,
                account_seq=self.account_seq
            )

            # 주문 기록 생성
            order_record = {
                "order_id": order_result.get("orderId"),
                "approval_id": None,
                "signal_id": signal.signal_id,
                "code": signal.code,
                "name": signal.name,
                "market": signal.market.value if hasattr(signal.market, 'value') else signal.market,
                "action": signal.action.value,
                "price": signal.current_price or 0,
                "quantity": signal.quantity or 0,
                "amount": (signal.current_price or 0) * (signal.quantity or 0),
                "status": order_result.get("status", "PENDING"),
                "executed_at": order_result.get("orderedAt"),
                "executed_by": "CEO (Human-in-the-Loop 승인)",
                "broker_order_id": order_result.get("orderId"),
                "error_message": None
            }

            return {
                "success": True,
                "order": order_record,
                "broker_response": order_result
            }

        except TossApiError as e:
            return {
                "success": False,
                "error": f"Toss API 오류: {e}",
                "signal": signal.model_dump()
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "signal": signal.model_dump()
            }

    async def process_approval(self, approval_id: str, user_action: ActionType) -> Dict[str, Any]:
        """
        CEO 승인 처리
        """
        # 승인 데이터 조회 (데이터.json에서)
        # 실제 구현에서는 데이터.json에서 승인 요청 조회
        # 여기서는 더미 데이터로 시뮬레이션
        return {
            "approval_id": approval_id,
            "status": "EXECUTED" if user_action == ActionType.BUY else "REJECTED",
            "executed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }