import asyncio
import logging
import sys
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.config import BOT_TOKEN
from src.database.connection import init_db, async_session
from src.database.models import GiftTracking, User
from src.handlers import setup_routers
from src.handlers.admin import setup_initial_admins
from src.services.fragment_parser import fragment_parser
from src.services.tracking_service import tracking_service
from sqlalchemy import select, and_


class Colors:
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class ColoredFormatter(logging.Formatter):
    FORMATS = {
        logging.DEBUG:
        f"{Colors.CYAN}%(asctime)s{Colors.RESET} │ {Colors.BLUE}%(levelname)-8s{Colors.RESET} │ %(message)s",
        logging.INFO:
        f"{Colors.CYAN}%(asctime)s{Colors.RESET} │ {Colors.GREEN}%(levelname)-8s{Colors.RESET} │ %(message)s",
        logging.WARNING:
        f"{Colors.CYAN}%(asctime)s{Colors.RESET} │ {Colors.YELLOW}%(levelname)-8s{Colors.RESET} │ %(message)s",
        logging.ERROR:
        f"{Colors.CYAN}%(asctime)s{Colors.RESET} │ {Colors.RED}%(levelname)-8s{Colors.RESET} │ %(message)s",
        logging.CRITICAL:
        f"{Colors.CYAN}%(asctime)s{Colors.RESET} │ {Colors.RED}{Colors.BOLD}%(levelname)-8s{Colors.RESET} │ %(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColoredFormatter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = []
    root.addHandler(handler)

    logging.getLogger('aiogram').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

bot_instance: Bot = None


def print_banner():
    banner = f"""
{Colors.PURPLE}{Colors.BOLD}
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║       🎁  NFT GIFT PARSER BOT  🎁                         ║
    ║                                                           ║
    ║       Telegram NFT Gift Search & Tracking                 ║
    ║       dev lolz.live/naebcom/                              ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
{Colors.RESET}"""
    print(banner)


async def tracking_monitor():
    global bot_instance

    while True:
        try:
            await asyncio.sleep(60)

            if not bot_instance:
                continue

            async with async_session() as session:
                now = datetime.utcnow()
                result = await session.execute(
                    select(GiftTracking).where(
                        and_(GiftTracking.is_active == True,
                             GiftTracking.next_check <= now)).limit(20))
                trackings = result.scalars().all()

                if trackings:
                    logger.info(
                        f"{Colors.YELLOW}🔍 Checking {len(trackings)} tracked gifts...{Colors.RESET}"
                    )

                for tracking in trackings:
                    try:
                        changes = await tracking_service.check_gift_status(
                            tracking)

                        if changes:
                            user_result = await session.execute(
                                select(User).where(User.id == tracking.user_id)
                            )
                            user = user_result.scalar_one_or_none()

                            if user:
                                text = f"👁 <b>Изменение подарка {tracking.slug}-{tracking.number}</b>\n\n"

                                if changes.get("owner_changed"):
                                    old = changes["owner_changed"][
                                        "old"] or "Неизвестен"
                                    new = changes["owner_changed"][
                                        "new"] or "Неизвестен"
                                    text += f"👤 Владелец: @{old} → @{new}\n"
                                    logger.info(
                                        f"{Colors.CYAN}👤 Owner changed: {tracking.slug}-{tracking.number}{Colors.RESET}"
                                    )

                                if changes.get("status_changed"):
                                    old = changes["status_changed"][
                                        "old"] or "Неизвестен"
                                    new = changes["status_changed"][
                                        "new"] or "Неизвестен"
                                    text += f"📊 Статус: {old} → {new}\n"
                                    logger.info(
                                        f"{Colors.BLUE}📊 Status changed: {tracking.slug}-{tracking.number}{Colors.RESET}"
                                    )

                                if changes.get("price_changed"):
                                    old = changes["price_changed"][
                                        "old"] or "N/A"
                                    new = changes["price_changed"][
                                        "new"] or "N/A"
                                    text += f"💰 Цена: {old} → {new} TON\n"
                                    logger.info(
                                        f"{Colors.GREEN}💰 Price changed: {tracking.slug}-{tracking.number}{Colors.RESET}"
                                    )

                                if changes.get("hidden"):
                                    text += f"🙈 Подарок скрыт/удалён\n"

                                if changes.get("unhidden"):
                                    text += f"👁 Подарок снова доступен\n"

                                text += f"\n🔗 https://fragment.com/gift/{tracking.slug}-{tracking.number}"

                                try:
                                    await bot_instance.send_message(
                                        user.telegram_id,
                                        text,
                                        parse_mode="HTML")
                                except Exception:
                                    pass

                        gift_data = await fragment_parser.get_gift_full_data(
                            tracking.slug, tracking.number)
                        await tracking_service.update_tracking_after_check(
                            session,
                            tracking,
                            gift_data,
                            is_hidden=changes.get("hidden", False)
                            if changes else False)

                    except Exception as e:
                        logger.error(
                            f"{Colors.RED}Error checking tracking {tracking.id}: {e}{Colors.RESET}"
                        )

                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(
                f"{Colors.RED}Tracking monitor error: {e}{Colors.RESET}")
            await asyncio.sleep(30)


async def on_startup(bot: Bot):
    global bot_instance
    bot_instance = bot

    print_banner()

    logger.info(f"{Colors.YELLOW}📦 Initializing database...{Colors.RESET}")
    await init_db()
    logger.info(f"{Colors.GREEN}✓ Database ready{Colors.RESET}")

    logger.info(f"{Colors.YELLOW}👤 Setting up admins...{Colors.RESET}")
    await setup_initial_admins()
    logger.info(f"{Colors.GREEN}✓ Admins configured{Colors.RESET}")

    collections = fragment_parser.get_all_collections()
    logger.info(
        f"{Colors.GREEN}✓ Loaded {Colors.BOLD}{len(collections)}{Colors.RESET}{Colors.GREEN} NFT collections{Colors.RESET}"
    )

    asyncio.create_task(tracking_monitor())
    logger.info(f"{Colors.GREEN}✓ Tracking monitor started{Colors.RESET}")

    logger.info(f"{Colors.GREEN}{Colors.BOLD}🚀 Bot is running!{Colors.RESET}")
    print(f"\n{Colors.CYAN}{'─' * 60}{Colors.RESET}\n")


async def main():
    if not BOT_TOKEN:
        print(
            f"\n{Colors.RED}{Colors.BOLD}╔═══════════════════════════════════════════════════╗"
        )
        print(f"║  ❌ ERROR: BOT_TOKEN is not configured!            ║")
        print(f"║                                                    ║")
        print(f"║  Please set the BOT_TOKEN environment variable.   ║")
        print(
            f"╚═══════════════════════════════════════════════════╝{Colors.RESET}\n"
        )
        await asyncio.sleep(5)
        return

    bot = Bot(token=BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher()

    dp.include_router(setup_routers())

    dp.startup.register(on_startup)

    try:
        await dp.start_polling(bot,
                               allowed_updates=dp.resolve_used_update_types())
    finally:
        await fragment_parser.close()
        await bot.session.close()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}👋 Bot stopped by user{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}❌ Bot stopped with error: {e}{Colors.RESET}")
