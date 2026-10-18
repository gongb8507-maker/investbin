import os
import json
import asyncio
from datetime import datetime

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# ==========================================
# Bin-Invest AI Backend Engine ( 작업 1 + 작업 2 )
# ==========================================

import pathlib
BASE_DIR = pathlib.Path(__file__).parent.resolve()
DATA_FILE = BASE_DIR / "public" / "data.json"


def load_data():
    """data.json 전체를 로드합니다. 파일 없으면 기본 구조 반환."""
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {
            "last_updated": None,
            "market_sentiment": {},
            "top_picks": [],
            "orders": [],
            "indices": [],
            "portfolio": None
        }


def save_data(data):
    """data.json에 영구 저장합니다. (주문 이력 포함)"""
    with open(DATA_FILE, 'w', encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_system_metrics():
    """CPU 및 메모리 사용량과 같은 실시간 시스템 메트릭을 수집합니다."""
    import psutil
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory.percent,
        "memory_available_gb": round(memory.available / (1024 ** 3), 2),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def model_to_dict(obj):
    """Pydantic 모델을 dict로 변환 (model_dump 호환성 유지)."""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    elif isinstance(obj, dict):
        return obj
    else:
        return str(obj)


async def generate_ai_market_data():
    """AI 퀀트 모델이 산출한 최신 시장 분석 데이터를 생성합니다.
    기존 주문 이력(orders)은 보존하고 시장 데이터만 업데이트합니다.
    또한 실시간 지수와 포트폴리오 정보를 포함합니다."""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 기존 데이터 로드하여 주문 이력 보존
    existing = load_data()
    existing_orders = existing.get("orders", [])

    # 시스템 메트릭스 수집
    system_metrics = get_system_metrics()

    # 시장 센티먼트와 추천 종목은 기존 하드코딩 데이터를 유지 (추후 동적 데이터로 대체 가능)
    market_sentiment = {
        "score": 78,
        "status": "강한 상승세",
        "summary": "외국인 수급 유입과 반도체/AI 섹터 호재가 집중되고 있습니다. AI 퀀트 모델이 산출한 금일 투자 적합도는 최상위권입니다.",
        "aum": "₩ 12,450,000",
        "daily_profit": "+ ₩ 342,000 (+2.8%)"
    }

    top_picks = [
        {
            "code": "005930",
            "name": "삼성전자",
            "score": 92,
            "recommendation": "강력 매수",
            "price": "72,000원",
            "reason": "증권사 목표가 일제 상향 (평균 95,000원) 및 외국인 3일 연속 순매수",
            "news_source": "한국경제",
            "news_url": "https://www.hankyung.com/finance"
        },
        {
            "code": "000660",
            "name": "SK하이닉스",
            "score": 89,
            "recommendation": "매수",
            "price": "185,000원",
            "reason": "HBM3e 독점 공급 모멘텀 지속 및 기관 수급 집중",
            "news_source": "매일경제",
            "news_url": "https://www.mk.co.kr/news/stock"
        },
        {
            "code": "035420",
            "name": "NAVER",
            "score": 85,
            "recommendation": "매수",
            "price": "210,000원",
            "reason": "사내 AI 검색 서비스 고도화 및 저점 매수세 유입",
            "news_source": "연합뉴스",
            "news_url": "https://www.yna.co.kr/market"
        }
    ]

    # 실시간 market 지수 가져오기
    from backend.api.market_fetcher import fetch_market_indices
    indices = await fetch_market_indices()
    # Convert Pydantic models to dicts for JSON serialization
    indices_dicts = [idx.model_dump() for idx in indices]

    # 포트폴리오 정보 가져오기 (Toss API 또는 시뮬레이션)
    from backend.teams.portfolio_team import PortfolioTeam
    from backend.api.toss_client import create_toss_client_from_env

    toss_client = None
    portfolio_result = None
    try:
        toss_client = create_toss_client_from_env()
        portfolio_team = PortfolioTeam(toss_client)
        portfolio_result = await portfolio_team.sync_portfolio()
    except ValueError as e:
        # 환경변수 미설정 등
        print(f"[Info] Toss API not configured: {e}. Using sample data.")
        portfolio_result = None
    except Exception as e:
        print(f"[Warning] Failed to fetch portfolio from Toss API: {e}. Using sample data.")
        portfolio_result = None
    finally:
        if toss_client:
            await toss_client.close()

    # portfolio_result가 PortfolioResult 객체 또는 None
    if portfolio_result is None:
        from backend.teams.portfolio_team import PortfolioTeam
        temp_team = PortfolioTeam(None)
        portfolio_result = temp_team._create_sample_portfolio()
        data_source = "sample"
        portfolio = portfolio_result
    else:
        portfolio = portfolio_result.portfolio
        data_source = portfolio_result.data_source

    # market_sentiment를 실제 포트폴리오 데이터로 업데이트
    if portfolio and portfolio.get("holdings"):
        # 실제 데이터가 있는 경우
        total_balance_str = portfolio.get("total_balance", "₩0")
        # 통화 기호 제거하고 숫자로 변환
        import re
        total_balance_num = float(re.sub(r'[^\d.]', '', total_balance_str)) if total_balance_str else 0
        market_sentiment = {
            "score": 78,
            "status": "강한 상승세",
            "summary": "실제 계좌 데이터 기반 분석 중...",
            "aum": total_balance_str,
            "daily_profit": "-"
        }
    else:
        # 샘플 데이터인 경우
        market_sentiment = {
            "score": 78,
            "status": "강한 상승세",
            "summary": "외국인 수급 유입과 반도체/AI 섹터 호재가 집중되고 있습니다. AI 퀀트 모델이 산출한 금일 투자 적합도는 최상위권입니다. (샘플 데이터)",
            "aum": "₩ 12,450,000",
            "daily_profit": "+ ₩ 342,000 (+2.8%)"
        }

    market_data = {
        "last_updated": current_time,
        "system_metrics": system_metrics,
        "market_sentiment": market_sentiment,
        "top_picks": top_picks,
        "orders": existing_orders,
        "indices": indices_dicts,
        "portfolio": portfolio,
        "data_source": data_source
    }

    return market_data


def record_order(code, name, price, action):
    """CEO의 Human-in-the-Loop 승인/거절 결과를 주문 이력으로 기록합니다.

    Args:
        code: 종목 코드 (예: "005930")
        name: 종목명 (예: "삼성전자")
        price: 현재가 (예: "72,000원")
        action: "BUY" (매수 승인) 또는 "REJECT" (매수 거절)

    Returns:
        기록된 주문 딕셔너리
    """
    data = load_data()
    if "orders" not in data:
        data["orders"] = []

    order = {
        "code": code,
        "name": name,
        "price": price,
        "action": action,
        "status": "체결완료" if action == "BUY" else "거절",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "executed_by": "CEO (Human-in-the-Loop 승인)"
    }

    # 최신 주문을 맨 위에 삽입
    data["orders"].insert(0, order)
    save_data(data)
    return order


def get_orders():
    """주문 이력 전체를 최신순으로 반환합니다."""
    data = load_data()
    return data.get("orders", [])


if __name__ == "__main__":
    import sys
    # 윈도우 터미널 기본 인코딩(cp949) 문제 해결을 위해 강제 UTF-8 설정
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("🤖 [investbin AI Backend] 실시간 시장 분석 엔진 가동 중...")
    data = asyncio.run(generate_ai_market_data())

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✅ [완료] AI 분석 결과가 성공적으로 {DATA_FILE}에 저장되었습니다!")
    print(f"   - 보존된 주문 이력: {len(data.get('orders', []))}건")