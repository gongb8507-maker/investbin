"""
Telegram Team - 텔레그램 팀
Human-in-the-Loop 승인/알림, 리포트 발송 (예측가/보유기간/목표가 포함)
"""
import asyncio
import json
import logging
import os
import pathlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

from backend.teams.orchestrator import get_orchestrator

load_dotenv()


@dataclass
class TelegramConfig:
    """텔레그램 설정"""
    bot_token: str = ""
    allowed_chat_ids: List[int] = None
    data_file: str = "public/data.json"


class TelegramTeam:
    """
    텔레그램 팀
    - CEO 승인/거절 버튼 UI (Human-in-the-Loop)
    - 실시간 시장 분석 리포트 발송 (예측가/보유기간/목표가 포함)
    - 주문 체결 알림
    - 시스템 상태 알림
    """

    def __init__(self, config: TelegramConfig = None):
        self.config = config or TelegramConfig()
        self.config.bot_token = self.config.bot_token or os.getenv('TELEGRAM_BOT_TOKEN') or "8715585247:AAFM5MSr1zx9B5lYcSXYiogP4pa6oqLo9j8"

        allowed_chat_str = os.getenv('ALLOWED_CHAT_ID', '')
        self.config.allowed_chat_ids = [
            int(x.strip()) for x in allowed_chat_str.split(',') if x.strip().isdigit()
        ]

        self.base_dir = pathlib.Path(__file__).parent.parent.parent
        self.data_file = self.base_dir / self.config.data_file

        self.application = None
        self._user_selections = {}  # chat_id -> {code: action}
        self._pending_approvals = {}  # approval_id -> approval_data

        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        self.logger = logging.getLogger(__name__)

    def is_allowed_chat(self, chat_id: int) -> bool:
        """채팅 ID 허용 여부 확인"""
        if not self.config.allowed_chat_ids:
            return True  # 허용 목록 없으면 모두 허용
        return chat_id in self.config.allowed_chat_ids

    async def send_unauthorized(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """미승인 채팅 안내"""
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚫 권한이 없습니다.\n채팅 ID: {chat_id}\n관리자에게 문의하여 ALLOWED_CHAT_ID에 등록하세요.",
            parse_mode='Markdown'
        )

    # ==========================================
    # 봇 핸들러들
    # ==========================================

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시작 명령어"""
        if not self.is_allowed_chat(update):
            await self.send_unauthorized(update, context)
            return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🤖 **investbin AI 트레이딩 봇**\n\n"
                 "📊 `/market` — 최신 시장 분석 및 AI 추천 종목 (승인/거절 선택 후 결정하기)\n"
                 "📋 `/orders` — 주문 체결 내역 확인\n"
                 "⚙️ `/status` — 시스템 파이프라인 상태 확인\n"
                 "🔔 `/alerts` — 알림 설정\n\n"
                 "토스증권 실계좌 연동 | 100% 실시간 데이터",
            parse_mode='Markdown'
        )

    async def market(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시장 분석 및 추천 종목 표시 (승인/거절 선택 UI)"""
        if not self.is_allowed_chat(update):
            await self.send_unauthorized(update, context)
            return

        data = self._load_market_data()
        if not data:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ 시장 데이터를 불러올 수 없습니다."
            )
            return

        chat_id = update.effective_chat.id
        if chat_id not in self._user_selections:
            self._user_selections[chat_id] = {}

        picks = data.get('top_picks', [])
        sentiment = data.get('market_sentiment', {})
        stock_details = data.get('stock_details', {})
        pipeline_status = data.get('pipeline_status', '알 수 없음')

        # 메시지 구성
        msg = f"📊 **최신 시장 분석** ({data.get('last_updated', 'N/A')})\n\n"
        msg += f"📈 **센티먼트:** {sentiment.get('status', 'N/A')} (점수: {sentiment.get('score', 0)}/100)\n"
        msg += f"💡 **요약:** {sentiment.get('summary', 'N/A')}\n"
        msg += f"🔄 **파이프라인:** {pipeline_status}\n\n"
        msg += "💰 **AI 추천 종목 (Top Picks):**\n"

        for p in picks:
            code = p['code']
            name = p['name']
            score = p.get('score', 0)
            rec = p.get('recommendation', 'N/A')
            price = p.get('price', '조회중...')
            reason = p.get('reason', '')
            news_url = p.get('news_url')
            news_text = f" [📰]({news_url})" if news_url else ""

            # 선택 상태 표시
            sel = self._user_selections[chat_id].get(code)
            if sel == 'BUY':
                status_icon = "✅ **승인 선택**"
            elif sel == 'REJECT':
                status_icon = "❌ **거절 선택**"
            else:
                status_icon = "⬜ **미선택**"

            # AI 분석 요약 (보유 종목만)
            detail = stock_details.get(code, {})
            ai_summary = ""
            if detail.get('expert_good') and '토스증권 API' not in detail.get('expert_good', ''):
                ai_summary = f"\n   🎯 {detail.get('ceo_comment', '')[:80]}..."

            msg += f"\n**{name} ({code})**{news_text}\n"
            msg += f"   점수: {score}/100 | {rec} | {price}\n"
            msg += f"   사유: {reason}\n"
            msg += f"   {status_icon}{ai_summary}\n"

        msg += "\n📌 각 종목을 **승인/거절** 선택 후 하단 **'결정하기'** 버튼을 눌러주세요."

        # 인라인 키보드 구성
        keyboard = []
        for p in picks:
            code = p['code']
            name = p['name']
            price = p['price']
            row = [
                InlineKeyboardButton(
                    f"✅ 승인",
                    callback_data=f"SELECT_BUY:{code}:{name}:{price}"
                ),
                InlineKeyboardButton(
                    f"❌ 거절",
                    callback_data=f"SELECT_REJECT:{code}:{name}:{price}"
                )
            ]
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton(
                "🚀 결정하기 (선택된 종목 일괄 처리)",
                callback_data="DECIDE_NOW"
            )
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode='Markdown',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

    async def orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """주문 체결 내역 조회"""
        if not self.is_allowed_chat(update):
            await self.send_unauthorized(update, context)
            return

        data = self._load_market_data()
        orders = data.get('orders', [])

        if not orders:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="📋 주문 체결 내역이 없습니다."
            )
            return

        msg = "📋 **주문 체결 내역** (최신 10건)\n\n"
        for o in orders[:10]:
            action = o.get('action', 'UNKNOWN')
            status = o.get('status', 'N/A')
            icon = "✅" if action == 'BUY' else "🔴" if action == 'SELL' else "❌"
            msg += f"{icon} **{o.get('name', 'N/A')}** ({o.get('code', 'N/A')})\n"
            msg += f"   액션: {action} | 상태: {status}\n"
            msg += f"   가격: {o.get('price', 'N/A')} | 수량: {o.get('quantity', 'N/A')}\n"
            msg += f"   시간: {o.get('timestamp', 'N/A')}\n"
            if o.get('broker_order_id'):
                msg += f"   증권사 주문번호: {o['broker_order_id']}\n"
            msg += "\n"

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg,
            parse_mode='Markdown'
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 상태 확인"""
        if not self.is_allowed_chat(update):
            await self.send_unauthorized(update, context)
            return

        data = self._load_market_data()
        orchestrator = get_orchestrator()
        state = orchestrator.get_state()

        msg = "⚙️ **시스템 상태**\n\n"
        msg += f"🔄 파이프라인: {data.get('pipeline_status', 'N/A')}\n"
        msg += f"🆔 실행 ID: {data.get('pipeline_run_id', 'N/A')}\n"
        msg += f"📊 버전: {data.get('pipeline_version', 1)}\n"
        msg += f"📡 데이터 소스: {data.get('data_source', 'N/A')}\n"
        msg += f"⏰ 최종 업데이트: {data.get('last_updated', 'N/A')}\n\n"

        sys_metrics = data.get('system_metrics', {})
        if sys_metrics:
            msg += f"💻 **시스템 메트릭스**\n"
            msg += f"   CPU: {sys_metrics.get('cpu_percent', 0)}%\n"
            msg += f"   메모리: {sys_metrics.get('memory_percent', 0)}%\n"
            msg += f"   가용 메모리: {sys_metrics.get('memory_available_gb', 0)} GB\n\n"

        portfolio = data.get('portfolio')
        if portfolio:
            msg += f"💼 **포트폴리오**\n"
            msg += f"   총 평가액: {portfolio.get('total_balance', 'N/A')}\n"
            msg += f"   보유 종목: {len(portfolio.get('holdings', []))}개\n"
            msg += f"   현금: {portfolio.get('cash', 'N/A')}\n"

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg,
            parse_mode='Markdown'
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """콜백 쿼리 처리 (승인/거절 선택, 결정하기)"""
        if not self.is_allowed_chat(update):
            await self.send_unauthorized(update, context)
            return

        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        data_str = query.data

        if data_str == "DECIDE_NOW":
            await self._process_decision(query, context, chat_id)
            return

        # 선택 토글: SELECT_BUY:CODE:NAME:PRICE 또는 SELECT_REJECT:...
        parts = data_str.split(':')
        if len(parts) >= 4 and parts[0] in ("SELECT_BUY", "SELECT_REJECT"):
            prefix = parts[0]
            action = "BUY" if prefix == "SELECT_BUY" else "REJECT"
            code = parts[1]
            name = parts[2]
            price = parts[3]

            if chat_id not in self._user_selections:
                self._user_selections[chat_id] = {}

            # 토글: 이미 같은 선택이면 해제
            if self._user_selections[chat_id].get(code) == action:
                del self._user_selections[chat_id][code]
            else:
                self._user_selections[chat_id][code] = action

            # 메시지 갱신 (market 화면 재렌더링)
            await self._refresh_market_view(query, context, chat_id)

    async def _refresh_market_view(self, query, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
        """마켓 뷰 갱신 (선택 상태 반영)"""
        data = self._load_market_data()
        if not data:
            await query.edit_message_text("시장 데이터를 불러올 수 없습니다.")
            return

        picks = data.get('top_picks', [])
        sentiment = data.get('market_sentiment', {})
        stock_details = data.get('stock_details', {})

        msg = f"📊 **최신 시장 분석** ({data.get('last_updated', 'N/A')})\n\n"
        msg += f"📈 **센티먼트:** {sentiment.get('status', 'N/A')} (점수: {sentiment.get('score', 0)}/100)\n"
        msg += f"💡 **요약:** {sentiment.get('summary', 'N/A')}\n\n"
        msg += "💰 **AI 추천 종목:**\n"

        for p in picks:
            code = p['code']
            name = p['name']
            price = p['price']
            sel = self._user_selections[chat_id].get(code)
            if sel == 'BUY':
                msg += f"✅ {name} ({code}): **승인 선택**\n"
            elif sel == 'REJECT':
                msg += f"❌ {name} ({code}): **거절 선택**\n"
            else:
                msg += f"⬜ {name} ({code}): **미선택**\n"

        msg += "\n📌 각 종목을 승인/거절 선택 후 **'결정하기'** 버튼을 눌러주세요."

        keyboard = []
        for p in picks:
            code = p['code']
            name = p['name']
            price = p['price']
            row = [
                InlineKeyboardButton(f"✅ 승인", callback_data=f"SELECT_BUY:{code}:{name}:{price}"),
                InlineKeyboardButton(f"❌ 거절", callback_data=f"SELECT_REJECT:{code}:{name}:{price}")
            ]
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton("🚀 결정하기 (선택된 종목 일괄 처리)", callback_data="DECIDE_NOW")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=msg,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

    async def _process_decision(self, query, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
        """최종 결정 처리 (실제 주문 실행 포함)"""
        selections = self._user_selections.get(chat_id, {})
        if not selections:
            await query.edit_message_text(
                text="⚠️ 선택된 종목이 없습니다. 먼저 승인/거절을 선택해주세요.",
                parse_mode='Markdown'
            )
            return

        await query.edit_message_text(text="⏳ 주문 실행 중...", parse_mode='Markdown')

        from backend import execute_toss_order, record_order
        from datetime import datetime
        import re

        results = []
        data = self._load_market_data()
        stock_details = data.get('stock_details', {})

        for code, action in selections.items():
            name = code
            price_str = "알 수 없음"
            current_price = 0

            # 종목 정보 조회
            for p in data.get('top_picks', []):
                if p['code'] == code:
                    name = p['name']
                    price_str = p['price']
                    price_match = re.search(r'[\d,]+', price_str.replace(',', ''))
                    if price_match:
                        current_price = float(price_match.group().replace(',', ''))
                    break

            if action == "BUY":
                # 상세 정보에서 목표가/손절가/보유기간 조회
                detail = stock_details.get(code, {})
                target_price = detail.get('target_price')
                stop_loss = detail.get('stop_loss')
                holding_period = detail.get('holding_period', '중기 (1-3개월)')

                # 실제 토스 주문 실행 (1주 기본)
                order_result = await execute_toss_order(
                    code=code,
                    name=name,
                    side="BUY",
                    quantity=1,
                    order_type="LIMIT",
                    price=current_price if current_price > 0 else None
                )

                if order_result.get("success"):
                    status = f"주문 접수됨 (ID: {order_result.get('order_id')})"
                    # 주문 기록에 상세 정보 추가
                    order_record = {
                        "code": code,
                        "name": name,
                        "price": price_str,
                        "action": "BUY",
                        "status": "체결완료",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "executed_by": "CEO (Telegram 승인)",
                        "broker_order_id": order_result.get('order_id'),
                        "target_price": target_price,
                        "stop_loss": stop_loss,
                        "holding_period": holding_period,
                        "confidence": detail.get('confidence', 0)
                    }
                    record_order(**order_record)
                else:
                    status = f"주문 실패: {order_result.get('error')}"
                    record_order(code, name, price_str, "BUY")
            else:
                order = record_order(code, name, price_str, "REJECT")
                status = order['status']

            results.append((name, code, action, status))

        # 선택 상태 초기화
        self._user_selections[chat_id] = {}

        # 결과 메시지
        msg = "🚀 **결정 처리 완료**\n\n"
        for name, code, action, status in results:
            icon = "✅" if action == "BUY" else "❌"
            msg += f"{icon} {name} ({code}) — {status}\n"
            msg += f"   시간: {datetime.now().strftime('%H:%M:%S')}\n\n"

        await query.edit_message_text(text=msg, parse_mode='Markdown')

    # ==========================================
    # 외부 알림 발송 메서드 (다른 팀에서 호출)
    # ==========================================

    async def send_approval_request(self, signals: List[Dict[str, Any]], stock_details: Dict[str, Any]) -> bool:
        """
        CEO에게 승인 요청 발송 (파이프라인 Step 5)
        - 예측가, 보유기간, 목표 매도가 포함된 리포트
        """
        if not self.application:
            self.logger.warning("Telegram application not initialized")
            return False

        for chat_id in self.config.allowed_chat_ids:
            try:
                msg = "🤖 **AI 트레이딩 승인 요청**\n\n"
                msg += f"⏰ 요청 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                msg += f"📋 추천 종목: {len(signals)}개\n\n"

                keyboard = []
                for sig in signals:
                    code = sig['code']
                    name = sig['name']
                    action = sig['action']
                    price = sig['current_price']
                    target = sig.get('target_price')
                    stop = sig.get('stop_loss')
                    qty = sig.get('quantity', 1)
                    confidence = sig.get('confidence', 0)
                    holding_period = sig.get('holding_period', '중기 (1-3개월)')
                    target_sell = sig.get('target_sell_price')
                    reason = sig.get('reason', '')

                    detail = stock_details.get(code, {})
                    expert_good = detail.get('expert_good', '')
                    expert_bad = detail.get('expert_bad', '')
                    ceo_comment = detail.get('ceo_comment', '')

                    msg += f"📌 **{name} ({code})**\n"
                    msg += f"   액션: {action} | 신뢰도: {confidence}%\n"
                    msg += f"   현재가: {price:,.0f}원 | 수량: {qty}주\n"
                    if target:
                        msg += f"   🎯 목표가: {target:,.0f}원\n"
                    if stop:
                        msg += f"   🛑 손절가: {stop:,.0f}원\n"
                    if target_sell:
                        msg += f"   💰 목표 매도가: {target_sell:,.0f}원\n"
                    msg += f"   📅 보유기간: {holding_period}\n"
                    msg += f"   사유: {reason}\n"
                    if expert_good and '토스증권 API' not in expert_good:
                        msg += f"   ✅ 호재: {expert_good}\n"
                        msg += f"   ⚠️ 악재: {expert_bad}\n"
                        msg += f"   💬 CEO: {ceo_comment}\n"
                    msg += "\n"

                    # 승인 ID 생성
                    approval_id = f"APP_{datetime.now().strftime('%Y%m%d%H%M%S')}_{code}"
                    self._pending_approvals[approval_id] = {
                        "approval_id": approval_id,
                        "signal": sig,
                        "chat_id": chat_id,
                        "status": "PENDING",
                        "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }

                    # 개별 승인/거절 버튼
                    row = [
                        InlineKeyboardButton(
                            f"✅ 승인: {name}",
                            callback_data=f"APPROVE:{approval_id}:BUY"
                        ),
                        InlineKeyboardButton(
                            f"❌ 거절: {name}",
                            callback_data=f"APPROVE:{approval_id}:REJECT"
                        )
                    ]
                    keyboard.append(row)

                msg += "📌 각 종목별로 승인/거절을 선택해주세요."

                keyboard.append([
                    InlineKeyboardButton("🚀 전체 승인 (모든 종목)", callback_data="APPROVE_ALL:BUY"),
                    InlineKeyboardButton("🚫 전체 거절", callback_data="APPROVE_ALL:REJECT")
                ])

                reply_markup = InlineKeyboardMarkup(keyboard)
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )

            except Exception as e:
                self.logger.error(f"승인 요청 발송 실패 (chat_id={chat_id}): {e}")
                return False

        return True

    async def send_order_notification(self, order: Dict[str, Any]) -> bool:
        """주문 체결 알림 발송"""
        if not self.application:
            return False

        action = order.get('action', 'UNKNOWN')
        icon = "✅" if action == 'BUY' else "🔴" if action == 'SELL' else "📋"

        msg = f"{icon} **주문 체결 알림**\n\n"
        msg += f"종목: {order.get('name')} ({order.get('code')})\n"
        msg += f"액션: {action}\n"
        msg += f"가격: {order.get('price')}\n"
        msg += f"수량: {order.get('quantity', 'N/A')}주\n"
        msg += f"상태: {order.get('status', 'N/A')}\n"
        msg += f"시간: {order.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n"
        if order.get('broker_order_id'):
            msg += f"증권사 주문번호: {order['broker_order_id']}\n"

        for chat_id in self.config.allowed_chat_ids:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode='Markdown'
                )
            except Exception as e:
                self.logger.error(f"주문 알림 발송 실패: {e}")

        return True

    async def send_system_alert(self, title: str, message: str, level: str = "INFO") -> bool:
        """시스템 알림 발송 (에러, 경고, 정보)"""
        if not self.application:
            return False

        icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨", "CRITICAL": "🔴"}
        icon = icons.get(level, "ℹ️")

        msg = f"{icon} **[{level}] {title}**\n\n{message}\n\n"
        msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        for chat_id in self.config.allowed_chat_ids:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode='Markdown'
                )
            except Exception as e:
                self.logger.error(f"시스템 알림 발송 실패: {e}")

        return True

    async def send_daily_report(self, data: Dict[str, Any]) -> bool:
        """일일 리포트 발송 (장 마감 후)"""
        if not self.application:
            return False

        portfolio = data.get('portfolio', {})
        orders = data.get('orders', [])
        top_picks = data.get('top_picks', [])

        msg = "📊 **일일 트레이딩 리포트**\n\n"
        msg += f"📅 {datetime.now().strftime('%Y년 %m월 %d일')}\n\n"

        if portfolio:
            msg += f"💼 **포트폴리오 현황**\n"
            msg += f"   총 평가액: {portfolio.get('total_balance', 'N/A')}\n"
            msg += f"   현금: {portfolio.get('cash', 'N/A')}\n"
            msg += f"   보유 종목: {len(portfolio.get('holdings', []))}개\n\n"

        if orders:
            today_orders = [o for o in orders if o.get('timestamp', '').startswith(datetime.now().strftime('%Y-%m-%d'))]
            buy_count = sum(1 for o in today_orders if o.get('action') == 'BUY')
            sell_count = sum(1 for o in today_orders if o.get('action') == 'SELL')
            msg += f"📋 **오늘 체결**\n"
            msg += f"   매수: {buy_count}건 | 매도: {sell_count}건\n\n"

        if top_picks:
            msg += f"🎯 **내일 주시 종목**\n"
            for p in top_picks[:3]:
                msg += f"   • {p['name']} ({p['code']}): {p.get('recommendation')} ({p.get('score')}/100)\n"

        for chat_id in self.config.allowed_chat_ids:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode='Markdown'
                )
            except Exception as e:
                self.logger.error(f"일일 리포트 발송 실패: {e}")

        return True

    # ==========================================
    # 유틸리티
    # ==========================================

    def _load_market_data(self) -> Optional[Dict[str, Any]]:
        """data.json 로드"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def run(self):
        """봇 실행 (블로킹)"""
        if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

        self.application = ApplicationBuilder().token(self.config.bot_token).build()

        self.application.add_handler(CommandHandler('start', self.start))
        self.application.add_handler(CommandHandler('market', self.market))
        self.application.add_handler(CommandHandler('orders', self.orders))
        self.application.add_handler(CommandHandler('status', self.status))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

        self.logger.info("Telegram Team 봇 가동 중... (승인/거절 + 리포트 모드)")
        self.application.run_polling()

    async def start_async(self):
        """비동기 시작 (다른 이벤트 루프에서 사용)"""
        self.application = ApplicationBuilder().token(self.config.bot_token).build()

        self.application.add_handler(CommandHandler('start', self.start))
        self.application.add_handler(CommandHandler('market', self.market))
        self.application.add_handler(CommandHandler('orders', self.orders))
        self.application.add_handler(CommandHandler('status', self.status))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        self.logger.info("Telegram Team 비동기 시작됨")

    async def stop_async(self):
        """비동기 중지"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()


import sys


def create_telegram_team() -> TelegramTeam:
    """TelegramTeam 인스턴스 생성"""
    config = TelegramConfig()
    return TelegramTeam(config)