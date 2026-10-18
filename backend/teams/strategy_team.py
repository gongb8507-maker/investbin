"""
Strategy Team - 전략 팀
1차/2차 검토 로직 통합, AI 분석 레이어 생성 (expert_good/expert_bad/ceo_comment)
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

from backend.models.schemas import (
    StockAnalysis, TradingSignal, ActionType, SignalStrength,
    MarketType, ReviewResult, PipelineStage, CollectedData
)
from backend.teams.expert_team import ExpertTeam
from backend.teams.market_team import MarketTeam


@dataclass
class StrategyConfig:
    """전략 설정"""
    top_picks_count: int = 3
    min_score_threshold: int = 60
    sentiment_weight: float = 0.3
    fundamental_weight: float = 0.4
    technical_weight: float = 0.3


class StrategyTeam:
    """
    전략 팀
    - 1차 검토: Expert + Market 데이터 종합 분석, 종목 점수화
    - 2차 검토: 포트폴리오 관점 최종 매수/매도 리스트 확정
    - AI 분석 레이어 생성: expert_good, expert_bad, ceo_comment
    """

    def __init__(self, expert_team: ExpertTeam = None, market_team: MarketTeam = None, config: StrategyConfig = None):
        self.expert_team = expert_team
        self.market_team = market_team
        self.config = config or StrategyConfig()

    # ==========================================
    # AI 분석 레이어 생성 (핵심: 상세 뷰용)
    # ==========================================

    def generate_ai_analysis(
        self,
        stock_name: str,
        stock_code: str,
        change_rate: float,
        volume: int,
        holding_info: Dict[str, Any] = None
    ) -> Dict[str, str]:
        """
        investbin AI 분석 레이어 생성 (토스 데이터 위에 얹는 분석만)
        - 전담팀 호재/악재 한줄 요약
        - AI CEO 투자 포인트 한줄 코멘트
        """
        is_up = change_rate >= 0
        is_high_volume = volume > 10_000_000
        is_holding = holding_info is not None

        # 보유 종목인 경우 손익 정보 활용
        unrealized_pnl = holding_info.get("unrealized_pnl", 0) if holding_info else 0
        roi = holding_info.get("roi", 0) if holding_info else 0

        if is_up:
            if is_high_volume:
                expert_good = "기관/외국인 동반 매수세 강력, 수급 주도권 확보"
                expert_bad = "단기 과열 구간 진입, 차익 실현 매물 경계"
                if is_holding and unrealized_pnl > 0:
                    ceo_comment = f"{stock_name}: 강한 수급 모멘텀 확인, 평가익 {unrealized_pnl:+,.0f}원 발생 → 추가 상승 여력 충분하나 분할 매도 고려"
                else:
                    ceo_comment = f"{stock_name}: 강한 수급 모멘텀 확인, 추가 상승 여력 충분하나 분할 매수 권장"
            else:
                expert_good = "저점 대비 반등 시도, 기술적 지지선 확인"
                expert_bad = "거래량 부족으로 상승 지속성 검증 필요"
                if is_holding and unrealized_pnl > 0:
                    ceo_comment = f"{stock_name}: 기술적 반등 구간, 평가익 {unrealized_pnl:+,.0f}원 → 거래량 동반 시 추세 전환 신호로 판단, 홀딩 유지"
                else:
                    ceo_comment = f"{stock_name}: 기술적 반등 구간이나 거래량 동반 시 추세 전환 신호로 판단"
        else:
            if is_high_volume:
                expert_good = "고점 매물 소화 과정, 조정 마무리 신호 가능성"
                expert_bad = "기관/외국인 매도세 집중, 추가 하락 리스크 상존"
                if is_holding and unrealized_pnl < 0:
                    ceo_comment = f"{stock_name}: 대량 거래 수반 하락, 평가손 {unrealized_pnl:+,.0f}원 → 조정 폭 깊을 수 있음, 손절가 {holding_info.get('avg_price', 0)*0.95:,.0f}원 설정 후 관망"
                else:
                    ceo_comment = f"{stock_name}: 대량 거래 수반 하락으로 조정 폭 깊을 수 있음, 관망 후 진입 고려"
            else:
                expert_good = "저가 매수세 유입 대기 중, 지지선 형성 시도"
                expert_bad = "수급 공백 구간, 방향성 확인 전까지 변동성 확대"
                if is_holding and unrealized_pnl < 0:
                    ceo_comment = f"{stock_name}: 거래량 실종 상태 하락, 평가손 {unrealized_pnl:+,.0f}원 → 저점 확인 필요, 섣부른 진입/매도 자제"
                else:
                    ceo_comment = f"{stock_name}: 거래량 실종 상태 하락으로 저점 확인 필요, 섣부른 진입 자제"

        return {
            "expert_good": expert_good,
            "expert_bad": expert_bad,
            "ceo_comment": ceo_comment
        }

    def generate_non_holding_analysis(self, stock_name: str, stock_code: str) -> Dict[str, str]:
        """비보유 종목 안내 메시지"""
        return {
            "expert_good": "토스증권 API: 비보유 종목 공공 시세 미제공",
            "expert_bad": "보유 종목만 실시간 시세 조회 가능",
            "ceo_comment": f"{stock_name}: 토스 계좌 보유 시 실시간 시세 표시됨 (현재 비보유)"
        }

    # ==========================================
    # 1차 검토: 종합 분석 및 점수화
    # ==========================================

    async def first_review(
        self,
        collected_data: CollectedData,
        market_sentiment: Dict[str, Any],
        top_picks: List[Dict[str, Any]],
        portfolio: Dict[str, Any] = None
    ) -> ReviewResult:
        """
        1차 검토 (AI Director 관점)
        - 수집된 공시/재무/뉴스/시장 데이터 종합
        - 각 종목 점수화 (0-100)
        - 상세 뷰용 AI 분석 생성 (expert_good/expert_bad/ceo_comment)
        """
        analyses = []
        stock_details = {}

        # 포트폴리오 보유 종목 맵 생성
        holding_map = {}
        if portfolio and portfolio.get("holdings"):
            for h in portfolio["holdings"]:
                holding_map[h["code"]] = h

        # 1. Expert 팀 분석 (공시/재무 기반)
        corp_codes = [p["code"] for p in top_picks]
        expert_analyses = []
        if self.expert_team:
            try:
                expert_analyses = await self.expert_team.analyze_stocks(corp_codes)
            except Exception as e:
                print(f"[StrategyTeam] Expert 분석 실패: {e}")

        # 2. Market 팀 분석 (뉴스/시장 흐름 기반)
        market_report = {}
        if self.market_team:
            try:
                market_report = await self.market_team.generate_market_report()
            except Exception as e:
                print(f"[StrategyTeam] Market 분석 실패: {e}")

        # 3. 종목별 종합 분석
        sentiment_score = market_sentiment.get("score", 50)

        for pick in top_picks:
            code = pick["code"]
            name = pick["name"]

            # 기본 분석 객체 생성
            analysis = StockAnalysis(
                code=code,
                name=name,
                market=MarketType.KR if code.isdigit() else MarketType.US,
                current_price=0,
                score=50,
                recommendation=SignalStrength.HOLD,
                reason=pick.get("reason", ""),
                risk_level="보통"
            )

            # Expert 분석 반영
            expert_match = next((a for a in expert_analyses if a.code == code), None)
            if expert_match:
                analysis.score = expert_match.score
                analysis.recommendation = expert_match.recommendation
                analysis.reason = expert_match.reason
                analysis.risk_level = expert_match.risk_level
                analysis.target_price = expert_match.target_price
                analysis.expected_return = expert_match.expected_return
                analysis.holding_period = expert_match.holding_period
                if expert_match.current_price > 0:
                    analysis.current_price = expert_match.current_price

            # Market 센티먼트 가중치 적용
            sentiment_factor = 1.0
            if sentiment_score > 70:
                sentiment_factor = 1.15
            elif sentiment_score > 50:
                sentiment_factor = 1.05
            elif sentiment_score < 30:
                sentiment_factor = 0.85

            analysis.score = min(100, max(0, int(analysis.score * sentiment_factor)))

            # 추천 재조정
            if analysis.score >= 85:
                analysis.recommendation = SignalStrength.STRONG_BUY
                analysis.risk_level = "낮음"
            elif analysis.score >= 70:
                analysis.recommendation = SignalStrength.BUY
                analysis.risk_level = "보통"
            elif analysis.score >= 55:
                analysis.recommendation = SignalStrength.WEAK_BUY
            elif analysis.score >= 45:
                analysis.recommendation = SignalStrength.HOLD
            elif analysis.score >= 30:
                analysis.recommendation = SignalStrength.WEAK_SELL
                analysis.risk_level = "높음"
            else:
                analysis.recommendation = SignalStrength.STRONG_SELL
                analysis.risk_level = "높음"

            # AI 분석 레이어 생성 (상세 뷰용)
            holding_info = holding_map.get(code)
            if holding_info:
                # 보유 종목: 실시간 시세 기반 분석
                change_rate = 0  # 포트폴리오에는 changeRate 없음, 추후 토스 API에서 가져와야 함
                volume = 0
                ai_analysis = self.generate_ai_analysis(
                    name, code, change_rate, volume, holding_info
                )
            else:
                # 비보유 종목
                ai_analysis = self.generate_non_holding_analysis(name, code)

            stock_details[code] = {
                "code": code,
                "name": name,
                **ai_analysis,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            analyses.append(analysis)

        # 승인/거절 종목 분류
        approved = [a.code for a in analyses if a.recommendation in (SignalStrength.STRONG_BUY, SignalStrength.BUY, SignalStrength.WEAK_BUY)]
        rejected = [a.code for a in analyses if a.recommendation in (SignalStrength.WEAK_SELL, SignalStrength.SELL, SignalStrength.STRONG_SELL)]

        review = ReviewResult(
            stage=PipelineStage.FIRST_REVIEW,
            reviewed_by="AI Director (Strategy Team)",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            analyses=[a.model_dump() for a in analyses],
            approved_codes=approved,
            rejected_codes=rejected,
            notes=f"1차 검토 완료: {len(approved)}개 승인, {len(rejected)}개 거절, 센티먼트 {sentiment_score}점 반영"
        )

        # stock_details를 데이터에 포함 (orchestrator가 저장)
        review.ai_stock_details = stock_details

        return review

    # ==========================================
    # 2차 검토: 포트폴리오 관점 최종 확정
    # ==========================================

    async def final_decision(
        self,
        first_review: ReviewResult,
        portfolio: Dict[str, Any],
        risk_check: Dict[str, Any],
        compliance_check: Dict[str, Any]
    ) -> ReviewResult:
        """
        2차 검토 (Portfolio Manager 관점)
        - 1차 검토 결과 + 리스크/컴플라이언스 체크 결과 종합
        - 포트폴리오 현재 구성 고려 (섹터 중복, 현금 비중 등)
        - 최종 매수/매도 시그널 생성 (TradingSignal)
        """
        # analyses already contains dict from model_dump()
        analyses = []
        for a in first_review.analyses:
            if isinstance(a, dict):
                analyses.append(StockAnalysis(**a))
            else:
                analyses.append(a)

        # 1차 승인 종목 중 리스크/컴플라이언스 통과한 것만 최종 후보로
        risk_passed = risk_check.get("passed", True) if risk_check else True
        compliance_passed = compliance_check.get("overall_passed", True) if compliance_check else True

        final_candidates = []
        for analysis in analyses:
            if analysis.code in first_review.approved_codes:
                if risk_passed and compliance_passed:
                    final_candidates.append(analysis)

        # 포트폴리오 관점 추가 필터링
        current_holdings = {h["code"]: h for h in portfolio.get("holdings", [])} if portfolio else {}
        sector_exposure = {}
        for h in portfolio.get("holdings", []) if portfolio else []:
            sector = self._get_sector(h["code"])
            sector_exposure[sector] = sector_exposure.get(sector, 0) + h["current_price"] * h["qty"]

        total_portfolio_value = sum(h["current_price"] * h["qty"] for h in portfolio.get("holdings", [])) if portfolio else 0

        # 최종 시그널 생성
        trading_signals = []
        for analysis in final_candidates[:self.config.top_picks_count]:
            # 현재가 결정 (보유 종목이면 포트폴리오에서, 아니면 0)
            current_price = analysis.current_price
            if analysis.code in current_holdings:
                current_price = current_holdings[analysis.code]["current_price"]

            # 수량 결정 (간단 휴리스틱)
            if analysis.recommendation == SignalStrength.STRONG_BUY:
                quantity = 10
            elif analysis.recommendation == SignalStrength.BUY:
                quantity = 5
            else:
                quantity = 1

            # 목표가/손절가 설정
            target_price = None
            stop_loss = None
            if current_price > 0:
                if analysis.recommendation in (SignalStrength.STRONG_BUY, SignalStrength.BUY):
                    target_price = round(current_price * 1.15, -2)  # +15%
                    stop_loss = round(current_price * 0.93, -2)    # -7%
                else:
                    target_price = round(current_price * 1.10, -2)  # +10%
                    stop_loss = round(current_price * 0.95, -2)    # -5%

            signal = TradingSignal(
                signal_id=f"SIG_{datetime.now().strftime('%Y%m%d%H%M%S')}_{analysis.code}",
                code=analysis.code,
                name=analysis.name,
                market=analysis.market,
                action=ActionType.BUY,
                current_price=current_price,
                target_price=target_price,
                stop_loss=stop_loss,
                quantity=quantity,
                confidence=analysis.score,
                reason=analysis.reason,
                strategy="AI_Quant_v2_Portfolio_Aware",
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                expires_at=(datetime.now() + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
            )
            trading_signals.append(signal)

        # 매도 시그널: 손실 큰 보유 종목 또는 리스크 위반 종목
        for code, holding in current_holdings.items():
            roi = holding.get("roi", 0)
            unrealized_pnl = holding.get("unrealized_pnl", 0)

            # -10% 이상 손실 또는 리스크 위반 시 매도 고려
            should_sell = roi < -10 or unrealized_pnl < -500000  # 50만원 이상 손실

            if should_sell:
                signal = TradingSignal(
                    signal_id=f"SIG_{datetime.now().strftime('%Y%m%d%H%M%S')}_{code}_SELL",
                    code=code,
                    name=holding["name"],
                    market=MarketType.KR if code.isdigit() else MarketType.US,
                    action=ActionType.SELL,
                    current_price=holding["current_price"],
                    target_price=None,
                    stop_loss=None,
                    quantity=holding["qty"],  # 전량 매도
                    confidence=70,
                    reason=f"손실 확대 방지: ROI {roi:.1f}%, 평가손익 {unrealized_pnl:+,.0f}원",
                    strategy="Risk_Management",
                    created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    expires_at=(datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
                )
                trading_signals.append(signal)

        # 2차 검토 결과
        approved_codes = [s.code for s in trading_signals if s.action == ActionType.BUY]
        rejected_codes = [s.code for s in trading_signals if s.action == ActionType.SELL]

        review = ReviewResult(
            stage=PipelineStage.FINAL_DECISION,
            reviewed_by="Portfolio Manager (Strategy Team)",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            analyses=[a.model_dump() for a in analyses],
            approved_codes=approved_codes,
            rejected_codes=rejected_codes,
            notes=f"2차 검토 완료: 매수 {len(approved_codes)}개, 매도 {len(rejected_codes)}개 확정"
        )

        review.trading_signals = [s.model_dump() for s in trading_signals]

        return review

    def _get_sector(self, stock_code: str) -> str:
        """종목 섹터 조회 (RiskTeam과 공유)"""
        sector_map = {
            "005930": "반도체", "000660": "반도체", "035420": "IT",
            "005940": "반도체", "000270": "자동차", "005380": "자동차",
            "035720": "IT", "006400": "조선", "051910": "화학",
            "068270": "바이오", "207940": "바이오", "028260": "바이오",
            "326030": "바이오", "096530": "2차전지", "003550": "조선",
            "012330": "철강", "009540": "조선", "017670": "통신",
            "030200": "통신", "015760": "전력", "001450": "조선",
            "010140": "철강", "010130": "화학", "055550": "게임",
            "034220": "IT", "259960": "게임",
        }
        return sector_map.get(stock_code, "기타")

    # ==========================================
    # 텔레그램 리포트용 예측 정보 생성
    # ==========================================

    def generate_telegram_report_data(
        self,
        signals: List[TradingSignal],
        stock_details: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        텔레그램 승인 요청용 리포트 데이터 생성
        - 예측가, 보유기간, 목표 매도가 포함
        """
        report_items = []
        for signal in signals:
            detail = stock_details.get(signal.code, {})

            # 보유기간 추정
            if signal.action == ActionType.BUY:
                if signal.confidence >= 85:
                    holding_period = "중장기 (3-6개월)"
                elif signal.confidence >= 70:
                    holding_period = "중기 (1-3개월)"
                else:
                    holding_period = "단기 (1-4주)"
            else:
                holding_period = "즉시"

            # 목표 매도가 (매수 시그널인 경우)
            target_sell_price = signal.target_price
            if signal.action == ActionType.SELL:
                target_sell_price = signal.current_price

            report_items.append({
                "code": signal.code,
                "name": signal.name,
                "action": signal.action.value,
                "current_price": signal.current_price,
                "target_price": signal.target_price,
                "stop_loss": signal.stop_loss,
                "target_sell_price": target_sell_price,
                "quantity": signal.quantity,
                "confidence": signal.confidence,
                "holding_period": holding_period,
                "reason": signal.reason,
                "expert_good": detail.get("expert_good", ""),
                "expert_bad": detail.get("expert_bad", ""),
                "ceo_comment": detail.get("ceo_comment", "")
            })

        return report_items


def create_strategy_team(
    expert_team: ExpertTeam = None,
    market_team: MarketTeam = None
) -> StrategyTeam:
    """StrategyTeam 인스턴스 생성"""
    return StrategyTeam(expert_team=expert_team, market_team=market_team)