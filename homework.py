import logging
import os
import time
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
from typing import Union

import requests
from dotenv import load_dotenv
from telebot import TeleBot

from exceptions import (
    CheckHomeworkError,
    CheckRequestError,
    CheckRequiredFieldsError,
    CheckResponseError,
    CheckStatusExpectedError,
    EnvVarsNotSetError,
)

# Инициализация переменных
load_dotenv()

PRACTICUM_TOKEN = os.getenv("PRACTICUM_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

RETRY_PERIOD = 600
ENDPOINT = "https://practicum.yandex.ru/api/user_api/homework_statuses/"
HEADERS = {"Authorization": f"OAuth {PRACTICUM_TOKEN}"}

HOMEWORK_VERDICTS = {
    "approved": "Работа проверена: ревьюеру всё понравилось. Ура!",
    "reviewing": "Работа взята на проверку ревьюером.",
    "rejected": "Работа проверена: у ревьюера есть замечания.",
}
LAST_HOMEWORK_STATUS = None


def error_dispatcher(
    msg: str, error=Exception, critical: bool = False
) -> None:
    """
    Обработчик ошибок. Логирует ошибку и выбрасывает исключение.
    :param msg: Сообщение об ошибке.
    :param error: Сама ошибка.
    :param critical: Логировать ли критическую ошибку.
    :raises error: Выбрасывается передаваемое исключение.
    """
    if critical:
        logging.critical(msg)
        raise error(msg)
    logging.error(msg)
    raise error(msg)


def check_tokens() -> None:
    """
    Проверяет наличие обязательных переменных окружения.
    :raises EnvVarsNotSetError: Если нет обязательных переменных окружения.
    """
    tokens = {
        "PRACTICUM_TOKEN": PRACTICUM_TOKEN,
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    absent_tokens = [token for token in tokens if not tokens[token]]
    if absent_tokens:
        error_dispatcher(
            f"Отсутствует одно или более обязательных"
            f" переменных окружения: {absent_tokens}",
            EnvVarsNotSetError,
            critical=True,
        )


def send_message(bot: TeleBot, message: str) -> None:
    """
    Отправляет сообщение в Telegram чат.
    :param bot: Актуальный бот.
    :param message: Сообщение, которое бот отправляет.
    """
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.debug(f"Успешно отправлено сообщение: {message}")
    except Exception as error:
        logging.error(f"Возникла ошибка при отправке сообщения: {error}")


def get_api_answer(timestamp: int) -> dict:
    """
    Выполняет запрос к API Практикум.
    :param timestamp: Текущее время в секундах.
    :return: Ответ от API в виде словаря.
    """
    headers = {"Authorization": f"OAuth {PRACTICUM_TOKEN}"}
    params = {"from_date": timestamp}
    try:
        response = requests.get(url=ENDPOINT, headers=headers, params=params)
        assert response.status_code == 200
        response_json = response.json()
        logging.debug(
            f"Выполнен запрос к {ENDPOINT}: Статус: {response.status_code}"
        )
        return response_json
    except JSONDecodeError as error:
        error_dispatcher(
            f"Возникла ошибка при декодировании ответа {ENDPOINT}: \n{error}",
            error,
        )
    except requests.RequestException as error:
        error_dispatcher(
            f"Возникла ошибка при запросе к API {ENDPOINT}: \n{error}", error
        )


def check_response(response: dict):
    """
    Проверяет ответ на наличие ошибок и отсутствие домашних заданий.

    :param response: Ответ от сервера в виде словаря.
    :raises CheckHomeworkError: Если отсутствуют домашки.
    :raises CheckRequestError: Если есть ошибка запроса.
    :raises CheckResponseError: Если есть ошибка ответа.
    """
    if not isinstance(response, dict):
        resp_type = type(response)
        error_dispatcher(
            f"Ответ должен быть словарем, но получили {resp_type}.", TypeError
        )

    if "homeworks" not in response:
        error_dispatcher(
            "Отсутствует ключ 'homeworks' в ответе", CheckHomeworkError
        )

    if not isinstance(response["homeworks"], list):
        hw_type = type(response["homeworks"])
        error_dispatcher(
            f"Значение по ключу 'homeworks' должно "
            f"быть списком, но получили {hw_type}.",
            TypeError,
        )

    if "error" in response:
        error_dispatcher(
            f"Ошибка при запросе: {response['error']}", CheckRequestError
        )

    if "code" in response:
        source = response.get("source", "неизвестный источник")
        message = response.get("message", "нет сообщения об ошибке")
        error_dispatcher(
            f"В {source} произошла ошибка {message}", CheckResponseError
        )

    required = ["status", "homework_name"]
    missing = [
        key for key in required if key not in response["homeworks"][-1].keys()
    ]
    if missing:
        error_dispatcher(
            f"Отсутствуют обязательные ключи в "
            f"элементе 'homeworks': {missing}",
            CheckRequiredFieldsError,
        )

    current_date = response.get("current_date", None)
    if current_date is None:
        error_dispatcher(
            "Отсутствует ключ 'current_date' в ответе", CheckHomeworkError
        )
    if not isinstance(current_date, int):
        error_dispatcher(
            f"Ключ 'current_date' должен быть целым числом. "
            f"Получили: {current_date} типа {type(current_date)}",
            CheckHomeworkError,
        )


def parse_status(homework: dict) -> Union[str, None]:
    """
    Парсит статус домашней работы.
    :param homework: Данные последнего ДЗ.
    :raises CheckStatusExpectedError: Если статус не изменился.
    """
    actual_status = homework["status"]
    try:
        homework_name = homework["homework_name"]
    except KeyError:
        error_dispatcher("В ответе API нет ключа homework_name", KeyError)
    if actual_status not in HOMEWORK_VERDICTS:
        error_dispatcher(
            f"Недокументированный статус: {actual_status}",
            CheckStatusExpectedError,
        )
    if actual_status != LAST_HOMEWORK_STATUS and actual_status is not None:
        verdict = HOMEWORK_VERDICTS[actual_status]
        return (
            "Изменился статус проверки работы "
            f'"{homework_name}". {verdict}'
        )
    logging.debug(f"Статус {actual_status} не изменился")


def main():
    """Основная логика работы бота."""
    check_tokens()
    # Создаем объект класса бота
    bot = TeleBot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    global LAST_HOMEWORK_STATUS
    last_tg_error = str()

    while True:
        try:
            response = get_api_answer(timestamp=timestamp)
            check_response(response=response)
            changes = parse_status(response["homeworks"][-1])
            if changes:
                timestamp = response["current_date"]
                send_message(bot=bot, message=changes)
            LAST_HOMEWORK_STATUS = changes
        except Exception as error:
            message = f"Сбой в работе программы: {error}"
            if message != last_tg_error:
                send_message(bot=bot, message=message)
                last_tg_error = message
            logging.critical(message)
        time.sleep(RETRY_PERIOD)


if __name__ == "__main__":
    # Настройка логгера
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(funcName)s:%(lineno)d] %(asctime)s "
               "%(name)s [%(levelname)s]: %(message)s",
    )
    file_log = "tg_bot.log"
    handler = RotatingFileHandler(
        filename=file_log,
        encoding="utf-8",
        mode="a",
        maxBytes=1000000,
        backupCount=5,
    )
    formatter = logging.Formatter(
        "[%(funcName)s:%(lineno)d] %(asctime)s [%(levelname)s]: %(message)s"
    )
    handler.setFormatter(formatter)
    logger = logging.getLogger(__name__)
    logger.addHandler(handler)

    # Запускаем бота
    main()
