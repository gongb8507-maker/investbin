"""
Orchestrator - 파이프라인 상태 머신 및 data.json 중앙 관리
7단계 워크플로우 오케스트레이션 + 파일 락/버전 관리
"""
import asyncio
import json
import os
import pathlib
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import portalocker
import copy


class PipelineStage(str, Enum):
    """파이프라인 단계"""
    DATA_COLLECTION = "데이터_수집"           # Step 1
    FIRST_REVIEW = "1차_검토"               # Step 2
    FINAL_DECISION = "최종_결정"            # Step 3
    RISK_COMPLIANCE_CHECK = "위험_규정_검증" # Step 4
    TELEGRAM_APPROVAL = "텔레그램_승인요청"  # Step 5
    ORDER_EXECUTION = "주문_체결"           # Step 6
    STATE_SYNC = "상태_동기화"              # Step 7
    COMPLETED = "완료"
    FAILED = "실패"


class PipelineStatus(str, Enum):
    """파이프라인 실행 상태"""
    IDLE = "대기중"
    RUNNING = "실행중"
    PAUSED = "일시정지"
    COMPLETED = "완료"
    FAILED = "실패"


@dataclass
class PipelineState:
    """파이프라인 전체 상태"""
    status: PipelineStatus = PipelineStatus.IDLE
    current_stage: Optional[PipelineStage] = None
    completed_stages: List[PipelineStage] = field(default_factory=list)
    failed_stage: Optional[PipelineStage] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    version: int = 1
    run_id: str = ""


class Orchestrator:
    """
    시스템 오케스트레이터
    - 7단계 파이프라인 상태 머신 관리
    - data.json 원자적 읽기/쓰기 (파일 락 + 버전 관리)
    - 단계별 롤백/복구 지원
    - 동시 실행 방지 (싱글톤 패턴)
    - 실시간 WebSocket 브로드캐스트 지원
    """

    def __init__(self, data_file: str = None):
        base_dir = pathlib.Path(__file__).parent.parent.parent
        self.data_file = pathlib.Path(data_file) if data_file else base_dir / "public" / "data.json"
        self.lock_file = self.data_file.with_suffix(".lock")
        self._state = PipelineState()
        self._data_cache: Optional[Dict[str, Any]] = None
        self._lock_acquired = False

        # WebSocket 서버 콜백 (지연 로딩으로 순환 import 방지)
        self._ws_server = None
        self._ws_notify_enabled = True

    # ==========================================
    # Data.json 원자적 읽기/쓰기 (파일 락)
    # ==========================================

    def _acquire_lock(self, timeout: float = 10.0) -> bool:
        """파일 락 획득 (portalocker 사용)"""
        try:
            self._lock_fh = open(self.lock_file, 'w')
            portalocker.lock(self._lock_fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
            self._lock_acquired = True
            return True
        except Exception as e:
            print(f"[Orchestrator] 락 획득 실패: {e}")
            return False

    def _release_lock(self):
        """파일 락 해제"""
        if self._lock_acquired and hasattr(self, '_lock_fh'):
            try:
                portalocker.unlock(self._lock_fh)
                self._lock_fh.close()
            except Exception:
                pass
            self._lock_acquired = False

    def load_data(self, force_reload: bool = False) -> Dict[str, Any]:
        """data.json 로드 (캐시 + 락)"""
        if self._data_cache and not force_reload:
            return self._data_cache

        if not self._acquire_lock():
            # 락 획득 실패 시 캐시된 데이터 반환 또는 기본 구조
            if self._data_cache:
                return self._data_cache
            return self._default_data()

        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self._data_cache = json.load(f)
            else:
                self._data_cache = self._default_data()
        except Exception as e:
            print(f"[Orchestrator] 데이터 로드 실패: {e}")
            self._data_cache = self._default_data()
        finally:
            self._release_lock()

        return self._data_cache

    def save_data(self, data: Dict[str, Any]) -> bool:
        """data.json 원자적 저장 (락 + 버전 증가 + 백업)"""
        if not self._acquire_lock():
            print("[Orchestrator] 저장 실패: 락 획득 불가")
            return False

        try:
            # 버전 증가
            data["pipeline_version"] = data.get("pipeline_version", 0) + 1
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 백업 생성 (이전 버전)
            if self.data_file.exists():
                backup_path = self.data_file.with_suffix(f".bak.{data['pipeline_version'] - 1}.json")
                try:
                    import shutil
                    shutil.copy2(self.data_file, backup_path)
                    # 백업 5개만 유지
                    self._cleanup_backups(keep=5)
                except Exception:
                    pass

            # 원자적 쓰기: 임시 파일 → rename
            temp_file = self.data_file.with_suffix(".tmp")
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Windows에서 atomic rename
            if os.name == 'nt':
                if self.data_file.exists():
                    os.replace(temp_file, self.data_file)
                else:
                    temp_file.rename(self.data_file)
            else:
                os.rename(temp_file, self.data_file)

            self._data_cache = data
            self._state.version = data["pipeline_version"]
            return True

        except Exception as e:
            print(f"[Orchestrator] 데이터 저장 실패: {e}")
            return False
        finally:
            self._release_lock()

    def _cleanup_backups(self, keep: int = 5):
        """오래된 백업 파일 정리"""
        try:
            backups = sorted(self.data_file.parent.glob(f"{self.data_file.stem}.bak.*.json"))
            for old_backup in backups[:-keep]:
                old_backup.unlink()
        except Exception:
            pass

    def _default_data(self) -> Dict[str, Any]:
        """기본 데이터 구조"""
        return {
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pipeline_version": 1,
            "pipeline_status": PipelineStage.DATA_COLLECTION.value,
            "pipeline_run_id": "",
            "system_metrics": {},
            "market_sentiment": {},
            "top_picks": [],
            "orders": [],
            "indices": [],
            "portfolio": None,
            "stock_details": {},
            "collected_data": None,
            "review_results": [],
            "trading_signals": [],
            "telegram_approvals": [],
            "risk_check": None,
            "compliance_check": None,
            "data_source": "unknown"
        }

    # ==========================================
    # 파이프라인 상태 관리
    # ==========================================

    def start_pipeline(self, run_id: str = None) -> PipelineState:
        """파이프라인 실행 시작"""
        self._state = PipelineState(
            status=PipelineStatus.RUNNING,
            current_stage=PipelineStage.DATA_COLLECTION,
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            run_id=run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self._update_pipeline_status_in_data()
        return self._state

    def advance_stage(self, stage: PipelineStage, success: bool = True, error: str = None) -> PipelineState:
        """다음 단계로 진행"""
        if success:
            if self._state.current_stage:
                self._state.completed_stages.append(self._state.current_stage)
            self._state.current_stage = stage
        else:
            self._state.status = PipelineStatus.FAILED
            self._state.failed_stage = stage
            self._state.error_message = error
            self._state.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._update_pipeline_status_in_data()
        return self._state

    def complete_pipeline(self) -> PipelineState:
        """파이프라인 완료"""
        self._state.status = PipelineStatus.COMPLETED
        self._state.current_stage = PipelineStage.COMPLETED
        self._state.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._update_pipeline_status_in_data()
        return self._state

    def _update_pipeline_status_in_data(self):
        """data.json의 파이프라인 상태 필드 업데이트"""
        if self._data_cache:
            self._data_cache["pipeline_status"] = self._state.current_stage.value if self._state.current_stage else PipelineStatus.IDLE.value
            self._data_cache["pipeline_run_id"] = self._state.run_id
            self._data_cache["pipeline_version"] = self._state.version
            # 저장하지 않고 메모리에만 반영 (단계 완료 시 save_data 호출)

    def get_state(self) -> PipelineState:
        return self._state

    # ==========================================
    # 단계별 데이터 읽기/쓰기 헬퍼
    # ==========================================

    def get_collected_data(self) -> Optional[Dict[str, Any]]:
        """Step 1 수집 데이터 조회"""
        data = self.load_data()
        return data.get("collected_data")

    def set_collected_data(self, collected: Dict[str, Any]) -> bool:
        """Step 1 수집 데이터 저장"""
        data = self.load_data(force_reload=True)
        data["collected_data"] = collected
        return self.save_data(data)

    def get_review_result(self, stage_index: int) -> Optional[Dict[str, Any]]:
        """Step 2/3 검토 결과 조회"""
        data = self.load_data()
        reviews = data.get("review_results", [])
        if 0 <= stage_index < len(reviews):
            return reviews[stage_index]
        return None

    def add_review_result(self, review: Dict[str, Any]) -> bool:
        """검토 결과 추가 (1차/2차)"""
        data = self.load_data(force_reload=True)
        if "review_results" not in data:
            data["review_results"] = []
        data["review_results"].append(review)
        return self.save_data(data)

    def get_trading_signals(self) -> List[Dict[str, Any]]:
        """Step 3 거래 시그널 조회"""
        data = self.load_data()
        return data.get("trading_signals", [])

    def set_trading_signals(self, signals: List[Dict[str, Any]]) -> bool:
        """거래 시그널 저장"""
        data = self.load_data(force_reload=True)
        data["trading_signals"] = signals
        return self.save_data(data)

    def get_risk_check(self) -> Optional[Dict[str, Any]]:
        """Step 4 리스크 체크 결과"""
        data = self.load_data()
        return data.get("risk_check")

    def set_risk_check(self, risk_check: Dict[str, Any]) -> bool:
        data = self.load_data(force_reload=True)
        data["risk_check"] = risk_check
        return self.save_data(data)

    def get_compliance_check(self) -> Optional[Dict[str, Any]]:
        """Step 4 컴플라이언스 체크 결과"""
        data = self.load_data()
        return data.get("compliance_check")

    def set_compliance_check(self, compliance_check: Dict[str, Any]) -> bool:
        data = self.load_data(force_reload=True)
        data["compliance_check"] = compliance_check
        return self.save_data(data)

    def get_telegram_approvals(self) -> List[Dict[str, Any]]:
        """Step 5 텔레그램 승인 요청/결과"""
        data = self.load_data()
        return data.get("telegram_approvals", [])

    def add_telegram_approval(self, approval: Dict[str, Any]) -> bool:
        data = self.load_data(force_reload=True)
        if "telegram_approvals" not in data:
            data["telegram_approvals"] = []
        data["telegram_approvals"].append(approval)
        return self.save_data(data)

    def update_telegram_approval(self, approval_id: str, update: Dict[str, Any]) -> bool:
        """승인 요청 상태 업데이트 (CEO 응답 반영)"""
        data = self.load_data(force_reload=True)
        approvals = data.get("telegram_approvals", [])
        for i, a in enumerate(approvals):
            if a.get("approval_id") == approval_id:
                approvals[i].update(update)
                data["telegram_approvals"] = approvals
                return self.save_data(data)
        return False

    def get_orders(self) -> List[Dict[str, Any]]:
        """주문 이력 조회"""
        data = self.load_data()
        return data.get("orders", [])

    def add_order(self, order: Dict[str, Any]) -> bool:
        """주문 기록 추가"""
        data = self.load_data(force_reload=True)
        if "orders" not in data:
            data["orders"] = []
        data["orders"].insert(0, order)  # 최신순
        return self.save_data(data)

    def update_portfolio(self, portfolio: Dict[str, Any]) -> bool:
        """포트폴리오 업데이트"""
        data = self.load_data(force_reload=True)
        data["portfolio"] = portfolio
        return self.save_data(data)

    def update_stock_details(self, stock_details: Dict[str, Any]) -> bool:
        """종목 상세 정보 업데이트 (AI 분석 포함)"""
        data = self.load_data(force_reload=True)
        data["stock_details"] = stock_details
        return self.save_data(data)

    def update_market_sentiment(self, sentiment: Dict[str, Any]) -> bool:
        data = self.load_data(force_reload=True)
        data["market_sentiment"] = sentiment
        return self.save_data(data)

    def update_top_picks(self, top_picks: List[Dict[str, Any]]) -> bool:
        data = self.load_data(force_reload=True)
        data["top_picks"] = top_picks
        return self.save_data(data)

    def update_indices(self, indices: List[Dict[str, Any]]) -> bool:
        data = self.load_data(force_reload=True)
        data["indices"] = indices
        return self.save_data(data)

    def update_system_metrics(self, metrics: Dict[str, Any]) -> bool:
        data = self.load_data(force_reload=True)
        data["system_metrics"] = metrics
        return self.save_data(data)

    # ==========================================
    # 전체 데이터 스냅샷 (읽기 전용)
    # ==========================================

    def get_full_snapshot(self) -> Dict[str, Any]:
        """전체 데이터 스냅샷 복사본 반환"""
        return copy.deepcopy(self.load_data())

    # ==========================================
    # Mock 모드 지원
    # ==========================================

    def enable_mock_mode(self):
        """Mock 모드 활성화 (데이터 파일 우회)"""
        self._data_cache = self._default_data()
        self._data_cache["data_source"] = "mock"

    # ==========================================
    # WebSocket 실시간 알림
    # ==========================================

    def _notify_ws_clients(self, changed_fields: Dict[str, Any]):
        """WebSocket 클라이언트들에게 변경 사항 알림 (비동기)"""
        if not self._ws_notify_enabled:
            return

        try:
            from backend.ws_server import ensure_ws_server_started
            ws_server = asyncio.get_event_loop().run_until_complete(ensure_ws_server_started())
            # 비동기 태스크로 브로드캐스트 (블로킹 방지)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(ws_server.broadcast_data_change(changed_fields))
        except Exception:
            # WS 서버가 없거나 에러 시 무시 (파일 기반 폴백 유지)
            pass

    def set_ws_notify(self, enabled: bool):
        """WebSocket 알림 활성화/비활성화"""
        self._ws_notify_enabled = enabled

    # save_data 오버라이드하여 변경 시 알림
    def save_data(self, data: Dict[str, Any]) -> bool:
        """data.json 원자적 저장 (락 + 버전 증가 + 백업 + WS 알림)"""
        if not self._acquire_lock():
            print("[Orchestrator] 저장 실패: 락 획득 불가")
            return False

        try:
            # 이전 데이터와 비교하여 변경된 필드 찾기
            old_data = self._data_cache or {}
            changed_fields = {}
            for key, value in data.items():
                if key not in old_data or old_data[key] != value:
                    changed_fields[key] = value

            # 버전 증가
            data["pipeline_version"] = data.get("pipeline_version", 0) + 1
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 백업 생성 (이전 버전)
            if self.data_file.exists():
                backup_path = self.data_file.with_suffix(f".bak.{data['pipeline_version'] - 1}.json")
                try:
                    import shutil
                    shutil.copy2(self.data_file, backup_path)
                    self._cleanup_backups(keep=5)
                except Exception:
                    pass

            # 원자적 쓰기: 임시 파일 → rename
            temp_file = self.data_file.with_suffix(".tmp")
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Windows에서 atomic rename
            if os.name == 'nt':
                if self.data_file.exists():
                    os.replace(temp_file, self.data_file)
                else:
                    temp_file.rename(self.data_file)
            else:
                os.rename(temp_file, self.data_file)

            self._data_cache = data
            self._state.version = data["pipeline_version"]

            # 변경된 필드가 있으면 WebSocket 브로드캐스트
            if changed_fields:
                self._notify_ws_clients(changed_fields)

            return True

        except Exception as e:
            print(f"[Orchestrator] 데이터 저장 실패: {e}")
            return False
        finally:
            self._release_lock()


# 싱글톤 인스턴스
_orchestrator_instance: Optional[Orchestrator] = None


def get_orchestrator(data_file: str = None) -> Orchestrator:
    """싱글톤 오케스트레이터 인스턴스 반환"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = Orchestrator(data_file)
    return _orchestrator_instance