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
        request_times = []
        base_delay = 2.0
        max_delay = 10.0
        backoff_factor = 1.5
        current_delay = base_delay
        try:
            with open('.buy', 'r', encoding='utf-8') as f:
                filters = json.load(f)
        except Exception as e:
            self._log('error', f"Ошибка чтения .buy: {e}")
            return

        for f in filters:
            if 'quantity' not in f:
                f['quantity'] = 1
            f['bought'] = 0

        bought_ids = set()
        current_page = 1
        direction = 1
        last_filter_index = 0
        consecutive_empty = 0
        error_400_count = 0
        token_refreshed = False
        sleep_done = False

        next_direction_change_time = time() + uniform(60, 1200)
        requests_in_current_direction = 0
        max_requests_in_random_direction = 0

        self._log('debug', f"Старт мониторинга рынка. Фильтры: {filters}")
        while True:
            f = filters[last_filter_index]
            self._log('debug', f"Текущий фильтр: {f}")
            if f['bought'] >= f['quantity']:
                last_filter_index = (last_filter_index + 1) % len(filters)
                continue
            self._log('debug', f"Переход к фильтру: {f}, страница: {current_page}, направление: {direction}")

            if random() < 0.2:
                last_filter_index = (last_filter_index + 1) % len(filters)
                current_page = 1
                direction = 1
                next_direction_change_time = time() + uniform(60, 1200)
                requests_in_current_direction = 0
                max_requests_in_random_direction = 0
                await asyncio.sleep(uniform(2, 4))
                continue

            params = {
                'page': current_page,
            }

            if 'equipment_type' in f and f['equipment_type'] != '*':
                params['market_type'] = f['equipment_type']
            if 'rarity' in f:
                params['rarity'] = f['rarity']

            has_statistic = 'required_stats' in f and f['required_stats']
            if has_statistic:
                params['statistic'] = f['required_stats'][0]['type']

            params['sort_by_price'] = 'asc'

            if has_statistic:
                params['sort_by_statistic'] = 'desc'

            self._log('debug', f"Параметры запроса: {params}")

            url = f"https://liyue.tonkombat.com/api/v1/market/equipment?{urlencode(params)}"
            headers = {
                **self.headers,
                'Authorization': f'tma {self._init_data}'
            }

            try:
                now = time()
                request_times = [t for t in request_times if now - t < TIME_WINDOW]

                if len(request_times) >= REQUEST_LIMIT:
                    sleep_time = TIME_WINDOW - (now - request_times[0]) + 0.5
                    await asyncio.sleep(sleep_time)
                    current_delay = min(current_delay * backoff_factor, max_delay)
                    continue

                if len(request_times) < REQUEST_LIMIT * 0.8:
                    current_delay = max(base_delay, current_delay / backoff_factor)

                actual_delay = current_delay * uniform(0.8, 1.2)
                await asyncio.sleep(actual_delay)

                request_times.append(time())
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url=url,
                        headers=headers,
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=20)
                    ) as response:
                        self._log('debug', f"GET {url} | status: {response.status}")
                        if response.status == 401:
                            self._log('error', f"Ошибка при получении рынка: {response.status}, message='{response.reason}', url={response.url}")
                            raise InvalidSession(f"Получена ошибка 401 для сессии {self.session_name}. Требуется переавторизация.")

                        error_400_count = 0
                        response.raise_for_status()
                        result = await response.json()
                        items = result.get('data', {}).get('items', [])
                        self._log('debug', f"Найдено предметов: {len(items)} на странице {current_page}")
                        total_pages = (result.get('data', {}).get('total', 0) + page_size - 1) // page_size

                        if not items:
                            self._log('debug', f"Пустая страница {current_page}")
                            consecutive_empty += 1
                            if consecutive_empty >= 3:
                                direction = -direction
                                consecutive_empty = 0
                                self._log('debug', f"Обнаружены 3 пустые страницы. Меняю направление на {direction}")
                                requests_in_current_direction = 0
                        else:
                            self._log('debug', f"Передаю {len(items)} предметов в _analyze_items")
                            consecutive_empty = 0
                            await self._analyze_items(items, f, current_page, bought_ids)

                        now = time()
                        if now >= next_direction_change_time and direction == 1:
                            direction = -1
                            self._log('debug', "Настало время сменить направление на обратное для имитации человека.")
                            max_requests_in_random_direction = randint(1, 3)
                            requests_in_current_direction = 0
                            next_direction_change_time = now + uniform(60, 1200)

                        if direction == -1 and requests_in_current_direction >= max_requests_in_random_direction:
                            direction = 1
                            self._log('debug', "Достигнут лимит случайных запросов назад. Возвращаюсь к основному направлению.")
                            requests_in_current_direction = 0

                        requests_in_current_direction += 1

                        if direction == 1 and current_page >= MARKET_PAGES_TO_MONITOR:
                            self._log('info', f"Достигнут лимит страниц {MARKET_PAGES_TO_MONITOR} при движении вперед. Начинаю с первой страницы.")
                            current_page = 1
                            next_direction_change_time = time() + uniform(60, 1200)
                            requests_in_current_direction = 0
                            max_requests_in_random_direction = 0
                            await asyncio.sleep(uniform(2, 4))
                        elif direction == -1 and current_page <= 1:
                             self._log('info', f"Достигнута первая страница при движении назад. Меняю направление на вперед.")
                             direction = 1
                             current_page = 1
                             next_direction_change_time = time() + uniform(60, 1200)
                             requests_in_current_direction = 0
                             max_requests_in_random_direction = 0
                             await asyncio.sleep(uniform(2, 4))

                        current_page += direction
                        if current_page < 1:
                            current_page = 1
                            direction = 1
                            self._log('debug', "Номер страницы стал меньше 1. Сброс на 1 и изменение направления на вперед.")
                            next_direction_change_time = time() + uniform(60, 1200)
                            requests_in_current_direction = 0
                            max_requests_in_random_direction = 0
                            await asyncio.sleep(uniform(2, 4))

                        delay_time = uniform(*settings.MARKET_MONITOR_DELAY_SECONDS)
                        self._log('debug', f"Задержка перед следующим запросом к рынку: {delay_time:.2f} с", emoji_key='sleep')
                        await asyncio.sleep(delay_time)

            except Exception as e:
                self._log('error', f"Ошибка при получении рынка: {e}")
                self._log('debug', traceback.format_exc())
                await asyncio.sleep(uniform(5, 10))
                continue

            if all(f['bought'] >= f['quantity'] for f in filters):
                self._log('success', 'Все задачи выполнены, мониторинг завершён.')
                return

    async def _analyze_items(self, items, filter_obj, page, bought_ids):
        def stat_color(level):
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

        self._log('debug', f"Анализ предметов на странице {page}, фильтр: {filter_obj}")
        for item in items:
            item_name = item.get('metadata', {}).get('equipment', {}).get('name', '???')
            stats = item.get('metadata', {}).get('equipment', {}).get('equipment_stats', [])
            self._log('debug', f"Проверяю предмет: {item_name}, статы: {stats}")
            stats = [stat for stat in stats if not stat.get('primary', False)]
            user_equipment_id = item.get('user_equipment_id')
            market_equipment_id = item.get('id')

            type_ok = filter_obj.get('equipment_type', '*') == '*' or item.get('equipment_type') == filter_obj.get('equipment_type')
            if not type_ok:
                self._log('debug', f"Пропуск по типу: {item.get('equipment_type')} != {filter_obj.get('equipment_type')}")
                continue

            price_tok = float(item.get('price_gross', 0)) / 1_000_000_000
            max_price = filter_obj.get('max_price_tok', 1e12)
            price_ok = price_tok <= max_price
            if not price_ok:
                self._log('debug', f"Пропуск по цене: {price_tok} > {max_price}")
                continue

            used_stats = set()
            matched_stats = []
            all_match = True
            for stat_filter in filter_obj['required_stats']:
                found = False
                for idx, stat in enumerate(stats):
                    if idx in used_stats:
                        continue
                    if stat.get('type') == stat_filter['type']:
                        stat_level = int(stat.get('level', 0))
                        min_level = int(stat_filter.get('min_level', 0))
                        if stat_level >= min_level:
                            matched_stats.append((stat_filter, stat, True))
                            used_stats.add(idx)
                            found = True
                            break
                if not found:
                    all_match = False
                    self._log('debug', f"Не найден required_stat: {stat_filter} в {item_name}")
                    break
            if not all_match or len(used_stats) != len(filter_obj['required_stats']):
                self._log('debug', f"Пропуск предмета {item_name} — не все required_stat найдены")
                continue

            status = 'success'
            formatted_stats = []
            for stat_filter, stat, is_ok in matched_stats:
                stat_name = stat_filter['type'].replace('-', ' ').capitalize()
                min_level = stat_filter.get('min_level', 0)
                stat_level = stat.get('level')
                stat_value = stat.get('value')
                color = stat_color(stat_level)
                if 'percent' in stat_filter['type']:
                    value_str = f"+{stat_value}%"
                else:
                    value_str = f"+{stat_value}"
                formatted_stats.append(f"{color} {stat_name} {value_str}")
            stats_str = ', '.join(formatted_stats)
            price_info = "✅ цена"
            message = f"{item_name} [{user_equipment_id}|{market_equipment_id}] ({stats_str}) | {price_info} {price_tok:.1f} TOK"
            self._log('info', message, status)
            if market_equipment_id and market_equipment_id not in bought_ids:
                self._log('debug', f"Пробую купить: {market_equipment_id}")
                ok = await self.buy_equipment(market_equipment_id)
                if ok:
                    filter_obj['bought'] += 1
                    bought_ids.add(market_equipment_id)
                    self._log('debug', f"Покупка успешна: {market_equipment_id}")
                    if filter_obj['bought'] >= filter_obj['quantity']:
                        self._log('success', 'Все задачи выполнены, мониторинг завершён.')
                        raise InvalidSession('Все задачи выполнены')
                else:
                    self._log('error', f"Покупка не удалась: {market_equipment_id}")
        return False

    async def process_bot_logic(self) -> None:
        if not hasattr(self, 'access_token_created_time'):
            self.access_token_created_time = 0
        if time() - self.access_token_created_time >= self._token_live_time or not getattr(self, '_init_data', None):
            await self.get_tg_web_data()
            self.access_token_created_time = time()
        await self.users_balance()
        await self.debug_monitor_market()
        await asyncio.sleep(uniform(2, 5))


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = MarketMonitorBot(tg_client=tg_client)
    return await bot.run()