import asyncio
import logging
import os
import re
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.enums import ChatType
import aiohttp

# --- Flask для Render ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

TRIGGER_WORDS = ["валюта", "валюты", "конверт", "конвертер", "convert", "курс", "обмен"]

CURRENCIES = {
    "USD": ("Доллар США", "🇺🇸"),
    "EUR": ("Евро", "🇪🇺"),
    "RUB": ("Российский рубль", "🇷🇺"),
    "GBP": ("Фунт стерлингов", "🇬🇧"),
    "CNY": ("Китайский юань", "🇨🇳"),
    "JPY": ("Японская йена", "🇯🇵"),
    "KZT": ("Казахстанский тенге", "🇰🇿"),
    "BYN": ("Белорусский рубль", "🇧🇾"),
    "UAH": ("Украинская гривна", "🇺🇦"),
    "TRY": ("Турецкая лира", "🇹🇷"),
    "CHF": ("Швейцарский франк", "🇨🇭"),
    "PLN": ("Польский злотый", "🇵🇱"),
    "AED": ("Дирхам ОАЭ", "🇦🇪"),
    "INR": ("Индийская рупия", "🇮🇳"),
    "KRW": ("Южнокорейская вона", "🇰🇷"),
    "CAD": ("Канадский доллар", "🇨🇦"),
    "AUD": ("Австралийский доллар", "🇦🇺"),
    "BTC": ("Биткоин", "₿"),
}

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class ConvertState(StatesGroup):
    waiting_for_amount = State()


def get_currency_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    items = list(CURRENCIES.items())
    for i in range(0, len(items), 2):
        row = []
        for code, (name, emoji) in items[i:i + 2]:
            row.append(InlineKeyboardButton(
                text=f"{emoji} {code} — {name}",
                callback_data=f"curr:{code}"
            ))
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_all_rates(base_currency: str) -> dict:
    url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            data = await response.json()
            if data.get("result") == "success":
                return data.get("rates", {})
            raise Exception("Не удалось получить курсы валют")


def convert_value(amount: float, rate: float) -> str:
    converted = amount * rate
    if converted >= 1000:
        return f"{converted:,.2f}"
    elif converted >= 1:
        return f"{converted:.2f}"
    else:
        return f"{converted:.6f}".rstrip("0").rstrip(".")


def format_result(amount: float, base_currency: str, rates: dict) -> str:
    lines = [f"💱 **{amount:,.2f} {base_currency}** равно:\n"]
    for code, (name, emoji) in CURRENCIES.items():
        if code == base_currency:
            continue
        if code in rates:
            value_str = convert_value(amount, rates[code])
            lines.append(f"{emoji} {code}: **{value_str}**")
    result_text = "\n".join(lines)
    if len(result_text) > 4000:
        result_text = result_text[:4000] + "..."
    return result_text


def get_result_keyboard(base_currency: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔁 Другая сумма", callback_data=f"again:{base_currency}"),
            InlineKeyboardButton(text="💱 Другая валюта", callback_data="change_currency"),
        ],
        [
            InlineKeyboardButton(text="📋 Скопировать", callback_data="copy_menu"),
        ],
    ])


# ================================================================
# ВАЖНО: process_amount идёт ПЕРЕД group_trigger!
# В aiogram 3 хендлеры проверяются в порядке регистрации.
# Более специфичный (с фильтром состояния) должен идти раньше.
# ================================================================

@dp.message(ConvertState.waiting_for_amount, F.text)
async def process_amount(message: types.Message, state: FSMContext):
    print(f"[AMOUNT] user={message.from_user.id} chat={message.chat.id} text={message.text!r}")

    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.reply(
            "❌ Пожалуйста, введите корректное положительное число.\n"
            "Пример: `100` или `99.5`",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    base_currency = data.get("base_currency", "USD")
    await state.clear()

    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        status_msg = await message.reply("🔄 Получаю актуальные курсы...")
    else:
        status_msg = await message.answer("🔄 Получаю актуальные курсы...")

    try:
        rates = await get_all_rates(base_currency)
        result_text = format_result(amount, base_currency, rates)
        if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            result_text = f"👤 {message.from_user.first_name}:\n\n" + result_text
        keyboard = get_result_keyboard(base_currency)
        await status_msg.edit_text(result_text, parse_mode="Markdown", reply_markup=keyboard)
        await state.update_data(last_amount=amount, last_base=base_currency, last_rates=rates)
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Ошибка: {str(e)}")


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text)
async def group_trigger(message: types.Message, state: FSMContext):
    if message.text.startswith("/"):
        return

    current_state = await state.get_state()
    print(f"[GROUP] user={message.from_user.id} chat={message.chat.id} state={current_state} text={message.text!r}")

    # Если пользователь сейчас вводит сумму — пропускаем (не мешаем process_amount)
    if current_state == ConvertState.waiting_for_amount.state:
        return

    text_lower = message.text.lower()
    words = re.findall(r"\b\w+\b", text_lower)
    if not any(trigger in words for trigger in TRIGGER_WORDS):
        return

    await state.clear()
    await message.reply(
        "💱 **Выберите исходную валюту:**",
        reply_markup=get_currency_keyboard(),
        parse_mode="Markdown"
    )


# ============ ЛИЧКА ============

@dp.message(Command("start", "help"), F.chat.type == ChatType.PRIVATE)
async def cmd_start_private(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для конвертации валют.\n\n"
        "📌 **Как пользоваться:**\n"
        "1. Нажми /convert\n"
        "2. Выбери исходную валюту\n"
        "3. Введи сумму\n"
        "4. Получи конвертацию во все валюты!\n\n"
        "💡 Добавь меня в группу — и я буду запускаться по слову `валюта`\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 Бот был создан @PHARAOH_G6"
    )


@dp.message(Command("convert"), F.chat.type == ChatType.PRIVATE)
async def cmd_convert_private(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "💱 **Выберите исходную валюту:**",
        reply_markup=get_currency_keyboard(),
        parse_mode="Markdown"
    )


# ============ ГРУППЫ: КОМАНДЫ ============

@dp.message(Command("start", "help"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_start_group(message: types.Message):
    await message.answer(
        "👋 Привет всем! Я бот для конвертации валют.\n\n"
        "📌 **Как пользоваться:**\n"
        "Напишите слово-триггер (например, `валюта` или `конверт`) — "
        "и я предложу выбрать исходную валюту.\n\n"
        "Или используйте команду /convert.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 Бот был создан @PHARAOH_G6"
    )


@dp.message(Command("convert"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_convert_group(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "💱 **Выберите исходную валюту:**",
        reply_markup=get_currency_keyboard(),
        parse_mode="Markdown"
    )


# ============ ОБЩИЕ CALLBACK-ХЕНДЛЕРЫ ============

@dp.callback_query(F.data.startswith("curr:"))
async def process_currency_choice(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[1]
    name, emoji = CURRENCIES.get(code, (code, ""))
    await state.update_data(base_currency=code)
    await state.set_state(ConvertState.waiting_for_amount)
    await callback.message.edit_text(
        f"✅ Выбрана валюта: {emoji} **{code}** ({name})\n\n"
        f"💵 {callback.from_user.first_name}, введите сумму для конвертации "
        f"(например, `100` или `99.5`):",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("again:"))
async def process_again(callback: CallbackQuery, state: FSMContext):
    base_currency = callback.data.split(":")[1]
    name, emoji = CURRENCIES.get(base_currency, (base_currency, ""))
    await state.set_data({"base_currency": base_currency})
    await state.set_state(ConvertState.waiting_for_amount)
    await callback.message.edit_text(
        f"✅ Валюта: {emoji} **{base_currency}** ({name})\n\n"
        f"💵 {callback.from_user.first_name}, введите новую сумму:",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data == "change_currency")
async def process_change_currency(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "💱 **Выберите исходную валюту:**",
        reply_markup=get_currency_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data == "copy_menu")
async def process_copy_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    last_rates = data.get("last_rates")
    last_base = data.get("last_base")
    last_amount = data.get("last_amount")
    if not last_rates or not last_base or last_amount is None:
        await callback.answer("❌ Данные устарели. Сделайте новый расчёт.", show_alert=True)
        return

    buttons = []
    row = []
    for code, (name, emoji) in CURRENCIES.items():
        if code == last_base or code not in last_rates:
            continue
        row.append(InlineKeyboardButton(text=f"{emoji} {code}", callback_data=f"copy:{code}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="copy_back")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"📋 **Что скопировать?**\n\n"
        f"Исходно: {last_amount:,.2f} {last_base}\n"
        f"Выберите валюту — пришлю отдельным сообщением:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("copy:"))
async def process_copy_value(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":")[1]
    data = await state.get_data()
    last_rates = data.get("last_rates")
    last_base = data.get("last_base")
    last_amount = data.get("last_amount")
    if not last_rates or code not in last_rates:
        await callback.answer("❌ Данные устарели. Сделайте новый расчёт.", show_alert=True)
        return

    value_str = convert_value(last_amount, last_rates[code])
    name, emoji = CURRENCIES.get(code, (code, ""))

    await callback.message.reply(f"`{value_str}`", parse_mode="Markdown")
    await callback.answer(f"✅ {value_str} {code} отправлено")

    result_text = format_result(last_amount, last_base, last_rates)
    if callback.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        result_text = f"👤 {callback.from_user.first_name}:\n\n" + result_text
    await callback.message.edit_text(
        result_text,
        parse_mode="Markdown",
        reply_markup=get_result_keyboard(last_base)
    )


@dp.callback_query(F.data == "copy_back")
async def process_copy_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    last_rates = data.get("last_rates")
    last_base = data.get("last_base")
    last_amount = data.get("last_amount")
    if not last_rates or not last_base or last_amount is None:
        await callback.answer("❌ Данные устарели.", show_alert=True)
        return

    result_text = format_result(last_amount, last_base, last_rates)
    if callback.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        result_text = f"👤 {callback.from_user.first_name}:\n\n" + result_text
    await callback.message.edit_text(
        result_text,
        parse_mode="Markdown",
        reply_markup=get_result_keyboard(last_base)
    )
    await callback.answer()


@dp.message(F.chat.type == ChatType.PRIVATE)
async def fallback_private(message: types.Message):
    await message.answer(
        "🤔 Не понимаю эту команду.\n"
        "Используй /convert, чтобы начать конвертацию."
    )


# ============ ЗАПУСК ============

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
