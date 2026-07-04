from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from database import db, User, Promo, Keyword, init_db
from dotenv import load_dotenv
import os
import requests

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
    db.create_all()  # Создаст новую таблицу keywords, старые данные останутся
    admin_username = os.getenv('ADMIN_USERNAME', 'admin')
    if not User.query.filter_by(username=admin_username).first():
        new_admin = User(username=admin_username)
        new_admin.set_password(os.getenv('ADMIN_PASSWORD', 'admin123'))
        db.session.add(new_admin)
        db.session.commit()
        print(f"Admin created: {admin_username}")

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://promobot-gdjx.onrender.com")

# ---- API ENDPOINTS ----
@app.route('/api/promo/<keyword>')
def get_promo(keyword):
    """Ищет промокод по ключевому слову (новая таблица + старое поле)"""
    kw = keyword.lower().strip()
    
    # 1. Ищем в новой таблице
    match = Keyword.query.filter_by(keyword=kw).first()
    if match:
        return jsonify(match.promo.to_dict())
    
    # 2. Фоллбэк на старое поле
    promo = Promo.query.filter_by(keyword=kw).first()
    if promo:
        return jsonify(promo.to_dict())
        
    return jsonify({'error': 'not found'}), 404

@app.route('/api/promos')
def get_all_promos():
    """Возвращает все промокоды с их ключевыми словами"""
    return jsonify([p.to_dict() for p in Promo.query.all()])

# ---- WEBHOOK (бот отвечает здесь) ----
@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработчик вебхука: ищет ключевые слова ВНУТРИ текста"""
    from database import Promo, Keyword
    
    update_data = request.get_json()
    if not update_data:
        print("❌ Webhook: нет данных", flush=True)
        return 'No data', 400

    try:
        message = update_data.get('message', {})
        text = message.get('text', '')
        chat_id = message.get('chat', {}).get('id')

        if not text or not chat_id:
            print(f"⚠️ Пропуск: text={bool(text)}, chat_id={bool(chat_id)}", flush=True)
            return 'ok', 200

        print(f"💬 Webhook: '{text}' from {chat_id}", flush=True)
        text_lower = text.lower().strip()

        # Загружаем все промокоды
        promos = Promo.query.all()
        print(f"📦 Загружено {len(promos)} промокодов из БД", flush=True)
        
        # Строим карту ключей с отладкой
        keyword_map = {}
        for promo in promos:
            # Собираем все ключи: новая таблица + старое поле
            keys = []
            if promo.keywords_list:
                keys.extend([k.keyword.lower().strip() for k in promo.keywords_list if k.keyword])
            if promo.keyword and promo.keyword.lower().strip() not in keys:
                keys.append(promo.keyword.lower().strip())
            
            for kw in keys:
                if kw and kw not in keyword_map:  # первый ключ побеждает при конфликте
                    keyword_map[kw] = promo.to_dict()
        
        print(f"🗂️ Построена карта из {len(keyword_map)} уникальных ключей", flush=True)
        # Покажем первые 5 ключей для отладки
        sample_keys = list(keyword_map.keys())[:5]
        print(f"🔑 Примеры ключей: {sample_keys}", flush=True)
        
        # Ищем совпадение (длинные ключи в приоритете)
        found_promo = None
        found_keyword = None
        sorted_keys = sorted(keyword_map.keys(), key=len, reverse=True)
        for kw in sorted_keys:
            if kw in text_lower:
                found_promo = keyword_map[kw]
                found_keyword = kw
                print(f"🎯 НАЙДЕНО: '{kw}' в '{text}'", flush=True)
                break
        
        if found_promo:
            reply = f"*{found_promo['title']}*\n"
            reply += f"Промокод: `{found_promo['promo']}`\n"
            if found_promo.get("conditions"):
                for line in found_promo["conditions"].split("\n"):
                    if line.strip():
                        reply += f" - {line.strip()}\n"
            if found_promo.get("link"):
                reply += f"\n[Перейти на сайт]({found_promo['link']})"

            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"},
                timeout=5
            )
            if response.status_code == 200 and response.json().get("ok"):
                print(f"✅ ОТВЕТ ОТПРАВЛЕН в {chat_id}", flush=True)
            else:
                print(f"❌ Telegram API error: {response.status_code} - {response.text}", flush=True)
        else:
            print(f"🤫 НЕ НАЙДЕНО ключей в тексте: '{text}'", flush=True)
            # Покажем, какие ключи БЛИЗКИ (для отладки)
            close_matches = [kw for kw in keyword_map.keys() if kw in text_lower or text_lower in kw]
            if close_matches:
                print(f"🔍 Близкие совпадения: {close_matches}", flush=True)

    except Exception as e:
        print(f"💥 Webhook CRASH: {e}", flush=True)
        import traceback
        traceback.print_exc()

    return 'ok', 200

# ---- САЙТ (Роуты) ----
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
            title=request.form.get('title'),
            promo_code=request.form.get('promo_code'),
            conditions=request.form.get('conditions'),
            link=request.form.get('link'),
            emoji=request.form.get('emoji', ''),
            author_id=current_user.id
        )
        try:
            db.session.add(promo)
            db.session.flush()  # Получаем ID до коммита
            
            # Сохраняем ключевые слова
            raw_keywords = request.form.get('keywords', '')
            kw_list = [k.strip().lower() for k in raw_keywords.split(',') if k.strip()]
            
            for kw_text in kw_list:
                if Keyword.query.filter_by(keyword=kw_text).first():
                    db.session.rollback()
                    flash(f'Ключ "{kw_text}" уже занят другим промокодом', 'error')
                    return redirect(url_for('add_promo'))
                db.session.add(Keyword(keyword=kw_text, promo_id=promo.id))
                
            # Дублируем первый ключ в старое поле для совместимости
            if kw_list:
                promo.keyword = kw_list[0]
                
            db.session.commit()
            flash('Промокод добавлен!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
    return render_template('promo_form.html', promo=None, action='add')

@app.route('/promo/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_promo(id):
    promo = Promo.query.get_or_404(id)
    if request.method == 'POST':
        promo.title = request.form.get('title')
        promo.promo_code = request.form.get('promo_code')
        promo.conditions = request.form.get('conditions')
        promo.link = request.form.get('link')
        promo.emoji = request.form.get('emoji', '')
        
        try:
            # Обновляем ключи: удаляем старые, добавляем новые
            Keyword.query.filter_by(promo_id=promo.id).delete()
            
            raw_keywords = request.form.get('keywords', '')
            kw_list = [k.strip().lower() for k in raw_keywords.split(',') if k.strip()]
            
            for kw_text in kw_list:
                exists = Keyword.query.filter_by(keyword=kw_text).first()
                if exists and exists.promo_id != promo.id:
                    db.session.rollback()
                    flash(f'Ключ "{kw_text}" уже занят', 'error')
                    return redirect(url_for('edit_promo', id=id))
                db.session.add(Keyword(keyword=kw_text, promo_id=promo.id))
                
            if kw_list:
                promo.keyword = kw_list[0]  # Обновляем старое поле
                
            db.session.commit()
            flash('Промокод обновлён!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {e}', 'error')
    return render_template('promo_form.html', promo=promo, action='edit')

@app.route('/promo/delete/<int:id>')
@login_required
def delete_promo(id):
    promo = Promo.query.get_or_404(id)
    db.session.delete(promo)
    db.session.commit()
    flash('Промокод удалён', 'success')
    return redirect(url_for('dashboard'))

# --- АВТОУСТАНОВКА ВЕБХУКА ПРИ СТАРТЕ ---
try:
    import requests
    webhook_url = f"{WEB_APP_URL}/webhook"
    # setWebhook вызывается один раз при загрузке модуля (idempotent)
    res = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={"url": webhook_url, "drop_pending_updates": True},
        timeout=10
    )
    print(f"🤖 Webhook setup: {res.status_code} - {res.text[:100]}")
except Exception as e:
    print(f"⚠️ Не удалось установить вебхук при старте: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)