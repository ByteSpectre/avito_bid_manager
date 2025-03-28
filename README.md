
# Бид-менеджер Авито

Это приложение реализовано на Python с использованием Flask и предназначено для управления аккаунтами Авито, добавления объявлений (как вручную, так и через API), автоматического извлечения идентификатора объявления (item_id) и обновления ставок продвижения через Avito API.

## Требования

* Python 3.7 или выше
* pip

## Установка

1. **Клонируйте репозиторий или скопируйте файлы проекта в локальную папку.**
2. **Создайте виртуальное окружение и активируйте его:**
   На Linux/MacOS:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

   На Windows:

   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```
3. **Установите зависимости:**

   ```bash
   pip install -r requirements.txt
   ```

## Конфигурация

Приложение не требует дополнительной конфигурации. Все настройки задаются через веб-интерфейс.

## Запуск приложения

1. **Запустите сервер:**

   ```bash
   python app.py
   ```

   Сервер запустится на порту 5000.
2. **Откройте браузер и перейдите по адресу:**
   [http://localhost:5000](http://localhost:5000/)

## Использование

### Добавление аккаунта

1. На главной странице нажмите ссылку «Добавить аккаунт».
2. Введите необходимые данные:
   * **avito_user_id** – идентификатор пользователя (как он указан в Авито, без пробелов)
   * **client_id** – идентификатор клиента, полученный в личном кабинете Авито
   * **client_secret** – секрет клиента, полученный в личном кабинете Авито

При добавлении аккаунта приложение автоматически запросит токен (access_token), действующий 24 часа.

### Добавление объявления

#### Ручное добавление

1. На странице аккаунта выберите «Добавить объявление вручную».
2. Заполните форму:
   * **Ссылка на объявление** – URL объявления (из него автоматически извлекается real item_id с помощью регулярного выражения)
   * **Ссылка на выдачу** – можно оставить пустой (если не требуется проверка позиции)
   * **Нижняя/Верхняя граница позиции** – допустимый диапазон позиций в выдаче
   * **Шаг изменения ставки** – в рублях (например, "10.00")
   * **Текущая ставка** – в рублях (например, "0.00")

#### Добавление через API

1. На странице аккаунта нажмите «Получить объявления из Авито».
2. Выберите нужное объявление из списка и нажмите «Выбрать». При этом информация о real_item_id будет автоматически извлечена.

### Редактирование объявления и обновление ставки

1. На странице аккаунта нажмите «Редактировать» для нужного объявления.
2. В форме редактирования можно изменить ссылку на объявление, ссылку на выдачу, диапазон позиций, шаг ставки и текущую ставку.

   Дополнительно можно указать реальный item_id вручную (если он не извлекается автоматически).
3. После сохранения изменений приложение отправит запрос к API для установки ручной ставки продвижения (endpoint `https://api.avito.ru/cpxpromo/1/setManual`). Значения ставок конвертируются из рублевых строк в копейки для отправки в API.

### Получение ставок

На странице аккаунта для каждого объявления есть кнопка «Обновить ставки». При нажатии отправляется запрос к API (endpoint `/cpxpromo/1/getBids/{itemID}`) для получения текущей ставки, которая затем обновляется в системе. Если ставка возвращается в копейках, она конвертируется в рубли для отображения.

## Кеширование выдачи

Если для нескольких объявлений указан один и тот же URL выдачи, приложение группирует их и выполняет парсинг выдачи только один раз (с использованием внутреннего кеша на 5 минут).

## Примечания

* Все значения ставок, вводимые пользователем, указываются в рублях (формат "10.20"), а для API они конвертируются в копейки.
* Токен авторизации действителен 24 часа. По истечении этого времени для каждого аккаунта автоматически запрашивается новый токен.
