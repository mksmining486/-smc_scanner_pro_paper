"""
SMC Trading Engine - Main Entry Point.

Этот модуль является точкой входа для запуска приложения.
Поддерживает запуск в режиме бэктестинга, live-трейдинга или демонстрации.

Пример использования:
    python -m src.main --mode demo --symbol BTCUSDT --timeframe 1h
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from src.config.settings import Settings, load_settings, AppEnvironment, LogLevel, DataProvider
from src.models.candle import Candle
from src.services.trading_service import SMCTradingService, AnalysisResult
from src.utils.data_loader import load_historical_data, MockDataLoader
from src.utils.helpers import format_duration

# Настройка логгера
logger = logging.getLogger(__name__)


def setup_logging(settings: Settings) -> None:
    """
    Настраивает систему логирования согласно конфигурации.

    Args:
        settings: Объект настроек приложения.
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Уровень логирования
    log_level = getattr(logging, settings.log_level.value)

    # Конфигурация root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Устанавливаем уровень для сторонних библиотек
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    logger.info(f"Logging configured: level={settings.log_level.value}")


def run_demo_mode(settings: Settings, candle_count: int = 100) -> None:
    """
    Запускает демонстрационный режим с генерацией случайных данных.

    Args:
        settings: Конфигурация приложения.
        candle_count: Количество свечей для обработки.
    """
    logger.info(f"Starting DEMO mode for {settings.symbol} on {settings.timeframe}")
    logger.info(f"Generating {candle_count} mock candles...")

    # Инициализация сервиса
    service = SMCTradingService(settings)

    # Генерация mock данных
    mock_loader = MockDataLoader(
        symbol=settings.symbol,
        start_price=50000.0,
        volatility=Decimal(str(settings.fvg_min_size_pct * 10))
    )
    candles = mock_loader.load_historical_data(limit=candle_count)

    logger.info(f"Processing {len(candles)} candles...")

    # Обработка свечей
    results = []
    structure_changes = 0
    signals_generated = 0

    for i, candle in enumerate(candles):
        result = service.process_candle(candle)
        results.append(result)

        # Подсчет значимых событий
        if result.new_structure_events:
            structure_changes += len(result.new_structure_events)
        if result.signal_type:
            signals_generated += 1

        # Логирование прогресса каждые 10 свечей
        if (i + 1) % 10 == 0:
            logger.debug(f"Processed {i + 1}/{len(candles)} candles. "
                        f"Trend: {result.current_trend.value}, "
                        f"Events: {structure_changes}, Signals: {signals_generated}")

    # Финальная статистика
    last_result = results[-1] if results else None
    logger.info("=" * 60)
    logger.info("DEMO MODE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total candles processed: {len(candles)}")
    logger.info(f"Structure changes detected: {structure_changes}")
    logger.info(f"Signals generated: {signals_generated}")

    if last_result:
        logger.info(f"Final trend: {last_result.current_trend.value}")
        logger.info(f"Active Order Blocks: {len(last_result.order_blocks)}")
        logger.info(f"Active FVGs: {len(last_result.fvgs)}")
        logger.info(f"Active Liquidity Pools: {len(last_result.liquidity_pools)}")

        if last_result.signal_type:
            logger.info(f"Latest signal: {last_result.signal_type} (strength: {last_result.signal_strength:.2f})")

    logger.info("=" * 60)


def run_backtest_mode(settings: Settings, data_path: Optional[str] = None) -> None:
    """
    Запускает режим бэктестинга на исторических данных.

    Args:
        settings: Конфигурация приложения.
        data_path: Путь к CSV файлу с данными (опционально).
    """
    logger.info(f"Starting BACKTEST mode for {settings.symbol}")

    # Загрузка данных
    if data_path:
        logger.info(f"Loading data from CSV: {data_path}")
        candles = load_historical_data('csv', file_path=data_path, limit=settings.data_limit)
    else:
        logger.info("No data path provided, using mock data for backtest")
        candles = load_historical_data('mock', symbol=settings.symbol, limit=settings.data_limit)

    logger.info(f"Loaded {len(candles)} candles")

    if not candles:
        logger.error("No data to process. Exiting.")
        return

    # Инициализация сервиса
    service = SMCTradingService(settings)

    # Обработка
    results = []
    for candle in candles:
        result = service.process_candle(candle)
        results.append(result)

    # Анализ результатов
    profitable_signals = 0
    total_signals = 0

    for result in results:
        if result.signal_type:
            total_signals += 1
            # Здесь можно добавить логику проверки прибыльности сигнала
            # Для демо просто считаем количество

    logger.info(f"Backtest complete. Total signals: {total_signals}")


def run_live_mode(settings: Settings) -> None:
    """
    Запускает режим live-трейдинга (требуется API ключ).

    Args:
        settings: Конфигурация приложения.
    """
    if settings.data_provider == DataProvider.MOCK:
        logger.warning("Live mode with MOCK provider. Use real provider for production.")

    logger.info(f"Starting LIVE mode for {settings.symbol}")
    logger.info("Press Ctrl+C to stop...")

    service = SMCTradingService(settings)

    try:
        # В реальном режиме мы используем потоковую передачу
        # Для демо эмулируем бесконечный цикл
        import time
        from decimal import Decimal
        import random

        last_price = Decimal("50000")

        while True:
            # Эмуляция получения новой свечи
            price_change = (Decimal(str(random.random())) - Decimal("0.5")) * Decimal("0.02")
            new_price = last_price * (Decimal("1") + price_change)

            candle = Candle(
                timestamp=datetime.now(timezone.utc),
                open=last_price,
                high=max(last_price, new_price) * Decimal("1.001"),
                low=min(last_price, new_price) * Decimal("0.999"),
                close=new_price,
                volume=Decimal(str(random.random() * 100))
            )

            result = service.process_candle(candle)

            if result.signal_type:
                logger.info(f"SIGNAL: {result.signal_type} | Strength: {result.signal_strength:.2f} | "
                           f"Entry: {result.entry_price} | SL: {result.stop_loss} | TP: {result.take_profit}")

            last_price = new_price
            time.sleep(1)  # Пауза между обновлениями

    except KeyboardInterrupt:
        logger.info("Live mode stopped by user")
    except Exception as e:
        logger.error(f"Error in live mode: {e}", exc_info=True)


def main() -> int:
    """
    Главная точка входа приложения.

    Returns:
        Код завершения (0 - успех, 1 - ошибка).
    """
    parser = argparse.ArgumentParser(
        description="SMC Pro Trader - Smart Money Concepts Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main --mode demo
  python -m src.main --mode backtest --data-path data/btc.csv
  python -m src.main --mode live --symbol ETHUSDT --timeframe 15m
        """
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["demo", "backtest", "live"],
        default="demo",
        help="Режим запуска приложения"
    )

    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Торговая пара (по умолчанию: BTCUSDT)"
    )

    parser.add_argument(
        "--timeframe",
        type=str,
        default="1h",
        help="Таймфрейм (по умолчанию: 1h)"
    )

    parser.add_argument(
        "--data-path",
        type=str,
        default=None,
        help="Путь к CSV файлу с данными (для backtest)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Включить режим отладки"
    )

    parser.add_argument(
        "--candles",
        type=int,
        default=100,
        help="Количество свечей для обработки (для demo)"
    )

    args = parser.parse_args()

    try:
        # Загрузка настроек
        settings = load_settings()

        # Переопределение настроек из аргументов командной строки
        if args.symbol:
            settings.symbol = args.symbol
        if args.timeframe:
            settings.timeframe = args.timeframe
        if args.debug:
            settings.debug_mode = True
            settings.log_level = LogLevel.DEBUG

        # Настройка логирования
        setup_logging(settings)

        logger.info(f"SMC Pro Trader v{settings.app_version} starting...")
        logger.info(f"Mode: {args.mode.upper()}")
        logger.info(f"Symbol: {settings.symbol} | Timeframe: {settings.timeframe}")

        # Запуск соответствующего режима
        if args.mode == "demo":
            run_demo_mode(settings, candle_count=args.candles)
        elif args.mode == "backtest":
            run_backtest_mode(settings, data_path=args.data_path)
        elif args.mode == "live":
            run_live_mode(settings)

        logger.info("Application finished successfully")
        return 0

    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        return 0
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    from decimal import Decimal
    sys.exit(main())