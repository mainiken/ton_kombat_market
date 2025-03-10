import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urlencode, unquote
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint
from time import time
from datetime import datetime, timezone
import json
import os

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.utils import logger, config_utils, CONFIG_PATH
from bot.exceptions import InvalidSession


class BaseBot:
    
    def __init__(self, tg_client: UniversalTelegramClient):
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client'):
            self.tg_client.client.no_updates = True
            
        self.session_name = tg_client.session_name
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._access_token: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical(f"CHECK accounts_config.json as it might be corrupted")
            exit(-1)
            
        self.proxy = session_config.get('proxy')
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            random_number = randint(1, 100)
            self._current_ref_id = settings.REF_ID if random_number <= 70 else '252453226_9cbd0abe-0540-4f94-98f5-5c4a7fc1283b'
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
            return tg_web_data
            
        except Exception as e:
            logger.error(f"Error getting TG Web Data: {str(e)}")
            raise InvalidSession("Failed to get TG Web Data")

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
            self._http_client = CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn)
            logger.info(f"Switched to new proxy: {new_proxy}")

        return True

    async def initialize_session(self) -> bool:
        try:
            self._is_first_run = await check_is_first_run(self.session_name)
            if self._is_first_run:
                logger.info(f"First run detected for session {self.session_name}")
                await append_recurring_session(self.session_name)
            return True
        except Exception as e:
            logger.error(f"Session initialization error: {str(e)}")
            return False

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client:
            raise InvalidSession("HTTP client not initialized")

        try:
            async with getattr(self._http_client, method.lower())(url, **kwargs) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"Request failed with status {response.status}")
                return None
        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return None

    async def run(self) -> None:
        if not await self.initialize_session():
            return

        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(f"Bot will start in {int(random_delay)}s")
        await asyncio.sleep(random_delay)

        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        async with CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn) as http_client:
            self._http_client = http_client

            while True:
                try:
                    session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
                    if not await self.check_and_update_proxy(session_config):
                        logger.warning('Failed to find working proxy. Sleep 5 minutes.')
                        await asyncio.sleep(300)
                        continue

                    await self.process_bot_logic()
                    
                except InvalidSession as e:
                    raise
                except Exception as error:
                    sleep_duration = uniform(60, 120)
                    logger.error(f"Unknown error: {error}. Sleeping for {int(sleep_duration)}")
                    await asyncio.sleep(sleep_duration)

    async def process_bot_logic(self) -> None:
        token_live_time = randint(3500, 3600)
        
        if time() - self.access_token_created_time >= token_live_time or not self._init_data:
            self._init_data = await self.get_tg_web_data()
            self.access_token_created_time = time()
            
        if not self.is_onboarded:
            is_onboarded = await self.check_onboard_status(self._init_data)
            if not is_onboarded:
                onboarding_result = await self.perform_onboarding(self._init_data)
                if not onboarding_result:
                    await asyncio.sleep(30)
                    return
            else:
                self.is_onboarded = True
            
        user_info = await self.get_user_info(self._init_data)
        if not user_info:
            await asyncio.sleep(30)
            return
            
        await self.partners_claim_reward(self._init_data)
            
        daily_result = await self.daily(self._init_data)
        if daily_result:
            await self.users_claim(self._init_data)
            await self.users_stars_spend(self._init_data)
            
        await self.season_reward_info(self._init_data)
        await self.season_me(self._init_data)
        await self.season_start(self._init_data)
        
        await self.check_and_do_upgrades(self._init_data)
        
        await self.tasks_progresses(self._init_data)
        
        await self.check_and_join_tournament(self._init_data)
            
        balance = await self.users_balance(self._init_data)
        if balance and 'data' in balance:
            logger.info(self.log_message(
                f"Balance: {float(balance['data'] / 1000000000):.2f} TOK",
                'balance'
            ))
            
        energy_info = await self.get_energy_info(self._init_data)
        if energy_info:
            current_energy = energy_info['current_energy']
            max_energy = energy_info['max_energy']
            logger.info(self.log_message(
                f"{current_energy}/{max_energy}",
                'energy'
            ))
            
            if current_energy > 0 and self.auto_fight:
                await self.combats_me(self._init_data)
            elif current_energy == 0:
                next_refill = energy_info['next_refill']
                time_to_next = (next_refill - datetime.now(timezone.utc)).total_seconds()
                
                logger.info(self.log_message(
                    f"Next energy in: {int(time_to_next)}s",
                    'energy'
                ))
                
        if self.auto_hunting:
            await self.check_and_start_hunting(self._init_data)
            
        await asyncio.sleep(uniform(2, 5))


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = TonKombatBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(bot.log_message(f"Invalid session: {e}", 'error'))
        raise  

class TonKombatBot(BaseBot):
    EMOJI = {
        'info': '🔵',
        'success': '✅',
        'warning': '⚠️',
        'error': '❌',
        'debug': '🔍',
        'combat': '⚔️',
        'win': '🏆',
        'loss': '💀',
        'reward': '💰',
        'energy': '⚡',
        'balance': '💎',
        'stars': '⭐',
        'hunt': '🏹',
        'season': '🎯',
        'tournament': '🎪',
        'task': '📋',
        'upgrade': '⬆️',
        'pet': '🐾',
        'onboard': '🎮'
    }

    def __init__(self, tg_client: UniversalTelegramClient):
        super().__init__(tg_client)
        session_config = config_utils.get_session_config(
            self.session_name, 
            CONFIG_PATH
        )
        
        self.pet_active_skill = settings.PET_ACTIVE_SKILL
        self.auto_fight = settings.AUTO_FIGHT
        self.auto_hunting = True
        self.user_level = 1
        self.is_onboarded = False
        
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

    def log_message(self, message: str, emoji_key: str = 'info') -> str:
        emoji = self.EMOJI.get(emoji_key, '')
        return f"{self.session_name} | {emoji} {message}"
        
    async def get_user_info(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/users/me'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    if 'data' in result:
                        user = result['data']
                        self.user_level = user.get('level', 1)
                        
                        balance = await self.users_balance(query)
                        current_balance = (
                            float(balance['data'] / 1000000000) 
                            if balance and 'data' in balance 
                            else 0
                        )
                        
                        logger.info(self.log_message(
                            f"Account: {user.get('username', 'Unknown')} | "
                            f"LVL: {self.user_level} | "
                            f"TOK: {current_balance:.2f} | "
                            f"Stars: {float(user.get('stars', 0) / 1000000000):.2f}",
                            'info'
                        ))
                    return result
        except Exception as error:
            logger.error(self.log_message(
                f"Error getting user information: {error}",
                'error'
            ))
            return None

    async def users_balance(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/users/balance'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
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
                    return await response.json()
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting balance: {e}",
                'error'
            ))
            return None

    async def users_claim(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/users/claim'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        if error_data.get('message') == 'claim too early':
                            logger.debug(self.log_message(
                                "Mining reward not available yet"
                            ))
                            return True
                        logger.warning(self.log_message(
                            f"Error claiming mining reward: "
                            f"{error_data.get('message')}"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result and 'amount' in result['data']:
                        amount = float(result['data']['amount'] / 1000000000)
                        logger.success(self.log_message(
                            f"Received {amount} TOK for mining"
                        ))
                    return True
        except Exception as e:
            logger.error(self.log_message(
                f"Error claiming mining reward: {e}",
                'error'
            ))
            return False

    async def users_stars_spend(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/users/stars/spend'
        data = json.dumps({'type': 'upgrade-army-rank'})
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': str(len(data)),
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    data=data,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        if error_data.get('message') == 'not enough stars to upgrade':
                            logger.debug(self.log_message(
                                "Not enough stars to upgrade rank"
                            ))
                            return True
                        logger.warning(self.log_message(
                            f"Error spending stars: {error_data.get('message')}"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result.get('data'):
                        logger.success(self.log_message(
                            "Army rank successfully upgraded"
                        ))
                    return True
        except Exception as e:
            logger.error(self.log_message(
                f"Error spending stars: {e}",
                'error'
            ))
            return False

    async def daily(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/daily'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        if error_data.get('message') == 'already claimed for today':
                            logger.debug(self.log_message(
                                "Daily bonus already claimed"
                            ))
                            return True
                        logger.warning(self.log_message(
                            f"Error claiming daily bonus: "
                            f"{error_data.get('message')}"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result and 'amount' in result['data']:
                        amount = float(result['data']['amount'] / 1000000000)
                        logger.success(self.log_message(
                            f"Received {amount} TOK daily bonus"
                        ))
                    return True
        except Exception as e:
            logger.error(self.log_message(
                f"Error claiming daily bonus: {e}",
                'error'
            ))
            return False

    async def get_energy_info(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/combats/energy'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
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
                    
                    if 'data' in result:
                        energy_data = result['data']
                        current_energy = energy_data.get('current_energy', 0)
                        max_energy = energy_data.get('max_energy', 20)
                        
                        if 'next_refill' in energy_data:
                            next_refill = datetime.fromisoformat(
                                energy_data['next_refill'].replace('Z', '+00:00')
                            ).astimezone(timezone.utc)
                            
                            return {
                                'current_energy': current_energy,
                                'max_energy': max_energy,
                                'next_refill': next_refill
                            }
                    return None
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting energy information: {e}",
                'error'
            ))
            return None

    async def combats_me(self, query: str) -> None:
        url = 'https://liyue.tonkombat.com/api/v1/combats/me'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
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
                    
                    if 'data' in result and 'pet' in result['data']:
                        if result['data']['pet'].get('active_skill') != self.pet_active_skill:
                            await self.combats_pets_skill(query)
                    
                    await self.combats_find(query)
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting combat information: {e}",
                'error'
            ))

    async def combats_pets_skill(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/combats/pets/skill'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        logger.debug(self.log_message(
                            "Pet skill not available"
                        ))
                        return None
                    elif response.status == 404:
                        logger.warning(self.log_message("Pet not found"))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    logger.success(self.log_message(
                        "Pet skill successfully used"
                    ))
                    return result
        except Exception as e:
            logger.error(self.log_message(
                f"Error using pet skill: {e}",
                'error'
            ))
            return None

    async def combats_find(self, query: str) -> None:
        url = 'https://liyue.tonkombat.com/api/v1/combats/find'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error = await response.json()
                        if error['message'] == 'out of energies':
                            logger.warning(self.log_message("No energy for battle"))
                            return
                            
                    response.raise_for_status()
                    await asyncio.sleep(randint(3, 5))
                    await self.combats_fight(query)
        except Exception as e:
            logger.error(self.log_message(
                f"Error finding opponent: {e}",
                'error'
            ))

    async def combats_fight(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/combats/fight'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error = await response.json()
                        if error['message'] == 'match not found':
                            logger.warning(self.log_message(
                                "Opponent not found",
                                'combat'
                            ))
                            return None
                        elif error['message'] == 'out of energies':
                            logger.warning(self.log_message(
                                "No energy for battle",
                                'energy'
                            ))
                            return None
                            
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        fight_data = result['data']
                        winner = fight_data.get('winner')
                        enemy = fight_data.get('enemy', {})
                        rank_gain = fight_data.get('rank_gain', 0)
                        
                        if winner == 'attacker':
                            logger.success(self.log_message(
                                f"Victory over {enemy.get('username', 'Unknown')} (+{rank_gain})",
                                'win'
                            ))
                        else:
                            logger.warning(self.log_message(
                                f"Defeat by {enemy.get('username', 'Unknown')}",
                                'loss'
                            ))
                            
                        await asyncio.sleep(randint(2, 4))
                        await self.combats_find(query)
                    return result
        except Exception as e:
            logger.error(self.log_message(
                f"Error conducting battle: {e}",
                'error'
            ))
            return None

    async def season_start(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/season/start'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        logger.warning(self.log_message(
                            f"Error starting season: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Season start endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        reward = result['data']
                        logger.success(self.log_message(
                            f"Season successfully started! Reward: {reward}"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error starting new season: {e}",
                'error'
            ))
            return False

    async def season_me(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/season/me'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        logger.debug(self.log_message(
                            "Season information endpoint unavailable",
                            'season'
                        ))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        season_data = result['data']
                        logger.info(self.log_message(
                            f"Season {season_data.get('current_season', 'Unknown')} | "
                            f"Rank {season_data.get('rank_latest', 0)} | "
                            f"Reward {season_data.get('reward', 0)}",
                            'season'
                        ))
                    return result
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting season information: {e}",
                'error'
            ))
            return None

    async def season_reward_info(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/season/reward'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        logger.debug(self.log_message(
                            "Season reward information endpoint unavailable"
                        ))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result and result['data']:
                        reward_data = result['data']
                        reward_tok = float(reward_data.get('reward_tok', 0)) / 1000000000
                        reward_star = float(reward_data.get('reward_star', 0)) / 1000000000
                        rank = reward_data.get('rank_latest', 0)
                        
                        logger.info(self.log_message(
                            f"Available season reward: {reward_tok:.2f} TOK, "
                            f"{reward_star:.2f} stars, rank: {rank}"
                        ))
                        
                        await self.season_reward_claim(query)
                    return result
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting season reward information: {e}",
                'error'
            ))
            return None

    async def season_reward_claim(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/season/reward'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        logger.warning(self.log_message(
                            f"Error claiming season reward: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Season reward claiming endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result and result['data']:
                        reward = float(result['data'].get('reward', 0) / 1000000000)
                        season = result['data'].get('current_season', 'Unknown')
                        logger.success(self.log_message(
                            f"Claimed season reward {season}: {reward:.2f} TOK"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error claiming season reward: {e}",
                'error'
            ))
            return False

    async def hunting_status(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/hunting/me/hunting'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        logger.debug(self.log_message(
                            "Hunting status endpoint unavailable",
                            'hunt'
                        ))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        hunting_data = result['data']
                        if hunting_data is None:
                            logger.info(self.log_message(
                                "No active hunting",
                                'hunt'
                            ))
                            return None
                        
                        pool_slug = hunting_data.get('pool_slug', 'unknown')
                        status = hunting_data.get('status', 'unknown')
                        
                        if 'end_time' in hunting_data:
                            end_time = datetime.fromisoformat(
                                hunting_data['end_time'].replace('Z', '+00:00')
                            ).astimezone(timezone.utc)
                            now = datetime.now(timezone.utc)
                            
                            if end_time > now:
                                time_left = end_time - now
                                hours, remainder = divmod(time_left.seconds, 3600)
                                minutes, seconds = divmod(remainder, 60)
                                logger.info(self.log_message(
                                    f"Hunting: {pool_slug} | {hours}h {minutes}m {seconds}s",
                                    'hunt'
                                ))
                                return {**hunting_data, 'time_left': time_left.total_seconds()}
                            else:
                                logger.info(self.log_message(
                                    f"Hunting finished: {pool_slug}",
                                    'hunt'
                                ))
                                await self.hunting_claim(query, pool_slug)
                                return None
                        else:
                            logger.info(self.log_message(
                                f"Hunting: {pool_slug} | {status}",
                                'hunt'
                            ))
                            return hunting_data
                    return None
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting hunting status: {e}",
                'error'
            ))
            return None

    async def hunting_start(self, query: str, pool_slug: str = "cursed-fortress") -> bool:
        url = f'https://liyue.tonkombat.com/api/v1/hunting/start/{pool_slug}'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        logger.warning(self.log_message(
                            f"Error starting hunting: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Hunting start endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result and result['data']:
                        logger.success(self.log_message(
                            f"Hunting in location {pool_slug} successfully started!"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error starting hunting: {e}",
                'error'
            ))
            return False

    async def hunting_claim(self, query: str, pool_slug: str = "cursed-fortress") -> bool:
        url = f'https://liyue.tonkombat.com/api/v1/hunting/claim/{pool_slug}'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        logger.warning(self.log_message(
                            f"Error claiming hunting reward: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Hunting reward claiming endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        reward_data = result['data']
                        stars = float(reward_data.get('stars', 0) / 1000000000)
                        tok = float(reward_data.get('reward_tok', 0) / 1000000000)
                        demons_killed = reward_data.get('total_demon_killed', 0)
                        
                        logger.success(self.log_message(
                            f"Claimed hunting reward: {tok:.2f} TOK, "
                            f"{stars:.2f} stars, demons killed: {demons_killed}"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error claiming hunting reward: {e}",
                'error'
            ))
            return False

    async def check_and_start_hunting(self, query: str) -> None:
        hunting_data = await self.hunting_status(query)
        
        if hunting_data is None:
            await self.hunting_start(query)
            hunting_data = await self.hunting_status(query)
            
        if hunting_data and 'time_left' in hunting_data:
            sleep_time = min(hunting_data['time_left'], 14400) 
            logger.info(self.log_message(
                f"Going to sleep for {int(sleep_time/3600)}h {int((sleep_time%3600)/60)}m",
                'hunt'
            ))
            await asyncio.sleep(sleep_time)
            await self.hunting_status(query)
            
        await asyncio.sleep(uniform(2, 5))

    async def tasks_progresses(self, query: str) -> None:
        url = 'https://liyue.tonkombat.com/api/v1/tasks/progresses'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
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
                    tasks = await response.json()
                    
                    for task in tasks.get('data', []):
                        if (task['task_user'] is None or 
                            (task['task_user'].get('reward_amount', 0) == 0 and 
                             task['task_user'].get('repeats', 0) == 0)):
                            
                            await self.tasks_execute(query, task['id'], task['name'])
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting task progresses: {e}",
                'error'
            ))

    async def tasks_execute(self, query: str, task_id: str, task_name: str) -> bool:
        url = f'https://liyue.tonkombat.com/api/v1/tasks/{task_id}'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    logger.success(self.log_message(
                        f"Completed: {task_name}",
                        'task'
                    ))
                    return True
        except Exception as e:
            logger.error(self.log_message(
                f"Error executing task {task_name}: {e}",
                'error'
            ))
            return False

    async def tournament_daily_status(self, query: str) -> Optional[str]:
        url = 'https://liyue.tonkombat.com/api/v1/tournament/daily/me'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        logger.debug(self.log_message(
                            "Tournament status endpoint unavailable"
                        ))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        status = result['data'].get('status', 'unknown')
                        logger.info(self.log_message(
                            f"Tournament status: {status}"
                        ))
                        return status
                    return None
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting tournament status: {e}",
                'error'
            ))
            return None

    async def tournament_register(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/tournament/register'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        logger.warning(self.log_message(
                            f"Error registering for tournament: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Tournament registration endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        logger.success(self.log_message(
                            "Successfully registered for tournament!"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error registering for tournament: {e}",
                'error'
            ))
            return False

    async def tournament_reward(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/tournament/reward'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        reward_data = result['data']
                        status = reward_data.get('status', 'unknown')
                        
                        if status != 'non-claimable':
                            top = reward_data.get('top', -1)
                            toks = float(
                                reward_data.get('toks', 0) / 1000000000
                            ) if reward_data.get('toks') else 0
                            stars = float(
                                reward_data.get('stars', 0) / 1000000000
                            ) if reward_data.get('stars') else 0
                            
                            if toks > 0 or stars > 0:
                                logger.info(self.log_message(
                                    f"Available tournament reward: {toks:.2f} TOK, "
                                    f"{stars:.2f} stars"
                                ))
                                if top > 0:
                                    logger.info(self.log_message(
                                        f"Tournament rank: {top}"
                                    ))
                        
                        return reward_data
                    return None
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting tournament reward: {e}",
                'error'
            ))
            return None

    async def check_and_join_tournament(self, query: str) -> None:
        status = await self.tournament_daily_status(query)
        
        if status == "unregistered":
            logger.info(self.log_message(
                "Registering for tournament..."
            ))
            registration_result = await self.tournament_register(query)
            
            if registration_result:
                status = await self.tournament_daily_status(query)
                logger.success(self.log_message(
                    "Successfully registered for tournament!"
                ))
        
        elif status == "qualified":
            logger.info(self.log_message(
                "You are qualified to claim tournament reward!"
            ))
            await self.tournament_reward(query)

    async def partners_balance(self, query: str) -> Optional[Dict]:
        url = 'https://liyue.tonkombat.com/api/v1/partners/balance'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 404:
                        logger.debug(self.log_message(
                            "Partners reward balance endpoint unavailable"
                        ))
                        return None
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        balance_data = result['data']
                        return balance_data
                    return None
        except Exception as e:
            logger.error(self.log_message(
                f"Error getting partners reward balance: {e}",
                'error'
            ))
            return None

    async def partners_claim_reward(self, query: str) -> bool:
        balance_data = await self.partners_balance(query)
        if not balance_data or float(balance_data.get('reward_tok', 0)) <= 0:
            return False

        url = 'https://liyue.tonkombat.com/api/v1/partners/claim-reward'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': '0'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        if error_data.get('message') == 'user already claimed':
                            return False
                        logger.warning(self.log_message(
                            f"Error claiming partners reward: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                    elif response.status == 404:
                        logger.debug(self.log_message(
                            "Partners reward claiming endpoint unavailable"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and result.get('data') is True:
                        reward_amount = float(
                            balance_data.get('reward_tok', 0) / 1000000000
                        )
                        logger.success(self.log_message(
                            f"Claimed partners reward: {reward_amount:.2f} TOK"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error claiming partners reward: {e}",
                'error'
            ))
            return False

    async def upgrades(self, query: str, upgrade_type: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/upgrades'
        data = json.dumps({'type': upgrade_type})
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': str(len(data)),
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    data=data,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 400:
                        error_data = await response.json()
                        if error_data.get('message') == 'not enough tok balance':
                            logger.debug(self.log_message(
                                f"Not enough TOK to upgrade {upgrade_type}"
                            ))
                            return False
                        logger.warning(self.log_message(
                            f"Error upgrading {upgrade_type}: "
                            f"{error_data.get('message', 'Unknown error')}"
                        ))
                        return False
                        
                    response.raise_for_status()
                    result = await response.json()
                    
                    if result and 'data' in result:
                        logger.success(self.log_message(
                            f"Successfully upgraded {upgrade_type}"
                        ))
                        return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error upgrading {upgrade_type}: {e}",
                'error'
            ))
            return False

    async def check_and_do_upgrades(self, query: str) -> None:
        upgrade_types = ['mining-tok', 'pocket-size']
        
        balance = await self.users_balance(query)
        if not balance or 'data' not in balance:
            return
            
        current_balance = float(balance['data'] / 1000000000)
        if current_balance <= 1:
            return
            
        for upgrade_type in upgrade_types:
            max_attempts = randint(1, 3)
            
            for _ in range(max_attempts):
                balance = await self.users_balance(query)
                if not balance or 'data' not in balance:
                    break
                    
                current_balance = float(balance['data'] / 1000000000)
                if current_balance <= 1:
                    break
                    
                upgrade_result = await self.upgrades(query, upgrade_type)
                
                if not upgrade_result:
                    break
                    
                await asyncio.sleep(uniform(1, 3))

    async def check_onboard_status(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/users/me'
        headers = {
            **self.headers,
            'Authorization': f'tma {query}'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url=url, 
                    headers=headers, 
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    return response.status == 200
        except Exception:
            return False

    async def perform_onboarding(self, query: str) -> bool:
        url = 'https://liyue.tonkombat.com/api/v1/users/onboard'
        data = json.dumps({'house_id': 0})
        headers = {
            **self.headers,
            'Authorization': f'tma {query}',
            'Content-Length': str(len(data)),
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=url, 
                    headers=headers, 
                    data=data,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result and 'data' in result:
                            logger.success(self.log_message(
                                "Account successfully registered",
                                'onboard'
                            ))
                            self.is_onboarded = True
                            return True
                    return False
        except Exception as e:
            logger.error(self.log_message(
                f"Error during registration: {e}",
                'error'
            ))
            return False
