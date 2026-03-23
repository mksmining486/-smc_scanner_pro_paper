from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import uuid
import logging

logger = logging.getLogger(__name__)

class FVGType(str, Enum):
    """
    Тип Fair Value Gap (имбаланса).
    
    BULLISH: Бычий имбаланс. Возникает при сильном движении вверх.
             Цена закрытия свечи 1 < Цены открытия свечи 3.
             Зона между High свечи 1 и Low свечи 3 является поддержкой.
    BEARISH: Медвежий имбаланс. Возникает при сильном движении вниз.
             Цена закрытия свечи 1 > Цены открытия свечи 3.
             Зона между Low свечи 1 и High свечи 3 является сопротивлением.
    """
    BULLISH = "bullish"
    BEARISH = "bearish"

class FVGStatus(str, Enum):
    """
    Статус заполнения Fair Value Gap.
    
    ACTIVE: FVG сформирован, цена еще не касалась его зоны.
    PARTIALLY_FILLED: Цена вошла в зону FVG, но не закрыла её полностью.
    FILLED: Цена полностью прошла через зону FVG (заполнила гэп).
            После этого FVG может считаться неактивным или работать как разворотная зона.
    REJECTED: Цена коснулась FVG и резко развернулась, не зайдя глубоко (редкий статус, зависит от стратегии).
    EXPIRED: FVG устарел по времени или количеству баров без теста.
    """
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"

@dataclass
class FairValueGap:
    """
    Модель Fair Value Gap (FVG) / Imbalance.
    
    FVG образуется тремя свечами, когда вторая свеча имеет такой большой диапазон,
    что тени первой и третьей свечей не перекрываются полностью. Это создает "неэффективную" зону цены,
    куда рынок часто возвращается для ребалансировки.
    
    Атрибуты:
        id: Уникальный идентификатор.
        fvg_type: Тип имбаланса (бычий/медвежий).
        candle_index_start: Индекс первой свечи формации (свеча 1 из 3).
        candle_index_imbalance: Индекс второй свечи (свеча 2), создавшей импульс.
        timestamp: Время формирования (время закрытия 3-й свечи).
        high: Верхняя граница зоны FVG.
                Для Bullish: High свечи 1.
                Для Bearish: High свечи 3.
        low: Нижняя граница зоны FVG.
                Для Bullish: Low свечи 3.
                Для Bearish: Low свечи 1.
        imbalance_price: Цена "середины" или точка максимального дисбаланса (обычно Open/Close свечи 2).
        status: Текущий статус заполнения.
        fill_percentage: Процент заполнения зоны (0.0 - 100.0).
        created_at: Время создания записи.
        touched_count: Количество касаний зоны.
        meta: Дополнительные данные.
    """
    fvg_type: FVGType
    candle_index_start: int
    candle_index_imbalance: int
    timestamp: datetime
    high: Decimal
    low: Decimal
    imbalance_price: Decimal
    status: FVGStatus = FVGStatus.ACTIVE
    fill_percentage: Decimal = Decimal("0")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    touched_count: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Валидация данных."""
        # Валидация индексов
        if self.candle_index_start < 0 or self.candle_index_imbalance < 0:
            raise ValueError("Индексы свечей не могут быть отрицательными")
        if self.candle_index_imbalance <= self.candle_index_start:
            # Свеча имбаланса должна идти после стартовой
            raise ValueError("Индекс свечи имбаланса должен быть больше индекса старта")

        # Валидация цен
        prices = [self.high, self.low, self.imbalance_price]
        for p in prices:
            if p <= 0:
                raise ValueError(f"Цена должна быть положительной: {p}")

        # Логическая валидация диапазона
        if self.high <= self.low:
            raise ValueError(f"High ({self.high}) должно быть строго больше Low ({self.low}) для существования FVG")

        # Проверка соответствия типа и направления
        if self.fvg_type == FVGType.BULLISH:
            # Для бычьего FVG зона поддержки, цена должна расти от неё.
            # Обычно Low свечи 3 > High свечи 1.
            pass 
        elif self.fvg_type == FVGType.BEARISH:
            # Для медвежьего FVG зона сопротивления.
            pass

        # Нормализация времени
        if self.timestamp.tzinfo is not None:
            self.timestamp = self.timestamp.astimezone(timezone.utc)

    @property
    def range_size(self) -> Decimal:
        """Вычисляет размер зоны FVG."""
        return self.high - self.low

    @property
    def mid_point(self) -> Decimal:
        """Вычисляет середину зоны FVG (часто используется как целевая точка входа)."""
        return (self.high + self.low) / 2

    def update_state(self, current_price: Decimal, timestamp: datetime) -> None:
        """
        Обновляет статус и процент заполнения FVG на основе текущей цены.
        
        Логика заполнения:
        - Bullish FVG заполняется, когда цена опускается вниз (касается High, затем Low).
          Заполнение считается от High к Low.
        - Bearish FVG заполняется, когда цена поднимается вверх (касается Low, затем High).
          Заполнение считается от Low к High.
        
        Args:
            current_price: Текущая рыночная цена.
            timestamp: Время обновления.
        """
        if self.status in [FVGStatus.FILLED, FVGStatus.EXPIRED]:
            return

        price = Decimal(str(current_price))
        ts = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp
        
        in_zone = self.low <= price <= self.high
        range_size = self.range_size
        
        if range_size == 0:
            self.fill_percentage = Decimal("100")
            self.status = FVGStatus.FILLED
            return

        old_status = self.status

        if self.fvg_type == FVGType.BULLISH:
            # Бычий FVG: цена приходит сверху вниз.
            # Если цена выше High -> 0%
            # Если цена ниже Low -> 100%
            if price >= self.high:
                self.fill_percentage = Decimal("0")
                if self.status == FVGStatus.PARTIALLY_FILLED:
                    # Цена вышла из зоны вверх, не заполнив до конца -> откат
                    self.status = FVGStatus.ACTIVE 
            elif price <= self.low:
                self.fill_percentage = Decimal("100")
                self.status = FVGStatus.FILLED
                logger.info(f"Bullish FVG {self.id} fully filled at {price}")
            else:
                # Цена внутри зоны
                distance_from_top = self.high - price
                self.fill_percentage = (distance_from_top / range_size) * Decimal("100")
                if self.status == FVGStatus.ACTIVE:
                    self.status = FVGStatus.PARTIALLY_FILLED
                
                if self.touched_count == 0:
                    self.touched_count = 1

        elif self.fvg_type == FVGType.BEARISH:
            # Медвежий FVG: цена приходит снизу вверх.
            # Если цена ниже Low -> 0%
            # Если цена выше High -> 100%
            if price <= self.low:
                self.fill_percentage = Decimal("0")
                if self.status == FVGStatus.PARTIALLY_FILLED:
                    self.status = FVGStatus.ACTIVE
            elif price >= self.high:
                self.fill_percentage = Decimal("100")
                self.status = FVGStatus.FILLED
                logger.info(f"Bearish FVG {self.id} fully filled at {price}")
            else:
                # Цена внутри зоны
                distance_from_bottom = price - self.low
                self.fill_percentage = (distance_from_bottom / range_size) * Decimal("100")
                if self.status == FVGStatus.ACTIVE:
                    self.status = FVGStatus.PARTIALLY_FILLED
                
                if self.touched_count == 0:
                    self.touched_count = 1

        # Логирование смены статуса
        if old_status != self.status:
            logger.debug(f"FVG {self.id} status changed: {old_status.value} -> {self.status.value} (Fill: {self.fill_percentage}%)")

    def is_fresh(self) -> bool:
        """Проверяет, является ли FVG свежим (нетронутым)."""
        return self.status == FVGStatus.ACTIVE and self.touched_count == 0

    def get_distance_to_entry(self, current_price: Decimal) -> Decimal:
        """
        Вычисляет расстояние от текущей цены до ближайшей границы FVG.
        
        Args:
            current_price: Текущая цена.
            
        Returns:
            Расстояние в пунктах цены. 0, если цена внутри зоны.
        """
        price = Decimal(str(current_price))
        if price < self.low:
            return self.low - price
        elif price > self.high:
            return price - self.high
        else:
            return Decimal("0")

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "type": self.fvg_type.value,
            "candle_index_start": self.candle_index_start,
            "candle_index_imbalance": self.candle_index_imbalance,
            "timestamp": self.timestamp.isoformat(),
            "high": str(self.high),
            "low": str(self.low),
            "imbalance_price": str(self.imbalance_price),
            "status": self.status.value,
            "fill_percentage": str(self.fill_percentage),
            "range_size": str(self.range_size),
            "touched_count": self.touched_count,
            "created_at": self.created_at.isoformat(),
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls,  Dict[str, Any]) -> FairValueGap:
        """Десериализация из словаря."""
        try:
            fvg_type = FVGType(data["type"])
            status = FVGStatus(data.get("status", "active"))
            
            ts_str = data.get("timestamp")
            timestamp = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            
            created_str = data.get("created_at")
            created_at = datetime.fromisoformat(created_str) if created_str else datetime.now(timezone.utc)

            def to_dec(val: Any) -> Decimal:
                if isinstance(val, Decimal):
                    return val
                return Decimal(str(val))

            return cls(
                id=data.get("id", str(uuid.uuid4())),
                fvg_type=fvg_type,
                candle_index_start=int(data["candle_index_start"]),
                candle_index_imbalance=int(data["candle_index_imbalance"]),
                timestamp=timestamp,
                high=to_dec(data["high"]),
                low=to_dec(data["low"]),
                imbalance_price=to_dec(data["imbalance_price"]),
                status=status,
                fill_percentage=to_dec(data.get("fill_percentage", 0)),
                created_at=created_at,
                touched_count=int(data.get("touched_count", 0)),
                meta=data.get("meta", {})
            )
        except Exception as e:
            raise ValueError(f"Ошибка парсинга FairValueGap: {e}")

    def __str__(self) -> str:
        direction = "BULL" if self.fvg_type == FVGType.BULLISH else "BEAR"
        return f"FVG({direction} [{self.low}-{self.high}] | Status: {self.status.value} | Fill: {self.fill_percentage}%)"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FairValueGap):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)