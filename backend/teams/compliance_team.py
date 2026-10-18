"""
Compliance Team - 컴플라이언스팀
금융규정 준수 검증 (미공개정보 이용 금지, 시세조종 방지, 투자자 보호)
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from backend.models.schemas import TradingSignal, ActionType, OrderRecord, OrderStatus


class ComplianceRule(str, Enum):
    """컴플라이언스 규칙"""
    NO_INSIDER_TRADING = "미공개정보_이용_금지"
    NO_MARKET_MANIPULATION = "시세조종_방지"
    INVESTOR_PROTECTION = "투자자_보호"
    POSITION_LIMIT = "포지션_한도"
    ORDER_FREQUENCY = "주문_빈도_제한"
    TRADING_HOURS = "거래시간_준수"
    DISCLOSURE_OBLIGATION = "공시_의무"


@dataclass
class ComplianceCheckResult:
    """컴플라이언스 체크 결과"""
    passed: bool
    rule: ComplianceRule
    details: Dict[str, Any]
    violations: List[str]
    warnings: List[str]
    timestamp: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ComplianceReport:
    """전체 컴플라이언스 리포트"""
    overall_passed: bool
    results: List[ComplianceCheckResult]
    summary: Dict[str, Any]
    timestamp: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ComplianceTeam:
    """
    컴플라이언스팀
    - 금융투자업규정 준수 검증
    - 미공개 중요정보 이용 금지
    - 시세조종 행위 방지
    - 투자자 보호 의무 이행
    - 주문 실행 전/후 컴플라이언스 체크
    """

    # 한국거래소 정규장 시간
    MARKET_OPEN = "09:00"
    MARKET_CLOSE = "15:30"
    EXTENDED_HOURS_START = "08:30"  # 시간외 단일가
    EXTENDED_HOURS_END = "18:00"

    # 주문 빈도 제한 (초당/분당)
    MAX_ORDERS_PER_SECOND = 5
    MAX_ORDERS_PER_MINUTE = 30

    def __init__(self):
        self._order_history: List[Dict[str, Any]] = []
        self._daily_order_count = 0
        self._last_reset_date = datetime.now().date()

    def _reset_daily_counters(self):
        """일일 카운터 리셋 (자정 기준)"""
        today = datetime.now().date()
        if today != self._last_reset_date:
            self._daily_order_count = 0
            self._last_reset_date = today

    def is_trading_hours(self, check_time: datetime = None) -> bool:
        """정규 거래시간 여부 확인"""
        check_time = check_time or datetime.now()
        current_time = check_time.strftime("%H:%M")
        current_day = check_time.weekday()  # 0=월, 4=금, 5=토, 6=일

        # 주말 체크
        if current_day >= 5:
            return False

        # 정규장 시간
        if self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE:
            return True

        # 시간외 단일가
        if self.EXTENDED_HOURS_START <= current_time <= self.EXTENDED_HOURS_END:
            return True

        return False

    def check_insider_trading(
        self,
        signal: TradingSignal,
        user_info: Dict[str, Any] = None
    ) -> ComplianceCheckResult:
        """
        미공개 중요정보 이용 금지 체크
        - 임직원/관계자 거래 여부
        - 공시 전 거래 여부
        """
        violations = []
        warnings = []
        details = {}

        # 임직원 여부 체크 (실제로는 인사DB 연동 필요)
        is_insider = user_info.get("is_insider", False) if user_info else False
        if is_insider:
            # 임직원 거래는 사전 신고 필수
            pre_reported = user_info.get("pre_reported", False)
            if not pre_reported:
                violations.append("임직원 거래: 사전 신고 미이행")
            details["is_insider"] = is_insider
            details["pre_reported"] = pre_reported

        # 주요 공시 직전 거래 체크 (DART 연동 필요)
        # TODO: DART API로 해당 종목 최근 공시 조회 후 시점 비교

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.NO_INSIDER_TRADING,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_market_manipulation(
        self,
        signal: TradingSignal,
        recent_orders: List[OrderRecord] = None
    ) -> ComplianceCheckResult:
        """
        시세조종 방지 체크
        - 허수성 주문 (대량 주문 후 취소 반복)
        - 통정매매 의심
        - 고가/저가 유인 주문
        """
        violations = []
        warnings = []
        details = {}

        recent_orders = recent_orders or []

        # 1. 동일 종목 단시간 반복 주문/취소 체크
        same_stock_orders = [o for o in recent_orders if o.code == signal.code]
        cancel_count = sum(1 for o in same_stock_orders if o.status == OrderStatus.CANCELLED)
        if cancel_count >= 3:
            violations.append(f"동일 종목 반복 주문/취소: {cancel_count}회 (시세조종 의심)")

        # 2. 비정상적 호가 패턴 (시가/종가 근처 대량 주문)
        # 현재가 대비 주문가격 편차 체크
        if signal.action == ActionType.BUY and signal.current_price > 0:
            # 지정가 주문인데 현재가 대비 너무 높거나 낮은 경우
            pass  # TODO: 호가창 데이터 연동 시 구현

        # 3. 통정매매 의심 (동일 시점 매수/매도 쌍)
        buy_orders = [o for o in same_stock_orders if o.action == ActionType.BUY]
        sell_orders = [o for o in same_stock_orders if o.action == ActionType.SELL]
        for buy in buy_orders:
            for sell in sell_orders:
                if abs((datetime.fromisoformat(buy.executed_at.replace("/", "-")) -
                       datetime.fromisoformat(sell.executed_at.replace("/", "-"))).total_seconds()) < 60:
                    if buy.price == sell.price and buy.quantity == sell.quantity:
                        warnings.append("통정매매 의심: 동일 가격/수량 매수-매도 쌍 발견")

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.NO_MARKET_MANIPULATION,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_investor_protection(
        self,
        signal: TradingSignal,
        portfolio_value: float = 0
    ) -> ComplianceCheckResult:
        """
        투자자 보호 의무 체크
        - 적합성 원칙 (투자자 성향 대비 적절한 상품)
        - 설명 의무 (위험 고지)
        - 분산 투자 권고
        """
        violations = []
        warnings = []
        details = {}

        # 1. 투자 금액이 총 자산 대비 과도한지
        order_value = signal.current_price * (signal.quantity or 1)
        if portfolio_value > 0:
            allocation_ratio = order_value / portfolio_value
            if allocation_ratio > 0.5:
                violations.append(f"단일 종목 투자 비중 과도: {allocation_ratio:.1%} (총자산 대비 50% 초과)")
            elif allocation_ratio > 0.3:
                warnings.append(f"단일 종목 투자 비중 높음: {allocation_ratio:.1%} (분산 투자 권고)")

        details["order_value"] = order_value
        details["portfolio_value"] = portfolio_value
        details["allocation_ratio"] = order_value / portfolio_value if portfolio_value > 0 else 0

        # 2. 고위험 상품 투자 시 적합성 확인 필요
        # TODO: 투자자 성향 분석 연동

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.INVESTOR_PROTECTION,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_position_limit(
        self,
        signal: TradingSignal,
        current_holding_qty: int = 0
    ) -> ComplianceCheckResult:
        """
        포지션 한도 체크
        - 종목별 최대 보유 한도
        - 일일 매수/매도 한도
        """
        violations = []
        warnings = []
        details = {}

        # 종목별 보유 한도 (예: 발행주식수 1% 등)
        # 실제로는 종목별 발행주식수 조회 필요
        max_holding_qty = 100000  # 임시값
        new_qty = current_holding_qty + (signal.quantity or 0)

        if new_qty > max_holding_qty:
            violations.append(f"종목 보유 한도 초과 예상: {new_qty} > {max_holding_qty}")

        details["current_holding"] = current_holding_qty
        details["order_qty"] = signal.quantity
        details["projected_holding"] = new_qty
        details["max_allowed"] = max_holding_qty

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.POSITION_LIMIT,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_order_frequency(self) -> ComplianceCheckResult:
        """
        주문 빈도 제한 체크 (API rate limit 및 거래소 규정 준수)
        """
        violations = []
        warnings = []
        details = {}

        self._reset_daily_counters()
        self._daily_order_count += 1

        # 최근 1초 내 주문 수
        now = datetime.now()
        recent_1s = sum(1 for o in self._order_history
                       if (now - datetime.fromisoformat(o["timestamp"].replace("/", "-"))).total_seconds() < 1)

        # 최근 1분 내 주문 수
        recent_1m = sum(1 for o in self._order_history
                       if (now - datetime.fromisoformat(o["timestamp"].replace("/", "-"))).total_seconds() < 60)

        details["daily_count"] = self._daily_order_count
        details["recent_1s"] = recent_1s
        details["recent_1m"] = recent_1m

        if recent_1s >= self.MAX_ORDERS_PER_SECOND:
            violations.append(f"초당 주문 한도 초과: {recent_1s} >= {self.MAX_ORDERS_PER_SECOND}")

        if recent_1m >= self.MAX_ORDERS_PER_MINUTE:
            violations.append(f"분당 주문 한도 초과: {recent_1m} >= {self.MAX_ORDERS_PER_MINUTE}")

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.ORDER_FREQUENCY,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_trading_hours(self, signal: TradingSignal) -> ComplianceCheckResult:
        """
        거래시간 준수 체크
        """
        violations = []
        warnings = []
        details = {}

        now = datetime.now()
        is_trading = self.is_trading_hours(now)

        details["current_time"] = now.strftime("%H:%M")
        details["is_trading_hours"] = is_trading
        details["market_day"] = now.weekday() < 5

        # 장외 시간 주문 시 경고 (가능은 하나 불리할 수 있음)
        if not is_trading:
            warnings.append("정규 거래시간 외 주문: 체결 불확실/불리한 가격 가능성")

        # 주말 거래 시도
        if now.weekday() >= 5:
            violations.append("주말 거래 불가 (한국 시장 휴장)")

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.TRADING_HOURS,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def check_disclosure_obligation(
        self,
        signal: TradingSignal,
        portfolio: Dict[str, Any] = None
    ) -> ComplianceCheckResult:
        """
        공시 의무 체크 (5%룰, 임원 주요주주 변동 등)
        """
        violations = []
        warnings = []
        details = {}

        # 5%룰 체크 (지분율 5% 이상 보유 시 공시 의무)
        # 실제로는 발행주식수 대비 보유비율 계산 필요
        if portfolio:
            total_shares = portfolio.get("total_shares", 0)
            holding_qty = portfolio.get("holding_qty", 0)
            if total_shares > 0:
                ratio = holding_qty / total_shares
                if ratio >= 0.05:
                    # 공시 여부 확인 필요
                    warnings.append(f"지분율 {ratio:.2%} ≥ 5%: 공시 의무 발생 가능성")

        details["signal"] = signal.dict() if hasattr(signal, 'dict') else str(signal)

        passed = len(violations) == 0
        return ComplianceCheckResult(
            passed=passed,
            rule=ComplianceRule.DISCLOSURE_OBLIGATION,
            details=details,
            violations=violations,
            warnings=warnings
        )

    def run_full_compliance_check(
        self,
        signal: TradingSignal,
        portfolio: Dict[str, Any] = None,
        recent_orders: List[OrderRecord] = None,
        user_info: Dict[str, Any] = None
    ) -> ComplianceReport:
        """
        전체 컴플라이언스 체크 실행 (주문 전 필수)
        """
        results = []

        # 1. 거래시간
        results.append(self.check_trading_hours(signal))

        # 2. 미공개정보 이용 금지
        results.append(self.check_insider_trading(signal, user_info))

        # 3. 시세조종 방지
        results.append(self.check_market_manipulation(signal, recent_orders))

        # 4. 투자자 보호
        portfolio_value = portfolio.get("total_value", 0) if portfolio else 0
        results.append(self.check_investor_protection(signal, portfolio_value))

        # 5. 포지션 한도
        current_holding = portfolio.get("holding_qty", 0) if portfolio else 0
        results.append(self.check_position_limit(signal, current_holding))

        # 6. 주문 빈도
        results.append(self.check_order_frequency())

        # 7. 공시 의무
        results.append(self.check_disclosure_obligation(signal, portfolio))

        # 전체 결과
        overall_passed = all(r.passed for r in results)
        all_violations = [v for r in results for v in r.violations]
        all_warnings = [w for r in results for w in r.warnings]

        # 주문 기록에 추가 (빈도 체크용)
        self._order_history.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": signal.code,
            "action": signal.action.value
        })
        # 최근 1시간만 유지
        cutoff = datetime.now() - timedelta(hours=1)
        self._order_history = [
            o for o in self._order_history
            if datetime.fromisoformat(o["timestamp"].replace("/", "-")) > cutoff
        ]

        return ComplianceReport(
            overall_passed=overall_passed,
            results=results,
            summary={
                "total_checks": len(results),
                "passed_checks": sum(1 for r in results if r.passed),
                "failed_checks": sum(1 for r in results if not r.passed),
                "total_violations": len(all_violations),
                "total_warnings": len(all_warnings),
                "violations": all_violations,
                "warnings": all_warnings
            }
        )

    def get_compliance_summary(self) -> Dict[str, Any]:
        """컴플라이언스 현황 요약 (대시보드용)"""
        self._reset_daily_counters()
        return {
            "daily_order_count": self._daily_order_count,
            "is_trading_hours": self.is_trading_hours(),
            "current_time": datetime.now().strftime("%H:%M:%S"),
            "market_status": "OPEN" if self.is_trading_hours() else "CLOSED",
            "recent_orders_1h": len(self._order_history)
        }


def create_compliance_team() -> ComplianceTeam:
    """ComplianceTeam 인스턴스 생성"""
    return ComplianceTeam()