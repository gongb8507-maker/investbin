"""
investbin AI 자동매매 시스템 - 데이터 모델 정의
Pydantic 기반으로 타입 안전성 보장
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime
from enum import Enum


class MarketType(str, Enum):
    """시장 구분"""
    KR = "KR"      # 한국 시장 (코스피/코스닥)
    US = "US"      # 미국 시장 (나스닥/뉴욕)


class ActionType(str, Enum):
    """거래 액션 타입"""
    BUY = "BUY"
    SELL = "SELL"
    REJECT = "REJECT"
    HOLD = "HOLD"


class SignalStrength(str, Enum):
    """시그널 강도"""
    STRONG_BUY = "강력 매수"
    BUY = "매수"
    WEAK_BUY = "약매수"
    HOLD = "보유"
    WEAK_SELL = "약매도"
    SELL = "매도"
    STRONG_SELL = "강력 매도"


class OrderStatus(str, Enum):
    """주문 상태"""
    PENDING = "대기중"
    APPROVED = "승인됨"
    REJECTED = "거절됨"
    EXECUTED = "체결완료"
    FAILED = "실패"
    CANCELLED = "취소됨"


class PipelineStage(str, Enum):
    """파이프라인 단계"""
    DATA_COLLECTION = "데이터_수집"
    FIRST_REVIEW = "1차_검토"
    FINAL_DECISION = "최종_결정"
    TELEGRAM_APPROVAL = "텔레그램_승인요청"
    ORDER_EXECUTION = "주문_체결"
    COMPLETED = "완료"
    FAILED = "실패"


# ==========================================
# 1. 수집 데이터 모델 (Step 1)
# ==========================================

class DisclosureData(BaseModel):
    """공시 데이터 (DART 등)"""
    corp_code: str
    corp_name: str
    report_name: str
    receipt_date: str
    summary: str
    url: str


class FinancialData(BaseModel):
    """재무 데이터"""
    code: str
    name: str
    market_cap: Optional[float] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    net_profit: Optional[float] = None
    debt_ratio: Optional[float] = None


class NewsData(BaseModel):
    """뉴스 데이터"""
    title: str
    summary: str
    url: str
    published_at: str
    source: str
    sentiment_score: float = 0.0  # -1.0 ~ 1.0


class MarketTrendData(BaseModel):
    """시장 트렌드 데이터"""
    index_name: str
    current_value: float
    change_rate: float
    volume: Optional[int] = None
    market_breadth: Optional[dict] = None  # 상승/하락 종목 수


class CollectedData(BaseModel):
    """수집된 전체 데이터 (Step 1 결과)"""
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    disclosures: List[DisclosureData] = []
    financials: List[FinancialData] = []
    news: List[NewsData] = []
    market_trends: List[MarketTrendData] = []
    system_metrics: dict = {}


# ==========================================
# 2. 분석/검토 모델 (Step 2-3)
# ==========================================

class StockAnalysis(BaseModel):
    """종목 분석 결과"""
    code: str
    name: str
    market: MarketType
    current_price: float
    target_price: Optional[float] = None
    score: int = Field(ge=0, le=100)  # 0-100 점수
    recommendation: SignalStrength
    reason: str
    risk_level: Literal["낮음", "보통", "높음"] = "보통"
    expected_return: Optional[float] = None
    holding_period: Optional[str] = None  # "단기", "중기", "장기"


class ReviewResult(BaseModel):
    """검토 결과 (1차/2차)"""
    stage: PipelineStage
    reviewed_by: str  # "AI Director" 등
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    analyses: List[StockAnalysis] = []
    approved_codes: List[str] = []
    rejected_codes: List[str] = []
    notes: str = ""


# ==========================================
# 3. 시그널/주문 모델 (Step 3-5)
# ==========================================

class TradingSignal(BaseModel):
    """거래 시그널"""
    signal_id: str
    code: str
    name: str
    market: MarketType
    action: ActionType
    current_price: float
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    quantity: Optional[int] = None
    amount: Optional[float] = None  # 주문 금액
    confidence: int = Field(ge=0, le=100)
    reason: str
    strategy: str  # 사용된 전략명
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    expires_at: Optional[str] = None


class TelegramApproval(BaseModel):
    """텔레그램 승인 요청/결과"""
    approval_id: str
    signal_id: str
    chat_id: int
    message_id: Optional[int] = None
    status: OrderStatus = OrderStatus.PENDING
    user_action: Optional[ActionType] = None
    requested_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    responded_at: Optional[str] = None


class OrderRecord(BaseModel):
    """주문 기록 (체결 내역)"""
    order_id: str
    approval_id: Optional[str] = None
    signal_id: Optional[str] = None
    code: str
    name: str
    market: MarketType
    action: ActionType
    price: float
    quantity: int
    amount: float
    status: OrderStatus
    executed_at: Optional[str] = None
    executed_by: str = "CEO (Human-in-the-Loop 승인)"
    broker_order_id: Optional[str] = None  # 증권사 주문 번호
    error_message: Optional[str] = None


# ==========================================
# 4. 전체 시스템 상태 모델
# ==========================================

class SystemMetrics(BaseModel):
    """시스템 메트릭스"""
    cpu_percent: float
    memory_percent: float
    memory_available_gb: float
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class MarketSentiment(BaseModel):
    """시장 센티먼트"""
    score: int = Field(ge=0, le=100)
    status: str
    summary: str
    aum: str
    daily_profit: str


class TopPick(BaseModel):
    """AI 추천 종목"""
    code: str
    name: str
    score: int
    recommendation: str
    price: str
    reason: str


class HoldingSchema(BaseModel):
    """개별 종목 holdings"""
    code: str
    name: str
    qty: int
    avg_price: float
    current_price: float
    unrealized_pnl: float
    roi: float  # percentage


class PortfolioSchema(BaseModel):
    """포트폴리오 요약"""
    total_balance: str  # formatted with currency symbol, e.g., "₩12,450,000"
    cash: str
    holdings: List[HoldingSchema]


class DataJsonSchema(BaseModel):
    """data.json 전체 스키마"""
    last_updated: str
    system_metrics: SystemMetrics
    market_sentiment: MarketSentiment
    top_picks: List[TopPick]
    orders: List[OrderRecord]
    pipeline_status: PipelineStage = PipelineStage.DATA_COLLECTION
    collected_data: Optional[CollectedData] = None
    review_results: List[ReviewResult] = []
    trading_signals: List[TradingSignal] = []
    telegram_approvals: List[TelegramApproval] = []
    # New fields for real-time market data and portfolio
    indices: List[MarketTrendData] = []
    portfolio: Optional[PortfolioSchema] = None


# ==========================================
# 5. 설정 모델
# ==========================================

class TossApiConfig(BaseModel):
    """Toss Securities API 설정"""
    api_key: str
    base_url: str = "https://api.tossinvest.com/v1"
    timeout: int = 30
    paper_trading: bool = True  # 모의투자 모드


class DartApiConfig(BaseModel):
    """DART API 설정"""
    api_key: str
    base_url: str = "https://opendart.fss.or.kr/api"


class SchedulerConfig(BaseModel):
    """스케줄러 설정"""
    pipeline_interval_minutes: int = 30
    data_collection_time: str = "09:00"  # 장 시작 전
    review_time: str = "09:30"
    signal_time: str = "10:00"
    max_concurrent_orders: int = 5


class SystemConfig(BaseModel):
    """전체 시스템 설정"""
    toss: TossApiConfig
    dart: DartApiConfig
    scheduler: SchedulerConfig
    data_file: str = "data.json"
    log_level: str = "INFO"