from selenium import webdriver
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import asyncio
from datetime import date
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ConversationHandler, CallbackQueryHandler, CallbackContext

# Константы для этапов разговора в Telegram
CHOOSE_COURIER, REPORT,CHOOSING, SURCHARGE = range(4)

# Глобальный список курьеров
couriers = []
driver = None



def setup_driver():
    global driver
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Новый headless режим
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36")
    
    # Маскировка Selenium
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            });
            Object.defineProperty(navigator, 'platform', {
              get: () => 'Win32'
            });
            window.chrome = {runtime: {}};
        """
    })
    
    driver.get('https://smena.samokat.ru/login')
    print("Запуск браузера и вход в систему...")

def login_to_site(username, password):
    global driver
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "phoneNumber"))
    )
    phone_input = driver.find_element(By.ID, "phoneNumber")
    phone_input.send_keys(username)
    print("Введен номер телефона")

    password_input = driver.find_element(By.ID, "password")
    password_input.send_keys(password)
    print("Введен пароль")

    submit_button = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.CLASS_NAME, "Button-module__Button--b74b"))
    )
    submit_button.click()
    print("Нажата кнопка 'Войти'")
    WebDriverWait(driver, 20).until(
        EC.url_contains('/schedule')
    )
    print("Авторизация успешна")

def fetch_timesheet_data(date=None):
    global driver
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    url = f'https://smena.samokat.ru/timesheet?date={date}'
    driver.get(url)
    print(f"Загрузка страницы для парсинга: {url}")

    # Замените time.sleep на asyncio.sleep
    time.sleep(1)  # Даем время странице загрузиться

    soup = BeautifulSoup(driver.page_source, 'html.parser')

    timesheet = []
    rows = soup.find_all('div', class_='Row__container--e468')
    print(f"Найдено курьеров: {len(rows)}")

    for row in rows:
        name_tag = row.find('span', class_='EmployeeInfo__name--da9c')
        name = name_tag.text.strip() if name_tag else 'Неизвестно'

        shift_tags = row.find_all('div', class_='Shifts__assigned--f90b')
        intervals = []
        for shift_tag in shift_tags:
            start_time = shift_tag.find('span', class_='Shifts__start--db07').text.strip()
            end_time = shift_tag.find('span', class_='Shifts__end--d418').text.strip()

            start_time = round_down_to_hour(start_time)
            end_time = round_up_to_hour(end_time)
            intervals.append((start_time, end_time))

        if intervals:
            start_time = intervals[0][0]
            end_time = intervals[-1][1]
            total_hours = sum(calculate_hours(start, end) for start, end in intervals)
        else:
            start_time = '00:00'
            end_time = '00:00'
            total_hours = 0.0

        total_time_tag = row.find('span', class_='TotalTime__totalTime--d25e')
        total_time_text = total_time_tag.text.strip() if total_time_tag else '0ч'

        try:
            total_hours = float(total_time_text.split('/')[1].replace('ч', '').strip()) if 'ч' in total_time_text else 0.0
        except (IndexError, ValueError):
            total_hours = 0.0

        orders = 0
        actions = row.find_all('div', class_='Actions__row--a69b')
        for action in actions:
            label = action.find('span', class_='Actions__label--c001')
            value = action.find('span', class_='_typography_1cc2v_1 _footnote_1nl11_1')
            if label and value:
                label_text = label.text.strip()
                value_text = value.text.strip()

                if 'зак.' in label_text:
                    value_parts = value_text.split('/')
                    if len(value_parts) > 1:
                        try:
                            orders = int(value_parts[0].strip())
                        except ValueError:
                            orders = 0

        print(f"Курьер: {name}, Интервалы смены: {intervals}, Начало смены: {start_time}, Конец смены: {end_time}, Общие часы: {total_hours}")
        timesheet.append((name, start_time, end_time, total_hours, orders))

    return timesheet

def round_down_to_hour(time_str):
    time_format = "%H:%M"
    dt = datetime.strptime(time_str, time_format)
    return dt.replace(minute=0, second=0, microsecond=0).strftime(time_format)

def round_up_to_hour(time_str):
    time_format = "%H:%M"
    dt = datetime.strptime(time_str, time_format)
    if dt.minute > 0 or dt.second > 0:
        dt = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return dt.strftime(time_format)

def calculate_hours(start_time, end_time):
    time_format = "%H:%M"
    start = datetime.strptime(start_time, time_format)
    end = datetime.strptime(end_time, time_format)
    if end < start:
        end += timedelta(days=1)
    delta = end - start
    return delta.seconds / 3600

def calculate_earnings(total_hours, start_time, end_time, orders, surcharge):
    hourly_rate = 100
    order_rate = 35

    start_dt = datetime.strptime(start_time, '%H:%M')
    end_dt = datetime.strptime(end_time, '%H:%M')

    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    base_earnings = total_hours * (hourly_rate + surcharge)

    first_last_hour_earnings = 0

    first_hour_start = datetime.strptime('08:00', '%H:%M')
    last_hour_end = datetime.strptime('23:00', '%H:%M')
    if start_dt <= first_hour_start < end_dt:
        first_last_hour_earnings += hourly_rate

    if start_dt < last_hour_end < end_dt:
        first_last_hour_earnings += hourly_rate

    order_earnings = orders * order_rate

    total_earnings = base_earnings + first_last_hour_earnings + order_earnings

    return total_earnings

def calculate_load(total_hours,start_time, end_time, current_time, orders):
    time_format = "%H:%M"

    start_dt = datetime.strptime(start_time, time_format)
    end_dt = datetime.strptime(end_time, time_format)
    current_dt = datetime.strptime(current_time, time_format)

    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    if current_dt < start_dt:
        current_load = 0
    else:
        time_passed = (current_dt - start_dt).seconds / 3600
        if time_passed > 0:
            current_load = orders / time_passed
        else:
            current_load = 0

    total_duration = total_hours
    if total_duration > 0:
        total_load = orders / total_duration
    else:
        total_load = 0

    return current_load, total_load
selected_courier_global = None
surcharge_global = 0
async def start(update: Update, context: CallbackContext) -> int:
    if not couriers:
        await update.message.reply_text("Ошибка: список курьеров пуст. Проверьте данные для входа и попробуйте снова.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{courier[0]}", callback_data=str(i))]
                for i, courier in enumerate(couriers)]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Выберите себя из списка:", reply_markup=reply_markup)
    return CHOOSING

async def choose_courier(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    choice = int(query.data)
    context.user_data['courier'] = couriers[choice]

    keyboard = [
    [InlineKeyboardButton("+20 🏖️", callback_data='20')],
    [InlineKeyboardButton("+30 🌞🌧️", callback_data='30')],
    [InlineKeyboardButton("+50 🌧️🏖️🌞", callback_data='50')],
    [InlineKeyboardButton("0 😞", callback_data='0')]
]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Надбавка?", reply_markup=reply_markup)
    return SURCHARGE

async def set_surcharge(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    surcharge = int(query.data)
    context.user_data['surcharge'] = surcharge

    courier = context.user_data['courier']

    current_time = datetime.now().strftime('%H:%M')
    current_load, total_load = calculate_load(courier[3],courier[1], courier[2], current_time, courier[4])

    earnings = calculate_earnings(courier[3], courier[1], courier[2], courier[4], surcharge)
    courier_name = courier[0].split()
    first_name = courier_name[0] if len(courier_name) > 0 else 'Неизвестно'
    last_name = courier_name[1] if len(courier_name) > 1 else 'Неизвестно'
    await query.edit_message_text(
		f"👤 {last_name} {first_name} | {courier[1]} - {courier[2]} \n ({courier[3]} ч)\n\n" 
		f"📦 За заказы: {courier[4] * 35} р. ({courier[4]})\n" 
		f"💵 За часы: {earnings - (courier[4] * 35)} р.\n" 
		f"💰 Всего: {earnings} р.\n\n" 
		f"🔢 Нагрузка: {current_load:.2f} || {total_load:.2f} зак/час\n"
)


    return ConversationHandler.END

async def update_couriers_data(context: CallbackContext) -> None:
    global couriers
    try:
        couriers = fetch_timesheet_data()
        print(f"Данные о курьерах обновлены: {len(couriers)} записей")
    except Exception as e:
        print(f"Ошибка при обновлении данных о курьерах: {e}")

async def scheduler(context: CallbackContext) -> None:
    while True:
        await update_couriers_data(context)
        await asyncio.sleep(10)  # Обновление данных каждые 10 секунд

async def current(update: Update, context: CallbackContext) -> None:
    if 'courier' not in context.user_data or 'surcharge' not in context.user_data:
        await update.message.reply_text("Ошибка: выберите сначала курьера и установите надбавку.")
        return

    courier = context.user_data['courier']
    surcharge = context.user_data['surcharge']

    # Поиск актуальных данных для выбранного курьера
    selected_courier = next((c for c in couriers if c[0] == courier[0]), None)
    if not selected_courier:
        await update.message.reply_text("Ошибка: выбранный курьер не найден в актуальных данных.")
        return

    # Обработка актуальных данных
    current_time = datetime.now().strftime('%H:%M')
    current_load, total_load = calculate_load(selected_courier[3],selected_courier[1], selected_courier[2], current_time, selected_courier[4])
    earnings = calculate_earnings(selected_courier[3], selected_courier[1], selected_courier[2], selected_courier[4], surcharge)

    courier_name = selected_courier[0].split()
    first_name = courier_name[0] if len(courier_name) > 0 else 'Неизвестно'
    last_name = courier_name[1] if len(courier_name) > 1 else 'Неизвестно'

    await update.message.reply_text(
        f"👤 {last_name} {first_name} | {selected_courier[1]} - {selected_courier[2]} \n ({selected_courier[3]} ч)\n\n"
        f"📦 За заказы: {selected_courier[4] * 35} р. ({selected_courier[4]})\n"
        f"💵 За часы: {earnings - (selected_courier[4] * 35)} р.\n"
        f"💰 Всего: {earnings} р.\n\n"
        f"🔢 Нагрузка: {current_load:.2f} || {total_load:.2f} зак/час\n"
    )

couriers_list = [
    "Левченко Дмитрий Олегович",
    "Антонов Иван Дмитриевич",
    "Громов Иван Николаевич",
    "Коновалов Данила Денисович",
    "Кузнецов Максим Павлович",
    "Сеничкин Леонид Владимирович",
    "Чирков Кирилл Максимович"
]

async def week(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton(courier, callback_data=str(i))] for i, courier in enumerate(couriers_list)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите курьера:", reply_markup=reply_markup)
    return CHOOSE_COURIER

async def choose_week_courier(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    choice = int(query.data)
    selected_courier = couriers_list[choice]
    context.user_data['selected_courier'] = selected_courier

    keyboard = [
        [InlineKeyboardButton("Отчет за прошлую неделю", callback_data='last_week')],
        [InlineKeyboardButton("Отчет за текущую неделю", callback_data='current_week')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(f"Выберите отчет для курьера {selected_courier}:", reply_markup=reply_markup)
    return REPORT

async def generate_report(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="⌛ Подождите, отчет формируется...")

    report_type = query.data
    selected_courier = context.user_data.get('selected_courier')

    if report_type == 'last_week':
        report = generate_week_report(selected_courier)
    else:
        report = generate_current_week_report(selected_courier)

    await query.edit_message_text(report)
    return ConversationHandler.END

def generate_week_report(courier_name):
    today = datetime.now()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)

    total_orders = 0
    total_hours = 0.0
    total_earnings = 0.0

    for i in range(7):
        date = (last_monday + timedelta(days=i)).strftime('%Y-%m-%d')
        timesheet = fetch_timesheet_data(date)

        for record in timesheet:
            if record[0] == courier_name:
                total_orders += record[4]
                total_hours += record[3]
                total_earnings += calculate_earnings(record[3], record[1], record[2], record[4], surcharge_global)

    average_orders_per_hour = total_orders / total_hours if total_hours > 0 else 0

    return (f"Отчет за прошедшую неделю для {courier_name} (с {last_monday.strftime('%d.%m.%Y')} по {last_sunday.strftime('%d.%m.%Y')}):\n"
            f"📦 Заказы: {total_orders}\n"
            f"⏰ Часы: {total_hours:.2f}\n"
            f"💰 Зарплата: {total_earnings:.2f} р.\n"
            f"⚖️ Нагрузка: {average_orders_per_hour:.2f} заказов/час")

def generate_current_week_report(courier_name):
    today = datetime.now()
    current_monday = today - timedelta(days=today.weekday())

    total_orders = 0
    total_hours = 0.0
    total_earnings = 0.0

    for i in range(today.weekday() + 1):
        date = (current_monday + timedelta(days=i)).strftime('%Y-%m-%d')
        timesheet = fetch_timesheet_data(date)

        for record in timesheet:
            if record[0] == courier_name:
                total_orders += record[4]
                total_hours += record[3]
                total_earnings += calculate_earnings(record[3], record[1], record[2], record[4], 0)

    average_orders_per_hour = total_orders / total_hours if total_hours > 0 else 0

    return (f"Отчет за текущую неделю для {courier_name}:\n"
            f"📅 С {current_monday.strftime('%Y-%m-%d')} по {today.strftime('%Y-%m-%d')}\n"
            f"📦 Всего заказов: {total_orders}\n"
            f"⏰ Всего часов: {total_hours:.2f}\n"
            f"💰 Общий заработок: {total_earnings:.2f} р.\n"
            f"⚖️ Нагрузка: {average_orders_per_hour:.2f} заказов/час")

def main():
    setup_driver()
    login_to_site("9377042875", "82424011")
    application = Application.builder().token("7114415477:AAEgVkm-owK9TbN9yArGhjG_nN0MsYH4ses").build()

        # Добавление ConversationHandler для команд /start и /week
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [CallbackQueryHandler(choose_courier)],
            SURCHARGE: [CallbackQueryHandler(set_surcharge)],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('current', current)],
    )

    week_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('week', week)],
        states={
            CHOOSE_COURIER: [CallbackQueryHandler(choose_week_courier)],
            REPORT: [CallbackQueryHandler(generate_report)],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('current', current)],
    )

    application.add_handler(week_conv_handler)
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('current', current))

    # Запуск функции для периодического обновления данных о курьерах
    application.job_queue.run_repeating(update_couriers_data, interval=20, first=1)

    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
