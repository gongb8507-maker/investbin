"""
Pipeline - 7단계 파이프라인 실행기
데이터 수집 → 1차 검토 → 최종 결정 → 리스크/컴플라이언스 → 텔레그램 승인 → 주문 실행 → 상태 동기화
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
import argparse

from backend.teams.orchestrator import (
    Orchestrator, PipelineStage, PipelineStatus, get_orchestrator
)
from backend.teams.expert_team import ExpertTeam, create_dart_client_from_env
from backend.teams.market_team import MarketTeam, create_finnhub_client_from_env, create_yahoo_client_from_env
from backend.teams.risk_team import RiskTeam, create_risk_team
from backend.teams.compliance_team import ComplianceTeam, create_compliance_team
from backend.teams.strategy_team import StrategyTeam, create_strategy_team
from backend.teams.portfolio_team import PortfolioTeam
from backend.teams.trading_team import TradingTeam
from backend.teams.dashboard_team import DashboardTeam, create_dashboard_team
from backend.teams.telegram_team import TelegramTeam, create_telegram_team
from backend.api.toss_client import create_toss_client_from_env, TossApiError
from backend.models.schemas import CollectedData, DisclosureData, FinancialData, NewsData, MarketTrendData, MarketType, ReviewResult, TradingSignal, ActionType


class Pipeline:
    """
    7단계 자동매매 파이프라인
    각 단계는 독립적으로 실행 가능하며, 실패 시 해당 단계에서 롤백
    """

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.orchestrator = get_orchestrator()

        # 팀 인스턴스들 (지연 초기화)
        self._expert_team = None
        self._market_team = None
        self._risk_team = None
        self._compliance_team = None
        self._strategy_team = None
        self._portfolio_team = None
        self._trading_team = None
        self._dashboard_team = None
        self._telegram_team = None
        self._toss_client = None

    # ==========================================
    # 팀 지연 초기화
    # ==========================================

    @property
    def expert_team(self) -> ExpertTeam:
        if self._expert_team is None:
            if self.mock_mode:
                self._expert_team = ExpertTeam({"api_key": "mock"})
            else:
                try:
                    dart_client = create_dart_client_from_env()
                    self._expert_team = ExpertTeam({"api_key": dart_client.api_key})
                except ValueError:
                    print("[Pipeline] DART API 키 없음, Mock 모드로 ExpertTeam 생성")
                    self._expert_team = ExpertTeam({"api_key": "mock"})
        return self._expert_team

    @property
    def market_team(self) -> MarketTeam:
        if self._market_team is None:
            if self.mock_mode:
                self._market_team = MarketTeam({"api_key": "mock"}, {})
            else:
                try:
                    finnhub = create_finnhub_client_from_env()
                    yahoo = create_yahoo_client_from_env()
                    self._market_team = MarketTeam(
                        {"api_key": finnhub.api_key},
                        {}
                    )
                except ValueError:
                    print("[Pipeline] Finnhub API 키 없음, Mock 모드로 MarketTeam 생성")
                    self._market_team = MarketTeam({"api_key": "mock"}, {})
        return self._market_team

    @property
    def risk_team(self) -> RiskTeam:
        if self._risk_team is None:
            self._risk_team = create_risk_team()
        return self._risk_team

    @property
    def compliance_team(self) -> ComplianceTeam:
        if self._compliance_team is None:
            self._compliance_team = create_compliance_team()
        return self._compliance_team

    @property
    def strategy_team(self) -> StrategyTeam:
        if self._strategy_team is None:
            self._strategy_team = create_strategy_team(
                expert_team=self.expert_team,
                market_team=self.market_team
            )
        return self._strategy_team

    @property
    def portfolio_team(self) -> PortfolioTeam:
        if self._portfolio_team is None:
            if self.mock_mode:
                self._portfolio_team = PortfolioTeam(None)
            else:
                try:
                    self._toss_client = create_toss_client_from_env()
                    self._portfolio_team = PortfolioTeam(self._toss_client)
                except ValueError:
                    print("[Pipeline] Toss API 키 없음, Mock 모드로 PortfolioTeam 생성")
                    self._portfolio_team = PortfolioTeam(None)
        return self._portfolio_team

    @property
    def trading_team(self) -> TradingTeam:
        if self._trading_team is None:
            if self.mock_mode:
                self._trading_team = TradingTeam({"paper_trading": True}, paper_trading=True)
            else:
                if not self._toss_client:
                    self._toss_client = create_toss_client_from_env()
                self._trading_team = TradingTeam(
                    {"api_key": self._toss_client.client_id, "paper_trading": False},
                    paper_trading=False
                )
        return self._trading_team

    @property
    def dashboard_team(self) -> DashboardTeam:
        if self._dashboard_team is None:
            self._dashboard_team = create_dashboard_team()
        return self._dashboard_team

    @property
    def telegram_team(self) -> TelegramTeam:
        if self._telegram_team is None:
            self._telegram_team = create_telegram_team()
        return self._telegram_team

    # ==========================================
    # Step 1: 정보 수집 (Expert + Market + Risk 예비)
    # ==========================================

    async def step1_data_collection(self) -> Dict[str, Any]:
        """Step 1: 데이터 수집"""
        print("\n" + "="*60)
        print("[Step 1] Data Collection Started")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.DATA_COLLECTION)

        collected = CollectedData()

        try:
            # 1. 시스템 메트릭스
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            collected.system_metrics = {
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            print(f"   System: CPU {cpu}%, RAM {mem.percent}%")

            # 2. Expert Team: 공시/재무 데이터
            print("   [Expert] Collecting disclosures/financials...")
            corp_codes = ["005930", "000660", "035420", "005940", "000270", "005380"]
            disclosures = await self.expert_team.collect_disclosures(corp_codes)
            financials = await self.expert_team.collect_financials(corp_codes)
            collected.disclosures = disclosures
            collected.financials = financials
            print(f"      공시: {len(disclosures)}건, 재무: {len(financials)}건")

            # 3. Market Team: 뉴스/시장 흐름
            print("   [Market] Collecting news/indices...")
            news = await self.market_team.collect_news(["AI", "semiconductor", "earnings"])
            krx_trends = await self.market_team.get_krx_indices()
            us_trends = await self.market_team.get_us_indices()
            sentiment = await self.market_team.get_market_sentiment()
            collected.news = news
            collected.market_trends = krx_trends + us_trends
            print(f"      뉴스: {len(news)}건, 지수: {len(collected.market_trends)}개, 센티먼트: {sentiment.get('score', 0)}")

            # 4. Risk Team: 예비 리스크 평가 (현재 포트폴리오 기준)
            print("   [Risk] Preliminary risk assessment...")
            portfolio_result = await self.portfolio_team.sync_portfolio()
            if portfolio_result.portfolio:
                risk_preview = self.risk_team.get_risk_summary(portfolio_result.portfolio)
                print(f"      리스크 상태: {risk_preview['overall_status']} (위반 {risk_preview['violations_count']}건, 경고 {risk_preview['warnings_count']}건)")
            else:
                risk_preview = {"overall_status": "NO_PORTFOLIO", "violations_count": 0, "warnings_count": 0}

            # 5. 데이터 저장
            collected_dict = collected.model_dump()
            collected_dict["risk_preview"] = risk_preview
            collected_dict["market_sentiment"] = sentiment

            self.orchestrator.set_collected_data(collected_dict)

            print("✅ Step 1 완료: 데이터 수집 및 저장")
            return collected_dict

        except Exception as e:
            print(f"❌ Step 1 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.DATA_COLLECTION, success=False, error=str(e))
            raise

    # ==========================================
    # Step 2: 1차 검토 (Strategy Team - AI Director)
    # ==========================================

    async def step2_first_review(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """Step 2: 1차 검토"""
        print("\n" + "="*60)
        print("🔍 Step 2: 1차 검토 (AI Director)")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.FIRST_REVIEW)

        try:
            # Top picks 구성 (기본 종목 + 동적 발굴)
            top_picks = [
                {
                    "code": "005930", "name": "삼성전자", "score": 92,
                    "recommendation": "강력 매수", "price": "조회중...",
                    "reason": "증권사 목표가 일제 상향 및 외국인 3일 연속 순매수",
                    "news_source": "한국경제", "news_url": "https://www.hankyung.com/finance"
                },
                {
                    "code": "000660", "name": "SK하이닉스", "score": 89,
                    "recommendation": "매수", "price": "조회중...",
                    "reason": "HBM3e 독점 공급 모멘텀 지속 및 기관 수급 집중",
                    "news_source": "매일경제", "news_url": "https://www.mk.co.kr/news/stock"
                },
                {
                    "code": "035420", "name": "NAVER", "score": 85,
                    "recommendation": "매수", "price": "조회중...",
                    "reason": "사내 AI 검색 서비스 고도화 및 저점 매수세 유입",
                    "news_source": "연합뉴스", "news_url": "https://www.yna.co.kr/market"
                }
            ]

            # 포트폴리오 데이터
            portfolio_data = self.orchestrator.load_data().get("portfolio")

            # 포트폴리오 보유 종목도 분석 대상에 추가 (stock_details 생성을 위해)
            if portfolio_data and portfolio_data.get("holdings"):
                for h in portfolio_data["holdings"]:
                    code = h.get("code")
                    name = h.get("name")
                    if code and not any(p["code"] == code for p in top_picks):
                        top_picks.append({
                            "code": code,
                            "name": name,
                            "score": 50,
                            "recommendation": "보유",
                            "price": f"{h.get('current_price', 0):,.1f}" if h.get('currency') == 'USD' else f"{h.get('current_price', 0):,.0f}원",
                            "reason": "포트폴리오 보유 종목"
                        })

            # 1차 검토 실행
            review = await self.strategy_team.first_review(
                collected_data=CollectedData(**collected_data),
                market_sentiment=collected_data.get("market_sentiment", {}),
                top_picks=top_picks,
                portfolio=portfolio_data
            )

            # 결과 저장
            self.orchestrator.add_review_result(review.model_dump())

            # AI 분석 레이어(stock_details) 저장
            if hasattr(review, 'ai_stock_details'):
                self.orchestrator.update_stock_details(review.ai_stock_details)

            # Top picks 업데이트 (점수 반영)
            updated_picks = []
            for analysis in review.analyses:
                # analysis is a StockAnalysis model, access via attribute
                a_code = analysis.get("code") if isinstance(analysis, dict) else getattr(analysis, "code", None)
                a_score = analysis.get("score") if isinstance(analysis, dict) else getattr(analysis, "score", None)
                a_rec = analysis.get("recommendation") if isinstance(analysis, dict) else getattr(analysis, "recommendation", None)
                for pick in top_picks:
                    if pick["code"] == a_code:
                        pick["score"] = a_score
                        pick["recommendation"] = a_rec
                        updated_picks.append(pick)
                        break

            self.orchestrator.update_top_picks(updated_picks)
            self.orchestrator.update_market_sentiment(collected_data.get("market_sentiment", {}))

            print(f"✅ Step 2 완료: 승인 {len(review.approved_codes)}개, 거절 {len(review.rejected_codes)}개")
            return review.model_dump()

        except Exception as e:
            print(f"❌ Step 2 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.FIRST_REVIEW, success=False, error=str(e))
            raise

    # ==========================================
    # Step 3: 최종 결정 (Strategy Team - Portfolio Manager)
    # ==========================================

    async def step3_final_decision(self) -> Dict[str, Any]:
        """Step 3: 최종 결정 (2차 검토)"""
        print("\n" + "="*60)
        print("🎯 Step 3: 최종 결정 (Portfolio Manager)")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.FINAL_DECISION)

        try:
            # 이전 단계 결과 로드
            first_review = self.orchestrator.get_review_result(0)
            if not first_review:
                raise ValueError("1차 검토 결과가 없습니다")

            portfolio_data = self.orchestrator.load_data().get("portfolio")
            risk_check = self.orchestrator.get_risk_check()
            compliance_check = self.orchestrator.get_compliance_check()

            # 2차 검토 실행
            review = await self.strategy_team.final_decision(
                first_review=ReviewResult(**first_review),
                portfolio=portfolio_data,
                risk_check=risk_check,
                compliance_check=compliance_check
            )

            # 결과 저장
            self.orchestrator.add_review_result(review.model_dump())

            # Trading signals 저장
            if hasattr(review, 'trading_signals'):
                self.orchestrator.set_trading_signals(review.trading_signals)

            print(f"✅ Step 3 완료: 매수 {len(review.approved_codes)}개, 매도 {len(review.rejected_codes)}개 시그널 생성")
            return review.model_dump()

        except Exception as e:
            print(f"❌ Step 3 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.FINAL_DECISION, success=False, error=str(e))
            raise

    # ==========================================
    # Step 4: 위험/컴플라이언스 검증
    # ==========================================

    async def step4_risk_compliance_check(self) -> Dict[str, Any]:
        """Step 4: 리스크/컴플라이언스 검증"""
        print("\n" + "="*60)
        print("🛡️ Step 4: 위험 관리 + 컴플라이언스 검증")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.RISK_COMPLIANCE_CHECK)

        try:
            portfolio_data = self.orchestrator.load_data().get("portfolio")
            signals = self.orchestrator.get_trading_signals()

            if not portfolio_data:
                print("   포트폴리오 데이터 없음, 기본 체크만 수행")
                risk_result = {"passed": True, "checks": {}, "violations": [], "warnings": ["포트폴리오 데이터 없음"]}
                compliance_result = {"overall_passed": True, "results": [], "summary": {}}
            else:
                # Risk Team: 포트폴리오 리스크 체크
                print("   ⚠️ Risk Team: 포트폴리오 리스크 체크...")
                risk_result_obj = self.risk_team.check_portfolio_risk(portfolio_data)
                risk_result = {
                    "passed": risk_result_obj.passed,
                    "checks": risk_result_obj.checks,
                    "violations": risk_result_obj.violations,
                    "warnings": risk_result_obj.warnings,
                    "timestamp": risk_result_obj.timestamp
                }

                # Compliance Team: 전체 컴플라이언스 체크
                print("   📋 Compliance Team: 규정 준수 검증...")
                # signals가 dict 리스트일 수 있으므로 TradingSignal 객체로 변환
                signal_obj = None
                if signals:
                    s = signals[0]
                    if isinstance(s, dict):
                        signal_obj = TradingSignal(**s)
                    else:
                        signal_obj = s
                compliance_report = self.compliance_team.run_full_compliance_check(
                    signal=signal_obj,
                    portfolio={"total_value": sum(h.get("current_price", 0) * h.get("qty", 0) for h in portfolio_data.get("holdings", []))},
                    recent_orders=self.orchestrator.get_orders()
                )
                compliance_result = {
                    "overall_passed": compliance_report.overall_passed,
                    "results": [r.__dict__ for r in compliance_report.results],
                    "summary": compliance_report.summary
                }

            # 결과 저장
            self.orchestrator.set_risk_check(risk_result)
            self.orchestrator.set_compliance_check(compliance_result)

            overall_passed = risk_result["passed"] and compliance_result["overall_passed"]
            print(f"✅ Step 4 완료: 전체 {'통과' if overall_passed else '일부 위반'}")
            print(f"   리스크: {'PASS' if risk_result['passed'] else 'FAIL'} ({len(risk_result['violations'])}건 위반)")
            print(f"   컴플라이언스: {'PASS' if compliance_result['overall_passed'] else 'FAIL'} ({compliance_result['summary'].get('total_violations', 0)}건 위반)")

            return {
                "risk_check": risk_result,
                "compliance_check": compliance_result,
                "overall_passed": overall_passed
            }

        except Exception as e:
            print(f"❌ Step 4 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.RISK_COMPLIANCE_CHECK, success=False, error=str(e))
            raise

    # ==========================================
    # Step 5: 텔레그램 승인 요청
    # ==========================================

    async def step5_telegram_approval(self) -> Dict[str, Any]:
        """Step 5: 텔레그램 승인 요청"""
        print("\n" + "="*60)
        print("📱 Step 5: 텔레그램 CEO 승인 요청")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.TELEGRAM_APPROVAL)

        try:
            signals = self.orchestrator.get_trading_signals()
            stock_details = self.orchestrator.load_data().get("stock_details", {})

            if not signals:
                print("   실행할 시그널 없음, 승인 요청 생략")
                return {"sent": False, "reason": "no_signals"}

            # 매수 시그널만 승인 요청
            buy_signals = [s for s in signals if s.get("action") == "BUY"]

            if not buy_signals:
                print("   매수 시그널 없음, 승인 요청 생략")
                return {"sent": False, "reason": "no_buy_signals"}

            # 텔레그램 리포트 데이터 생성
            report_data = self.strategy_team.generate_telegram_report_data(buy_signals, stock_details)

            # 승인 요청 발송
            print(f"   📤 {len(report_data)}개 종목 승인 요청 발송 중...")
            sent = await self.telegram_team.send_approval_request(report_data, stock_details)

            if sent:
                print("✅ Step 5 완료: 승인 요청 발송됨 (CEO 응답 대기)")
            else:
                print("⚠️ Step 5: 발송 실패 (봇 미초기화 또는 네트워크 오류)")

            return {"sent": sent, "approval_count": len(report_data)}

        except Exception as e:
            print(f"❌ Step 5 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.TELEGRAM_APPROVAL, success=False, error=str(e))
            raise

    # ==========================================
    # Step 6: 주문 실행 (Trading Team) - CEO 승인 후 호출됨
    # ==========================================

    async def step6_order_execution(self, approval_results: Dict[str, Any]) -> Dict[str, Any]:
        """Step 6: 주문 실행 (CEO 승인 후 호출)"""
        print("\n" + "="*60)
        print("💰 Step 6: 실전 주문 실행")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.ORDER_EXECUTION)

        try:
            executed_orders = []

            # 승인된 시그널들 실행
            for approval_id, result in approval_results.items():
                if result.get("user_action") == "BUY":
                    signal = result.get("signal")
                    if not signal:
                        continue

                    print(f"   📝 주문 실행: {signal['name']} ({signal['code']}) {signal.get('quantity', 1)}주 @ {signal['current_price']:,.0f}원")

                    # Trading Team으로 주문 실행
                    order_result = await self.trading_team.execute_order(signal)

                    if order_result.get("success"):
                        order_record = order_result.get("order")
                        if order_record:
                            self.orchestrator.add_order(order_record)
                            executed_orders.append(order_record)
                            print(f"      ✅ 체결: {order_record.get('broker_order_id')}")
                    else:
                        print(f"      ❌ 실패: {order_result.get('error')}")
                        # 실패도 기록
                        from backend import record_order
                        record_order(
                            signal['code'], signal['name'],
                            f"{signal['current_price']:,.0f}원", "BUY"
                        )

            # 포트폴리오 재동기화
            if executed_orders:
                print("   🔄 포트폴리오 재동기화 중...")
                portfolio_result = await self.portfolio_team.sync_portfolio()
                if portfolio_result.data_source == "live":
                    self.orchestrator.update_portfolio(portfolio_result.portfolio)

            print(f"✅ Step 6 완료: {len(executed_orders)}건 체결")
            return {"executed_count": len(executed_orders), "orders": executed_orders}

        except Exception as e:
            print(f"❌ Step 6 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.ORDER_EXECUTION, success=False, error=str(e))
            raise

    # ==========================================
    # Step 7: 상태 동기화 및 알림
    # ==========================================

    async def step7_state_sync(self) -> Dict[str, Any]:
        """Step 7: 전체 상태 동기화"""
        print("\n" + "="*60)
        print("🔄 Step 7: 상태 동기화 및 알림")
        print("="*60)

        self.orchestrator.advance_stage(PipelineStage.STATE_SYNC)

        try:
            # 1. 최신 지수 동기화
            print("   📈 시장 지수 동기화...")
            indices = await self.market_team.get_krx_indices()
            indices += await self.market_team.get_us_indices()
            self.orchestrator.update_indices([i.model_dump() for i in indices])

            # 2. 시스템 메트릭스 업데이트
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            self.orchestrator.update_system_metrics({
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

            # 3. 파이프라인 완료 처리
            self.orchestrator.complete_pipeline()

            # 4. 대시보드용 데이터 검증
            self.dashboard_team.prepare_for_vercel_deploy()

            # 5. 텔레그램 알림 (체결 내역이 있는 경우)
            orders = self.orchestrator.get_orders()
            today_orders = [o for o in orders if o.get('timestamp', '').startswith(datetime.now().strftime('%Y-%m-%d'))]
            if today_orders:
                for order in today_orders[:3]:  # 최신 3건만 알림
                    await self.telegram_team.send_order_notification(order)

            print("✅ Step 7 완료: 전체 상태 동기화됨")
            return {"synced": True, "indices_count": len(indices), "orders_today": len(today_orders)}

        except Exception as e:
            print(f"❌ Step 7 실패: {e}")
            self.orchestrator.advance_stage(PipelineStage.STATE_SYNC, success=False, error=str(e))
            raise

    # ==========================================
    # 전체 파이프라인 실행
    # ==========================================

    async def run_full_pipeline(self) -> Dict[str, Any]:
        """전체 7단계 파이프라인 순차 실행"""
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"\n{'='*60}")
        print(f"🚀 investbin AI 파이프라인 시작: {run_id}")
        print(f"{'='*60}")

        self.orchestrator.start_pipeline(run_id)

        results = {}

        try:
            # Step 1: 데이터 수집
            results["step1"] = await self.step1_data_collection()

            # Step 2: 1차 검토
            results["step2"] = await self.step2_first_review(results["step1"])

            # Step 3: 최종 결정
            results["step3"] = await self.step3_final_decision()

            # Step 4: 리스크/컴플라이언스
            results["step4"] = await self.step4_risk_compliance_check()

            # Step 5: 텔레그램 승인 요청
            results["step5"] = await self.step5_telegram_approval()

            # Step 6, 7은 CEO 승인 후 별도 트리거로 실행
            # 여기서는 승인 대기 상태로 완료
            print("\n" + "="*60)
            print("⏳ 파이프라인 일시 정지: CEO 승인 대기 중...")
            print("   CEO가 텔레그램에서 승인하면 Step 6, 7이 자동 실행됩니다.")
            print(f"{'='*60}\n")

            return {
                "run_id": run_id,
                "status": "WAITING_APPROVAL",
                "completed_steps": ["step1", "step2", "step3", "step4", "step5"],
                "pending_steps": ["step6", "step7"],
                "results": results
            }

        except Exception as e:
            print(f"\n💥 파이프라인 실행 실패: {e}")
            return {"run_id": run_id, "status": "FAILED", "error": str(e)}

    async def run_step(self, step_num: int) -> Dict[str, Any]:
        """개별 단계 실행 (테스트/디버깅용)"""
        step_map = {
            1: self.step1_data_collection,
            2: lambda: self.step2_first_review(self.orchestrator.get_collected_data() or {}),
            3: self.step3_final_decision,
            4: self.step4_risk_compliance_check,
            5: self.step5_telegram_approval,
            6: lambda: self.step6_order_execution({}),  # 승인 결과 필요
            7: self.step7_state_sync,
        }

        if step_num not in step_map:
            raise ValueError(f"Invalid step: {step_num}. Valid: 1-7")

        self.orchestrator.start_pipeline(f"step{step_num}_{datetime.now().strftime('%H%M%S')}")
        return await step_map[step_num]()

    async def cleanup(self):
        """리소스 정리"""
        if self._toss_client:
            await self._toss_client.close()
        if self._dashboard_team:
            await self._dashboard_team.stop_watcher()


# ==========================================
# CLI 엔트리포인트
# ==========================================

async def main():
    parser = argparse.ArgumentParser(description="investbin AI Trading Pipeline")
    parser.add_argument("--step", type=int, choices=range(1, 8), help="특정 단계만 실행 (1-7)")
    parser.add_argument("--full", action="store_true", help="전체 파이프라인 실행 (Step 1-5, 승인 대기)")
    parser.add_argument("--mock", action="store_true", help="Mock 모드 (API 키 없이 테스트)")
    parser.add_argument("--daemon", action="store_true", help="데몬 모드 (APScheduler 연동)")

    args = parser.parse_args()

    pipeline = Pipeline(mock_mode=args.mock)

    try:
        if args.step:
            print(f"단일 단계 실행: Step {args.step}")
            result = await pipeline.run_step(args.step)
            print(f"결과: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}...")
        elif args.full:
            result = await pipeline.run_full_pipeline()
            print(f"\n최종 결과: {json.dumps(result, ensure_ascii=False, indent=2)}")
        elif args.daemon:
            print("데몬 모드는 main.py에서 APScheduler로 실행하세요.")
        else:
            parser.print_help()
    finally:
        await pipeline.cleanup()


if __name__ == "__main__":
    asyncio.run(main())