import asyncio
import logging
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '8625632756:AAE9eQ347b06t8v9cH9vv0MyLCBA1OIklus')
WEB_APP_URL = os.getenv('WEB_APP_URL', 'https://promobot-gdjx.onrender.com')

def get_all_promos():
    """Получает ВСЕ промокоды из API для поиска по тексту"""
    try:
        response = requests.get(f"{WEB_APP_URL}/api/promos", timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"API error (get_all_promos): {e}")
    return []

def find_promo_in_text(text, promos):
    """Ищет промокод, чье ключевое слово содержится в тексте сообщения"""
    text_lower = text.lower()
    
    # Сортируем по длине ключа (длинные сначала) — чтобы "вкусвилл" не ловился внутри "вкусвилл доставка"
    sorted_promos = sorted(promos, key=lambda p: len(p['keyword']), reverse=True)
    
    for promo in sorted_promos:
        keyword = promo['keyword'].lower()
        # Проверяем, есть ли ключевое слово в тексте как отдельное слово или часть фразы
        if keyword in text_lower:
            logger.info(f"🔍 Найдено совпадение: '{keyword}' в '{text}'")
            return promo
    
    return None

def format_reply(promo):
    """Форматирует ответ с промокодом"""
    text = f"*{promo['title']}*\n"
    text += f"Промокод: `{promo['promo']}`\n"
    
    if promo.get("conditions"):
        for line in promo["conditions"].split("\n"):
            if line.strip():
                text += f" - {line.strip()}\n"
    
    if promo.get("link"):
        text += f"\n[Перейти на сайт]({promo['link']})"
    
    return text

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

@dp.message()
async def handle_message(message: types.Message):
    """Обрабатывает сообщения: ищет ключевые слова ВНУТРИ текста"""
    if not message.text:
        return
    
    user = message.from_user
    text = message.text.strip()
    logger.info(f"💬 Сообщение от @{user.username if user.username else user.id}: '{text}'")
    
    # Получаем все промокоды и ищем совпадение в тексте
    promos = get_all_promos()
    if not promos:
        logger.warning("⚠️ Не удалось получить список промокодов")
        return
    
    promo = find_promo_in_text(text, promos)
    
    if promo:
        reply = format_reply(promo)
        try:
            await message.answer(reply)
            logger.info(f"✅ Ответ отправлен в чат {message.chat.id}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
            try:
                await message.answer(reply, parse_mode=None)
            except:
                pass
    else:
        # Промокод не найден — молчим
        logger.info(f"🤫 Не найдено ключевых слов в: '{text}'")

async def main():
    logger.info("🤖 Bot starting...")
    logger.info(f"🔑 Token: {BOT_TOKEN[:10]}...")
    logger.info(f"🌐 API URL: {WEB_APP_URL}")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🪝 Webhook удалён, запускаем polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
    finally:
        await bot.session.close()
        logger.info("🔚 Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())