import logging
import time
import re
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from urllib.parse import urlparse

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Глобальное хранилище аккаунтов и объявлений
accounts = []
next_account_id = 1
next_ad_id = 1

# Кеш для выдач: ключ – search_link, значение – dict с "soup" и "timestamp"
search_cache = {}
CACHE_TTL = 300  # 5 минут

# ---------------------------
# Функции работы с токеном
# ---------------------------
def refresh_token(account):
    url = "https://api.avito.ru/token/"
    data = {
        "grant_type": "client_credentials",
        "client_id": account["client_id"],
        "client_secret": account["client_secret"]
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(url, data=data, headers=headers)
    if response.status_code == 200:
        token_data = response.json()
        account["access_token"] = token_data["access_token"]
        account["token_expiration"] = time.time() + 24 * 3600 - 60
        logger.info("Получен новый токен для аккаунта %s", account["id"])
    else:
        logger.error("Ошибка получения токена для аккаунта %s: %s", account["id"], response.text)

def get_access_token(account):
    if "access_token" not in account or time.time() > account.get("token_expiration", 0):
        refresh_token(account)
    return account.get("access_token", "")

# ---------------------------
# Функции конвертации
# ---------------------------
def convert_kopecks_to_rubles(kopecks):
    """Преобразует значение в копейках в строку с рублями (формат 10.20)"""
    rubles = kopecks / 100.0
    return f"{rubles:.2f}"

def convert_rubles_to_kopecks(rubles_str):
    """Преобразует строку с рублями (например, "10.20") в копейки (целое число)"""
    try:
        return int(round(float(rubles_str) * 100))
    except:
        return 0

# ---------------------------
# Функция извлечения item_id из URL
# ---------------------------
def extract_item_id(link: str) -> str:
    match = re.search(r'(\d+)$', link)
    if match:
        return match.group(1)
    return None

# ---------------------------
# HTML шаблоны
# ---------------------------
HTML_INDEX = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Бид-менеджер Авито</title>
</head>
<body>
    <h1>Список аккаунтов</h1>
    <a href="/add-account">Добавить аккаунт</a>
    <ul>
    {% for account in accounts %}
        <li>
            Аккаунт ID: {{ account.id }} — avito_user_id: {{ account.avito_user_id }}
            (<a href="/account/{{ account.id }}">Просмотр</a>)
        </li>
    {% endfor %}
    </ul>
</body>
</html>
"""

HTML_ADD_ACCOUNT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Добавить аккаунт</title>
</head>
<body>
    <h1>Добавить новый аккаунт</h1>
    <form action="/add-account" method="post">
        <label>avito_user_id:<br>
            <input type="text" name="avito_user_id" required>
        </label><br><br>
        <label>client_id:<br>
            <input type="text" name="client_id" required>
        </label><br><br>
        <label>client_secret:<br>
            <input type="text" name="client_secret" required>
        </label><br><br>
        <button type="submit">Добавить аккаунт</button>
    </form>
    <br>
    <a href="/">Вернуться к списку аккаунтов</a>
</body>
</html>
"""

HTML_ACCOUNT_DETAIL = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Детали аккаунта</title>
</head>
<body>
    <h1>Аккаунт ID: {{ account.id }}</h1>
    <p>avito_user_id: {{ account.avito_user_id }}</p>
    <p>client_id: {{ account.client_id }}</p>
    <p>client_secret: {{ account.client_secret }}</p>
    <p>access_token: {{ account.access_token }}</p>
    <h2>Объявления</h2>
    <a href="/account/{{ account.id }}/add-ad">Добавить объявление вручную</a> |
    <a href="/account/{{ account.id }}/fetch-ads">Получить объявления из Авито</a>
    <ul>
    {% for ad in account.ads %}
        <li>
            Объявление ID: {{ ad.id }}<br>
            ad_link: {{ ad.ad_link }}<br>
            search_link: {{ ad.search_link if ad.search_link else "не указана" }}<br>
            real_item_id: {{ ad.item_id if ad.item_id else "не указан" }}<br>
            текущая ставка: {{ convert_kopecks_to_rubles(ad.current_bid) }} руб.<br>
            (<a href="/account/{{ account.id }}/edit-ad/{{ ad.id }}">Редактировать</a>) |
            (<a href="/account/{{ account.id }}/update-bids/{{ ad.id }}">Обновить ставки</a>)
        </li>
    {% endfor %}
    </ul>
    <br>
    <a href="/">Вернуться к списку аккаунтов</a>
</body>
</html>
"""

HTML_ADD_AD = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Добавить объявление</title>
</head>
<body>
    <h1>Добавить объявление для аккаунта ID: {{ account.id }}</h1>
    <form action="/account/{{ account.id }}/add-ad" method="post">
        <label>Ссылка на объявление:<br>
            <input type="text" name="ad_link" required>
        </label><br><br>
        <label>Ссылка на выдачу (можно оставить пустой):<br>
            <input type="text" name="search_link">
        </label><br><br>
        <label>Нижняя граница позиции:<br>
            <input type="number" name="lower_range" value="1" required>
        </label><br><br>
        <label>Верхняя граница позиции:<br>
            <input type="number" name="upper_range" value="10" required>
        </label><br><br>
        <label>Шаг изменения ставки (в рублях):<br>
            <input type="text" name="bid_step" value="10.00" required>
        </label><br><br>
        <label>Текущая ставка (в рублях):<br>
            <input type="text" name="current_bid" value="0.00" required>
        </label><br><br>
        <button type="submit">Добавить объявление</button>
    </form>
    <br>
    <a href="/account/{{ account.id }}">Вернуться к аккаунту</a>
</body>
</html>
"""

HTML_EDIT_AD = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Редактировать объявление</title>
</head>
<body>
    <h1>Редактировать объявление ID: {{ ad.id }} для аккаунта ID: {{ account.id }}</h1>
    <form action="/account/{{ account.id }}/edit-ad/{{ ad.id }}" method="post">
        <label>Ссылка на объявление:<br>
            <input type="text" name="ad_link" value="{{ ad.ad_link }}" required>
        </label><br><br>
        <label>Ссылка на выдачу (можно оставить пустой):<br>
            <input type="text" name="search_link" value="{{ ad.search_link }}">
        </label><br><br>
        <label>Нижняя граница позиции:<br>
            <input type="number" name="lower_range" value="{{ ad.position_range.lower }}" required>
        </label><br><br>
        <label>Верхняя граница позиции:<br>
            <input type="number" name="upper_range" value="{{ ad.position_range.upper }}" required>
        </label><br><br>
        <label>Шаг изменения ставки (в рублях):<br>
            <input type="text" name="bid_step" value="{{ convert_kopecks_to_rubles(ad.bid_step) }}" required>
        </label><br><br>
        <label>Текущая ставка (в рублях):<br>
            <input type="text" name="current_bid" value="{{ convert_kopecks_to_rubles(ad.current_bid) }}" required>
        </label><br><br>
        <label>Реальный item_id (если известен):<br>
            <input type="text" name="item_id" value="{{ ad.item_id if ad.item_id else '' }}">
        </label><br><br>
        <button type="submit">Сохранить изменения</button>
    </form>
    <br>
    <a href="/account/{{ account.id }}">Вернуться к аккаунту</a>
</body>
</html>
"""

HTML_FETCH_ADS = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Список объявлений из Авито</title>
</head>
<body>
    <h1>Объявления для аккаунта ID: {{ account.id }}</h1>
    <ul>
    {% for item in ads %}
        <li>
            ID: {{ item.id }}, Title: {{ item.title }}, URL: <a href="{{ item.url }}" target="_blank">{{ item.url }}</a>
            (<a href="/account/{{ account.id }}/add-ad-from-api/{{ item.id }}">Выбрать</a>)
        </li>
    {% endfor %}
    </ul>
    <br>
    <a href="/account/{{ account.id }}">Вернуться к аккаунту</a>
</body>
</html>
"""

# Добавим фильтр для конвертации в шаблонах
app.jinja_env.globals.update(convert_kopecks_to_rubles=convert_kopecks_to_rubles)

# ---------------------------
# Маршруты
# ---------------------------
@app.route("/")
def index():
    return render_template_string(HTML_INDEX, accounts=accounts)

@app.route("/add-account", methods=["GET", "POST"])
def add_account():
    global accounts, next_account_id
    if request.method == "GET":
        return render_template_string(HTML_ADD_ACCOUNT)
    else:
        avito_user_id = request.form["avito_user_id"]
        client_id = request.form["client_id"]
        client_secret = request.form["client_secret"]
        account = {
            "id": next_account_id,
            "avito_user_id": avito_user_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "ads": [],
            "access_token": "",
            "token_expiration": 0
        }
        next_account_id += 1
        accounts.append(account)
        logger.info("Добавлен аккаунт ID %s с avito_user_id %s", account["id"], avito_user_id)
        return redirect(url_for("index"))

@app.route("/account/<int:account_id>")
def account_detail(account_id):
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    return render_template_string(HTML_ACCOUNT_DETAIL, account=account)

@app.route("/account/<int:account_id>/add-ad", methods=["GET", "POST"])
def add_ad(account_id):
    global next_ad_id
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    if request.method == "GET":
        return render_template_string(HTML_ADD_AD, account=account)
    else:
        ad_link = request.form["ad_link"]
        search_link = request.form.get("search_link", "")
        lower_range = int(request.form["lower_range"])
        upper_range = int(request.form["upper_range"])
        bid_step = convert_rubles_to_kopecks(request.form["bid_step"])
        current_bid = convert_rubles_to_kopecks(request.form["current_bid"])
        ad = {
            "id": next_ad_id,
            "ad_link": ad_link,
            "search_link": search_link,
            "position_range": {"lower": lower_range, "upper": upper_range},
            "bid_step": bid_step,
            "current_bid": current_bid,
            "item_id": None
        }
        next_ad_id += 1
        account["ads"].append(ad)
        logger.info("Добавлено объявление ID %s для аккаунта ID %s", ad["id"], account["id"])
        check_position_and_update()
        return redirect(url_for("account_detail", account_id=account["id"]))

@app.route("/account/<int:account_id>/edit-ad/<int:ad_id>", methods=["GET", "POST"])
def edit_ad(account_id, ad_id):
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    ad = next((item for item in account["ads"] if item["id"] == ad_id), None)
    if ad is None:
        return "Объявление не найдено", 404
    if request.method == "GET":
        return render_template_string(HTML_EDIT_AD, account=account, ad=ad)
    else:
        ad["ad_link"] = request.form["ad_link"]
        ad["search_link"] = request.form.get("search_link", "")
        ad["position_range"]["lower"] = int(request.form["lower_range"])
        ad["position_range"]["upper"] = int(request.form["upper_range"])
        # Конвертируем введённые значения ставок из рублей в копейки
        ad["bid_step"] = convert_rubles_to_kopecks(request.form["bid_step"])
        ad["current_bid"] = convert_rubles_to_kopecks(request.form["current_bid"])
        ad["item_id"] = request.form.get("item_id") or None
        logger.info("Объявление ID %s для аккаунта ID %s обновлено", ad["id"], account["id"])
        
        # Если у объявления указан реальный item_id, отправляем запрос установки ручной ставки
        if ad.get("item_id"):
            payload = {
                "actionTypeID": 5,          # Например, 5 для пакета кликов
                "bidPenny": ad["current_bid"],# Ставка в копейках
                "itemID": int(ad["item_id"])  # Реальный идентификатор объявления
            }
            api_url = "https://api.avito.ru/cpxpromo/1/setManual"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {get_access_token(account)}"
            }
            response = requests.post(api_url, json=payload, headers=headers)
            if response.status_code == 200:
                logger.info("Установка ручной ставки для объявления %s выполнена успешно", ad["id"])
            else:
                logger.error("Ошибка установки ручной ставки для объявления %s: %s — %s", ad["id"], response.status_code, response.text)
        else:
            logger.warning("Для объявления %s не указан реальный item_id – ручная ставка не обновлена", ad["id"])
        
        check_position_and_update()
        return redirect(url_for("account_detail", account_id=account["id"]))


# Маршрут для получения объявлений из API Авито
@app.route("/account/<int:account_id>/fetch-ads", methods=["GET"])
def fetch_ads(account_id):
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    url = "https://api.avito.ru/core/v1/items"
    headers = {
        "Authorization": f"Bearer {get_access_token(account)}",
        "Content-Type": "application/json"
    }
    params = {
        "per_page": 100,
        "page": 1,
        "status": "active"
    }
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        logger.error("Ошибка получения объявлений из Авито: %s", response.text)
        return "Ошибка получения объявлений", response.status_code
    data = response.json()
    ads = data.get("resources", [])
    return render_template_string(HTML_FETCH_ADS, account=account, ads=ads)

# Маршрут для добавления объявления из API по выбранному item_id
@app.route("/account/<int:account_id>/add-ad-from-api/<int:item_id>", methods=["GET"])
def add_ad_from_api(account_id, item_id):
    global next_ad_id
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    url = f"https://api.avito.ru/core/v1/accounts/{''.join(account['avito_user_id'].split())}/items/{item_id}/"
    headers = {
        "Authorization": f"Bearer {get_access_token(account)}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        logger.error("Ошибка получения информации об объявлении: %s", response.text)
        return "Ошибка получения информации об объявлении", response.status_code
    item = response.json()
    real_item_id = item.get("id")
    if not real_item_id:
        ad_url = item.get("url", "")
        real_item_id = extract_item_id(ad_url)
    ad = {
        "id": next_ad_id,
        "ad_link": item.get("url", ""),
        "search_link": "",  # Можно заполнить вручную или реализовать логику формирования
        "position_range": {"lower": 1, "upper": 10},
        "bid_step": convert_rubles_to_kopecks("10.00"),
        "current_bid": convert_rubles_to_kopecks("0.00"),
        "item_id": real_item_id
    }
    next_ad_id += 1
    account["ads"].append(ad)
    logger.info("Добавлено объявление (из API) ID %s для аккаунта ID %s, real_item_id: %s", ad["id"], account["id"], ad["item_id"])
    check_position_and_update()
    return redirect(url_for("account_detail", account_id=account["id"]))

# Маршрут для обновления ставок для конкретного объявления через API
@app.route("/account/<int:account_id>/update-bids/<int:ad_id>", methods=["GET"])
def update_bids(account_id, ad_id):
    account = next((acc for acc in accounts if acc["id"] == account_id), None)
    if account is None:
        return "Аккаунт не найден", 404
    ad = next((item for item in account["ads"] if item["id"] == ad_id), None)
    if ad is None:
        return "Объявление не найдено", 404
    real_item_id = ad.get("item_id")
    if not real_item_id:
        return "Объявлению не присвоен реальный item_id. Пожалуйста, обновите объявление и укажите его вручную.", 400
    api_url = f"https://api.avito.ru/cpxpromo/1/getBids/{real_item_id}"
    headers = {
        "Authorization": f"Bearer {get_access_token(account)}",
        "Content-Type": "application/json"
    }
    response = requests.get(api_url, headers=headers)
    if response.status_code != 200:
        logger.error("Ошибка получения ставок для объявления %s: %s", ad_id, response.text)
        return f"Ошибка получения ставок: {response.text}", response.status_code
    data = response.json()
    # Предполагаем, что для ручного продвижения ставка хранится в data["manual"]["bidPenny"]
    if "manual" in data and "bidPenny" in data["manual"]:
        bid_in_kopecks = data["manual"]["bidPenny"]
        logger.info("Получена ставка в копейках: %s", bid_in_kopecks)
        ad["current_bid"] = bid_in_kopecks
    else:
        return "Не удалось получить ставку из ответа API", 400
    logger.info("Ставка для объявления %s обновлена до %s руб.", ad["id"], convert_kopecks_to_rubles(ad["current_bid"]))
    return redirect(url_for("account_detail", account_id=account["id"]))

def canonical_link(link: str) -> str:
    parsed = urlparse(link)
    path = parsed.path
    if path.startswith('/'):
        path = path[1:]
    segments = path.split('/')
    if len(segments) > 1:
        return '/'.join(segments[1:])
    else:
        return path

def check_position_and_update():
    logger.info("Запуск проверки позиций объявлений для всех аккаунтов")
    search_groups = {}
    for account in accounts:
        for ad in account["ads"]:
            if not ad["ad_link"]:
                logger.warning("Объявление ID %s: отсутствует ссылка на объявление", ad["id"])
                continue
            if not ad.get("search_link"):
                logger.debug("Объявление ID %s: поле search_link пустое, пропускаем проверку", ad["id"])
                continue
            search_groups.setdefault(ad["search_link"], []).append((account, ad))
    
    for search_link, group in search_groups.items():
        try:
            response = requests.get(search_link)
            if response.status_code != 200:
                logger.error("Ошибка получения выдачи по %s, код %s", search_link, response.status_code)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            ad_links = soup.find_all("a", itemprop="url")
            logger.debug("По выдаче %s: найдено %d ссылок", search_link, len(ad_links))
            for account, ad in group:
                user_canonical = canonical_link(ad["ad_link"])
                logger.debug("Объявление ID %s: пользовательская ссылка (каноническая): %s", ad["id"], user_canonical)
                ad_position = None
                for idx, link in enumerate(ad_links, start=1):
                    href = link.get("href", "")
                    found_canonical = canonical_link(href)
                    logger.debug("Элемент позиции %d: исходная href = %s, каноническая = %s", idx, href, found_canonical)
                    if user_canonical == found_canonical:
                        ad_position = idx
                        logger.info("Объявление ID %s: найдено на позиции %d", ad["id"], idx)
                        break
                if ad_position is None:
                    logger.info("Объявление ID %s не найдено в выдаче", ad["id"])
                    continue
                logger.info("Объявление ID %s: текущая позиция %s", ad["id"], ad_position)
                lower = ad["position_range"]["lower"]
                upper = ad["position_range"]["upper"]
                if ad_position < lower or ad_position > upper:
                    new_bid = ad["current_bid"] + ad["bid_step"]
                    ad["current_bid"] = new_bid
                    update_bid_on_avito(account, ad, new_bid)
                    logger.info("Объявление ID %s: ставка обновлена до %s (в копейках)", ad["id"], new_bid)
                else:
                    logger.info("Объявление ID %s: позиция в пределах допустимого диапазона", ad["id"])
        except Exception as e:
            logger.error("Ошибка проверки выдачи %s: %s", search_link, e)

def update_bid_on_avito(account, ad, new_bid):
    api_url = "https://api.avito.ru/cpxpromo/1/setManual"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_access_token(account)}"
    }
    data = {
        "actionTypeID": 5,          # Например, 5 для пакета кликов
        "bidPenny": new_bid,        # Новая ставка в копейках
        "itemID": int(ad["item_id"]) 
    }
    try:
        response = requests.post(api_url, json=data, headers=headers)
        if response.status_code == 200:
            logger.info("Объявление ID %s: ставка успешно обновлена через Avito API", ad["id"])
        else:
            logger.error("Объявление ID %s: ошибка обновления ставки: %s — %s", ad["id"], response.status_code, response.text)
    except Exception as e:
        logger.error("Объявление ID %s: ошибка вызова Avito API — %s", ad["id"], e)

# Планировщик для периодической проверки каждые 5 минут
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_position_and_update, trigger="interval", minutes=5)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
