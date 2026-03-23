from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import uuid
import logging

logger = logging.getLogger(__name__)

class OrderBlockType(str, Enum):
    """
    Тип ордер блока в зависимости от направления сделки.
    
    BULLISH: Бычий ордер блок (зона спроса). Формируется перед импульсом вверх.
             Обычно это последняя медвежья свеча перед ростом.
    BEARISH: Медвежий ордер блок (зона предложения). Формируется перед импульсом вниз.
             Обычно это последняя бычья свеча перед падением.
    """
    BULLISH = "bullish"
    BEARISH = "bearish"

class OrderBlockStatus(str, Enum):
    """
    Статус жизни ордер блока.
    
    ACTIVE: Ордер блок сформирован и еще не тестировался ценой. Ожидает возврата цены.
    TESTED: Цена коснулась зоны ордер блока, но не закрылась полностью внутри него.
    MITIGATED: Цена вошла в зону и исполнила отложенные ордера (частично или полностью).
    EXHAUSTED: Ордер блок полностью отработан, вероятность реакции низкая.
    INVALIDATED: Цена пробила ордер блок в противоположном направлении без реакции (стоп-лосс сработал).
    EXPIRED: Ордер блок устарел по времени или количеству баров.
    """
    ACTIVE = "active"
    TESTED = "tested"
    MITIGATED = "mitigated"
    EXHAUSTED = "exhausted"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"

@dataclass
class OrderBlock:
    """
    Модель Ордер Блока (Order Block).
    
    Ордер блок — это конкретная свеча (или зона свечи), с которой крупный игрок начал движение.
    При возврате цены к этой зоне ожидается продолжение движения.
    
    Атрибуты:
        id: Уникальный идентификатор.
        ob_type: Тип блока (бычий/медвежий).
        candle_index: Индекс свечи, формирующей блок.
        timestamp: Время открытия свечи блока.
        open: Цена открытия свечи блока.
        high: Максимум свечи блока.
        low: Минимум свечи блока.
        close: Цена закрытия свечи блока.
        status: Текущий статус блока.
        mitigation_level: Уровень цены, до которого блок был протестирован/митигирован (0.0 - 1.0).
        created_at: Время создания записи.
        strength: Сила блока (зависит от импульса после него, объема и т.д.).
        touched_count: Количество касаний цены.
        meta: Дополнительные данные (например, ID вызвавшего BOS события).
    """
    ob_type: OrderBlockType
    candle_index: int
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    status: OrderBlockStatus = OrderBlockStatus.ACTIVE
    mitigation_level: Decimal = Decimal("0")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strength: int = 1
    touched_count: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Валидация данных."""
        if self.candle_index < 0:
            raise ValueError(f"Индекс свечи не может быть отрицательным: {self.candle_index}")
        
        # Валидация цен
        prices = [self.open, self.high, self.low, self.close]
        for p in prices:
            if p <= 0:
                raise ValueError(f"Цена должна быть положительной: {p}")
        
        if self.high < self.low:
            raise ValueError(f"High ({self.high}) < Low ({self.low})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Open вне диапазона свечи")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Close вне диапазона свечи")

        # Логическая проверка типа
        if self.ob_type == OrderBlockType.BULLISH and self.close <= self.open:
            # Бычий ОБ обычно формируется зеленой свечой, но в некоторых трактовках это последняя красная перед ростом.
            # Здесь мы принимаем строгое определение: Бычий ОБ - это зона спроса, часто последняя нисходящая свеча.
            # Однако для простоты моделирования оставим проверку на усмотрение движка.
            # В классической SMC: Bullish OB = Last Down Candle before Up Move.
            pass 
        
        if self.created_at.tzinfo is not None:
            self.created_at = self.created_at.astimezone(timezone.utc)

    @property
    def zone_top(self) -> Decimal:
        """Верхняя граница зоны ордер блока."""
        return self.high

    @property
    def zone_bottom(self) -> Decimal:
        """Нижняя граница зоны ордер блока."""
        return self.low

    @property
    def mean_threshold(self) -> Decimal:
        """Средняя точка (50% уровень) ордер блока. Часто используется как цель или вход."""
        return (self.high + self.low) / 2

    def get_unmitigated_range(self) -> Tuple[Decimal, Decimal]:
        """
        Возвращает диапазон еще не митигированной части блока.
        
        Для бычьего блока: митигация идет сверху вниз (от High к Low).
        Для медвежьего блока: митигация идет снизу вверх (от Low к High).
        
        Returns:
            Кортеж (min_price, max_price) активной зоны.
        """
        range_size = self.high - self.low
        mitigated_amount = range_size * self.mitigation_level

        if self.ob_type == OrderBlockType.BULLISH:
            # Митигация съедает верхнюю часть
            new_top = self.high - mitigated_amount
            if new_top < self.low:
                return self.low, self.low # Полностью митигирован
            return new_top, self.high
        else:
            # Медвежий: митигация съедает нижнюю часть
            new_bottom = self.low + mitigated_amount
            if new_bottom > self.high:
                return self.high, self.high
            return self.low, new_bottom

    def update_state(self, current_price: Decimal, timestamp: datetime) -> None:
        """
        Обновляет статус и уровень митигации на основе текущей цены.
        
        Логика:
        1. Проверка на невалидацию (пробой против направления).
        2. Проверка на касание.
        3. Расчет процента митигации.
        
        Args:
            current_price: Текущая цена рынка.
            timestamp: Время обновления.
        """
        price = Decimal(str(current_price))
        ts = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp

        # 1. Проверка на невалидацию
        if self.ob_type == OrderBlockType.BULLISH:
            # Бычий ОБ невалиден, если цена закрылась ниже его минимума (полный пробой вниз)
            # В реальной торговле стоп обычно под минимумом, но для модели считаем пробой Low сигналом
            if price < self.low and self.status not in [OrderBlockStatus.INVALIDATED, OrderBlockStatus.EXHAUSTED]:
                # Дополнительная логика: если это просто фитиль, то может быть TESTED, если закрытие - INVALIDATED
                # Для упрощения: сильное пробитие -> INVALIDATED
                if self.status == OrderBlockStatus.ACTIVE:
                     self.status = OrderBlockStatus.INVALIDATED
                     logger.warning(f"Bullish OB {self.id} invalidated at {price}")
                     return
        else:
            # Медвежий ОБ невалиден, если цена пробила максимум вверх
            if price > self.high and self.status not in [OrderBlockStatus.INVALIDATED, OrderBlockStatus.EXHAUSTED]:
                if self.status == OrderBlockStatus.ACTIVE:
                    self.status = OrderBlockStatus.INVALIDATED
                    logger.warning(f"Bearish OB {self.id} invalidated at {price}")
                    return

        # Если уже неактивен, просто обновляем статистику
        if self.status in [OrderBlockStatus.EXHAUSTED, OrderBlockStatus.INVALIDATED, OrderBlockStatus.EXPIRED]:
            return

        # 2. Проверка на попадание в зону
        in_zone = self.low <= price <= self.high
        
        if in_zone:
            if self.status == OrderBlockStatus.ACTIVE:
                self.status = OrderBlockStatus.TESTED
            
            self.touched_count += 1
            
            # 3. Расчет митигации
            range_size = self.high - self.low
            if range_size == 0:
                self.mitigation_level = Decimal("1")
                self.status = OrderBlockStatus.MITIGATED
                return

            if self.ob_type == OrderBlockType.BULLISH:
                # Для бычьего: чем ниже цена, тем больше митигация. 
                # Price = High -> 0%, Price = Low -> 100%
                mitigated_dist = self.high - price
                self.mitigation_level = min(Decimal("1"), mitigated_dist / range_size)
            else:
                # Для медвежьего: чем выше цена, тем больше митигация.
                # Price = Low -> 0%, Price = High -> 100%
                mitigated_dist = price - self.low
                self.mitigation_level = min(Decimal("1"), mitigated_dist / range_size)

            # Проверка на полную митигацию
            if self.mitigation_level >= Decimal("0.95"): # Порог 95%
                self.status = OrderBlockStatus.MITIGATED
                logger.info(f"OB {self.id} fully mitigated at {price}")
            
            # Проверка на истощение (многократные тесты)
            if self.touched_count >= 3:
                self.status = OrderBlockStatus.EXHAUSTED
                logger.info(f"OB {self.id} exhausted after {self.touched_count} touches")

    def is_fresh(self) -> bool:
        """Проверяет, является ли блок свежим (не тестировался)."""
        return self.status == OrderBlockStatus.ACTIVE and self.touched_count == 0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "type": self.ob_type.value,
            "candle_index": self.candle_index,
            "timestamp": self.timestamp.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "status": self.status.value,
            "mitigation_level": str(self.mitigation_level),
            "touched_count": self.touched_count,
            "strength": self.strength,
            "created_at": self.created_at.isoformat(),
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OrderBlock:
        """Десериализация из словаря."""
        try:
            ob_type = OrderBlockType(data["type"])
            status = OrderBlockStatus(data.get("status", "active"))
            
            ts_str = data.get("timestamp")
            timestamp = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            
            created_str = data.get("created_at")
            created_at = datetime.fromisoformat(created_str) if created_str else datetime.now(timezone.utc)

            def to_dec(val: Any) -> Decimal:
                return Decimal(str(val))

            return cls(
                id=data.get("id", str(uuid.uuid4())),
                ob_type=ob_type,
                candle_index=int(data["candle_index"]),
                timestamp=timestamp,
                open=to_dec(data["open"]),
                high=to_dec(data["high"]),
                low=to_dec(data["low"]),
                close=to_dec(data["close"]),
                status=status,
                mitigation_level=to_dec(data.get("mitigation_level", 0)),
                created_at=created_at,
                strength=int(data.get("strength", 1)),
                touched_count=int(data.get("touched_count", 0)),
                meta=data.get("meta", {})
            )
        except Exception as e:
            raise ValueError(f"Ошибка парсинга OrderBlock: {e}")

    def __str__(self) -> str:
        return f"OrderBlock({self.ob_type.value} @ [{self.low}-{self.high}] | Status: {self.status.value} | Mitigation: {self.mitigation_level})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OrderBlock):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)