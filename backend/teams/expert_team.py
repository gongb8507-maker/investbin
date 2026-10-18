"""
전문 분석팀 (Stock Expert Team)
공시/재무 데이터 수집 및 분석
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import httpx

from backend.models.schemas import (
    DisclosureData, FinancialData, CollectedData, StockAnalysis
)
from backend.api.dart import DartClient, create_dart_client_from_env  # DART API 클라이언트


class ExpertTeamError(Exception):
    """전문 분석팀 에러"""
    pass


class ExpertTeam:
    """
전문 분석팀 - 데이터 수집 및 분석

    역할: DART API를 통한 공시/재무 데이터 수집, 정량적 분석
    """

    def __init__(self, dart_config: dict, httpx_timeout: int = 30):
        self.dart_client = DartClient(dart_config, httpx_timeout)
        self.cache = {}  # 캐싱을 위한 딕셔너리
        self.cache_ttl = timedelta(minutes=30)

    async def _is_cache_valid(self, key: str) -> bool:
        """캐시 유효성 검사"""
        if key not in self.cache:
            return False
        cached_time, _ = self.cache[key]
        return datetime.now() - cached_time < self.cache_ttl

    async def _get_from_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """캐시에서 데이터 조회"""
        if await self._is_cache_valid(key):
            _, data = self.cache[key]
            return data
        return None

    async def _set_cache(self, key: str, data: Dict[str, Any]):
        """캐시에 데이터 저장"""
        self.cache[key] = (datetime.now(), data)

    async def collect_disclosures(self, corp_codes: List[str]) -> List[DisclosureData]:
        """
        DART에서 공시 데이터 수집
        corp_codes: 종목 코드 목록 (예: ["005930", "000660"])
        """
        cache_key = f"disclosures_{'_'.join(sorted(corp_codes))}"
        cached = await self._get_from_cache(cache_key)
        if cached:
            return [DisclosureData(**item) for item in cached]

        try:
            # DART에서 최신 공시 데이터 가져오기
            disclosures = []
            for code in corp_codes:
                corp_disclosures = await self.dart_client.get_corp_disclosures(code)
                for d in corp_disclosures:
                    disclosures.append(DisclosureData(
                        corp_code=code,
                        corp_name=d.get('corp_name', ''),
                        report_name=d.get('report_nm', ''),
                        receipt_date=d.get('rcept_no', '')[:8],
                        summary=d.get('summary', '')[:200],
                        url=d.get('url', '')
                    ))
            await self._set_cache(cache_key, [d.dict() for d in disclosures])
            return disclosures
        except Exception as e:
            print(f"[Expert Team] 공시 데이터 수집 실패: {e}")
            return []

    async def collect_financials(self, corp_codes: List[str]) -> List[FinancialData]:
        """
        DART에서 재무제표 데이터 수집
        """
        cache_key = f"financials_{'_'.join(sorted(corp_codes))}"
        cached = await self._get_from_cache(cache_key)
        if cached:
            return [FinancialData(**item) for item in cached]

        try:
            financials = []
            for code in corp_codes:
                corp_financials = await self.dart_client.get_financial_statements(code)
                for f in corp_financials:
                    # 1년치 재무 데이터 수집
                    financials.append(FinancialData(
                        code=code,
                        name=f.get('corp_name', ''),
                        market_cap=float(f.get('market_cap', 0)) / 1e9,  # 조 단위
                        per=float(f.get('per', 0)) if f.get('per') else None,
                        pbr=float(f.get('pbr', 0)) if f.get('pbr') else None,
                        roe=float(f.get('roe', 0)) if f.get('roe') else None,
                        revenue=float(f.get('revenue', 0)) / 1e9,
                        operating_profit=float(f.get('operating_profit', 0)) / 1e9,
                        net_profit=float(f.get('net_profit', 0)) / 1e9,
                        debt_ratio=float(f.get('debt_ratio', 0)) if f.get('debt_ratio') else None
                    ))
            await self._set_cache(cache_key, [f.dict() for f in financials])
            return financials
        except Exception as e:
            print(f"[Expert Team] 재무 데이터 수집 실패: {e}")
            return []

    async def analyze_stocks(self, corp_codes: List[str]) -> List[StockAnalysis]:
        """
        수집된 데이터를 기반으로 종목 분석
        """
        disclosures = await self.collect_disclosures(corp_codes)
        financials = await self.collect_financials(corp_codes)

        analyses = []
        for code in corp_codes:
            # 기본 정보 초기화
            analysis = StockAnalysis(
                code=code,
                name="",
                market="KR",
                current_price=0,
                score=50,
                recommendation="보유",
                reason="",
                risk_level="보통"
            )

            # 재무 데이터 분석
            matching_financials = [f for f in financials if f.code == code]
            if matching_financials:
                latest = matching_financials[0]
                analysis.name = latest.name
                analysis.current_price = latest.per * latest.pbr if latest.per and latest.pbr else 100000
                analysis.market_cap = latest.market_cap

                # 기본 점수 계산 (0-100)
                score = 50
                if latest.roe and latest.roe > 0.15:
                    score += 15
                if latest.per and 10000 <= latest.per <= 50000:
                    score += 10
                if latest.pbr and 0.5 <= latest.pbr <= 3.0:
                    score += 10
                if latest.debt_ratio and latest.debt_ratio < 0.4:
                    score += 5
                analysis.score = min(100, max(0, score))

                # 추천 로직
                if score >= 80:
                    analysis.recommendation = "강력 매수"
                    analysis.risk_level = "낮음"
                elif score >= 60:
                    analysis.recommendation = "매수"
                    analysis.risk_level = "보통"
                elif score >= 40:
                    analysis.recommendation = "보유"
                elif score >= 20:
                    analysis.recommendation = "약매도"
                else:
                    analysis.recommendation = "매도"
                    analysis.risk_level = "높음"

            # 공시 분석
            matching_disclosures = [d for d in disclosures if d.corp_code == code]
            if matching_disclosures:
                latest_disc = max(matching_disclosures, key=lambda x: x.receipt_date)
                analysis.reason += f" 최근 공시: {latest_disc.report_name} ({latest_disc.receipt_date})"

            analyses.append(analysis)

        return analyses

    async def get_top_picks(self, top_n: int = 3) -> List[StockAnalysis]:
        """
        상위 추천 종목 추출 (기존 top_picks 대체)
        """
        # KRX에서 주요 종목 목록 가져오기
        krx_codes = ["005930", "000660", "035420", "005940", "000270", "005380"]
        analyses = await self.analyze_stocks(krx_codes)
        # 점수 순으로 정렬
        sorted_analyses = sorted(analyses, key=lambda x: x.score, reverse=True)
        return sorted_analyses[:top_n]

    async def generate_expert_recommendations(self) -> Dict[str, Any]:
        """
        전체 분석 보고서 생성
        """
        top_picks = await self.get_top_picks(5)
        all_analyses = await self.analyze_stocks([a.code for a in top_picks])

        return {
            "expert_analysis": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "recommendations": [a.dict() for a in top_picks],
                "all_analyses": [a.dict() for a in all_analyses]
            }
        }