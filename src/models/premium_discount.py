from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import uuid
import logging
import math

logger = logging.getLogger(__name__)

class PDZoneType(str, Enum):
    """
    Тип зоны относительно равновесной цены (50% уровня).
    
    PREMIUM: Зона выше 50%. Ожидается продажа (Short) в этой зоне.
    DISCOUNT: Зона ниже 50%. Ожидается покупка (Long) в этой зоне.
    EQUILIBRIUM: Сама зона 50% (равновесие).
    """
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"

@dataclass(frozen=True)
class FibonacciLevel:
    """
    Отдельный уровень Фибоначчи в рамках диапазона.
    
    Атрибуты:
        ratio: Коэффициент Фибоначчи (например, 0.0, 0.5, 0.618, 1.0).
        price: Рассчитанная цена для этого уровня.
        label: Опциональная метка (например, "0.618", "OSE", "Fair Value").
    """
    ratio: float
    price: Decimal
    label: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.ratio <= 1.0):
            raise ValueError(f"Коэффициент Фибоначчи должен быть в диапазоне [0.0, 1.0]. Получено: {self.ratio}")
        if self.price < 0:
            raise ValueError(f"Цена уровня не может быть отрицательной: {self.price}")

@dataclass
class PremiumDiscountZone:
    """
    Модель зоны Premium/Discount, построенной на основе структурного диапазона (Swing High - Swing Low).
    
    Основная концепция SMC:
    - Покупать только в зоне Discount (ниже 50% диапазона).
    - Продавать только в зоне Premium (выше 50% диапазона).
    
    Атрибуты:
        id: Уникальный идентификатор зоны.
        swing_high: Цена верхнего экстремума диапазона.
        swing_low: Цена нижнего экстремума диапазона.
        created_at: Время создания зоны.
        fib_levels: Список рассчитанных уровней Фибоначчи внутри диапазона.
        equilibrium_price: Цена 50% (равновесие).
        is_valid: Флаг валидности зоны (цена не должна выйти за пределы диапазона полностью, иначе зона пересчитывается).
        meta: Дополнительные метаданные.
    """
    swing_high: Decimal
    swing_low: Decimal
    created_at: datetime
    fib_levels: List[FibonacciLevel] = field(default_factory=list)
    equilibrium_price: Decimal = field(init=False)
    is_valid: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Валидация и расчет уровней."""
        if self.swing_high <= self.swing_low:
            raise ValueError(f"Swing High ({self.swing_high}) должен быть больше Swing Low ({self.swing_low})")
        
        if self.created_at.tzinfo is not None:
            self.created_at = self.created_at.astimezone(timezone.utc)

        # Расчет равновесия (50%)
        range_size = self.swing_high - self.swing_low
        self.equilibrium_price = self.swing_low + (range_size / Decimal("2"))

        # Если уровни Фибоначчи не переданы явно, генерируем стандартные
        if not self.fib_levels:
            self.fib_levels = self._generate_standard_fib_levels()

    def _generate_standard_fib_levels(self) -> List[FibonacciLevel]:
        """
        Генерирует стандартные уровни Фибоначчи: 0, 0.5, 1.0, а также ключевые OTE (0.62, 0.705).
        
        Returns:
            Отсортированный список уровней.
        """
        standard_ratios = [0.0, 0.5, 0.618, 0.705, 0.786, 0.886, 1.0]
        range_size = self.swing_high - self.swing_low
        
        levels = []
        for ratio in standard_ratios:
            price = self.swing_low + (range_size * Decimal(str(ratio)))
            label = str(ratio)
            if ratio == 0.5:
                label = "Equilibrium (50%)"
            elif ratio == 0.618:
                label = "Golden Pocket"
            elif ratio == 0.705:
                label = "OTE (Optimal Trade Entry)"
            
            levels.append(FibonacciLevel(ratio=ratio, price=price, label=label))
        
        return levels

    def get_zone_type_for_price(self, price: Decimal) -> PDZoneType:
        """
        Определяет тип зоны для заданной цены.
        
        Args:
            price: Текущая цена.
            
        Returns:
            Тип зоны (PREMIUM, DISCOUNT или EQUILIBRIUM, если цена ровно на 50%).
        """
        p = Decimal(str(price))
        
        # Допуск для сравнения с равновесием (чтобы избежать проблем с плавающей точкой)
        tolerance = (self.swing_high - self.swing_low) * Decimal("0.001")
        
        if abs(p - self.equilibrium_price) <= tolerance:
            return PDZoneType.EQUILIBRIUM
        elif p > self.equilibrium_price:
            return PDZoneType.PREMIUM
        else:
            return PDZoneType.DISCOUNT

    def get_entry_zone(self, trend_direction: str) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Возвращает рекомендуемую зону входа в зависимости от тренда.
        
        Args:
            trend_direction: "bullish" (ищем вход в Discount) или "bearish" (ищем вход в Premium).
            
        Returns:
            Кортеж (min_price, max_price) целевой зоны, или None если тренд неизвестен.
        """
        if trend_direction == "bullish":
            # В бычьем тренде ищем покупку в Discount (между 0.5 и 0.618 обычно, или до 0.786)
            # Возвращаем зону между 50% и 78.6%
            lower = self.swing_low
            upper = self.equilibrium_price
            # Более консервативно: зона OTE
            ote_level = self.get_fib_price(0.618)
            return (lower, ote_level) # Упрощенно: весь дисконт
            
        elif trend_direction == "bearish":
            # В медвежьем тренде ищем продажу в Premium
            ote_level = self.get_fib_price(0.618)
            return (ote_level, self.swing_high)
            
        return None

    def get_fib_price(self, ratio: float) -> Decimal:
        """
        Возвращает цену для конкретного коэффициента Фибоначчи.
        
        Args:
            ratio: Коэффициент (0.0 - 1.0).
            
        Returns:
            Цена уровня.
        """
        if not (0.0 <= ratio <= 1.0):
            raise ValueError("Ratio must be between 0 and 1")
        
        range_size = self.swing_high - self.swing_low
        return self.swing_low + (range_size * Decimal(str(ratio)))

    def get_ratio_for_price(self, price: Decimal) -> float:
        """
        Возвращает коэффициент Фибоначчи для заданной цены.
        
        Args:
            price: Цена.
            
        Returns:
            Коэффициент (0.0 - 1.0).
        """
        p = Decimal(str(price))
        range_size = self.swing_high - self.swing_low
        
        if range_size == 0:
            return 0.0
            
        ratio = (p - self.swing_low) / range_size
        return float(ratio)

    def invalidate(self) -> None:
        """Помечает зону как невалидную (например, если цена обновила максимум/минимум)."""
        self.is_valid = False
        logger.debug(f"PD Zone {self.id} invalidated.")

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "swing_high": str(self.swing_high),
            "swing_low": str(self.swing_low),
            "equilibrium_price": str(self.equilibrium_price),
            "is_valid": self.is_valid,
            "created_at": self.created_at.isoformat(),
            "fib_levels": [
                {"ratio": lvl.ratio, "price": str(lvl.price), "label": lvl.label}
                for lvl in self.fib_levels
            ],
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PremiumDiscountZone:
        """Десериализация из словаря."""
        try:
            swing_high = Decimal(str(data["swing_high"]))
            swing_low = Decimal(str(data["swing_low"]))
            created_str = data.get("created_at")
            created_at = datetime.fromisoformat(created_str) if created_str else datetime.now(timezone.utc)
            
            fib_data = data.get("fib_levels", [])
            fib_levels = [
                FibonacciLevel(
                    ratio=float(lvl["ratio"]),
                    price=Decimal(str(lvl["price"])),
                    label=lvl.get("label", "")
                )
                for lvl in fib_data
            ]
            
            instance = cls(
                id=data.get("id", str(uuid.uuid4())),
                swing_high=swing_high,
                swing_low=swing_low,
                created_at=created_at,
                fib_levels=fib_levels,
                is_valid=bool(data.get("is_valid", True)),
                meta=data.get("meta", {})
            )
            # Пересчет равновесия вручную, так как field(init=False)
            object.__setattr__(instance, 'equilibrium_price', instance.equilibrium_price)
            
            return instance
        except Exception as e:
            raise ValueError(f"Ошибка парсинга PremiumDiscountZone: {e}")

    def __str__(self) -> str:
        return f"PDZone(High: {self.swing_high}, Low: {self.swing_low}, Equilibrium: {self.equilibrium_price}, Valid: {self.is_valid})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PremiumDiscountZone):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)