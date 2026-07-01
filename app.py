from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from database import db, User, Promo, init_db
from dotenv import load_dotenv
import os
import asyncio
import json
import requests as req

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
init_db(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()
    admin_username = os.getenv('ADMIN_USERNAME', 'admin')
    if not User.query.filter_by(username=admin_username).first():
        new_admin = User(username=admin_username)
        new_admin.set_password(os.getenv('ADMIN_PASSWORD', 'admin123'))
        db.session.add(new_admin)
        db.session.commit()
        print(f"Admin created: {admin_username}")

# ---- НАСТРОЙКИ БОТА ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://promobot-gdjx.onrender.com")

# ---- ИНИЦИАЛИЗАЦИЯ БОТА ----
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

bot = None
dp = None

if BOT_TOKEN:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher()

    @dp.message()
    async def handle_message(message: types.Message):
        if not message.text:
            return
        print(f"Got message: {message.text}")
        keyword = message.text.lower().strip()
        try:
            response = req.get(f"{WEB_APP_URL}/api/promo/{keyword}", timeout=5)
            if response.status_code == 200:
                promo = response.json()
                if "error" not in promo:
                    text = f"*{promo['title']}*\n"
                    text += f"Промокод: `{promo['promo']}`\n"
                    if promo.get("conditions"):
                        for line in promo["conditions"].split("\n"):
                            if line.strip():
                                text += f" - {line.strip()}\n"
                    if promo.get("link"):
                        text += f"\n[Перейти на сайт]({promo['link']})"
                    try:
                        await message.answer(text)
                    except Exception:
                        await message.answer(text, parse_mode=None)
        except Exception as e:
            print(f"Bot error: {e}")

    # Устанавливаем webhook при старте
    async def setup_webhook():
        await bot.delete_webhook()
        await bot.set_webhook(f"{WEB_APP_URL}/webhook")
        print(f"Webhook set to {WEB_APP_URL}/webhook")

    asyncio.run(setup_webhook())
else:
    print("WARNING: BOT_TOKEN not set!")


@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработчик вебхука: ищет ключевые слова ВНУТРИ текста сообщения"""
    update_data = request.get_json()
    if not update_data:
        return 'No data', 400

    try:
        message = update_data.get('message', {})
        text = message.get('text', '')
        chat_id = message.get('chat', {}).get('id')

        if not text or not chat_id:
            return 'ok', 200

        print(f"💬 Webhook: '{text}' from {chat_id}")
        text_lower = text.lower().strip()

        # 1. Получаем ВСЕ промокоды
        promos_response = req.get(f"{WEB_APP_URL}/api/promos", timeout=5)
        if promos_response.status_code != 200:
            print("⚠️ Не удалось загрузить список промокодов")
            return 'ok', 200
        
        promos = promos_response.json()
        
        # 2. Ищем совпадение внутри текста (длинные ключи в приоритете)
        found_promo = None
        sorted_promos = sorted(promos, key=lambda p: len(p['keyword']), reverse=True)
        for promo in sorted_promos:
            keyword = promo['keyword'].lower()
            if keyword in text_lower:
                print(f"🔍 Найдено: '{keyword}' в '{text}'")
                found_promo = promo
                break
        
        # 3. Если нашли — отправляем ответ
        if found_promo:
            reply = f"*{found_promo['title']}*\n"
            reply += f"Промокод: `{found_promo['promo']}`\n"
            if found_promo.get("conditions"):
                for line in found_promo["conditions"].split("\n"):
                    if line.strip():
                        reply += f" - {line.strip()}\n"
            if found_promo.get("link"):
                reply += f"\n[Перейти на сайт]({found_promo['link']})"

            response = req.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "Markdown"
                },
                timeout=5
            )
            
            # Проверяем ответ от Telegram API
            if response.status_code == 200 and response.json().get("ok"):
                print(f"✅ Ответ отправлен в {chat_id}")
            else:
                print(f"❌ Telegram API error: {response.text}")
        else:
            print(f"🤫 Ключевые слова не найдены в: '{text}'")

    except Exception as e:
        print(f"💥 Webhook error: {e}")

    return 'ok', 200


# ---- САЙТ ----
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Неверный логин или пароль', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    promos = Promo.query.order_by(Promo.created_at.desc()).all()
    return render_template('dashboard.html', promos=promos)

@app.route('/promo/add', methods=['GET', 'POST'])
@login_required
def add_promo():
    if request.method == 'POST':
        promo = Promo(
            keyword=request.form.get('keyword').lower().strip(),
            title=request.form.get('title'),
            promo_code=request.form.get('promo_code'),
            conditions=request.form.get('conditions'),
            link=request.form.get('link'),
            emoji=request.form.get('emoji', ''),
            author_id=current_user.id
        )
        try:
            db.session.add(promo)
            db.session.commit()
            flash('Промокод добавлен!', 'success')
            return redirect(url_for('dashboard'))
        except:
            db.session.rollback()
            flash('Ошибка: такое ключевое слово уже существует', 'error')
    return render_template('promo_form.html', promo=None, action='add')

@app.route('/promo/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_promo(id):
    promo = Promo.query.get_or_404(id)
    if request.method == 'POST':
        promo.keyword = request.form.get('keyword').lower().strip()
        promo.title = request.form.get('title')
        promo.promo_code = request.form.get('promo_code')
        promo.conditions = request.form.get('conditions')
        promo.link = request.form.get('link')
        promo.emoji = request.form.get('emoji', '')
        try:
            db.session.commit()
            flash('Промокод обновлён!', 'success')
            return redirect(url_for('dashboard'))
        except:
            db.session.rollback()
            flash('Ошибка', 'error')
    return render_template('promo_form.html', promo=promo, action='edit')

@app.route('/promo/delete/<int:id>')
@login_required
def delete_promo(id):
    promo = Promo.query.get_or_404(id)
    db.session.delete(promo)
    db.session.commit()
    flash('Промокод удалён', 'success')
    return redirect(url_for('dashboard'))

@app.route('/api/promo/<keyword>')
def get_promo(keyword):
    promo = Promo.query.filter_by(keyword=keyword.lower()).first()
    if promo:
        return jsonify(promo.to_dict())
    return jsonify({'error': 'not found'}), 404

@app.route('/api/promos')
def get_all_promos():
    return jsonify([p.to_dict() for p in Promo.query.all()])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)