"""
매수/매도 승인팀 (Trading Strategy Team)
시그널 생성 및 최종 결정 로직
"""
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from backend.models.schemas import (
    StockAnalysis, TradingSignal, ActionType, SignalStrength, OrderStatus
)
from backend.api.toss import TossClient


class TradingTeamError(Exception):
    """트레이딩 팀 에러"""
    pass


class TradingTeam:
    """
트레이딩 팀 - 시그널 생성 및 결정

    역할: Expert/Market 데이터를 기반으로 매수/매도 시그널 생성, 2차 검토
    """

    def __init__(self, toss_config: dict, paper_trading: bool = True):
        self.toss_client = TossClient(toss_config, paper_trading)
        self.signal_history = []  # 시그널 이력

    async def generate_trading_signals(self, analyses: List[StockAnalysis], market_sentiment: Dict[str, Any]) -> List[TradingSignal>:
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

            # 현재가 조회 (모의투자용 더미 가격 사용)
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

    async def filter_and_rank_signals(self, signals: List[TradingSignal]) -> List[TradingSignal>:
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

    async def create_approval_requests(self, signals: List[TradingSignal]) -> List[Dict[str, Any>:
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
                "signal_data": signal.dict()
            }
            approvals.append(approval)
        return approvals

    async def execute_order(self, signal: TradingSignal) -> Dict[str, Any]:
        """
        실제 주문 실행 (Toss API 연동)
        """
        try:
            # 현재가 조회
            price_data = await self.toss_client.get_current_price(signal.code)
            current_price = float(price_data.get('current_price', signal.current_price))

            # 수량 결정 (고정 수량 또는 금액 기반)
            quantity = signal.quantity if signal.quantity else 1

            # 주문 실행
            broker_response = await self.toss_client.place_order(
                code=signal.code,
                action=signal.action,
                quantity=quantity,
                price=current_price if signal.action == ActionType.BUY else 0,  # 매수 시 지정가, 매도 시 시장가
                order_type="limit" if signal.action == ActionType.BUY else "market"
            )

            # 주문 기록 생성
            order_record = self.toss_client.create_order_record(
                signal.code, signal.name, signal.market, signal.action,
                current_price, quantity, broker_response
            )

            return {
                "success": True,
                "order": order_record.dict(),
                "broker_response": broker_response
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "signal": signal.dict()
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