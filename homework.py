import logging
import os
import sys
import time
from json import JSONDecodeError

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

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(stream=sys.stdout)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)


def check_tokens() -> None:
    """
    Проверяет наличие обязательных переменных окружения.
    :raises EnvVarsNotSetError: Если нет обязательных переменных окружения.
    """
    if not all((PRACTICUM_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)):
        logger.critical(
            "Отсутствуют обязательные переменные окружения: "
            "PRACTICUM_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID."
        )
        raise EnvVarsNotSetError(
            "Отсутствуют обязательные переменные окружения!"
        )


def send_message(bot: TeleBot, message: str) -> None:
    """
    Отправляет сообщение в Telegram чат.
    :param bot: Актуальный бот.
    :param message: Сообщение, которое бот отправляет.
    """
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.debug(f"Успешно отправлено сообщение: {message}")
    except Exception as error:
        logger.error(f"Возникла ошибка при отправке сообщения: {error}")


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
        logger.debug(
            f"Выполнен запрос к {ENDPOINT}: Статус: {response.status_code}"
        )
        return response_json
    except JSONDecodeError as error:
        logger.error(
            f"Возникла ошибка при декодировании ответа {ENDPOINT}: \n{error}"
        )
        raise error
    except requests.RequestException as error:
        logger.error(f"Возникла ошибка при обращении {ENDPOINT}: \n{error}")
        raise AssertionError(
            "При запросе к API возникло исключение "
            "`requests.RequestException`."
        ) from error


def check_response(response: dict):
    """
    Проверяет ответ на наличие ошибок и отсутствие домашних заданий.

    :param response: Ответ от сервера в виде словаря.
    :raises CheckHomeworkError: Если отсутствуют домашки.
    :raises CheckRequestError: Если есть ошибка запроса.
    :raises CheckResponseError: Если есть ошибка ответа.
    """
    if not isinstance(response, dict):
        raise TypeError("Ответ должен быть словарем")

    if "homeworks" not in response:
        raise CheckHomeworkError("Отсутствует ключ 'homeworks' в ответе")

    if not isinstance(response["homeworks"], list):
        raise TypeError("Значение по ключу 'homeworks' должно быть списком")

    error = response.get("error")
    if error:
        raise CheckRequestError(f"Ошибка при запросе: {error}")

    code = response.get("code")
    if code:
        source = response.get("source", "неизвестный источник")
        message = response.get("message", "нет сообщения об ошибке")
        raise CheckResponseError(f"В {source} произошла ошибка {message}")

    required = ["status", "homework_name"]
    missing = [
        key for key in required if key not in response["homeworks"][-1].keys()
    ]
    if missing:
        raise CheckRequiredFieldsError(
            f"Отсутствуют обязательные ключи в элементе 'homeworks': {missing}"
        )

    current_date = response.get("current_date")
    if current_date is None or not isinstance(current_date, int):
        raise CheckHomeworkError(
            "Ключ 'current_date' должен быть целым числом"
        )


def parse_status(homework: dict) -> str | None:
    """
    Парсит статус домашней работы.
    :param homework: Данные последнего ДЗ.
    :raises CheckStatusExpectedError: Если статус не изменился.
    """
    actual_status = homework["status"]
    try:
        homework_name = homework["homework_name"]
    except KeyError as error:
        raise AssertionError(
            "В ответе API нет ключа homework_name"
        ) from error
    if actual_status not in HOMEWORK_VERDICTS:
        message = f"Недокументированный статус: {actual_status}"
        logger.error(message)
        raise CheckStatusExpectedError(message)
    if actual_status != LAST_HOMEWORK_STATUS and actual_status is not None:
        verdict = HOMEWORK_VERDICTS[actual_status]
        return "Изменился статус проверки работы " \
               f'"{homework_name}". {verdict}'
    logger.debug(f"Статус {actual_status} не изменился")
    return f"Статус проверки работы {homework_name} остался прежним."


def main():
    """Основная логика работы бота."""
    check_tokens()
    # Создаем объект класса бота
    bot = TeleBot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    global LAST_HOMEWORK_STATUS

    while True:
        try:
            response = get_api_answer(timestamp=timestamp)
            check_response(response=response)
            changes = parse_status(response["homeworks"][0])
            if "Изменился статус проверки" in changes:
                timestamp = response["current_date"]
                send_message(bot=bot, message=changes)
            LAST_HOMEWORK_STATUS = changes
        except Exception as error:
            message = f"Сбой в работе программы: {error}"
            logger.critical(message)
        time.sleep(RETRY_PERIOD)


if __name__ == "__main__":
    main()
