"""
Main Entry Point - investbin AI 자동매매 시스템
APScheduler 기반 24시간 무인 자동 실행 + CLI 지원
"""
import asyncio
import argparse
import sys
import os
import signal
from datetime import datetime, time as dt_time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.pipeline import Pipeline
from backend.teams.orchestrator import get_orchestrator, PipelineStatus
from backend.teams.dashboard_team import create_dashboard_team
from backend.ws_server import run_ws_server, get_ws_server


class InvestbinDaemon:
    """
    investbin AI 데몬 프로세스
    - 장중(09:00-15:30): 5분 간격 파이프라인 실행
    - 장외: 30분 간격 파이프라인 실행 (시장 데이터만 업데이트)
    - 주말/공휴일: 1시간 간격 (시스템 헬스체크만)
    """

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.pipeline = Pipeline(mock_mode=mock_mode)
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.dashboard_team = create_dashboard_team()
        self._running = False
        self._shutdown_event = asyncio.Event()

    def _is_market_hours(self) -> bool:
        """한국 주식 시장 거래시간 여부"""
        now = datetime.now()
        # 주말 체크
        if now.weekday() >= 5:  # 토(5), 일(6)
            return False

        current_time = now.time()
        # 정규장: 09:00 ~ 15:30
        market_open = dt_time(9, 0)
        market_close = dt_time(15, 30)

        return market_open <= current_time <= market_close

    def _is_extended_hours(self) -> bool:
        """시간외 거래 시간 여부"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False

        current_time = now.time()
        # 시간외 단일가: 16:00 ~ 18:00
        extended_start = dt_time(16, 0)
        extended_end = dt_time(18, 0)

        return extended_start <= current_time <= extended_end

    async def _run_pipeline_job(self, job_type: str = "full"):
        """스케줄러 잡으로 실행될 파이프라인"""
        if self._shutdown_event.is_set():
            return

        print(f"\n{'='*60}")
        print(f"⏰ 스케줄 실행: {job_type} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        try:
            if job_type == "market_hours":
                # 장중: 전체 파이프라인 (Step 1-5, 승인 대기)
                result = await self.pipeline.run_full_pipeline()
            elif job_type == "extended_hours":
                # 시간외: 시장 데이터만 업데이트 (Step 1, 7)
                print("시간외 거래: 시장 지수/뉴스 업데이트만 수행")
                await self.pipeline.step1_data_collection()
                await self.pipeline.step7_state_sync()
                result = {"status": "EXTENDED_HOURS_UPDATE"}
            else:
                # 장외/주말: 시스템 헬스체크 + 데이터 동기화
                print("장외/주말: 시스템 헬스체크 및 데이터 동기화")
                await self.pipeline.step1_data_collection()
                await self.pipeline.step7_state_sync()
                result = {"status": "AFTER_HOURS_SYNC"}

            print(f"✅ 잡 완료: {result.get('status', 'UNKNOWN')}")

        except Exception as e:
            print(f"❌ 잡 실행 실패: {e}")
            # 오케스트레이터에 에러 기록
            orchestrator = get_orchestrator()
            orchestrator.advance_stage(
                orchestrator._state.current_stage or None,
                success=False,
                error=str(e)
            )

    async def start_scheduler(self):
        """스케줄러 시작 및 잡 등록"""
        print("\n📅 APScheduler 잡 등록 중...")

        # 1. 장중 (09:00-15:30, 평일): 5분 간격
        self.scheduler.add_job(
            self._run_pipeline_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute="*/5",
                timezone="Asia/Seoul"
            ),
            args=["market_hours"],
            id="market_hours_pipeline",
            name="장중 파이프라인 (5분 간격)",
            max_instances=1,
            coalesce=True,  # 누락된 실행 병합
            misfire_grace_time=60
        )

        # 2. 시간외 (16:00-18:00, 평일): 10분 간격
        self.scheduler.add_job(
            self._run_pipeline_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="16-17",
                minute="*/10",
                timezone="Asia/Seoul"
            ),
            args=["extended_hours"],
            id="extended_hours_pipeline",
            name="시간외 파이프라인 (10분 간격)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60
        )

        # 3. 장외 (평일 06:00-08:55, 18:05-23:00): 30분 간격
        self.scheduler.add_job(
            self._run_pipeline_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="6-8,18-22",
                minute="*/30",
                timezone="Asia/Seoul"
            ),
            args=["after_hours"],
            id="after_hours_pipeline",
            name="장외 파이프라인 (30분 간격)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120
        )

        # 4. 주말/공휴일: 1시간 간격 (시스템 헬스체크)
        self.scheduler.add_job(
            self._run_pipeline_job,
            trigger=CronTrigger(
                day_of_week="sat,sun",
                hour="*",
                minute=0,
                timezone="Asia/Seoul"
            ),
            args=["weekend"],
            id="weekend_healthcheck",
            name="주말 헬스체크 (1시간 간격)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300
        )

        # 5. 일일 리포트 발송 (평일 15:40)
        self.scheduler.add_job(
            self._send_daily_report,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute=40,
                timezone="Asia/Seoul"
            ),
            id="daily_report",
            name="일일 리포트 발송",
            max_instances=1
        )

        # 6. 대시보드 파일 감시 시작
        await self.dashboard_team.start_watcher()

        # 7. WebSocket 실시간 서버 시작
        self._ws_runner = await get_ws_server().start()
        print("✅ WebSocket 서버 시작됨 (ws://0.0.0.0:8080/ws)")

        self.scheduler.start()
        self._running = True
        print("✅ 스케줄러 시작됨")
        print("   - 장중 (09:00-15:30): 5분 간격")
        print("   - 시간외 (16:00-18:00): 10분 간격")
        print("   - 장외 (평일 그 외): 30분 간격")
        print("   - 주말: 1시간 간격 (헬스체크)")
        print("   - 일일 리포트: 평일 15:40")

    async def _send_daily_report(self):
        """일일 리포트 발송 (텔레그램)"""
        print("📊 일일 리포트 발송 중...")
        try:
            orchestrator = get_orchestrator()
            data = orchestrator.get_full_snapshot()
            await self.pipeline.telegram_team.send_daily_report(data)
            print("✅ 일일 리포트 발송 완료")
        except Exception as e:
            print(f"❌ 일일 리포트 발송 실패: {e}")

    async def stop_scheduler(self):
        """스케줄러 정지"""
        print("\n🛑 스케줄러 정지 중...")
        self._shutdown_event.set()
        self._running = False

        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

        await self.dashboard_team.stop_watcher()

        # WebSocket 서버 정지
        try:
            from backend.ws_server import get_ws_server
            await get_ws_server().stop()
            if hasattr(self, '_ws_runner'):
                await self._ws_runner.cleanup()
        except Exception as e:
            print(f"WS 서버 정지 중 에러: {e}")

        await self.pipeline.cleanup()
        print("✅ 스케줄러 정지 완료")

    async def run_daemon(self):
        """데몬 모드 실행 (블로킹)"""
        await self.start_scheduler()

        # 종료 시그널 핸들러 등록
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: self._shutdown_event.set())
            except NotImplementedError:
                # Windows에서는 add_signal_handler 미지원
                pass

        print("\n🤖 investbin AI 데몬 실행 중... (Ctrl+C로 종료)")
        print("   로그: 콘솔 출력 확인")
        print("   대시보드: https://investbin-2.vercel.app")

        # 무한 대기 (shutdown_event가 set될 때까지)
        await self._shutdown_event.wait()

        await self.stop_scheduler()


# ==========================================
# CLI 엔트리포인트
# ==========================================

async def run_cli(args):
    """CLI 명령 실행"""
    pipeline = Pipeline(mock_mode=args.mock)

    try:
        if args.step:
            print(f"단일 단계 실행: Step {args.step}")
            if args.step == 1:
                result = await pipeline.step1_data_collection()
            elif args.step == 2:
                collected = pipeline.orchestrator.get_collected_data() or {}
                result = await pipeline.step2_first_review(collected)
            elif args.step == 3:
                result = await pipeline.step3_final_decision()
            elif args.step == 4:
                result = await pipeline.step4_risk_compliance_check()
            elif args.step == 5:
                result = await pipeline.step5_telegram_approval()
            elif args.step == 6:
                result = await pipeline.step6_order_execution({})
            elif args.step == 7:
                result = await pipeline.step7_state_sync()

            print(f"\n결과: {result}")

        elif args.full:
            print("전체 파이프라인 실행 (Step 1-5)")
            result = await pipeline.run_full_pipeline()
            print(f"\n최종 상태: {result.get('status')}")

        elif args.daemon:
            print("데몬 모드 시작...")
            daemon = InvestbinDaemon(mock_mode=args.mock)
            await daemon.run_daemon()

        elif args.status:
            orchestrator = get_orchestrator()
            state = orchestrator.get_state()
            data = orchestrator.get_full_snapshot()

            print("\n📊 시스템 상태")
            print(f"   파이프라인: {state.status.value}")
            print(f"   현재 단계: {state.current_stage.value if state.current_stage else 'N/A'}")
            print(f"   완료 단계: {[s.value for s in state.completed_stages]}")
            print(f"   실행 ID: {state.run_id}")
            print(f"   시작 시간: {state.started_at}")
            print(f"   완료 시간: {state.completed_at}")
            print(f"   버전: {state.version}")
            print(f"   데이터 소스: {data.get('data_source')}")
            print(f"   최종 업데이트: {data.get('last_updated')}")

            portfolio = data.get('portfolio')
            if portfolio:
                print(f"   보유 종목: {len(portfolio.get('holdings', []))}개")
                print(f"   총 평가액: {portfolio.get('total_balance')}")

        elif args.test_telegram:
            print("텔레그램 봇 테스트 모드...")
            await pipeline.telegram_team.start_async()
            print("봇 실행 중... (Ctrl+C로 종료)")
            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                await pipeline.telegram_team.stop_async()

        elif args.test_toss:
            print("Toss API 연결 테스트...")
            from backend.api.toss_client import create_toss_client_from_env
            client = create_toss_client_from_env()
            try:
                accounts = await client.get_accounts()
                print(f"계좌 목록: {len(accounts)}개")
                for acc in accounts:
                    print(f"  - {acc.get('accountName', 'N/A')} (seq: {acc.get('accountSeq')})")

                if accounts:
                    holdings = await client.get_holdings(accounts[0]['accountSeq'])
                    print(f"보유 종목: {len(holdings.get('items', []))}개")
                    for h in holdings.get('items', [])[:5]:
                        print(f"  - {h.get('name')} ({h.get('symbol')}): {h.get('quantity')}주 @ {h.get('lastPrice')}")
            finally:
                await client.close()

        else:
            print("명령어를 지정하세요. --help 참조")

    finally:
        await pipeline.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="investbin AI 자동매매 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py --full              # 전체 파이프라인 1회 실행 (Step 1-5)
  python main.py --step 1            # Step 1만 실행 (데이터 수집)
  python main.py --step 3            # Step 3만 실행 (최종 결정)
  python main.py --daemon            # 24시간 데몬 모드 (APScheduler)
  python main.py --status            # 현재 시스템 상태 확인
  python main.py --test-toss         # Toss API 연결 테스트
  python main.py --test-telegram     # 텔레그램 봇 테스트
  python main.py --mock --full       # Mock 모드로 전체 테스트 (API 키 불필요)
        """
    )

    parser.add_argument("--step", type=int, choices=range(1, 8),
                       help="특정 단계만 실행 (1-7)")
    parser.add_argument("--full", action="store_true",
                       help="전체 파이프라인 실행 (Step 1-5, 승인 대기)")
    parser.add_argument("--daemon", action="store_true",
                       help="데몬 모드: 24시간 무인 자동 실행")
    parser.add_argument("--status", action="store_true",
                       help="시스템 상태 확인")
    parser.add_argument("--test-telegram", action="store_true",
                       help="텔레그램 봇 테스트 모드")
    parser.add_argument("--test-toss", action="store_true",
                       help="Toss API 연결 테스트")
    parser.add_argument("--mock", action="store_true",
                       help="Mock 모드 (API 키 없이 테스트)")

    args = parser.parse_args()

    # 인코딩 설정 (Windows)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 환경변수에서 Mock 모드 확인
    if os.getenv("MOCK_MODE", "").lower() == "true":
        args.mock = True
        print("🔧 MOCK_MODE 환경변수 감지: Mock 모드 활성화")

    asyncio.run(run_cli(args))


if __name__ == "__main__":
    main()