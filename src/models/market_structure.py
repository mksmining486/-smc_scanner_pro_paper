from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import uuid
import logging

logger = logging.getLogger(__name__)

class MarketTrend(str, Enum):
    """
    Направление текущего тренда на основе структуры рынка.
    
    BULLISH: Восходящий тренд (серия Higher Highs и Higher Lows).
    BEARISH: Нисходящий тренд (серия Lower Highs и Lower Lows).
    RANGING: Боковое движение (цена находится между структурными уровнями без явного пробоя).
    UNKNOWN: Недостаточно данных для определения тренда.
    """
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"
    UNKNOWN = "unknown"

class StructureEventType(str, Enum):
    """
    Типы событий изменения или подтверждения рыночной структуры.
    
    BOS (Break of Structure): Пробой предыдущего экстремума в направлении тренда.
    CHOCH (Change of Character): Первый пробой против тренда, сигнализирующий о возможном развороте.
    HH (Higher High): Формирование более высокого максимума.
    HL (Higher Low): Формирование более высокого минимума.
    LH (Lower High): Формирование более низкого максимума.
    LL (Lower Low): Формирование более низкого минимума.
    """
    BOS = "bos"
    CHOCH = "choch"
    HH = "hh"
    HL = "hl"
    LH = "lh"
    LL = "ll"

@dataclass(frozen=True)
class StructureEvent:
    """
    Неизменяемое событие, произошедшее на рынке (пробой, разворот, формирование экстремума).
    
    Атрибуты:
        id: Уникальный идентификатор события.
        event_type: Тип события (BOS, CHOCH и т.д.).
        timestamp: Время события.
        price: Цена, по которой произошло событие (цена пробоя).
        candle_index: Индекс свечи, закрывшей событие.
        swing_breaker_id: ID пивота, который был пробит (источник ликвидности).
        swing_creator_id: ID пивота, который стал результатом события (новый экстремум).
        strength: Сила события (например, процент пробоя или объем).
        meta: Дополнительные данные.
    """
    event_type: StructureEventType
    timestamp: datetime
    price: Decimal
    candle_index: int
    swing_breaker_id: Optional[str] = None
    swing_creator_id: Optional[str] = None
    strength: Decimal = Decimal("0")
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Валидация данных события."""
        if self.price <= 0:
            raise ValueError(f"Цена события должна быть положительной: {self.price}")
        if self.candle_index < 0:
            raise ValueError(f"Индекс свечи не может быть отрицательным: {self.candle_index}")
        if self.strength < 0:
            raise ValueError(f"Сила события не может быть отрицательной: {self.strength}")
        
        # Нормализация времени
        if self.timestamp.tzinfo is not None:
            object.__setattr__(self, 'timestamp', self.timestamp.astimezone(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "price": str(self.price),
            "candle_index": self.candle_index,
            "swing_breaker_id": self.swing_breaker_id,
            "swing_creator_id": self.swing_creator_id,
            "strength": str(self.strength),
            "meta": self.meta
        }

@dataclass
class MarketStructureState:
    """
    Состояние рыночной структуры в текущий момент времени.
    
    Этот объект является мутируемым и обновляется по мере поступления новых свечей.
    Он хранит историю событий и текущий статус тренда.
    
    Атрибуты:
        trend: Текущий определенный тренд.
        last_high: Цена последнего зафиксированного максимума (HH или LH).
        last_low: Цена последнего зафиксированного минимума (HL или LL).
        structural_high: Последний значимый максимум, удерживающий структуру (для медвежьего тренда).
        structural_low: Последний значимый минимум, удерживающий структуру (для бычьего тренда).
        events: Список всех произошедших событий структуры.
        updated_at: Время последнего обновления состояния.
    """
    trend: MarketTrend = MarketTrend.UNKNOWN
    last_high: Optional[Decimal] = None
    last_low: Optional[Decimal] = None
    structural_high: Optional[Decimal] = None
    structural_low: Optional[Decimal] = None
    events: List[StructureEvent] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_event(self, event: StructureEvent) -> None:
        """
        Добавляет событие в историю и обновляет состояние структуры.
        
        Логика обновления:
        1. Добавление события в список.
        2. Обновление временной метки.
        3. Корректировка структурных уровней (structural_high/low) в зависимости от типа события.
        4. Определение нового тренда, если событие является значимым (BOS/CHOCH).
        
        Args:
            event: Событие структуры для обработки.
        """
        self.events.append(event)
        self.updated_at = event.timestamp

        try:
            if event.event_type == StructureEventType.BOS:
                # Пробой структуры подтверждает текущий тренд или усиливает его
                # Если пробой вверх -> обновляем structural_high (если были в бычьем тренде)
                # Если пробой вниз -> обновляем structural_low
                if event.swing_breaker_id:
                    logger.debug(f"BOS подтвержден: пробит уровень {event.swing_breaker_id} по цене {event.price}")
            
            elif event.event_type == StructureEventType.CHOCH:
                # Разворот характера рынка
                old_trend = self.trend
                if self.trend == MarketTrend.BULLISH:
                    self.trend = MarketTrend.BEARISH
                    self.structural_high = event.price # Новый LH становится структурой
                    logger.info(f"CHOCH: Тренд сменился с {old_trend.value} на {MarketTrend.BEARISH.value}")
                elif self.trend == MarketTrend.BEARISH:
                    self.trend = MarketTrend.BULLISH
                    self.structural_low = event.price # Новый HL становится структурой
                    logger.info(f"CHOCH: Тренд сменился с {old_trend.value} на {MarketTrend.BULLISH.value}")
                else:
                    # Если тренд был неизвестен или ранж, определяем по направлению пробоя
                    # Это упрощение, в реальной логике нужно смотреть контекст
                    pass

            elif event.event_type == StructureEventType.HH:
                self.trend = MarketTrend.BULLISH
                self.last_high = event.price
                # В бычьем тренде HH обновляет структурный максимум, если он выше предыдущего
                if self.structural_high is None or event.price > self.structural_high:
                    self.structural_high = event.price

            elif event.event_type == StructureEventType.HL:
                self.last_low = event.price
                # HL подтверждает бычий тренд, если он выше предыдущего Low
                if self.trend == MarketTrend.BULLISH:
                    if self.structural_low is None or event.price > self.structural_low:
                        self.structural_low = event.price

            elif event.event_type == StructureEventType.LH:
                self.last_high = event.price
                # LH подтверждает медвежий тренд
                if self.trend == MarketTrend.BEARISH:
                    if self.structural_high is None or event.price < self.structural_high:
                        self.structural_high = event.price

            elif event.event_type == StructureEventType.LL:
                self.trend = MarketTrend.BEARISH
                self.last_low = event.price
                if self.structural_low is None or event.price < self.structural_low:
                    self.structural_low = event.price

        except Exception as e:
            logger.error(f"Ошибка при обновлении состояния структуры событием {event.id}: {e}", exc_info=True)
            # Не прерываем работу, но логируем ошибку

    def get_last_event(self, event_type: Optional[StructureEventType] = None) -> Optional[StructureEvent]:
        """
        Получает последнее событие, опционально фильтруя по типу.
        
        Args:
            event_type: Тип события для фильтрации. Если None, возвращает последнее любое.
            
        Returns:
            Последнее подходящее событие или None.
        """
        if not self.events:
            return None
        
        if event_type is None:
            return self.events[-1]
        
        for event in reversed(self.events):
            if event.event_type == event_type:
                return event
        return None

    def get_last_bos(self) -> Optional[StructureEvent]:
        """Получает последнее событие Break of Structure."""
        return self.get_last_event(StructureEventType.BOS)

    def get_last_choch(self) -> Optional[StructureEvent]:
        """Получает последнее событие Change of Character."""
        return self.get_last_event(StructureEventType.CHOCH)

    def is_trend_valid(self) -> bool:
        """Проверяет, определен ли тренд (не UNKNOWN)."""
        return self.trend != MarketTrend.UNKNOWN

    def get_price_range(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Возвращает текущий структурный диапазон (Low, High)."""
        return self.structural_low, self.structural_high

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация состояния в словарь."""
        return {
            "trend": self.trend.value,
            "last_high": str(self.last_high) if self.last_high else None,
            "last_low": str(self.last_low) if self.last_low else None,
            "structural_high": str(self.structural_high) if self.structural_high else None,
            "structural_low": str(self.structural_low) if self.structural_low else None,
            "events_count": len(self.events),
            "last_event_type": self.events[-1].event_type.value if self.events else None,
            "updated_at": self.updated_at.isoformat()
        }

    def __str__(self) -> str:
        return f"MarketStructure(Trend: {self.trend.value}, HH: {self.last_high}, LL: {self.last_low})"