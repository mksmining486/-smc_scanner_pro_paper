from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, List, Dict, Any, Set
import uuid
import logging

logger = logging.getLogger(__name__)

class LiquidityType(str, Enum):
    """
    Типы ликвидности в контексте SMC.
    
    BSL (Buy Side Liquidity): Ликвидность на стороне покупателей (стоп-лоссы продавцов над максимумами).
    SSL (Sell Side Liquidity): Ликвидность на стороне продавцов (стоп-лоссы покупателей под минимумами).
    EQH (Equal Highs): Равные максимумы (двойная/тройная вершина), создающие сильную зону ликвидности.
    EQL (Equal Lows): Равные минимумы (двойное/тройное дно), создающие сильную зону ликвидности.
    TREND_LINE: Ликвидность, скопившаяся вдоль трендовой линии.
    """
    BSL = "bsl"
    SSL = "ssl"
    EQH = "eqh"
    EQL = "eql"
    TREND_LINE = "trend_line"

class LiquidityStatus(str, Enum):
    """
    Статус ликвидности относительно текущей цены.
    
    ACTIVE: Ликвидность еще не затронута ценой.
    TOUCHED: Цена коснулась уровня ликвидности (тест).
    SWEEPED: Цена пробила уровень и забрала ликвидность (свип), но возможно вернется.
    MITIGATED: Ликвидность полностью поглощена (цена ушла далеко за уровень).
    EXPIRED: Ликвидность утратила актуальность (например, слишком старая).
    """
    ACTIVE = "active"
    TOUCHED = "touched"
    SWEEPED = "sweeped"
    MITIGATED = "mitigated"
    EXPIRED = "expired"

@dataclass
class LiquidityPool:
    """
    Модель пула ликвидности.
    
    Представляет собой ценовую зону, где скопилось большое количество стоп-ордеров.
    Обычно формируется вокруг свингов (Pivot Points) или равных экстремумов.
    
    Атрибуты:
        id: Уникальный идентификатор пула.
        liquidity_type: Тип ликвидности (BSL, SSL, EQH, EQL).
        price_level: Ключевой ценовой уровень пула.
        min_price: Нижняя граница зоны ликвидности.
        max_price: Верхняя граница зоны ликвидности.
        created_at: Время создания пула.
        source_pivot_ids: Список ID пивотов, сформировавших этот пул.
        status: Текущий статус ликвидности.
        sweep_count: Количество свипов (пробоев с возвратом).
        last_touched_at: Время последнего касания.
        meta: Дополнительные метаданные.
    """
    liquidity_type: LiquidityType
    price_level: Decimal
    min_price: Decimal
    max_price: Decimal
    created_at: datetime
    source_pivot_ids: List[str] = field(default_factory=list)
    status: LiquidityStatus = LiquidityStatus.ACTIVE
    sweep_count: int = 0
    last_touched_at: Optional[datetime] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Валидация и нормализация данных."""
        if self.price_level <= 0:
            raise ValueError(f"Уровень цены должен быть положительным: {self.price_level}")
        if self.min_price > self.max_price:
            raise ValueError(f"min_price ({self.min_price}) не может быть больше max_price ({self.max_price})")
        if self.price_level < self.min_price or self.price_level > self.max_price:
            # Корректируем уровень, если он вне зоны, или кидаем ошибку
            # В строгом режиме лучше кинуть ошибку
            raise ValueError(f"price_level ({self.price_level}) вне диапазона [{self.min_price}, {self.max_price}]")
        
        if self.created_at.tzinfo is not None:
            self.created_at = self.created_at.astimezone(timezone.utc)

    def update_status(self, current_price: Decimal, timestamp: datetime) -> None:
        """
        Обновляет статус ликвидности на основе текущей цены.
        
        Логика:
        - Если цена коснулась диапазона -> TOUCHED
        - Если цена пробила диапазон -> SWEEPED (увеличиваем счетчик)
        - Если цена ушла далеко -> MITIGATED
        
        Args:
            current_price: Текущая рыночная цена.
            timestamp: Время обновления.
        """
        c_price = Decimal(str(current_price))
        ts = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp

        # Проверка на касание или пробой
        touched = self.min_price <= c_price <= self.max_price
        swept_above = c_price > self.max_price
        swept_below = c_price < self.min_price

        if swept_above or swept_below:
            if self.status != LiquidityStatus.SWEEPED:
                self.sweep_count += 1
                logger.info(f"Liquidity Sweep detected on {self.id} at {c_price}")
            self.status = LiquidityStatus.SWEEPED
            self.last_touched_at = ts
        elif touched:
            if self.status == LiquidityStatus.ACTIVE:
                self.status = LiquidityStatus.TOUCHED
                self.last_touched_at = ts
        else:
            # Если цена ушла далеко после свипа, можно считать митигированной
            # Логика расстояния зависит от волатильности, здесь упрощенно
            if self.status == LiquidityStatus.SWEEPED:
                dist = abs(c_price - self.price_level)
                range_size = self.max_price - self.min_price
                if dist > (range_size * 2): # Ушло на 2 размера диапазона
                    self.status = LiquidityStatus.MITIGATED

    def contains_price(self, price: Decimal) -> bool:
        """Проверяет, находится ли цена в диапазоне пула."""
        p = Decimal(str(price))
        return self.min_price <= p <= self.max_price

    def get_distance_to_price(self, price: Decimal) -> Decimal:
        """Вычисляет расстояние от ближайшей границы пула до цены."""
        p = Decimal(str(price))
        if p < self.min_price:
            return self.min_price - p
        elif p > self.max_price:
            return p - self.max_price
        else:
            return Decimal("0")

    def add_source_pivot(self, pivot_id: str) -> None:
        """Добавляет ID исходного пивота в список источников."""
        if pivot_id not in self.source_pivot_ids:
            self.source_pivot_ids.append(pivot_id)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "type": self.liquidity_type.value,
            "price_level": str(self.price_level),
            "min_price": str(self.min_price),
            "max_price": str(self.max_price),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_touched_at": self.last_touched_at.isoformat() if self.last_touched_at else None,
            "sweep_count": self.sweep_count,
            "source_pivot_ids": self.source_pivot_ids,
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LiquidityPool:
        """Десериализация из словаря."""
        try:
            liq_type = LiquidityType(data["type"])
            status = LiquidityStatus(data.get("status", "active"))
            
            created_at_str = data.get("created_at")
            created_at = datetime.fromisoformat(created_at_str) if created_at_str else datetime.now(timezone.utc)
            
            last_touched_str = data.get("last_touched_at")
            last_touched = datetime.fromisoformat(last_touched_str) if last_touched_str else None

            return cls(
                id=data.get("id", str(uuid.uuid4())),
                liquidity_type=liq_type,
                price_level=Decimal(str(data["price_level"])),
                min_price=Decimal(str(data["min_price"])),
                max_price=Decimal(str(data["max_price"])),
                created_at=created_at,
                source_pivot_ids=data.get("source_pivot_ids", []),
                status=status,
                sweep_count=int(data.get("sweep_count", 0)),
                last_touched_at=last_touched,
                meta=data.get("meta", {})
            )
        except Exception as e:
            raise ValueError(f"Ошибка парсинга LiquidityPool: {e}")

    def __str__(self) -> str:
        return f"LiquidityPool({self.liquidity_type.value} @ {self.price_level} | Status: {self.status.value} | Sweeps: {self.sweep_count})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LiquidityPool):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)