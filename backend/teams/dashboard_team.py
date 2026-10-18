"""
Dashboard Team - 대시보드 팀
data.json → index.html 동기화, 실시간 웹 대시보드 관리
"""
import asyncio
import json
import pathlib
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass
import threading
import time


@dataclass
class DashboardConfig:
    """대시보드 설정"""
    data_file: str = "public/data.json"
    update_interval_seconds: int = 5
    enable_file_watcher: bool = True
    safe_write: bool = True  # 임시 파일 → rename 방식


class DashboardTeam:
    """
    대시보드 팀
    - data.json 변경 감지 → index.html 자동 반영 (정적 사이트는 JSON만 업데이트)
    - Vercel 배포 시 public/data.json이 자동으로 서빙됨
    - 로컬 개발 시 파일 감시로 실시간 업데이트
    """

    def __init__(self, config: DashboardConfig = None):
        self.config = config or DashboardConfig()
        self.base_dir = pathlib.Path(__file__).parent.parent.parent
        self.data_file = self.base_dir / self.config.data_file
        self._watcher_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_modified = 0
        self._callbacks = []  # 데이터 변경 시 호출할 콜백들

    # ==========================================
    # 데이터 읽기/쓰기 (안전한 방식)
    # ==========================================

    def load_data(self) -> Dict[str, Any]:
        """data.json 로드"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[DashboardTeam] 데이터 로드 실패: {e}")
            return self._default_data()

    def save_data(self, data: Dict[str, Any]) -> bool:
        """data.json 안전 저장 (임시 파일 → atomic rename)"""
        try:
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.config.safe_write:
                temp_file = self.data_file.with_suffix(".tmp")
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # Atomic rename
                if self.data_file.exists():
                    import os
                    os.replace(temp_file, self.data_file)
                else:
                    temp_file.rename(self.data_file)
            else:
                with open(self.data_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"[DashboardTeam] 데이터 저장 실패: {e}")
            return False

    def _default_data(self) -> Dict[str, Any]:
        return {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "system_metrics": {},
            "market_sentiment": {},
            "top_picks": [],
            "orders": [],
            "indices": [],
            "portfolio": None,
            "stock_details": {},
            "data_source": "unknown"
        }

    # ==========================================
    # 파일 변경 감시 (로컬 개발용)
    # ==========================================

    async def start_watcher(self):
        """파일 감시 시작 (비동기)"""
        if self._running:
            return

        self._running = True
        self._last_modified = self.data_file.stat().st_mtime if self.data_file.exists() else 0
        self._watcher_task = asyncio.create_task(self._watch_loop())
        print("[DashboardTeam] 파일 감시 시작")

    async def stop_watcher(self):
        """파일 감시 중지"""
        self._running = False
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
        print("[DashboardTeam] 파일 감시 중지")

    async def _watch_loop(self):
        """파일 변경 감시 루프"""
        while self._running:
            try:
                if self.data_file.exists():
                    current_mtime = self.data_file.stat().st_mtime
                    if current_mtime > self._last_modified:
                        self._last_modified = current_mtime
                        print(f"[DashboardTeam] data.json 변경 감지: {datetime.now().strftime('%H:%M:%S')}")
                        await self._notify_callbacks()
            except Exception as e:
                print(f"[DashboardTeam] 감시 루프 오류: {e}")

            await asyncio.sleep(self.config.update_interval_seconds)

    def register_callback(self, callback):
        """데이터 변경 시 호출될 콜백 등록"""
        self._callbacks.append(callback)

    async def _notify_callbacks(self):
        """등록된 콜백들에게 알림"""
        data = self.load_data()
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                print(f"[DashboardTeam] 콜백 실행 오류: {e}")

    # ==========================================
    # 데이터 업데이트 헬퍼 (타입별)
    # ==========================================

    def update_stock_details(self, stock_details: Dict[str, Any]) -> bool:
        """종목 상세 정보 업데이트 (AI 분석 포함)"""
        data = self.load_data()
        data["stock_details"] = stock_details
        return self.save_data(data)

    def update_portfolio(self, portfolio: Dict[str, Any]) -> bool:
        """포트폴리오 업데이트"""
        data = self.load_data()
        data["portfolio"] = portfolio
        return self.save_data(data)

    def update_top_picks(self, top_picks: list) -> bool:
        """추천 종목 업데이트"""
        data = self.load_data()
        data["top_picks"] = top_picks
        return self.save_data(data)

    def update_market_sentiment(self, sentiment: Dict[str, Any]) -> bool:
        """시장 센티먼트 업데이트"""
        data = self.load_data()
        data["market_sentiment"] = sentiment
        return self.save_data(data)

    def update_indices(self, indices: list) -> bool:
        """지수 업데이트"""
        data = self.load_data()
        data["indices"] = indices
        return self.save_data(data)

    def update_system_metrics(self, metrics: Dict[str, Any]) -> bool:
        """시스템 메트릭스 업데이트"""
        data = self.load_data()
        data["system_metrics"] = metrics
        return self.save_data(data)

    def add_order(self, order: Dict[str, Any]) -> bool:
        """주문 이력 추가"""
        data = self.load_data()
        if "orders" not in data:
            data["orders"] = []
        data["orders"].insert(0, order)
        return self.save_data(data)

    def update_pipeline_status(self, status: str, stage: str = None, run_id: str = None) -> bool:
        """파이프라인 상태 업데이트"""
        data = self.load_data()
        data["pipeline_status"] = status
        if stage:
            data["pipeline_stage"] = stage
        if run_id:
            data["pipeline_run_id"] = run_id
        return self.save_data(data)

    # ==========================================
    # Vercel 배포용 헬퍼
    # ==========================================

    def prepare_for_vercel_deploy(self) -> bool:
        """
        Vercel 배포 전 data.json 검증
        - 필수 필드 존재 확인
        - JSON 유효성 검증
        """
        data = self.load_data()
        required_fields = [
            "last_updated", "market_sentiment", "top_picks",
            "indices", "portfolio", "stock_details"
        ]

        for field in required_fields:
            if field not in data:
                print(f"[DashboardTeam] 필수 필드 누락: {field}")
                return False

        # stock_details의 AI 분석 필드 확인
        for code, detail in data.get("stock_details", {}).items():
            if not all(k in detail for k in ["expert_good", "expert_bad", "ceo_comment"]):
                print(f"[DashboardTeam] {code}: AI 분석 필드 누락")
                # 누락된 필드 기본값 채우기
                detail.setdefault("expert_good", "분석 준비 중...")
                detail.setdefault("expert_bad", "분석 준비 중...")
                detail.setdefault("ceo_comment", "분석 준비 중...")

        return self.save_data(data)

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """대시보드 요약 정보 (헬스체크용)"""
        data = self.load_data()
        return {
            "last_updated": data.get("last_updated"),
            "data_source": data.get("data_source"),
            "top_picks_count": len(data.get("top_picks", [])),
            "holdings_count": len(data.get("portfolio", {}).get("holdings", [])) if data.get("portfolio") else 0,
            "stock_details_count": len(data.get("stock_details", {})),
            "orders_count": len(data.get("orders", [])),
            "indices_count": len(data.get("indices", [])),
            "pipeline_status": data.get("pipeline_status"),
            "file_size_kb": round(self.data_file.stat().st_size / 1024, 1) if self.data_file.exists() else 0
        }


def create_dashboard_team(data_file: str = None) -> DashboardTeam:
    """DashboardTeam 인스턴스 생성"""
    config = DashboardConfig()
    if data_file:
        config.data_file = data_file
    return DashboardTeam(config)