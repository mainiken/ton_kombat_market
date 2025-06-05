import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urlencode, unquote
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint, random
from time import time
from datetime import datetime, timezone, timedelta
from dateutil import parser
import json
import os
import traceback
import colorama
from colorama import init, Fore, Style
import sys
from loguru import logger
from bot.config import settings
from datetime import date


HTTP_TIMEOUT_SECONDS = 100
RETRY_DELAY_SECONDS = 25
PROXY_CHECK_SLEEP_MINUTES = 20
ERROR_SLEEP_SECONDS = (180, 360)


MAX_401_RETRIES = 3
MARKET_PAGES_TO_MONITOR = 10


LONG_SLEEP_MINUTES = (60, 120)


TOKEN_LIVE_TIME_MIN = 3500
TOKEN_LIVE_TIME_MAX = 3600


BALANCE_CHECK_DELAY = (1, 30)

init()

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.utils import logger, config_utils, CONFIG_PATH
from bot.exceptions import InvalidSession


class FilterManager:
    def __init__(self, filters: List[Dict]):
        self._filters = filters
        for filter_obj in self._filters:
            if 'quantity' not in filter_obj:
                filter_obj['quantity'] = 1
            filter_obj['bought'] = 0
        self._current_filter_index = 0

    @property
    def current_filter(self) -> Dict:
        return self._filters[self._current_filter_index]

    def next_filter(self) -> None:
        self._current_filter_index = \
            (self._current_filter_index + 1) % len(self._filters)

    def is_current_filter_complete(self) -> bool:
        return (self._filters[self._current_filter_index]['bought'] >=
                self._filters[self._current_filter_index]['quantity'])

    def all_filters_complete(self) -> bool:
        return all(f['bought'] >= f['quantity'] for f in self._filters)

    def mark_bought(self, market_equipment_id: str, bought_ids: set) -> None:
        if market_equipment_id not in bought_ids:
            self._filters[self._current_filter_index]['bought'] += 1
            bought_ids.add(market_equipment_id)

    def __str__(self) -> str:
        return str(self._filters)


class ItemEvaluator:
    def __init__(self, log_method):
        self._log = log_method

    def evaluate(self, item: Dict, filter_obj: Dict, bought_ids: set) -> \
            Optional[Tuple[str, str, float, List, str]]:
        item_name = item.get('metadata', {}).get('equipment', {}).get('name', '???')
        stats = item.get('metadata', {}).get('equipment', {}).get(
            'equipment_stats', [])

        user_equipment_id = item.get('user_equipment_id')
        market_equipment_id = item.get('id')

        type_ok = (filter_obj.get('equipment_type', '*') == '*' or
                   item.get('equipment_type') ==
                   filter_obj.get('equipment_type'))
        if not type_ok:
            return None

        price_tok = float(item.get('price_gross', 0)) / 1_000_000_000
        max_price = filter_obj.get('max_price_tok', 1e12)
        price_ok = price_tok <= max_price
        if not price_ok:
            return None

        required_stats_match = True
        used_stats_indices = set()
        matched_stats_info = []

        required_stats_filters = filter_obj.get('required_stats', [])
        if required_stats_filters:
            for stat_filter in required_stats_filters:
                found_match = False
                for idx, stat in enumerate(stats):
                    if idx in used_stats_indices:
                        continue
                    if stat.get('type') == stat_filter.get('type'):
                        stat_level = int(stat.get('level', 0))
                        min_level = int(stat_filter.get('min_level', 0))
                        if stat_level >= min_level:
                            matched_stats_info.append((stat_filter, stat))
                            used_stats_indices.add(idx)
                            found_match = True
                            break
                if not found_match:
                    required_stats_match = False
                    break

            if len(matched_stats_info) != len(required_stats_filters):
                 required_stats_match = False
                 self._log('debug',
                           f"Предмет {item_name} не соответствует фильтру - не "
                           f"все required_stats найдены или количество не "
                           "совпадает.")


        if not required_stats_match:
            return None

        status = 'success'
        formatted_stats = []
        for stat_filter, stat in matched_stats_info:
            stat_name = stat_filter['type'].replace('-', ' ').capitalize()
            stat_level = stat.get('level')
            stat_value = stat.get('value')
            color = self._stat_color(stat_level)
            if 'percent' in stat_filter['type']:
                value_str = f"+{stat_value}%" if stat_value is not None else "+?"
            else:
                value_str = f"+{stat_value}" if stat_value is not None else "+?"
            formatted_stats.append(f"{color} {stat_name} {value_str}")
        stats_str = ', '.join(formatted_stats)
        price_info = "✅ цена"
        message = (f"{item_name} [market_id:{market_equipment_id}] "
                   f"({stats_str}) | {price_info} {price_tok:.1f} TOK")
        self._log('info', message, status)

        return (item_name, market_equipment_id, price_tok, formatted_stats,
                stats_str)

    def _stat_color(self, level: int) -> str:
        if level >= 5:
            return '🟣'
        elif level == 4:
            return '🔵'
        elif level == 3:
            return '🟢'
        elif level == 2:
            return '🟡'
        elif level == 1:
            return '🟠'
        else:
            return '⚪'


class MarketNavigator:
    def __init__(self, max_pages: int, log_method):
        self._max_pages = max_pages
        self._log = log_method
        self._current_page = 1
        self._direction = 1
        self._consecutive_empty = 0
        self._next_direction_change_time = time() + uniform(60, 1200)
        self._requests_in_current_direction = 0
        self._max_requests_in_random_direction = 0

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def direction(self) -> int:
        return self._direction

    def process_page_result(self, items: List[Dict]) -> None:
        now = time()
        if not items:
            self._consecutive_empty += 1
            if self._consecutive_empty >= 3:
                self._direction = -self._direction
                self._consecutive_empty = 0
                self._log('debug',
                          f"Обнаружены 3 пустые страницы. Меняю направление на "
                          f"{self._direction}", emoji_key='info')
                self._requests_in_current_direction = 0
        else:
            self._consecutive_empty = 0

        if now >= self._next_direction_change_time and self._direction == 1:
            self._direction = -1
            self._log('debug',
                      "Настало время сменить направление на обратное для "
                      "имитации человека.", emoji_key='info')
            self._max_requests_in_random_direction = randint(1, 3)
            self._requests_in_current_direction = 0
            self._next_direction_change_time = now + uniform(60, 1200)

        if (self._direction == -1 and self._requests_in_current_direction >=
                self._max_requests_in_random_direction):
            self._direction = 1
            self._log('debug',
                      "Достигнут лимит случайных запросов назад. Возвращаюсь "
                      "к основному направлению.", emoji_key='info')
            self._requests_in_current_direction = 0

        self._requests_in_current_direction += 1

        if self._direction == 1:
            self._current_page += 1
            if self._current_page > self._max_pages:
                self._log('info',
                          f"Достигнут лимит страниц {self._max_pages} при "
                          f"движении вперед. Начинаю с первой страницы.",
                          emoji_key='info')
                self._current_page = 1
                self._next_direction_change_time = time() + uniform(60, 1200)
                self._requests_in_current_direction = 0
                self._max_requests_in_random_direction = 0

        elif self._direction == -1:
             self._current_page -= 1
             if self._current_page < 1:
                 self._log('info',
                           f"Достигнута первая страница при движении назад. "
                           f"Меняю направление на вперед.", emoji_key='info')
                 self._direction = 1
                 self._current_page = 1
                 self._next_direction_change_time = time() + uniform(60, 1200)
                 self._requests_in_current_direction = 0
                 self._max_requests_in_random_direction = 0


class RateLimiter:
    def __init__(self, request_limit: int, time_window: int, log_method):
        self._request_limit = request_limit
        self._time_window = time_window
        self._log = log_method
        self._request_times = []

    async def wait_for_next_request(self) -> None:
        now = time()
        self._request_times = [t for t in self._request_times if now - t <
                               self._time_window]

        if len(self._request_times) >= self._request_limit:
            sleep_time = self._time_window - (now - self._request_times[0]) + 0.5
            self._log('debug',
                      f"Достигнут лимит запросов ({self._request_limit}/"
                      f"{self._time_window}s). Сон на {sleep_time:.2f}s",
                      emoji_key='sleep')
            await asyncio.sleep(sleep_time)

        self._request_times.append(time())


class BaseBot:
    EMOJI = {
        'debug': '🔍',
        'success': '✅',
        'info': 'ℹ️',
        'warning': '⚠️',
        'error': '❌',
        'balance': '💎',
        'reward': '💰',
        'equipment': '🗡️',
        'proxy': '🌐',
        'sleep': '😴',
        'mission': '🎯',
    }

    def __init__(self, tg_client: UniversalTelegramClient):
        self.error_401_count = 0
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client') and self.tg_client.client is not None:
            self.tg_client.client.no_updates = True
        self.session_name = tg_client.session_name
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._access_token: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical(f"CHECK accounts_config.json as it might be corrupted")
            exit(-1)
        self.proxy = session_config.get('proxy')
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def _log(self, level: str, message: str, emoji_key: Optional[str] = None) -> None:
        if level == 'debug' and not settings.DEBUG_LOGGING:
            return

        emoji = self.EMOJI.get(emoji_key, self.EMOJI.get(level, ''))

        full_message = f"{self.session_name} | {emoji} {message}" if emoji else f"{self.session_name} | {message}"

        if hasattr(logger, level):
            log_method = getattr(logger, level)
            log_method(full_message)
        else:
            logger.info(full_message)

    async def get_tg_web_data(self, app_name: str = "Ton_kombat_bot", path: str = "app") -> str:
        try:
            webview_url = await self.tg_client.get_app_webview_url(
                app_name,
                path,
                settings.REF_ID
            )
            if not webview_url:
                raise InvalidSession("Failed to get webview URL")
            tg_web_data = unquote(
                string=webview_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0]
            )
            self._init_data = tg_web_data
            return tg_web_data
        except aiohttp.ClientError as e:
            self._log('error', f"Сетевая ошибка при получении TG Web Data: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            raise InvalidSession("Ошибка сети при получении TG Web Data")
        except Exception as e:
            self._log('error', f"Неизвестная ошибка при получении TG Web Data: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            raise InvalidSession("Критическая ошибка при получении TG Web Data")

    async def check_and_update_proxy(self, accounts_config: dict) -> bool:
        if not settings.USE_PROXY:
            return True
        if not self._current_proxy or not await check_proxy(self._current_proxy):
            new_proxy = await get_working_proxy(accounts_config, self._current_proxy)
            if not new_proxy:
                return False
            self._current_proxy = new_proxy
            if self._http_client and not self._http_client.closed:
                await self._http_client.close()
            proxy_conn = {'connector': ProxyConnector.from_url(new_proxy)}
            self._http_client = CloudflareScraper(timeout=aiohttp.ClientTimeout(HTTP_TIMEOUT_SECONDS), **proxy_conn)
            self._log('info', f"Switched to new proxy: {new_proxy}", emoji_key='info')
        return True

    async def initialize_session(self) -> bool:
        try:
            self._is_first_run = await check_is_first_run(self.session_name)
            if self._is_first_run:
                self._log('info', f"First run detected for session {self.session_name}", emoji_key='info')
                await append_recurring_session(self.session_name)
            return True
        except aiohttp.ClientError as e:
            self._log('error', f"Ошибка сети при инициализации сессии: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            await asyncio.sleep(RETRY_DELAY_SECONDS)
            return await self.initialize_session()
        except Exception as e:
            self._log('error', f"Критическая ошибка при инициализации сессии: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            return False

    async def handle_401_error(self, error_401_count: int) -> int:
        if error_401_count >= MAX_401_RETRIES:
            self._log('warning', "Ошибка 401 - Обновляем токен и уходим в длительный сон...")

            await self.get_tg_web_data()

            sleep_time = randint(*[x * 60 for x in LONG_SLEEP_MINUTES])
            self._log('info', f"Сон на {sleep_time // 60} минут")
            await asyncio.sleep(sleep_time)

            return 0
        return error_401_count

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client:
            raise InvalidSession("HTTP client not initialized")
        try:
            start_time = time()
            self._log('debug', f"Making {method.upper()} request to {url}")

            async with getattr(self._http_client, method.lower())(url, **kwargs) as response:
                duration = time() - start_time
                status = response.status

                if status == 401:
                    self.error_401_count += 1
                    self.error_401_count = await self.handle_401_error(self.error_401_count)
                    return await self.make_request(method, url, **kwargs)

                if status == 200:
                    json_resp = await response.json()
                    self._log(
                        'debug',
                        message=f"Request {method.upper()} {url} | Status: {status} | Duration: {duration:.2f}s | Response: {str(json_resp)[:500]}..."
                    )
                    return json_resp

                self._log('debug', f"Request {method.upper()} {url} failed with status {status} | Duration: {duration:.2f}s")
                self._log('debug', f"Request {method.upper()} {url} | Status: {status} | Duration: {duration:.2f}s | Error: Request failed with status {status}")
                return None

        except aiohttp.ClientError as e:
            error_msg = f"Сетевая ошибка при выполнении запроса: {str(e)}\n{traceback.format_exc()}"
            self._log('error', error_msg)
            self._log('debug', f"Request {method.upper()} {url} | Error: {str(e)}")
            await asyncio.sleep(RETRY_DELAY_SECONDS)
            return await self.make_request(method, url, **kwargs)

        except Exception as e:
            error_msg = f"Критическая ошибка при выполнении запроса: {str(e)}\n{traceback.format_exc()}"
            self._log('error', error_msg)
            self._log('debug', f"Request {method.upper()} {url} | Error: {str(e)}")
            return None

    async def run(self) -> None:
        if not await self.initialize_session():
            return
        random_delay = uniform(1, settings.SESSION_START_DELAY)
        self._log('info', f'Бот запустится через ⌚<g> {int(random_delay)}s </g>', emoji_key='sleep')
        await asyncio.sleep(random_delay)
        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        async with CloudflareScraper(timeout=aiohttp.ClientTimeout(HTTP_TIMEOUT_SECONDS), **proxy_conn) as http_client:
            self._http_client = http_client
            while True:
                try:
                    session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
                    if not await self.check_and_update_proxy(session_config):
                        self._log('warning', 'Не удалось найти рабочий прокси. Сон 5 минут.', emoji_key='proxy')
                        await asyncio.sleep(PROXY_CHECK_SLEEP_MINUTES * 60)
                        continue
                    await self.process_bot_logic()
                except InvalidSession as e:
                    self._log('debug', f"Завершение работы: {str(e)}")
                    return
                except Exception as error:
                    sleep_duration = uniform(*ERROR_SLEEP_SECONDS)
                    self._log('debug', f"Unknown error: {error}. Sleeping for {int(sleep_duration)}")
                    self._log('debug', traceback.format_exc())
                    await asyncio.sleep(sleep_duration)


class MarketMonitorBot(BaseBot):
    def __init__(self, tg_client: UniversalTelegramClient):
        super().__init__(tg_client)
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        self._token_live_time = randint(TOKEN_LIVE_TIME_MIN, TOKEN_LIVE_TIME_MAX)
        self.headers = {
            'Host': 'liyue.tonkombat.com',
            'Origin': 'https://staggering.tonkombat.com',
            'Referer': 'https://staggering.tonkombat.com/',
            'User-Agent': session_config.get('user_agent'),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/json'
        }
        self.access_token_created_time = 0
        self._current_ref_id = None
        self._item_evaluator = ItemEvaluator(self._log)

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            self._current_ref_id = settings.REF_ID
        return self._current_ref_id

    async def get_tg_web_data(self, app_name: str = "Ton_kombat_bot", path: str = "app") -> str:
        try:
            webview_url = await self.tg_client.get_app_webview_url(
                app_name,
                path,
                self.get_ref_id()
            )
            if not webview_url:
                raise InvalidSession("Failed to get webview URL")
            tg_web_data = unquote(
                string=webview_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0]
            )
            self._init_data = tg_web_data
            self._log('debug', f'Получены TG Web Data для {app_name}: {tg_web_data}', emoji_key='info')
            return tg_web_data
        except aiohttp.ClientError as e:
            self._log('error', f"Сетевая ошибка при получении TG Web Data: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            raise InvalidSession("Ошибка сети при получении TG Web Data")
        except Exception as e:
            self._log('error', f"Неизвестная ошибка при получении TG Web Data: {str(e)}\n{traceback.format_exc()}", emoji_key='error')
            raise InvalidSession("Критическая ошибка при получении TG Web Data")

    async def users_balance(self) -> Optional[float]:
        await asyncio.sleep(uniform(*BALANCE_CHECK_DELAY))
        url = 'https://liyue.tonkombat.com/api/v1/users/balance'
        headers = {
            **self.headers,
            'Authorization': f'tma {self._init_data}'
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    balance_tok = float(result.get('data', 0)) / 1_000_000_000
                    self._log('info', f"Баланс: {balance_tok:.2f} TOK", 'balance')
                    return balance_tok
        except Exception:
            return None

    async def get_purchase_history(self, page: int = 1, page_size: int = 50) -> Optional[List[dict]]:
        await asyncio.sleep(uniform(*BALANCE_CHECK_DELAY))
        url = f"https://liyue.tonkombat.com/api/v1/market-equipment-history/me?page={page}&page_size={page_size}"
        headers = {
            **self.headers,
            'Authorization': f'tma {self._init_data}'
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    items = result.get('data', {}).get('items', [])
                    for item in items:
                        name = item.get('metadata', {}).get('equipment', {}).get('name', 'Unknown')
                        price = float(item.get('price_gross', 0)) / 1_000_000_000
                        self._log('info', f"Покупка: {name} за {price:.2f} TOK", 'equipment')
                    return items
        except Exception:
            return None

    async def buy_equipment(self, market_equipment_id: str, attempt: int = 1, max_attempts: int = 3) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/market/equipment/buy'
        headers = {
            **self.headers,
            'Authorization': f'tma {self._init_data}'
        }
        data = json.dumps({"market_equipment_id": market_equipment_id})
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url,
                    headers=headers,
                    data=data,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    if result.get('data'):
                        self._log('success', f'Покупка успешна: {market_equipment_id}')
                        return True
        except aiohttp.ClientError as e:
            if attempt < max_attempts:
                self._log('warning', f"Не удалось купить (попытка {attempt}/{max_attempts}): {market_equipment_id}. Повтор...")
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                return await self.buy_equipment(market_equipment_id, attempt=attempt+1, max_attempts=max_attempts)
            else:
                self._log('error', f"Не удалось купить после {max_attempts} попыток: {market_equipment_id}. Ошибка: {str(e)}")
                return False
        except Exception as e:
            self._log('error', f"Критическая ошибка при покупке: {str(e)}")
            self._log('error', f'Ошибка при покупке: {e}')
            return False

    async def debug_monitor_market(self, page_size: int = 20):
        REQUEST_LIMIT = 25
        TIME_WINDOW = 60
        ERROR_400_THRESHOLD = 5

        try:
            with open('.buy', 'r', encoding='utf-8') as f:
                filters_data = json.load(f)
            filter_manager = FilterManager(filters_data)
        except Exception as e:
            self._log('error', f"Ошибка чтения .buy: {e}", emoji_key='error')
            return

        market_navigator = MarketNavigator(MARKET_PAGES_TO_MONITOR, self._log)
        rate_limiter = RateLimiter(REQUEST_LIMIT, TIME_WINDOW, self._log)
        bought_ids = set()
        error_400_count = 0

        self._log('debug', f"Старт мониторинга рынка. Фильтры: "
                           f"{filter_manager}", emoji_key='debug')

        while True:
            if filter_manager.is_current_filter_complete():
                 self._log('debug',
                           "Задача для текущего фильтра выполнена. "
                           "Переход к следующему.")
                 filter_manager.next_filter()
                 if filter_manager.all_filters_complete():
                     self._log('success',
                               'Все задачи по мониторингу выполнены.',
                               emoji_key='success')
                     raise InvalidSession('Все задачи по мониторингу выполнены.')
                 # Reset navigation/rate limiter for the new filter?
                 # Decide if RateLimiter/Navigator state should persist across filters
                 market_navigator = MarketNavigator(MARKET_PAGES_TO_MONITOR, self._log)
                 rate_limiter = RateLimiter(REQUEST_LIMIT, TIME_WINDOW, self._log)
                 await asyncio.sleep(uniform(2, 4)) # Small delay between filters
                 continue


            current_filter = filter_manager.current_filter
            current_page = market_navigator.current_page
            direction = market_navigator.direction

            self._log('debug',
                      f"Текущий фильтр: {current_filter}, страница: "
                      f"{current_page}, направление: {direction}")

            params = {
                'page': current_page,
                'page_size': page_size,
            }

            if 'equipment_type' in current_filter and \
                    current_filter['equipment_type'] != '*':
                params['market_type'] = current_filter['equipment_type']
            if 'rarity' in current_filter:
                params['rarity'] = current_filter['rarity']

            has_statistic = ('required_stats' in current_filter and
                             current_filter['required_stats'])
            if has_statistic:
                params['statistic'] = current_filter['required_stats'][0]['type']

            params['sort_by_price'] = 'asc'

            if has_statistic:
                params['sort_by_statistic'] = 'desc'

            self._log('debug', f"Параметры запроса: {params}")

            url = (f"https://liyue.tonkombat.com/api/v1/market/equipment?"
                   f"{urlencode(params)}")
            headers = {
                **self.headers,
                'Authorization': f'tma {self._init_data}'
            }

            try:
                await rate_limiter.wait_for_next_request()
                result = await self.make_request(method='get', url=url,
                                                 headers=headers, ssl=False,
                                                 timeout=aiohttp.ClientTimeout(total=20))

                if result is None:
                     self._log('warning', "make_request вернул None. Пропускаем "
                                          "обработку ответа и продолжаем цикл.",
                               emoji_key='warning')
                     await asyncio.sleep(uniform(*ERROR_SLEEP_SECONDS))
                     continue

                error_400_count = 0

                items = result.get('data', {}).get('items', [])
                self._log('debug', f"Найдено предметов: {len(items)} на странице "
                                   f"{current_page}")

                market_navigator.process_page_result(items)

                if items:
                    self._log('debug', f"Передаю {len(items)} предметов в "
                                       f"_analyze_items")
                    await self._analyze_items(items, current_filter,
                                               current_page, bought_ids,
                                               filter_manager)


                # Original page logic moved to MarketNavigator
                # Original delay logic left here for now as it depends on global setting
                delay_time = uniform(*settings.MARKET_MONITOR_DELAY_SECONDS)
                self._log('debug',
                          f"Задержка перед следующим запросом к рынку согласно "
                          f"настройкам: {delay_time:.2f} с", emoji_key='sleep')
                await asyncio.sleep(delay_time)


            except aiohttp.ClientError as e:
                if isinstance(e, aiohttp.ClientResponseError) and e.status == 400:
                    self._log('warning',
                              f"Получена ошибка 400 (Bad Request) при "
                              f"получении рынка: {e}", emoji_key='warning')
                    error_400_count += 1
                    if error_400_count >= ERROR_400_THRESHOLD:
                        self._log('error',
                                  f"Получено {error_400_count} последовательных "
                                  f"ошибок 400. Завершаю сессию для перезапуска.",
                                  emoji_key='error')
                        raise InvalidSession(
                            f"Получено {error_400_count} последовательных "
                            f"ошибок 400 для сессии {self.session_name}. "
                            f"Требуется перезапуск.")
                    else:
                        self._log('debug', f"Ошибка 400: {e}. Количество "
                                           f"последовательных ошибок 400: "
                                           f"{error_400_count}. Короткий сон.",
                                           emoji_key='sleep')
                        await asyncio.sleep(uniform(10, 20))
                else:
                    self._log('error',
                              f"Ошибка при получении рынка (после make_request "
                              f"retries?): {e}", emoji_key='error')
                    self._log('debug', traceback.format_exc())
                    await asyncio.sleep(uniform(15, 30))
                continue

            except InvalidSession:
                 raise # Re-raise InvalidSession to be caught in run()

            except Exception as e:
                 self._log('error',
                           f"Неизвестная ошибка в debug_monitor_market: {e}",
                           emoji_key='error')
                 self._log('debug', traceback.format_exc())
                 await asyncio.sleep(uniform(60, 120))
                 continue

            # This check is now handled by FilterManager and InvalidSession exception
            # if all(f['bought'] >= f['quantity'] for f in filters):
            #     self._log('success', 'Все задачи по мониторингу выполнены.', emoji_key='success')
            #     raise InvalidSession('Все задачи по мониторингу выполнены.')


    async def _analyze_items(self, items, filter_obj, current_page, \
                              bought_ids, filter_manager: FilterManager):

        self._log('debug', f"Анализ предметов на странице {current_page}, "
                           f"фильтр: {filter_obj}")
        for item in items:
            evaluation_result = self._item_evaluator.evaluate(item, filter_obj, \
                                                              bought_ids)

            if evaluation_result:
                (item_name, market_equipment_id, price_tok, formatted_stats,
                 stats_str) = evaluation_result
                status = 'success'
                price_info = "✅ цена"
                message = (f"{item_name} [market_id:{market_equipment_id}] "
                           f"({stats_str}) | {price_info} {price_tok:.1f} TOK")
                self._log('info', message, status)

                if market_equipment_id and market_equipment_id not in bought_ids:
                    self._log('debug', f"Пробую купить: {item_name} "
                                       f"({market_equipment_id}) за "
                                       f"{price_tok:.1f} TOK")
                    ok = await self.buy_equipment(market_equipment_id)
                    if ok:
                        filter_manager.mark_bought(market_equipment_id, \
                                                   bought_ids)
                        self._log('success',
                                  f"Успешно куплено: {item_name} "
                                  f"({market_equipment_id}). Куплено "
                                  f"{filter_obj['bought']}/"
                                  f"{filter_obj['quantity']}.")
                        if filter_manager.is_current_filter_complete():
                            self._log('success',
                                      f"Задача для фильтра {filter_obj} "
                                      f"выполнена. Куплено "
                                      f"{filter_obj['bought']}/"
                                      f"{filter_obj['quantity']}.",
                                      emoji_key='success')
                            # Raise here to break the inner loop and check
                            # if all filters are done in the outer loop
                            raise InvalidSession(
                                f'Задача для фильтра {filter_obj} выполнена.')

                    else:
                        self._log('error',
                                  f"Покупка не удалась: {item_name} "
                                  f"({market_equipment_id})")
        # No explicit return is needed here


    async def process_bot_logic(self) -> None:
        if not hasattr(self, 'access_token_created_time'):
            self.access_token_created_time = 0
        if not getattr(self, '_init_data', None) or (time() - self.access_token_created_time) >= self._token_live_time:
             self._log('info', "Получение или обновление TG Web Data...", emoji_key='info')
             await self.get_tg_web_data()
             self.access_token_created_time = time()
             expiration_time = datetime.fromtimestamp(self.access_token_created_time + self._token_live_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
             self._log('info', f"TG Web Data обновлены. Токен действует примерно до {expiration_time}", emoji_key='success')

        await self.users_balance()

        try:
            await self.debug_monitor_market()
        except InvalidSession as e:
            self._log('info', f"Завершение работы по причине: {e}", emoji_key='info')
            raise e
        except Exception as error:
            sleep_duration = uniform(*ERROR_SLEEP_SECONDS)
            self._log('error', f"Неизвестная ошибка в process_bot_logic: {error}. Сон на {int(sleep_duration)}s.")
            self._log('debug', traceback.format_exc())


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = MarketMonitorBot(tg_client=tg_client)
    return await bot.run()