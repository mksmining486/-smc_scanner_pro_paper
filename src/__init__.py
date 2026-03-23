"""
SMC Trading Engine - Core Package.

Этот пакет предоставляет полный набор инструментов для анализа рыночной структуры
на основе концепций Smart Money Concepts (SMC).

Основные компоненты:
- Models: Структуры данных (Candle, Pivot, OrderBlock, FVG, Liquidity).
- Engines: Движки анализа (PivotDetector, MarketStructure, Liquidity, etc.).
- Services: Оркестраторы бизнес-логики (SMCTradingService).
- Utils: Утилиты загрузки данных и хелперы.

Пример использования:
    from src import SMCTradingService, Settings, Candle

    settings = Settings()
    service = SMCTradingService(settings)

    # Обработка свечи
    result = service.process_candle(candle)
    print(f"Current Trend: {result.trend}")
"""

# =============================================================================
# Configuration Imports
# =============================================================================
from src.config.settings import Settings, AppEnvironment, LogLevel, DataProvider

# =============================================================================
# Model Imports
# =============================================================================
from src.models.candle import Candle
from src.models.pivot import PivotPoint, PivotType
from src.models.market_structure import (
    MarketStructureState,
    StructureEvent,
    MarketTrend,
    StructureEventType
)
from src.models.liquidity import LiquidityPool, LiquidityType, LiquidityStatus
from src.models.order_block import OrderBlock, OrderBlockType, OrderBlockStatus
from src.models.fvg import FairValueGap, FVGType, FVGStatus
from src.models.premium_discount import PremiumDiscountZone, PDZoneType, FibonacciLevel

# =============================================================================
# Service Imports
# =============================================================================
from src.services.trading_service import SMCTradingService, AnalysisResult

# =============================================================================
# Engine Imports
# =============================================================================
from src.engines.pivot_detector import PivotDetector
from src.engines.market_structure_engine import MarketStructureEngine
from src.engines.liquidity_engine import LiquidityEngine
from src.engines.order_block_engine import OrderBlockEngine
from src.engines.fvg_engine import FVGEngine
from src.engines.premium_discount_engine import PremiumDiscountEngine

# =============================================================================
# Utils Imports
# =============================================================================
from src.utils.data_loader import (
    load_historical_data,
    stream_candles,
    DataLoaderError,
    DataNotFoundError,
    InvalidDataFormatError,
    UnsupportedSourceError
)
from src.utils.helpers import (
    to_decimal,
    calculate_percentage_change,
    is_within_tolerance,
    round_price,
    normalize_timezone,
    safe_divide,
    find_max_drawdown,
    format_duration,
    batch_list,
    deep_merge_dicts,
    clamp
)

# =============================================================================
# Package Metadata
# =============================================================================
__version__ = "1.0.0"
__author__ = "Senior Python Engineer"
__email__ = "engineer@example.com"
__description__ = "Production-ready Smart Money Concepts (SMC) trading analysis engine"

# =============================================================================
# Public API Definition
# =============================================================================
__all__ = [
    # --- Configuration ---
    "Settings",
    "AppEnvironment",
    "LogLevel",
    "DataProvider",

    # --- Core Models ---
    "Candle",
    "PivotPoint",
    "PivotType",
    "MarketStructureState",
    "StructureEvent",
    "MarketTrend",
    "StructureEventType",

    # --- SMC Concepts Models ---
    "LiquidityPool",
    "LiquidityType",
    "LiquidityStatus",
    "OrderBlock",
    "OrderBlockType",
    "OrderBlockStatus",
    "FairValueGap",
    "FVGType",
    "FVGStatus",
    "PremiumDiscountZone",
    "PDZoneType",
    "FibonacciLevel",

    # --- Services & Facades ---
    "SMCTradingService",
    "AnalysisResult",

    # --- Engines ---
    "PivotDetector",
    "MarketStructureEngine",
    "LiquidityEngine",
    "OrderBlockEngine",
    "FVGEngine",
    "PremiumDiscountEngine",

    # --- Utils ---
    "load_historical_data",
    "stream_candles",
    "DataLoaderError",
    "DataNotFoundError",
    "InvalidDataFormatError",
    "UnsupportedSourceError",
    "to_decimal",
    "calculate_percentage_change",
    "is_within_tolerance",
    "round_price",
    "normalize_timezone",
    "safe_divide",
    "find_max_drawdown",
    "format_duration",
    "batch_list",
    "deep_merge_dicts",
    "clamp",

    # --- Metadata ---
    "__version__",
    "__author__",
]