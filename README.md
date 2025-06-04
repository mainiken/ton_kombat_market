# Ton Kombat Market Bot

[🇷🇺 Russian](README_RU.md) | [🇬🇧 English](README.md)

[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-market_ksivis.svg" alt="Market Link" width="200">](https://t.me/MaineMarketBot?start=8HVF7S9K)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-channel_psjoqn.svg" alt="Channel Link" width="200">](https://t.me/+vpXdTJ_S3mo0ZjIy)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-chat_ixoikd.svg" alt="Chat Link" width="200">](https://t.me/+wWQuct9bljQ0ZDA6)

---

## 📑 Table of Contents
1. [Description](#description)
2. [Key Features](#key-features)
3. [Installation](#installation)
   - [Quick Start](#quick-start)
   - [Manual Installation](#manual-installation)
4. [Settings](#settings)
5. [Support and Donations](#support-and-donations)
6. [Contact](#contact)

---

## 📜 Description
**Ton Kombat Market Bot** is an automated market bot for buying necessary items in the game [Ton Kombat Market Bot](https://t.me/Ton_kombat_bot/app?startapp=252453226_9cbd0abe-0540-4f94-98f5-5c4a7fc1283b). It supports multithreading, proxy integration, and automatic game management.

---

## 🌟 Key Features
- 🔄 **Multithreading** — ability to work with multiple accounts in parallel
- 🔐 **Proxy Support** — secure operation through proxy servers
- 🎯 **Quest Management** — automatic quest completion
- 📊 **Statistics** — detailed session statistics tracking

---

## 🛠️ Installation

### Quick Start
1. **Download the project:**
   ```bash
   git clone https://github.com/mainiken/ton_kombat_market.git
   cd ton_kombat_market
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure parameters in the `.env` file:**
   ```bash
   API_ID=your_api_id
   API_HASH=your_api_hash
   ```

### Manual Installation
1. **Linux:**
   ```bash
   sudo sh install.sh
   python3 -m venv venv
   source venv/bin/activate
   pip3 install -r requirements.txt
   cp .env-example .env
   nano .env  # Specify your API_ID and API_HASH
   python3 main.py
   ```

2. **Windows:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   copy .env-example .env
   python main.py
   ```

---

## ⚙️ Settings

| Parameter                  | Default Value         | Description                                                 |
|---------------------------|----------------------|-------------------------------------------------------------|
| **API_ID**                |                      | Telegram API application ID                                 |
| **API_HASH**              |                      | Telegram API application hash                               |
| **GLOBAL_CONFIG_PATH**    |                      | Path for configuration files. By default, uses the TG_FARM environment variable |
| **FIX_CERT**              | False                | Fix SSL certificate errors                                  |
| **SESSION_START_DELAY**   | 360                  | Delay before starting the session (seconds)                 |
| **REF_ID**                |                      | Referral ID for new accounts                                |
| **USE_PROXY**             | True                 | Use proxy                                                   |
| **SESSIONS_PER_PROXY**    | 1                    | Number of sessions per proxy                                |
| **DISABLE_PROXY_REPLACE** | False                | Disable proxy replacement on errors                         |
| **BLACKLISTED_SESSIONS**  | ""                   | Sessions that will not be used (comma-separated)            |
| **DEBUG_LOGGING**         | False                | Enable detailed logging                                     |
| **DEVICE_PARAMS**         | False                | Use custom device parameters                                |
| **AUTO_UPDATE**           | True                 | Automatic updates                                           |
| **CHECK_UPDATE_INTERVAL** | 300                  | Update check interval (seconds)                             |

---

## 🛍️ .buy File Format

The `.buy` file is a JSON array of objects, each describing a rule for buying items on the market. The bot will look for items matching each rule and buy them up to the specified quantity (`quantity`).

Example `.buy-example` file structure:

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

Description of fields:

- `equipment_type` (string): Item type (e.g., "sword", "shield", "wings", "necklace", "helmet", "armor", "boots", "animal"). Use "*" for any type.
- `max_price_tok` (number): Maximum price in TOK you are willing to pay for an item.
- `rarity` (string): Item rarity ("common", "uncommon", "rare", "epic", "legendary", "mythic").
- `required_stats` (array of objects): List of required stats and their minimum levels. If multiple stats are listed, the item must have *all* of them at the specified or higher level.
  - `type` (string): Stat type (e.g., "reflect-percent", "life-steal-percent", "attack-percent", "hp-flat-primary", etc.).
  - `min_level` (number): Minimum required stat level.
- `quantity` (number): The number of items with this set of stats and maximum price that the bot should buy.
- `bought` (number, optional, added by bot): The number of items already bought under this rule. Do not edit this field manually.

---

## 💰 Support and Donations

Support the development:

| Currency      | Address |
|---------------|---------|
| **Bitcoin**   | `bc1pfuhstqcwwzmx4y9jx227vxcamldyx233tuwjy639fyspdrug9jjqer6aqe` |
| **Ethereum**  | `0x9c7ee1199f3fe431e45d9b1ea26c136bd79d8b54` |
| **TON**       | `UQBpZGp55xrezubdsUwuhLFvyqy6gldeo-h22OkDk006e1CL` |
| **BNB**       | `0x9c7ee1199f3fe431e45d9b1ea26c136bd79d8b54` |
| **Solana**    | `HXjHPdJXyyddd7KAVrmDg4o8pRL8duVRMCJJF2xU8JbK` |

---

## 📞 Contact

If you have questions or suggestions:
- **Telegram**: [Join our channel](https://t.me/+vpXdTJ_S3mo0ZjIy)

---

## ⚠️ Disclaimer

This software is provided "as is" without any warranties. By using this bot, you accept full responsibility for its use and any consequences that may arise.

The author is not responsible for:
- Any direct or indirect damages related to the use of the bot
- Possible violations of third-party service terms of use
- Account blocking or access restrictions

Use the bot at your own risk and in compliance with applicable laws and third-party service terms of use.

