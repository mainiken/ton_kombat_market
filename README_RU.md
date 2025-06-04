# Ton Kombat Market Bot

[🇷🇺 Русский](README_RU.md) | [🇬🇧 English](README.md)

[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-market_ksivis.svg" alt="Market Link" width="200">](https://t.me/MaineMarketBot?start=8HVF7S9K)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-channel_psjoqn.svg" alt="Channel Link" width="200">](https://t.me/+vpXdTJ_S3mo0ZjIy)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-chat_ixoikd.svg" alt="Chat Link" width="200">](https://t.me/+wWQuct9bljQ0ZDA6)

---

## 📑 Оглавление
1. [Описание](#описание)
2. [Ключевые особенности](#ключевые-особенности)
3. [Установка](#установка)
   - [Быстрый старт](#быстрый-старт)
   - [Ручная установка](#ручная-установка)
4. [Настройки](#настройки)
5. [Поддержка и донаты](#поддержка-и-донаты)
6. [Контакты](#контакты)

---

## 📜 Описание
**Ton Kombat Market Bot** — это автоматизированный маркет бот для покупки необходимых предметов игры [Ton Kombat Market Bot](https://t.me/Ton_kombat_bot/app?startapp=252453226_9cbd0abe-0540-4f94-98f5-5c4a7fc1283b). Поддерживает многопоточность, интеграцию прокси и автоматическое управление игрой.

---

## 🌟 Ключевые особенности
- 🔄 **Многопоточность** — возможность работы с несколькими аккаунтами параллельно
- 🔐 **Поддержка прокси** — безопасная работа через прокси-серверы
- 🎯 **Управление квестами** — автоматическое выполнение квестов
- 📊 **Статистика** — подробный учет статистики сессий

---

## 🛠️ Установка

```bash
git clone https://github.com/mainiken/ton_kombat_market.git
cd mrkt
pip install -r requirements.txt
```

Создайте файл `.env`:

```bash
API_ID=ваш_api_id
API_HASH=ваш_api_hash
```

### Ручная установка

#### Linux

```bash
sudo sh install.sh
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
cp .env-example .env
nano .env  # Укажите свои API_ID и API_HASH
python3 main.py
```

#### Windows

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env-example .env
python main.py
```

## ⚙️ Настройки

| Параметр                  | Значение по умолчанию | Описание                                                 |
|---------------------------|----------------------|---------------------------------------------------------|
| **API_ID**                |                      | Идентификатор приложения Telegram API                   |
| **API_HASH**              |                      | Хэш приложения Telegram API                              |
| **GLOBAL_CONFIG_PATH**    |                      | Путь к файлам конфигурации. По умолчанию используется переменная окружения TG_FARM |
| **FIX_CERT**              | False                | Исправить ошибки сертификата SSL                        |
| **SESSION_START_DELAY**   | 360                  | Задержка перед началом сессии (в секундах)             |
| **REF_ID**                |                      | Идентификатор реферала для новых аккаунтов             |
| **USE_PROXY**             | True                 | Использовать прокси                                     |
| **SESSIONS_PER_PROXY**    | 1                    | Количество сессий на один прокси                        |
| **DISABLE_PROXY_REPLACE** | False                | Отключить замену прокси при ошибках                     |
| **BLACKLISTED_SESSIONS**  | ""                   | Сессии, которые не будут использоваться (через запятую)|
| **DEBUG_LOGGING**         | False                | Включить подробный логгинг                              |
| **DEVICE_PARAMS**         | False                | Использовать пользовательские параметры устройства        |
| **AUTO_UPDATE**           | True                 | Автоматические обновления                               |
| **CHECK_UPDATE_INTERVAL** | 300                  | Интервал проверки обновлений (в секундах)              |

---

## 🛍️ Формат файла .buy

Файл `.buy` представляет собой JSON массив объектов, каждый из которых описывает правило покупки предмета на рынке. Бот будет искать предметы, соответствующие каждому правилу, и покупать их до достижения указанного количества (`quantity`).

Пример структуры файла `.buy-example`:

```json
[
  {
    "equipment_type": "*",
    "max_price_tok": 6000,
    "rarity": "uncommon",
    "required_stats": [
      {"type": "reflect-percent", "min_level": 4},
      {"type": "reflect-percent", "min_level": 3}
    ],
    "quantity": 1
  }
]
```

Описание полей:

- `equipment_type` (строка): Тип предмета (например, "sword", "shield", "wings", "necklace", "helmet", "armor", "boots", "animal"). Используйте "*" для любого типа.
- `max_price_tok` (число): Максимальная цена в токенах (TOK), которую вы готовы заплатить за предмет.
- `rarity` (строка): Редкость предмета ("common", "uncommon", "rare", "epic", "legendary", "mythic").
- `required_stats` (массив объектов): Список обязательных характеристик и их минимальных уровней. Если указано несколько характеристик, предмет должен иметь *все* из них с указанным или более высоким уровнем.
  - `type` (строка): Тип характеристики (например, "reflect-percent", "life-steal-percent", "attack-percent", "hp-flat-primary" и т.д.).
  - `min_level` (число): Минимальный требуемый уровень характеристики.
- `quantity` (число): Количество предметов с данным набором характеристик и максимальной ценой, которое бот должен купить.
- `bought` (число, необязательно, добавляется ботом): Количество уже купленных предметов по этому правилу. Не редактируйте это поле вручную.

---

## 💰 Поддержка и донаты

Поддержите разработку:

| Валюта        | Адрес |
|---------------|-------|
| **Bitcoin**   | `bc1pfuhstqcwwzmx4y9jx227vxcamldyx233tuwjy639fyspdrug9jjqer6aqe` |
| **Ethereum**  | `0x9c7ee1199f3fe431e45d9b1ea26c136bd79d8b54` |
| **TON**       | `UQBpZGp55xrezubdsUwuhLFvyqy6gldeo-h22OkDk006e1CL` |
| **BNB**       | `0x9c7ee1199f3fe431e45d9b1ea26c136bd79d8b54` |
| **Solana**    | `HXjHPdJXyyddd7KAVrmDg4o8pRL8duVRMCJJF2xU8JbK` |

---

## 📞 Контакты

Если у вас возникли вопросы или предложения:
- **Telegram**: [Присоединяйтесь к нашему каналу](https://t.me/+vpXdTJ_S3mo0ZjIy)

---
## ⚠️ Дисклеймер

Данное программное обеспечение предоставляется "как есть", без каких-либо гарантий. Используя этот бот, вы принимаете на себя полную ответственность за его использование и любые последствия, которые могут возникнуть.

Автор не несет ответственности за:
- Любой прямой или косвенный ущерб, связанный с использованием бота
- Возможные нарушения условий использования сторонних сервисов
- Блокировку или ограничение доступа к аккаунтам

Используйте бота на свой страх и риск и в соответствии с применимым законодательством и условиями использования сторонних сервисов.
