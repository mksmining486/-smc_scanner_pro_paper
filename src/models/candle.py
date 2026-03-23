"""
SMC Trading Engine - Candle Model.

Модель свечи (OHLCV) с высокой точностью вычислений (Decimal).
Используется как базовый элемент входных данных для всех движков анализа.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candle:
    """
    Представление одной свечи рынка.
    
    Атрибуты:
        timestamp: Время открытия свечи (UTC).
        open: Цена открытия.
        high: Максимальная цена.
        low: Минимальная цена.
        close: Цена закрытия.
        volume: Объем торгов.
        quote_volume: Объем в котировочной валюте (опционально).
        trades_count: Количество сделок (опционально).
        is_complete: Флаг завершенности свечи (False для текущей формирующейся свечи).
    """
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Optional[Decimal] = None
    trades_count: Optional[int] = None
    is_complete: bool = True

    def __post_init__(self):
        """
        Валидация целостности данных свечи сразу после создания.
        Так как dataclass frozen, ошибки валидации предотвращают создание объекта.
        """
        # 1. Проверка логики цен: High >= Low, High >= Open/Close, Low <= Open/Close
        if self.high < self.low:
            raise ValueError(f"High ({self.high}) не может быть меньше Low ({self.low})")
        
        if self.high < self.open or self.high < self.close:
            raise ValueError(f"High ({self.high}) должен быть >= Open ({self.open}) и Close ({self.close})")
            
        if self.low > self.open or self.low > self.close:
            raise ValueError(f"Low ({self.low}) должен быть <= Open ({self.open}) и Close ({self.close})")

        # 2. Проверка неотрицательности объема
        if self.volume < 0:
            raise ValueError(f"Объем не может быть отрицательным: {self.volume}")
            
        if self.quote_volume is not None and self.quote_volume < 0:
            raise ValueError(f"Quote объем не может быть отрицательным: {self.quote_volume}")

        # 3. Нормализация времени до UTC
        # Если время без таймзоны, считаем его UTC. Если есть - конвертируем.
        object.__setattr__(self, 'timestamp', self._normalize_timestamp(self.timestamp))

    @staticmethod
    def _normalize_timestamp(ts: datetime) -> datetime:
        """Приводит timestamp к UTC."""
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Candle':
        """
        Создает объект Candle из словаря.
        Автоматически конвертирует числовые значения в Decimal и строки времени в datetime.
        
        Args:
            data: Словарь с ключами ('time', 'open', 'high', 'low', 'close', 'volume', ...).
        
        Returns:
            Экземпляр Candle.
            
        Raises:
            ValueError: При ошибке преобразования типов или отсутствии обязательных полей.
        """
        required_fields = ['time', 'open', 'high', 'low', 'close', 'volume']
        for f in required_fields:
            if f not in data:
                raise ValueError(f"Отсутствует обязательное поле: {f}")

        try:
            # Парсинг времени
            ts = data['time']
            if isinstance(ts, str):
                # Поддержка ISO формата и Unix timestamp в строке
                if ts.isdigit():
                    ts = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                else:
                    # Попытка парсинга ISO строки
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            elif isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            
            # Конвертация чисел в Decimal для точности
            open_price = Decimal(str(data['open']))
            high_price = Decimal(str(data['high']))
            low_price = Decimal(str(data['low']))
            close_price = Decimal(str(data['close']))
            volume = Decimal(str(data['volume']))
            
            quote_vol = None
            if 'quote_volume' in data and data['quote_volume'] is not None:
                quote_vol = Decimal(str(data['quote_volume']))
                
            trades = data.get('trades_count')
            is_complete = data.get('is_complete', True)

            return cls(
                timestamp=ts,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                quote_volume=quote_vol,
                trades_count=trades,
                is_complete=is_complete
            )
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"Ошибка парсинга данных свечи: {e}") from e

    def to_dict(self) -> Dict[str, Any]:
        """Сериализует свечу в словарь (JSON-совместимый формат)."""
        return {
            'time': int(self.timestamp.timestamp()),  # Unix timestamp для совместимости
            'open': float(self.open),
            'high': float(self.high),
            'low': float(self.low),
            'close': float(self.close),
            'volume': float(self.volume),
            'quote_volume': float(self.quote_volume) if self.quote_volume else None,
            'trades_count': self.trades_count,
            'is_complete': self.is_complete
        }

    @property
    def range(self) -> Decimal:
        """Возвращает диапазон свечи (High - Low)."""
        return self.high - self.low

    @property
    def body(self) -> Decimal:
        """Возвращает размер тела свечи (abs(Open - Close))."""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> Decimal:
        """Возвращает длину верхней тени."""
        top = max(self.open, self.close)
        return self.high - top

    @property
    def lower_shadow(self) -> Decimal:
        """Возвращает длину нижней тени."""
        bottom = min(self.open, self.close)
        return bottom - self.low

    @property
    def is_bullish(self) -> bool:
        """True, если свеча бычья (Close > Open)."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True, если свеча медвежья (Close < Open)."""
        return self.close < self.open

    @property
    def is_doji(self) -> bool:
        """
        Определяет, является ли свеча дожи (тело очень маленькое относительно диапазона).
        Порог: тело < 10% от диапазона.
        """
        if self.range == 0:
            return True
        return (self.body / self.range) < Decimal('0.1')

    def overlaps(self, other: 'Candle') -> bool:
        """
        Проверяет, перекрываются ли диапазоны двух свечей.
        
        Args:
            other: Другая свеча для сравнения.
            
        Returns:
            True, если есть перекрытие по ценам High/Low.
        """
        return not (self.low > other.high or self.high < other.low)

    def contains(self, price: Decimal) -> bool:
        """Проверяет, находится ли цена внутри диапазона свечи."""
        return self.low <= price <= self.high

    def __str__(self) -> str:
        direction = "🟢" if self.is_bullish else "🔴" if self.is_bearish else "⚪"
        return (f"{direction} Candle[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] "
                f"O:{self.open} H:{self.high} L:{self.low} C:{self.close} V:{self.volume}")

    def __repr__(self):
        return f"Candle(time={self.timestamp}, O={self.open}, C={self.close})"