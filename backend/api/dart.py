"""
DART Open API Client (Korean Financial Supervisory Service)
공시/재무 데이터 수집용
"""
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import os
import json
from pathlib import Path


class DartApiError(Exception):
    """DART API 예외"""
    def __init__(self, message: str, status_code: int = None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class DartClient:
    """
    DART Open API 클라이언트
    - 공시 목록 조회
    - 재무제표 조회
    - 기업 개요 조회
    """

    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(self, config: dict, timeout: int = 30):
        self.api_key = config.get("api_key") or os.getenv("DART_API_KEY")
        if not self.api_key:
            raise ValueError("DART_API_KEY가 필요합니다 (config 또는 환경변수)")

        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "investbin-ai-trading/1.0"}
        )

        # 캐시 디렉토리
        self._cache_dir = Path(__file__).parent.parent.parent / ".dart_cache"
        self._cache_dir.mkdir(exist_ok=True)
        self._cache_ttl = timedelta(hours=1)

    async def close(self):
        await self._client.aclose()

    def _get_cache_path(self, endpoint: str, params: dict) -> Path:
        """캐시 파일 경로 생성"""
        key = f"{endpoint}_{json.dumps(params, sort_keys=True)}"
        safe_key = "".join(c for c in key if c.isalnum() or c in "_-")
        return self._cache_dir / f"{safe_key}.json"

    def _is_cache_valid(self, path: Path) -> bool:
        """캐시 유효성 검사"""
        if not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < self._cache_ttl

    async def _get_with_cache(self, endpoint: str, params: dict) -> Dict[str, Any]:
        """캐시 지원 GET 요청"""
        cache_path = self._get_cache_path(endpoint, params)

        if self._is_cache_valid(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        # API 호출
        params["crtfc_key"] = self.api_key
        try:
            resp = await self._client.get(endpoint, params=params)
            if resp.status_code != 200:
                raise DartApiError(f"HTTP {resp.status_code}", status_code=resp.status_code)

            data = resp.json()
            status = data.get("status", "000")
            if status != "000":
                error_msg = data.get("message", "Unknown error")
                raise DartApiError(f"DART API 오류 [{status}]: {error_msg}", error_code=status)

            # 캐시 저장
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            return data

        except httpx.RequestError as e:
            raise DartApiError(f"네트워크 오류: {e}")

    # ==========================================
    # 공시 데이터
    # ==========================================

    async def get_corp_disclosures(
        self,
        corp_code: str,
        bgn_de: str = None,
        end_de: str = None,
        page_no: int = 1,
        page_count: int = 100
    ) -> List[Dict[str, Any]]:
        """
        기업 공시 목록 조회 (list.json)
        corp_code: 종목코드 (6자리, 예: "005930")
        """
        if not bgn_de:
            bgn_de = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        params = {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": str(page_no),
            "page_count": str(page_count)
        }

        data = await self._get_with_cache("/list.json", params)
        return data.get("list", [])

    async def get_disclosure_detail(self, rcept_no: str) -> Dict[str, Any]:
        """공시 상세 조회 (document.xml)"""
        params = {"rcept_no": rcept_no}
        # XML 반환이므로 별도 처리
        params["crtfc_key"] = self.api_key
        resp = await self._client.get("/document.xml", params=params)
        if resp.status_code != 200:
            raise DartApiError(f"HTTP {resp.status_code}", status_code=resp.status_code)
        return {"xml": resp.text, "rcept_no": rcept_no}

    # ==========================================
    # 재무제표 데이터
    # ==========================================

    async def get_financial_statements(
        self,
        corp_code: str,
        bsns_year: str = None,
        reprt_code: str = "11011"  # 11011: 사업보고서, 11012: 반기보고서, 11013: 1분기, 11014: 3분기
    ) -> List[Dict[str, Any]]:
        """
        단일회사 전체 재무제표 조회 (fnlttSinglAcntAll.json)
        """
        if not bsns_year:
            bsns_year = str(datetime.now().year - 1)  # 전년도 결산

        params = {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": "CFS"  # 연결재무제표
        }

        data = await self._get_with_cache("/fnlttSinglAcntAll.json", params)
        return data.get("list", [])

    async def get_financial_ratios(
        self,
        corp_code: str,
        bsns_year: str = None,
        reprt_code: str = "11011"
    ) -> List[Dict[str, Any]]:
        """
        재무비율 조회 (fnlttCmpnyAll.json)
        PER, PBR, ROE, 부채비율 등 주요 지표 포함
        """
        if not bsns_year:
            bsns_year = str(datetime.now().year - 1)

        params = {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code
        }

        data = await self._get_with_cache("/fnlttCmpnyAll.json", params)
        return data.get("list", [])

    # ==========================================
    # 기업 정보
    # ==========================================

    async def get_corp_info(self, corp_code: str) -> Dict[str, Any]:
        """기업 기본 정보 조회 (company.json)"""
        params = {"corp_code": corp_code}
        data = await self._get_with_cache("/company.json", params)
        return data

    async def get_corp_code_by_stock_code(self, stock_code: str) -> Optional[str]:
        """종목코드(6자리)로 기업고유번호(corp_code) 조회"""
        # corpCode.xml 다운로드 후 파싱 필요
        # 여기서는 주요 종목 하드코딩 매핑 (실제로는 corpCode.xml 파싱 권장)
        corp_code_map = {
            "005930": "00126380",  # 삼성전자
            "000660": "00164779",  # SK하이닉스
            "035420": "00165224",  # NAVER
            "005940": "00126381",  # 삼성전자우
            "000270": "00164778",  # 기아
            "005380": "00164780",  # 현대차
        }
        return corp_code_map.get(stock_code)

    # ==========================================
    # 편의 메서드: 종목코드(6자리) 기준 조회
    # ==========================================

    async def get_disclosures_by_stock_code(self, stock_code: str) -> List[Dict[str, Any]]:
        """종목코드로 공시 조회"""
        corp_code = await self.get_corp_code_by_stock_code(stock_code)
        if not corp_code:
            return []
        return await self.get_corp_disclosures(corp_code)

    async def get_financials_by_stock_code(self, stock_code: str) -> List[Dict[str, Any]]:
        """종목코드로 재무제표 조회"""
        corp_code = await self.get_corp_code_by_stock_code(stock_code)
        if not corp_code:
            return []
        return await self.get_financial_statements(corp_code)

    async def get_ratios_by_stock_code(self, stock_code: str) -> Dict[str, Any]:
        """종목코드로 재무비율 조회 (주요 지표만 추출)"""
        corp_code = await self.get_corp_code_by_stock_code(stock_code)
        if not corp_code:
            return {}

        ratios = await self.get_financial_ratios(corp_code)
        if not ratios:
            return {}

        # 주요 지표만 추출 (최신 연도 기준)
        latest = ratios[0] if ratios else {}
        return {
            "corp_code": corp_code,
            "corp_name": latest.get("corp_name", ""),
            "stock_code": stock_code,
            "bsns_year": latest.get("bsns_year", ""),
            "market_cap": float(latest.get("thstrm_market_cap", 0)) if latest.get("thstrm_market_cap") else None,
            "per": float(latest.get("thstrm_per", 0)) if latest.get("thstrm_per") else None,
            "pbr": float(latest.get("thstrm_pbr", 0)) if latest.get("thstrm_pbr") else None,
            "roe": float(latest.get("thstrm_roe", 0)) if latest.get("thstrm_roe") else None,
            "revenue": float(latest.get("thstrm_revenue", 0)) if latest.get("thstrm_revenue") else None,
            "operating_profit": float(latest.get("thstrm_operating_profit", 0)) if latest.get("thstrm_operating_profit") else None,
            "net_profit": float(latest.get("thstrm_net_profit", 0)) if latest.get("thstrm_net_profit") else None,
            "debt_ratio": float(latest.get("thstrm_debt_ratio", 0)) if latest.get("thstrm_debt_ratio") else None,
        }


def create_dart_client_from_env() -> DartClient:
    """환경변수에서 설정 읽어 DartClient 생성"""
    config = {"api_key": os.getenv("DART_API_KEY")}
    return DartClient(config)