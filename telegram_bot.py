import json
import logging
import os
import pathlib
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

# 환경 변수 로딩
load_dotenv()
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') or "8715585247:AAFM5MSr1zx9B5lYcSXYiogP4pa6oqLo9j8"

# 허용된 채팅 ID (콤마로 구분된 문자열 -> 리스트)
ALLOWED_CHAT_IDS = [
    int(x.strip()) for x in os.getenv('ALLOWED_CHAT_ID', '').split(',') if x.strip().isdigit()
]

# data.json 경로: public/ 폴더 아래
BASE_DIR = pathlib.Path(__file__).parent.resolve()
DATA_FILE = BASE_DIR / "public" / "data.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# backend.py의 주문 기록 함수 import ( 같은 디렉토리 )
import backend


def is_allowed_chat(update: Update) -> bool:
    """채팅 ID가 허용 목록에 있는지 확인"""
    if not ALLOWED_CHAT_IDS:
        # 허용 목록이 비어있으면 모두 허용 (하위 호환성)
        return True
    chat_id = update.effective_chat.id
    return chat_id in ALLOWED_CHAT_IDS


async def send_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """미승인 채팅에 안내 메시지 전송"""
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🚫 권한이 없습니다.\n채팅 ID: {chat_id}\n관리자에게 문의하여 ALLOWED_CHAT_ID에 등록하세요.",
        parse_mode='Markdown'
    )


def load_market_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        await send_unauthorized(update, context)
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🤖 투자 AI 봇이 대기 중입니다.\n\n"
             "/market — 최신 시장 분석 및 AI 추천 종목 (선택 후 결정하기)\n"
             "/orders — 주문 승인 내역 확인",
        parse_mode='Markdown'
    )


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI 추천 종목 목록을 보여주고, 승인/거절 선택 후 결정하기 버튼으로 최종 처리."""
    if not is_allowed_chat(update):
        await send_unauthorized(update, context)
        return
    data = load_market_data()
    if not data:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="시장 데이터를 불러올 수 없습니다.")
        return

    # 초기 선택 상태 저장 (user_data)
    if 'selections' not in context.user_data:
        context.user_data['selections'] = {}

    # Debug: 보여줄 시스템 메트릭스 출력
    system_metrics = data.get('system_metrics', {})
    if system_metrics:
        msg += f"🖥️ *CPU 사용량:* {system_metrics.get('cpu_percent', 0)}% | 🗃️ *메모리 사용량:* {system_metrics.get('memory_percent', 0)}%\n"

    picks = data['top_picks']

    # 메시지 포맷팅
    msg = f"📊 *최신 시장 분석 결과* ({data['last_updated']})\n\n"
    msg += f"💻 *시스템 메트릭스:* CPU {data.get('system_metrics', {}).get('cpu_percent', 0)}%, 메모리 {data.get('system_metrics', {}).get('memory_percent', 0)}%\n"
    msg += f"💾 *사용 가능 메모리:* {data.get('system_metrics', {}).get('memory_available_gb', 0)} GB\n"
    msg += f"📈 *센티먼트:* {data['market_sentiment']['status']} (점수: {data['market_sentiment']['score']})\n"
    msg += f"💡 *요약:* {data['market_sentiment']['summary']}\n\n"
    msg += "💰 *Top Picks:*\n"

    # 선택 상태 표시 추가
    selections = context.user_data.get('selections', {})
    for p in picks:
        code = p['code']
        name = p['name']
        price = p['price']
        news_url = p.get('news_url')
        news_text = f" [출처기사]({news_url})" if news_url else ""
        sel = selections.get(code)
        if sel == 'BUY':
            msg += f"✅ {name} ({code}){news_text}: 승인 선택\n"
        elif sel == 'REJECT':
            msg += f"❌ {name} ({code}){news_text}: 거절 선택\n"
        else:
            msg += f"⬜ {name} ({code}){news_text}: 미선택\n"

    msg += "\n📌 각 종목을 승인 또는 거절 선택한 후, 아래 '결정하기' 버튼을 눌러주세요."

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

    # 하단 결정하기 버튼
    keyboard.append([
        InlineKeyboardButton(
            "🚀 결정하기 (선택된 종목 일괄 처리)",
            callback_data="DECIDE_NOW"
        )
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    return

    # 콜백 처리 부분은 아래에 유지
    for p in picks:
        code = p['code']
        sel = context.user_data['selections'].get(code)
        if sel == 'BUY':
            msg += f"✅ {p['name']} ({p['code']}): 승인 선택�\n"
        elif sel == 'REJECT':
            msg += f"❌ {p['name']} ({p['code']}): 거절 선택�\n"
        else:
            msg += f"⬜ {p['name']} ({p['code']}): 미선택\n"

    msg += "\n📌 각 종목을 승인 또는 거리로 선택한 후, 아래 '결정하기' 버튼을 눌러주세요."

    # 인라인 키보드 구성
    keyboard = []
    for p in picks:
        code = p['code']
        name = p['name']
        price = p['price']
        # 두 개의 버튼: 승인 선택, 거절 선택
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

    # 하단 결정하기 버튼
    keyboard.append([
        InlineKeyboardButton(
            "🚀 결정하기 (선택된 종목 일괄 처리)",
            callback_data="DECIDE_NOW"
        )
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """주문 이력 목록을 전송합니다."""
    if not is_allowed_chat(update):
        await send_unauthorized(update, context)
        return
    orders = backend.get_orders()
    if not orders:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="등록된 주문 이력이 없습니다.")
        return

    msg = "📋 *주문 승인 내역*\n\n"
    for o in orders[:10]:  # 최신 10건만
        status_icon = "✅" if o['action'] == 'BUY' else "❌"
        msg += f"{status_icon} {o['name']} ({o['code']}) — {o['price']}\n"
        msg += f"   {o['status']} | {o['timestamp']}\n\n"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """콜백 처리: 선택 토글 또는 최종 결정"""
    if not is_allowed_chat(update):
        await send_unauthorized(update, context)
        return
    query = update.callback_query
    await query.answer()  # 로딩 중 표시 제거

    data = query.data
    parts = data.split(':')

    if data == "DECIDE_NOW":
        # 최종 결정 처리
        selections = context.user_data.get('selections', {})
        if not selections:
            await query.edit_message_text(
                text="⚠️ 선택된 종목이 없습니다. 먼저 승인/거절을 선택해주세요.",
                parse_mode='Markdown'
            )
            return

        # 기록
        results = []
        for code, action in selections.items():
            # 이름과 가격은 data.json에서 찾아야 하지만, 간단히 콜백에서 받지 못해 여기서 다시 조회
            market_data = load_market_data()
            if market_data:
                for p in market_data['top_picks']:
                    if p['code'] == code:
                        name = p['name']
                        price = p['price']
                        break
                else:
                    name = code
                    price = "알 수 없음"
            else:
                name = code
                price = "알 수 없음"

            order = backend.record_order(code, name, price, action)
            results.append((name, code, action, order['status'], order['timestamp']))

        # 선택 상태 초기화
        context.user_data['selections'] = {}

        # 결과 메시지
        msg = "🚀 *결정 처리 완료*\n\n"
        for name, code, action, status, ts in results:
            icon = "✅" if action == "BUY" else "❌"
            msg += f"{icon} {name} ({code}) — {status}\n"
            msg += f"   시간: {ts}\n\n"

        await query.edit_message_text(
            text=msg,
            parse_mode='Markdown'
        )
        return

    # 선택 토글 처리: SELECT_BUY:CODE:NAME:PRICE 또는 SELECT_REJECT:...
    if len(parts) >= 4 and parts[0] in ("SELECT_BUY", "SELECT_REJECT"):
        prefix = parts[0]
        action = "BUY" if prefix == "SELECT_BUY" else "REJECT"
        code = parts[1]
        name = parts[2]
        price = parts[3]

        # 선택 상태 업데이트
        if 'selections' not in context.user_data:
            context.user_data['selections'] = {}
        context.user_data['selections'][code] = action

        # 메시지 갱신 (다시 market 화면 보여주기)
        # 간단히 동일한 market 로직을 재사용하기 위해 쿼리 메시지를 편집
        # 여기서는 market() 함수를 직접 호출하지 않고, 현재 채팅의 메시지를 편집하여 최신 상태로 갱신
        # 최신 시장 데이터 로드
        market_data = load_market_data()
        if not market_data:
            await query.edit_message_text("시장 데이터를 불러올 수 없습니다.")
            return

        picks = market_data['top_picks']

        msg = f"📊 *최신 시장 분석 결과* ({market_data['last_updated']})\n\n"
        msg += f"📈 *센티먼트:* {market_data['market_sentiment']['status']} (점수: {market_data['market_sentiment']['score']})\n"
        msg += f"💡 *요약:* {market_data['market_sentiment']['summary']}\n\n"
        msg += "💰 *Top Picks:*\n"
        for p in picks:
            code_p = p['code']
            sel = context.user_data['selections'].get(code_p)
            if sel == 'BUY':
                msg += f"✅ {p['name']} ({p['code']}): 승인 선택�\n"
            elif sel == 'REJECT':
                msg += f"❌ {p['name']} ({p['code']}): 거절 선택�\n"
            else:
                msg += f"⬜ {p['name']} ({p['code']}): 미선택\n"

        msg += "\n📌 각 종목을 승인 또는 거리로 선택한 후, 아래 '결정하기' 버튼을 눌러주세요."

        # 키보드 다시 그리기
        keyboard = []
        for p in picks:
            code_p = p['code']
            name_p = p['name']
            price_p = p['price']
            row = [
                InlineKeyboardButton(
                    f"✅ 승인",
                    callback_data=f"SELECT_BUY:{code_p}:{name_p}:{price_p}"
                ),
                InlineKeyboardButton(
                    f"❌ 거절",
                    callback_data=f"SELECT_REJECT:{code_p}:{name_p}:{price_p}"
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
        await query.edit_message_text(
            text=msg,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return

    # 알 수 없는 콜백
    await query.edit_message_text("⚠️ 알 수 없는 요청입니다.")


if __name__ == '__main__':
    import sys
    import io
    # 윈도우 터미널 인코딩 문제 해결 (UTF-8 강제)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('market', market))
    application.add_handler(CommandHandler('orders', orders))
    application.add_handler(CallbackQueryHandler(handle_callback))

    print("Telegram Bot 가동 중... (선택 후 결정하기 모드)")
    application.run_polling()