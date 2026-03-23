"""
SMC Trading Engine - Configuration Module.

Этот модуль управляет всеми настройками приложения через Pydantic Settings.
Поддерживает загрузку из .env файлов, переменных окружения и значений по умолчанию.
Гарантирует валидацию всех критических параметров перед запуском.
"""

import os
import sys
import logging
from typing import List, Optional, Dict, Any
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import (
    Field,
    field_validator,
    model_validator,
    ValidationError,
    ConfigDict,
    BaseSettings
)

# Настройка логгера для модуля конфигурации
logger = logging.getLogger(__name__)


class LogLevel(str, Enum):
    """Уровни логирования приложения."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AppEnvironment(str, Enum):
    """Окружение запуска приложения."""
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"
    STAGING = "staging"


class DataProvider(str, Enum):
    """Поддерживаемые провайдеры данных."""
    BINANCE = "binance"
    BYBIT = "bybit"
    MOCK = "mock"
    CCXT = "ccxt"
    CSV = "csv"


class Settings(BaseSettings):
    """
    Основная конфигурация приложения.
    
    Все поля могут быть переопределены через переменные окружения.
    Префикс переменных: SMC_ (например, SMC_APP_ENV).
    """

    # -------------------------------------------------------------------------
    # Общие настройки приложения
    # -------------------------------------------------------------------------
    app_name: str = Field(default="SMC Pro Trader", description="Имя приложения")
    app_version: str = Field(default="1.0.0", description="Версия приложения")
    app_env: AppEnvironment = Field(
        default=AppEnvironment.DEVELOPMENT, 
        description="Окружение запуска"
    )
    debug_mode: bool = Field(
        default=False, 
        description="Режим отладки (подробные логи, отключение кэша)"
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO, 
        description="Уровень логирования"
    )
    
    # -------------------------------------------------------------------------
    # Настройки данных
    # -------------------------------------------------------------------------
    data_provider: DataProvider = Field(
        default=DataProvider.MOCK, 
        description="Провайдер рыночных данных"
    )
    api_key: Optional[str] = Field(
        default=None, 
        description="API ключ провайдера данных"
    )
    api_secret: Optional[str] = Field(
        default=None, 
        description="Секретный ключ API"
    )
    base_url: Optional[str] = Field(
        default=None, 
        description="Базовый URL API провайдера"
    )
    
    symbol: str = Field(
        default="BTCUSDT", 
        description="Торговая пара (символ)"
    )
    timeframe: str = Field(
        default="1h", 
        description="Таймфрейм свечей (1m, 5m, 15m, 1h, 4h, 1d)"
    )
    data_limit: int = Field(
        default=1000, 
        description="Количество свечей для загрузки при инициализации"
    )
    
    # -------------------------------------------------------------------------
    # Параметры анализа SMC
    # -------------------------------------------------------------------------
    pivot_left_bars: int = Field(
        default=5, 
        description="Количество баров слева для определения пивота"
    )
    pivot_right_bars: int = Field(
        default=5, 
        description="Количество баров справа для определения пивота"
    )
    min_structure_strength: int = Field(
        default=3, 
        description="Минимальная сила структуры (кол-во подтверждений)"
    )
    
    # Настройки Order Blocks
    ob_min_volume_ratio: float = Field(
        default=1.5, 
        description="Минимальное отношение объема Ордерблока к среднему"
    )
    ob_max_mitigation_pct: float = Field(
        default=0.5, 
        description="Максимальный процент митигации для активного Ордерблока"
    )
    
    # Настройки FVG (Fair Value Gaps)
    fvg_min_size_pct: float = Field(
        default=0.0005, 
        description="Минимальный размер FVG в процентах от цены"
    )
    fvg_inversion_enabled: bool = Field(
        default=True, 
        description="Включить инверсию FVG"
    )
    
    # Настройки Ликвидности
    liquidity_sweep_threshold: float = Field(
        default=0.001, 
        description="Порог пробоя ликвидности (в процентах)"
    )
    liquidity_lookback_bars: int = Field(
        default=50, 
        description="Глубина поиска свингов для ликвидности"
    )
    
    # Настройки Фибоначчи
    fib_levels: str = Field(
        default="0.0,0.382,0.5,0.618,0.786,1.0", 
        description="Уровни Фибоначчи через запятую"
    )
    premium_discount_zone: str = Field(
        default="0.5", 
        description="Граница зоны премиум/дисконт (обычно 0.5)"
    )

    # -------------------------------------------------------------------------
    # Настройки уведомлений и внешних сервисов
    # -------------------------------------------------------------------------
    telegram_bot_token: Optional[str] = Field(
        default=None, 
        description="Токен бота Telegram"
    )
    telegram_chat_id: Optional[str] = Field(
        default=None, 
        description="ID чата Telegram для уведомлений"
    )
    webhook_url: Optional[str] = Field(
        default=None, 
        description="URL вебхука для отправки сигналов"
    )
    
    # База данных (опционально)
    db_url: Optional[str] = Field(
        default=None, 
        description="URL подключения к базе данных (SQLite/PostgreSQL)"
    )
    
    # Redis (опционально для кэша)
    redis_url: Optional[str] = Field(
        default=None, 
        description="URL подключения к Redis"
    )

    # -------------------------------------------------------------------------
    # Конфигурация Pydantic
    # -------------------------------------------------------------------------
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SMC_",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True
    )
    
       # -------------------------------------------------------------------------
    # Валидаторы полей
    # -------------------------------------------------------------------------
    @field_validator('timeframe')
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        """
        Валидирует формат таймфрейма.
        Поддерживаемые форматы: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w, 1M.
        """
        valid_timeframes = {
            '1m', '3m', '5m', '15m', '30m',
            '1h', '2h', '4h', '6h', '8h', '12h',
            '1d', '3d', '1w', '2w', '1M'
        }
        if v not in valid_timeframes:
            raise ValueError(
                f"Недопустимый таймфрейм '{v}'. "
                f"Разрешенные значения: {', '.join(sorted(valid_timeframes))}"
            )
        return v

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """
        Валидирует формат торгового символа.
        Ожидается формат BASEQUOTE (например, BTCUSDT, ETHUSDC).
        """
        v = v.upper().strip()
        if not v:
            raise ValueError("Символ не может быть пустым")
        
        # Простая проверка: символ должен содержать только буквы и цифры, длина >= 4
        if not v.isalnum():
            raise ValueError(f"Символ '{v}' содержит недопустимые символы. Только буквы и цифры.")
        
        if len(v) < 4:
            raise ValueError(f"Слишком короткий символ '{v}'. Минимальная длина 4 символа.")
            
        return v

    @field_validator('data_limit', 'pivot_left_bars', 'pivot_right_bars', 'min_structure_strength', 'liquidity_lookback_bars')
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        """Проверяет, что целочисленное значение строго больше нуля."""
        if v <= 0:
            raise ValueError(f"Значение должно быть положительным (>0), получено: {v}")
        return v

    @field_validator('ob_min_volume_ratio', 'ob_max_mitigation_pct', 'fvg_min_size_pct', 'liquidity_sweep_threshold')
    @classmethod
    def validate_positive_float(cls, v: float) -> float:
        """Проверяет, что float значение неотрицательное."""
        if v < 0:
            raise ValueError(f"Значение не может быть отрицательным, получено: {v}")
        return v

    @field_validator('fib_levels')
    @classmethod
    def validate_fib_levels(cls, v: str) -> str:
        """
        Валидирует строку уровней Фибоначчи.
        Формат: "0.0,0.382,0.5,0.618,1.0"
        Все числа должны быть в диапазоне [0, 1].
        """
        try:
            parts = [x.strip() for x in v.split(',')]
            levels = [float(x) for x in parts]
            
            if len(levels) < 2:
                raise ValueError("Требуется минимум 2 уровня Фибоначчи")
            
            for level in levels:
                if not (0.0 <= level <= 1.0):
                    raise ValueError(f"Уровень Фибоначчи {level} выходит за пределы диапазона [0, 1]")
            
            # Возвращаем отсортированную строку без дубликатов
            unique_levels = sorted(set(levels))
            return ','.join(str(l) for l in unique_levels)
            
        except ValueError as e:
            if "could not convert" in str(e).lower() or "invalid literal" in str(e).lower():
                raise ValueError(f"Неверный формат чисел в уровнях Фибоначчи: {v}")
            raise

    @field_validator('premium_discount_zone')
    @classmethod
    def validate_pd_zone(cls, v: str) -> str:
        """Валидирует границу зоны премиум/дисконт (должна быть в [0, 1])."""
        try:
            val = float(v)
            if not (0.0 <= val <= 1.0):
                raise ValueError("Граница зоны должна быть между 0 и 1")
            return str(val)
        except ValueError:
            raise ValueError(f"Неверное значение границы зоны: {v}")

    @field_validator('telegram_bot_token')
    @classmethod
    def validate_telegram_token(cls, v: Optional[str]) -> Optional[str]:
        """Базовая проверка формата токена Telegram (длина > 10)."""
        if v is None:
            return None
        if len(v) < 10:
            raise ValueError("Невалидный токен Telegram (слишком короткий)")
        return v

    # -------------------------------------------------------------------------
    # Кросс-валидация модели
    # -------------------------------------------------------------------------
    @model_validator(mode='after')
    def validate_production_constraints(self) -> 'Settings':
        """
        Проверка ограничений для продакшн-окружения.
        - Debug режим должен быть выключен.
        - Mock провайдер данных запрещен.
        - Логирование не должно быть DEBUG (для производительности).
        """
        if self.app_env == AppEnvironment.PRODUCTION:
            errors = []
            
            if self.debug_mode:
                errors.append("Debug режим должен быть отключен в PRODUCTION")
            
            if self.data_provider == DataProvider.MOCK:
                errors.append("Mock провайдер данных запрещен в PRODUCTION")
            
            if self.log_level == LogLevel.DEBUG:
                # Предупреждение или ошибка? Для строгости сделаем ошибкой
                errors.append("Уровень логирования DEBUG не рекомендуется для PRODUCTION")
            
            if errors:
                raise ValueError("; ".join(errors))
                
        return self

    @model_validator(mode='after')
    def validate_notification_config(self) -> 'Settings':
        """
        Проверяет согласованность настроек уведомлений.
        Если указан токен Telegram, должен быть указан и chat_id.
        """
        has_token = self.telegram_bot_token is not None
        has_chat_id = self.telegram_chat_id is not None
        
        if has_token and not has_chat_id:
            raise ValueError("Telegram bot token указан, но chat_id отсутствует")
        if has_chat_id and not has_token:
            raise ValueError("Telegram chat_id указан, но bot token отсутствует")
            
        return self

    # -------------------------------------------------------------------------
    # Вычисляемые свойства (Helpers)
    # -------------------------------------------------------------------------
    @property
    def is_development(self) -> bool:
        """True, если приложение запущено в режиме разработки."""
        return self.app_env == AppEnvironment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        """True, если приложение запущено в продакшене."""
        return self.app_env == AppEnvironment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        """True, если приложение запущено в режиме тестирования."""
        return self.app_env == AppEnvironment.TESTING

    @property
    def fib_levels_list(self) -> List[float]:
        """Возвращает уровни Фибоначчи в виде списка float."""
        return [float(x) for x in self.fib_levels.split(',')]

    @property
    def pd_zone_threshold(self) -> float:
        """Возвращает порог зоны Premium/Discount как float."""
        return float(self.premium_discount_zone)

    def get_database_url(self) -> Optional[str]:
        """Безопасное получение URL базы данных."""
        return self.db_url

    def get_redis_url(self) -> Optional[str]:
        """Безопасное получение URL Redis."""
        return self.redis_url

    def get_api_credentials(self) -> Dict[str, Optional[str]]:
        """Возвращает словарь с учетными данными API."""
        return {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "base_url": self.base_url
        }

    def get_notification_providers(self) -> List[str]:
        """Возвращает список активных провайдеров уведомлений."""
        providers = []
        if self.telegram_bot_token and self.telegram_chat_id:
            providers.append("telegram")
        if self.webhook_url:
            providers.append("webhook")
        return providers

    def to_safe_dict(self) -> Dict[str, Any]:
        """
        Возвращает словарь настроек, скрывая чувствительные данные.
        Секретные ключи заменяются на '***'.
        """
        data = self.model_dump()
        sensitive_fields = ['api_key', 'api_secret', 'telegram_bot_token', 'db_url', 'redis_url']
        
        for field in sensitive_fields:
            if field in data and data[field] is not None:
                data[field] = "***REDACTED***"
                
        return data


# =============================================================================
# Глобальный экземпляр настроек
# =============================================================================

def load_settings() -> Settings:
    """
    Загружает и валидирует настройки приложения.
    
    Порядок загрузки:
    1. Значения по умолчанию в классе.
    2. Переменные окружения (с префиксом SMC_).
    3. Файл .env (если существует).
    
    Raises:
        SystemExit: Если конфигурация не прошла валидацию.
    """
    try:
        logger.info("Инициализация конфигурации приложения...")
        settings_instance = Settings()
        
        # Логирование базовой информации (без секретов)
        logger.info(f"Приложение: {settings_instance.app_name} v{settings_instance.app_version}")
        logger.info(f"Окружение: {settings_instance.app_env.value}")
        logger.info(f"Провайдер данных: {settings_instance.data_provider.value}")
        logger.info(f"Символ: {settings_instance.symbol}, Таймфрейм: {settings_instance.timeframe}")
        
        if settings_instance.debug_mode:
            logger.warning("Режим отладки ВКЛЮЧЕН. Не используйте в продакшене!")
            
        logger.info("Конфигурация успешно загружена и валидирована.")
        return settings_instance
        
    except ValidationError as e:
        logger.critical(f"Ошибка валидации конфигурации:\n{e}")
        print("\n=== CRITICAL CONFIGURATION ERROR ===")
        print("Пожалуйста, проверьте переменные окружения или файл .env")
        print("------------------------------------")
        # Выводим детали ошибок в более читаемом виде
        for error in e.errors():
            loc = " -> ".join(str(x) for x in error['loc'])
            msg = error['msg']
            print(f"[{loc}] {msg}")
        print("====================================\n")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Непредвиденная ошибка при загрузке настроек: {e}")
        sys.exit(1)


# Создаем глобальный экземпляр при импорте модуля
try:
    settings: Settings = load_settings()
except SystemExit:
    # Переподнимаем исключение, чтобы остановить импорт при ошибке конфига
    raise
except Exception:
    # Фолбэк на создание объекта с дефолтными значениями, если что-то пошло не так
    # (хотя load_settings уже обрабатывает ошибки, это страховка)
    settings = Settings()