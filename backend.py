import os
import json
import asyncio
from datetime import datetime

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# ==========================================
# Bin-Invest AI Backend Engine
# ==========================================
# 모든 시세 데이터는 토스증권 공식 Open API에서만 가져옴
# - 보유 종목: GET /api/v1/holdings → 실시간 lastPrice, changeRate 등 포함
# - 시장 지수: GET /api/v1/market-indicators/prices (KOSPI, KOSDAQ)
# - 공공 시세 API 미제공: 비보유 종목은 토스에서 실시간 조회 불가

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


def generate_ai_analysis(stock_name: str, stock_code: str, change_rate: float, volume: int) -> dict:
    """
    investbin AI 분석 레이어 생성 (토스 데이터 위에 얹는 분석만)
    - 전담팀 호재/악재 한줄 요약
    - AI CEO 투자 포인트 한줄 코멘트
    """
    is_up = change_rate >= 0
    is_high_volume = volume > 10_000_000

    if is_up:
        if is_high_volume:
            expert_good = "기관/외국인 동반 매수세 강력, 수급 주도권 확보"
            expert_bad = "단기 과열 구간 진입, 차익 실현 매물 경계"
            ceo_comment = f"{stock_name}: 강한 수급 모멘텀 확인, 추가 상승 여력 충분하나 분할 매수 권장"
        else:
            expert_good = "저점 대비 반등 시도, 기술적 지지선 확인"
            expert_bad = "거래량 부족으로 상승 지속성 검증 필요"
            ceo_comment = f"{stock_name}: 기술적 반등 구간이나 거래량 동반 시 추세 전환 신호로 판단"
    else:
        if is_high_volume:
            expert_good = "고점 매물 소화 과정, 조정 마무리 신호 가능성"
            expert_bad = "기관/외국인 매도세 집중, 추가 하락 리스크 상존"
            ceo_comment = f"{stock_name}: 대량 거래 수반 하락으로 조정 폭 깊을 수 있음, 관망 후 진입 고려"
        else:
            expert_good = "저가 매수세 유입 대기 중, 지지선 형성 시도"
            expert_bad = "수급 공백 구간, 방향성 확인 전까지 변동성 확대"
            ceo_comment = f"{stock_name}: 거래량 실종 상태 하락으로 저점 확인 필요, 섣부른 진입 자제"

    return {
        "expert_good": expert_good,
        "expert_bad": expert_bad,
        "ceo_comment": ceo_comment
    }


def extract_holding_price_data(holding: dict) -> dict:
    """
    토스증권 holdings 응답에서 실시간 시세 데이터 추출
    (API 원본 필드 그대로 매핑, 가공 없음)
    """
    # holdings API 응답 필드:
    # symbol, name, marketCountry, currency, quantity, averagePurchasePrice,
    # lastPrice(현재가), profitLoss{amount, rate}, etc.
    return {
        "code": holding.get("symbol", ""),
        "name": holding.get("name", ""),
        "current_price": float(holding.get("lastPrice", 0)),
        "change_rate": round(float(holding.get("changeRate", 0)), 2) if holding.get("changeRate") is not None else 0.0,
        "change_amount": round(float(holding.get("changeAmount", 0)), 2) if holding.get("changeAmount") is not None else 0.0,
        "volume": int(holding.get("volume", 0)) if holding.get("volume") else 0,
        "high": round(float(holding.get("highPrice", 0)), 2) if holding.get("highPrice") else 0.0,
        "low": round(float(holding.get("lowPrice", 0)), 2) if holding.get("lowPrice") else 0.0,
        "open": round(float(holding.get("openPrice", 0)), 2) if holding.get("openPrice") else 0.0,
        "chart_data": [],  # 토스 API는 보유 종목 차트 별도 제공 안 함
        "currency": holding.get("currency", "KRW"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "qty": int(holding.get("quantity", 0)),
        "avg_price": float(holding.get("averagePurchasePrice", 0)),
        "unrealized_pnl": float(holding.get("profitLoss", {}).get("amount", 0)),
        "roi": round(float(holding.get("profitLoss", {}).get("rate", 0)) * 100, 2)
    }


def format_toss_holding_for_display(holding_data: dict) -> dict:
    """
    보유 종목 시세 데이터를 상세 뷰 표시용으로 포맷 (필드 매핑만)
    """
    return {
        "code": holding_data.get("code", ""),
        "name": holding_data.get("name", ""),
        "current_price": holding_data.get("current_price", 0),
        "change_rate": holding_data.get("change_rate", 0),
        "change_amount": holding_data.get("change_amount", 0),
        "volume": holding_data.get("volume", 0),
        "high": holding_data.get("high", 0),
        "low": holding_data.get("low", 0),
        "open": holding_data.get("open", 0),
        "chart_data": holding_data.get("chart_data", []),
        "currency": holding_data.get("currency", "KRW"),
        "timestamp": holding_data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    }


async def build_stock_details_from_holdings(toss_client, top_picks: list, account_seq: int) -> dict:
    """
    토스증권 API에서 보유 종목 실시간 시세 조회 후 top_picks와 매칭
    - GET /api/v1/holdings 응답의 lastPrice, changeRate 등 사용
    - 비보유 종목은 토스 API에서 실시간 조회 불가 (공공 시세 API 미제공)
    """
    stock_details = {}

    try:
        # 1. 보유 종목 전체 조회 (실시간 시세 포함)
        holdings_data = await toss_client.get_holdings(account_seq)
        holdings = holdings_data.get("items", [])

        # 심볼별로 인덱싱
        holding_map = {h.get("symbol"): h for h in holdings if h.get("symbol")}

        print(f"[Backend] Fetched {len(holdings)} holdings from Toss API")

    except Exception as e:
        print(f"[Backend] Error fetching holdings: {e}")
        holding_map = {}

    # 2. top_picks 각각에 대해 보유 여부 확인 후 데이터 구성
    for pick in top_picks:
        code = pick["code"]
        name = pick["name"]

        if code in holding_map:
            # 보유 중인 종목: 토스 API 실시간 데이터 사용
            holding = holding_map[code]
            holding_price = extract_holding_price_data(holding)
            display_data = format_toss_holding_for_display(holding_price)

            # investbin AI 분석 레이어 추가
            ai_analysis = generate_ai_analysis(
                name, code,
                display_data.get("change_rate", 0),
                display_data.get("volume", 0)
            )
            display_data.update(ai_analysis)

            stock_details[code] = display_data

            # top_picks price도 실시간으로 업데이트
            pick["price"] = f"{int(display_data['current_price']):,}원"

            print(f"[Backend] Real-time Toss data for holding: {name}({code}) - {display_data['current_price']:,}원 ({display_data['change_rate']:+.2f}%)")
        else:
            # 비보유 종목: 토스 공공 시세 API 미제공
            # 데이터 없음을 명시 (임의 생성 금지)
            stock_details[code] = {
                "code": code,
                "name": name,
                "current_price": 0,
                "change_rate": 0,
                "change_amount": 0,
                "volume": 0,
                "high": 0,
                "low": 0,
                "open": 0,
                "chart_data": [],
                "currency": "KRW",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "expert_good": "토스증권 API: 비보유 종목 공공 시세 미제공",
                "expert_bad": "보유 종목만 실시간 시세 조회 가능",
                "ceo_comment": f"{name}: 토스 계좌 보유 시 실시간 시세 표시됨 (현재 비보유)"
            }
            print(f"[Backend] Non-holding stock (no public quote from Toss): {name}({code})")

    return stock_details


async def generate_ai_market_data():
    """AI 퀀트 모델이 산출한 최신 시장 분석 데이터를 생성합니다.
    - 모든 시세/보유/지수 데이터: 토스증권 Open API 100% 다이렉트
    - investbin AI 분석: 토스 실시간 데이터 위에 레이어로만 추가
    - 비보유 종목 공공 시세: 토스 API 미제공으로 표시
    """
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 기존 데이터 로드하여 주문 이력 보존
    existing = load_data()
    existing_orders = existing.get("orders", [])

    # 시스템 메트릭스 수집
    system_metrics = get_system_metrics()

    # 추천 종목 (하드코딩 유지 - 추후 동적 데이터로 대체 가능)
    top_picks = [
        {
            "code": "005930",
            "name": "삼성전자",
            "score": 92,
            "recommendation": "강력 매수",
            "price": "조회중...",
            "reason": "증권사 목표가 일제 상향 (평균 95,000원) 및 외국인 3일 연속 순매수",
            "news_source": "한국경제",
            "news_url": "https://www.hankyung.com/finance"
        },
        {
            "code": "000660",
            "name": "SK하이닉스",
            "score": 89,
            "recommendation": "매수",
            "price": "조회중...",
            "reason": "HBM3e 독점 공급 모멘텀 지속 및 기관 수급 집중",
            "news_source": "매일경제",
            "news_url": "https://www.mk.co.kr/news/stock"
        },
        {
            "code": "035420",
            "name": "NAVER",
            "score": 85,
            "recommendation": "매수",
            "price": "조회중...",
            "reason": "사내 AI 검색 서비스 고도화 및 저점 매수세 유입",
            "news_source": "연합뉴스",
            "news_url": "https://www.yna.co.kr/market"
        }
    ]

    # 실시간 market 지수 가져오기 (토스 API - KOSPI, KOSDAQ만 제공)
    from backend.api.market_fetcher import fetch_market_indices
    indices = await fetch_market_indices()
    indices_dicts = [idx.model_dump() for idx in indices]

    # 포트폴리오 + 토스 클라이언트 초기화
    from backend.teams.portfolio_team import PortfolioTeam
    from backend.api.toss_client import create_toss_client_from_env

    toss_client = None
    portfolio_result = None
    data_source = "sample"
    portfolio = None
    account_seq = None

    try:
        toss_client = create_toss_client_from_env()
        portfolio_team = PortfolioTeam(toss_client)
        portfolio_result = await portfolio_team.sync_portfolio()

        if portfolio_result and portfolio_result.data_source == "live":
            portfolio = portfolio_result.portfolio
            data_source = "live"
            # 계좌 번호 확보
            accounts = await toss_client.get_accounts()
            if accounts:
                if toss_client.account_seq:
                    for acc in accounts:
                        if acc.get("accountSeq") == toss_client.account_seq:
                            account_seq = acc.get("accountSeq")
                            break
                if not account_seq:
                    account_seq = accounts[0].get("accountSeq")
    except ValueError as e:
        print(f"[Info] Toss API not configured: {e}. Using sample data.")
    except Exception as e:
        print(f"[Warning] Failed to fetch portfolio from Toss API: {e}. Using sample data.")
    finally:
        pass

    # 포트폴리오 폴백 처리
    if portfolio_result is None or portfolio_result.data_source == "sample":
        from backend.teams.portfolio_team import PortfolioTeam
        temp_team = PortfolioTeam(None)
        portfolio_result = temp_team._create_sample_portfolio()
        data_source = "sample"
        portfolio = portfolio_result

    # market_sentiment 업데이트
    if portfolio and portfolio.get("holdings"):
        total_balance_str = portfolio.get("total_balance", "₩0")
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
        market_sentiment = {
            "score": 78,
            "status": "강한 상승세",
            "summary": "외국인 수급 유입과 반도체/AI 섹터 호재가 집중되고 있습니다. AI 퀀트 모델이 산출한 금일 투자 적합도는 최상위권입니다. (샘플 데이터)",
            "aum": "₩ 12,450,000",
            "daily_profit": "+ ₩ 342,000 (+2.8%)"
        }

    # ==========================================
    # 핵심: 토스증권 API 보유 종목 실시간 시세로 stock_details 구성
    # ==========================================
    stock_details = {}
    if toss_client and account_seq and data_source == "live":
        try:
            # 1. top_picks 종목 상세 (보유 여부에 따라 실시간 또는 안내)
            stock_details = await build_stock_details_from_holdings(toss_client, top_picks, account_seq)

            # 2. 포트폴리오 보유 종목도 상세 뷰에 추가 (실시간 시세 포함)
            if portfolio and portfolio.get("holdings"):
                for holding in portfolio["holdings"]:
                    code = holding.get("code")
                    name = holding.get("name")
                    if code and code not in stock_details:
                        # 포트폴리오 데이터에서 상세 뷰용 데이터 구성
                        holding_detail = {
                            "code": code,
                            "name": name,
                            "current_price": holding.get("current_price", 0),
                            "change_rate": 0,  # 포트폴리오 API에 changeRate 없음
                            "change_amount": 0,
                            "volume": 0,
                            "high": 0,
                            "low": 0,
                            "open": 0,
                            "chart_data": [],
                            "currency": holding.get("currency", "KRW"),
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "expert_good": "보유 종목 실시간 시세 (토스증권 API)",
                            "expert_bad": "차트/호가 데이터는 토스 API 미제공",
                            "ceo_comment": f"{name}: 보유 수량 {holding.get('qty', 0)}주, 평단가 {holding.get('avg_price', 0):,.0f}{holding.get('currency', 'KRW')}, 평가손익 {holding.get('unrealized_pnl', 0):+,.0f}"
                        }
                        stock_details[code] = holding_detail

        except Exception as e:
            print(f"[Backend] Error building stock details from holdings: {e}")
    else:
        print("[Backend] Toss API not available or sample mode - stock details from holdings only")

    # 클라이언트 정리
    if toss_client:
        await toss_client.close()

    market_data = {
        "last_updated": current_time,
        "system_metrics": system_metrics,
        "market_sentiment": market_sentiment,
        "top_picks": top_picks,
        "orders": existing_orders,
        "indices": indices_dicts,
        "portfolio": portfolio,
        "stock_details": stock_details,
        "data_source": data_source
    }

    return market_data


def record_order(code, name, price, action):
    """CEO의 Human-in-the-Loop 승인/거절 결과를 주문 이력으로 기록합니다."""
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

    data["orders"].insert(0, order)
    save_data(data)
    return order


def get_orders():
    """주문 이력 전체를 최신순으로 반환합니다."""
    data = load_data()
    return data.get("orders", [])


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("🤖 [investbin AI Backend] 실시간 시장 분석 엔진 가동 중...")
    print("   - 토스증권 Open API 100% 다이렉트 연동 (보유 종목 실시간 시세)")
    print("   - 비보유 종목: 토스 공공 시세 API 미제공으로 표시")
    data = asyncio.run(generate_ai_market_data())

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✅ [완료] AI 분석 결과가 성공적으로 {DATA_FILE}에 저장되었습니다!")
    print(f"   - 데이터 소스: {data.get('data_source')}")
    print(f"   - 보존된 주문 이력: {len(data.get('orders', []))}건")
    print(f"   - 종목 상세 조회: {len(data.get('stock_details', {}))}개 (보유 종목만 실시간)")