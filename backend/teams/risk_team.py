"""
Risk Team - 위험 관리팀
종목/섹터/계좌 리스크 한도 체크
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

from backend.models.schemas import (
    PortfolioSchema, HoldingSchema, TradingSignal, ActionType, MarketType
)


@dataclass
class RiskLimit:
    """리스크 한도 설정"""
    max_sector_weight: float = 0.30      # 섹터별 최대 30%
    max_stock_weight: float = 0.10       # 종목별 최대 10%
    min_cash_ratio: float = 0.10         # 현금 비중 최소 10%
    max_daily_loss: float = 0.05         # 일일 최대 손실 5%
    max_drawdown: float = 0.15           # 최대 낙폭 15%
    max_leverage: float = 1.0            # 레버리지 제한 (현금 거래만)


@dataclass
class RiskCheckResult:
    """리스크 체크 결과"""
    passed: bool
    checks: Dict[str, Any]
    violations: List[str]
    warnings: List[str]
    timestamp: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RiskTeam:
    """
    위험 관리팀
    - 포트폴리오 리스크 한도 모니터링
    - 신규 주문 전 리스크 사전 체크
    - 섹터/종목/계좌 단위 한도 관리
    """

    # 섹터 매핑 (종목코드 → 섹터)
    SECTOR_MAP = {
        # 반도체
        "005930": "반도체", "000660": "반도체", "035420": "IT",
        "005940": "반도체", "000270": "자동차", "005380": "자동차",
        "035720": "IT", "000660": "반도체", "006400": "조선",
        "051910": "화학", "068270": "바이오", "207940": "바이오",
        "028260": "바이오", "326030": "바이오", "096530": "2차전지",
        "003550": "조선", "012330": "철강", "009540": "조선",
        "017670": "통신", "030200": "통신", "015760": "전력",
        "001450": "조선", "010140": "철강", "010130": "화학",
        "055550": "게임", "034220": "IT", "259960": "게임",
    }

    DEFAULT_SECTOR = "기타"

    def __init__(self, limits: RiskLimit = None):
        self.limits = limits or RiskLimit()
        self._daily_pnl = 0.0
        self._peak_value = 0.0

    def get_sector(self, stock_code: str) -> str:
        """종목 섹터 조회"""
        return self.SECTOR_MAP.get(stock_code, self.DEFAULT_SECTOR)

    def check_portfolio_risk(self, portfolio) -> RiskCheckResult:
        """
        현재 포트폴리오 리스크 체크
        portfolio can be PortfolioSchema or dict
        """
        checks = {}
        violations = []
        warnings = []

        holdings = portfolio.holdings if hasattr(portfolio, 'holdings') else portfolio.get("holdings", [])

        if not portfolio or not holdings:
            return RiskCheckResult(
                passed=True,
                checks={"message": "보유 종목 없음"},
                violations=[],
                warnings=[]
            )

        # 1. 총 평가액 계산
        total_value = 0
        krw_value = 0
        usd_value = 0

        for h in holdings:
            current_price = h.current_price if hasattr(h, 'current_price') else h.get("current_price", 0)
            qty = h.qty if hasattr(h, 'qty') else h.get("qty", 0)
            currency = h.currency if hasattr(h, 'currency') else h.get("currency", "KRW")

            holding_value = current_price * qty
            total_value += holding_value
            if currency == "KRW":
                krw_value += holding_value
            else:
                usd_value += holding_value

        # 2. 종목별 비중 체크
        stock_weights = {}
        for h in holdings:
            code = h.code if hasattr(h, 'code') else h.get("code", "")
            name = h.name if hasattr(h, 'name') else h.get("name", "")
            current_price = h.current_price if hasattr(h, 'current_price') else h.get("current_price", 0)
            qty = h.qty if hasattr(h, 'qty') else h.get("qty", 0)

            weight = (current_price * qty) / total_value if total_value > 0 else 0
            stock_weights[code] = weight
            if weight > self.limits.max_stock_weight:
                violations.append(
                    f"종목 비중 초과: {name}({code}) {weight:.1%} > {self.limits.max_stock_weight:.0%}"
                )
            elif weight > self.limits.max_stock_weight * 0.8:
                warnings.append(
                    f"종목 비중 주의: {name}({code}) {weight:.1%} (한도 {self.limits.max_stock_weight:.0%})"
                )

        checks["stock_weights"] = stock_weights

        # 3. 섹터별 비중 체크
        sector_weights = {}
        for h in holdings:
            code = h.code if hasattr(h, 'code') else h.get("code", "")
            current_price = h.current_price if hasattr(h, 'current_price') else h.get("current_price", 0)
            qty = h.qty if hasattr(h, 'qty') else h.get("qty", 0)

            sector = self.get_sector(code)
            holding_value = current_price * qty
            sector_weights[sector] = sector_weights.get(sector, 0) + holding_value

        sector_weight_pct = {s: v / total_value for s, v in sector_weights.items()} if total_value > 0 else {}
        for sector, weight in sector_weight_pct.items():
            if weight > self.limits.max_sector_weight:
                violations.append(
                    f"섹터 비중 초과: {sector} {weight:.1%} > {self.limits.max_sector_weight:.0%}"
                )
            elif weight > self.limits.max_sector_weight * 0.8:
                warnings.append(
                    f"섹터 비중 주의: {sector} {weight:.1%} (한도 {self.limits.max_sector_weight:.0%})"
                )

        checks["sector_weights"] = sector_weight_pct

        # 4. 현금 비중 체크
        cash_krw = portfolio.cash_krw if hasattr(portfolio, 'cash_krw') else portfolio.get("cash_krw", 0)
        cash_usd = portfolio.cash_usd if hasattr(portfolio, 'cash_usd') else portfolio.get("cash_usd", 0)
        # USD 현금도 원화 환산 필요 (간단히 1350원 가정)
        total_cash = cash_krw + (cash_usd * 1350)
        cash_ratio = total_cash / (total_value + total_cash) if (total_value + total_cash) > 0 else 1.0

        checks["cash_ratio"] = cash_ratio
        if cash_ratio < self.limits.min_cash_ratio:
            violations.append(
                f"현금 비중 부족: {cash_ratio:.1%} < {self.limits.min_cash_ratio:.0%}"
            )
        elif cash_ratio < self.limits.min_cash_ratio * 1.5:
            warnings.append(
                f"현금 비중 낮음: {cash_ratio:.1%} (권장 {self.limits.min_cash_ratio:.0%} 이상)"
            )

        # 5. 일일 손실 체크 (간단히 미실현 손익 기준)
        total_unrealized_pnl = 0
        for h in holdings:
            unrealized_pnl = h.unrealized_pnl if hasattr(h, 'unrealized_pnl') else h.get("unrealized_pnl", 0)
            total_unrealized_pnl += unrealized_pnl
        daily_loss_pct = abs(total_unrealized_pnl) / total_value if total_value > 0 else 0

        checks["daily_loss_pct"] = daily_loss_pct
        if daily_loss_pct > self.limits.max_daily_loss:
            violations.append(
                f"일일 손실 한도 초과: {daily_loss_pct:.1%} > {self.limits.max_daily_loss:.0%}"
            )

        # 6. 최대 낙폭 체크 (피크 대비 현재 가치)
        if total_value > self._peak_value:
            self._peak_value = total_value
        drawdown = (self._peak_value - total_value) / self._peak_value if self._peak_value > 0 else 0

        checks["drawdown"] = drawdown
        if drawdown > self.limits.max_drawdown:
            violations.append(
                f"최대 낙폭 초과: {drawdown:.1%} > {self.limits.max_drawdown:.0%}"
            )

        passed = len(violations) == 0

        return RiskCheckResult(
            passed=passed,
            checks=checks,
            violations=violations,
            warnings=warnings
        )

    def check_order_risk(
        self,
        signal: TradingSignal,
        portfolio,
        current_price: float
    ) -> RiskCheckResult:
        """
        신규 주문 전 리스크 사전 체크
        주문 실행 시 포트폴리오가 어떻게 변할지 시뮬레이션
        """
        checks = {}
        violations = []
        warnings = []

        if signal.action != ActionType.BUY:
            # 매도/거절은 리스크 완화 방향이므로 패스
            return RiskCheckResult(
                passed=True,
                checks={"message": f"{signal.action.value} 주문은 리스크 완화 방향"},
                violations=[],
                warnings=[]
            )

        holdings = portfolio.holdings if hasattr(portfolio, 'holdings') else portfolio.get("holdings", [])

        # 시뮬레이션: 주문 후 포트폴리오 구성
        order_value = signal.current_price * (signal.quantity or 1)
        new_total_value = 0
        new_holdings = []

        # 기존 보유 종목 복사
        for h in holdings:
            h_price = h.current_price if hasattr(h, 'current_price') else h.get("current_price", 0)
            h_qty = h.qty if hasattr(h, 'qty') else h.get("qty", 0)
            h_code = h.code if hasattr(h, 'code') else h.get("code", "")
            h_name = h.name if hasattr(h, 'name') else h.get("name", "")

            holding_value = h_price * h_qty
            new_total_value += holding_value
            new_holdings.append({
                "code": h_code,
                "name": h_name,
                "value": holding_value,
                "sector": self.get_sector(h_code)
            })

        # 신규 주문 추가
        new_total_value += order_value
        new_holdings.append({
            "code": signal.code,
            "name": signal.name,
            "value": order_value,
            "sector": self.get_sector(signal.code)
        })

        # 종목별 비중 체크
        for h in new_holdings:
            weight = h["value"] / new_total_value
            if weight > self.limits.max_stock_weight:
                violations.append(
                    f"주문 후 종목 비중 초과 예상: {h['name']}({h['code']}) {weight:.1%}"
                )

        # 섹터별 비중 체크
        sector_values = {}
        for h in new_holdings:
            sector_values[h["sector"]] = sector_values.get(h["sector"], 0) + h["value"]

        for sector, value in sector_values.items():
            weight = value / new_total_value
            if weight > self.limits.max_sector_weight:
                violations.append(
                    f"주문 후 섹터 비중 초과 예상: {sector} {weight:.1%}"
                )

        # 현금 비중 체크 (주문 후 현금 감소 가정)
        cash_krw = portfolio.cash_krw if hasattr(portfolio, 'cash_krw') else portfolio.get("cash_krw", 0)
        cash_usd = portfolio.cash_usd if hasattr(portfolio, 'cash_usd') else portfolio.get("cash_usd", 0)
        total_cash = cash_krw + (cash_usd * 1350)
        remaining_cash = total_cash - order_value
        new_cash_ratio = remaining_cash / new_total_value if new_total_value > 0 else 0

        if new_cash_ratio < self.limits.min_cash_ratio:
            violations.append(
                f"주문 후 현금 비중 부족 예상: {new_cash_ratio:.1%}"
            )

        passed = len(violations) == 0

        return RiskCheckResult(
            passed=passed,
            checks={
                "order_value": order_value,
                "new_total_value": new_total_value,
                "new_cash_ratio": new_cash_ratio,
                "simulated_weights": {h["code"]: h["value"]/new_total_value for h in new_holdings}
            },
            violations=violations,
            warnings=warnings
        )

    def check_daily_loss_limit(self, portfolio) -> bool:
        """일일 손실 한도 체크 (실시간 모니터링용)"""
        holdings = portfolio.holdings if hasattr(portfolio, 'holdings') else portfolio.get("holdings", [])
        if not portfolio or not holdings:
            return True

        total_value = 0
        total_unrealized_pnl = 0
        for h in holdings:
            current_price = h.current_price if hasattr(h, 'current_price') else h.get("current_price", 0)
            qty = h.qty if hasattr(h, 'qty') else h.get("qty", 0)
            unrealized_pnl = h.unrealized_pnl if hasattr(h, 'unrealized_pnl') else h.get("unrealized_pnl", 0)
            total_value += current_price * qty
            total_unrealized_pnl += unrealized_pnl
        daily_loss_pct = abs(total_unrealized_pnl) / total_value if total_value > 0 else 0

        return daily_loss_pct <= self.limits.max_daily_loss

    def get_risk_summary(self, portfolio) -> Dict[str, Any]:
        """리스크 요약 정보 (대시보드/리포트용)"""
        result = self.check_portfolio_risk(portfolio)
        return {
            "overall_status": "PASS" if result.passed else "VIOLATION",
            "violations_count": len(result.violations),
            "warnings_count": len(result.warnings),
            "key_metrics": {
                "max_stock_weight": max(result.checks.get("stock_weights", {}).values()) if result.checks.get("stock_weights") else 0,
                "max_sector_weight": max(result.checks.get("sector_weights", {}).values()) if result.checks.get("sector_weights") else 0,
                "cash_ratio": result.checks.get("cash_ratio", 0),
                "drawdown": result.checks.get("drawdown", 0)
            },
            "violations": result.violations,
            "warnings": result.warnings,
            "checked_at": result.timestamp
        }


def create_risk_team() -> RiskTeam:
    """기본 설정으로 RiskTeam 생성"""
    return RiskTeam()