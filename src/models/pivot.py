from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid

class PivotType(str, Enum):
    """
    Тип точки разворота (свинга).
    
    HIGH: Локальный максимум (Swing High).
    LOW: Локальный минимум (Swing Low).
    """
    HIGH = "high"
    LOW = "low"

@dataclass(frozen=False)  # frozen=False необходим для обновления счетчика касаний (touched_count)
class PivotPoint:
    """
    Модель точки разворота (Pivot Point / Swing High/Low).
    
    Представляет собой ключевую точку на графике, где цена развернулась, 
    сформировав локальный экстремум. Используется для построения рыночной структуры.
    
    Атрибуты:
        id: Уникальный идентификатор пивота (UUID).
        pivot_type: Тип пивота (HIGH или LOW).
        timestamp: Время формирования экстремума (время свечи).
        price: Цена экстремума (High для HIGH, Low для LOW).
        candle_index: Индекс свечи в исходном массиве данных, на которой сформировался пивот.
        strength: Сила пивота. Количество баров слева и справа, которые были пробиты/подтверждены.
        is_confirmed: Флаг подтверждения. Если False, пивот может исчезнуть при поступлении новых данных (repaint).
        touched_count: Количество раз, когда цена возвращалась к этому уровню после формирования.
        meta: Словарь для хранения дополнительных метаданных.
    """
    pivot_type: PivotType
    timestamp: datetime
    price: Decimal
    candle_index: int
    strength: int = 5
    is_confirmed: bool = True
    touched_count: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """
        Валидация данных после инициализации объекта.
        """
        # Валидация цены
        if not isinstance(self.price, Decimal):
            try:
                self.price = Decimal(str(self.price))
            except (InvalidOperation, ValueError):
                raise ValueError(f"Некорректное значение цены пивота: {self.price}")
        
        if self.price <= 0:
            raise ValueError(f"Цена пивота должна быть положительной. Получено: {self.price}")

        # Валидация силы
        if self.strength < 1:
            raise ValueError(f"Сила пивота (strength) должна быть >= 1. Получено: {self.strength}")

        # Валидация индекса
        if self.candle_index < 0:
            raise ValueError(f"Индекс свечи не может быть отрицательным. Получено: {self.candle_index}")

        # Нормализация времени до UTC
        if self.timestamp.tzinfo is not None:
            self.timestamp = self.timestamp.astimezone(timezone.utc)

    @property
    def is_high(self) -> bool:
        """Проверка, является ли пивот максимумом (Swing High)."""
        return self.pivot_type == PivotType.HIGH

    @property
    def is_low(self) -> bool:
        """Проверка, является ли пивот минимумом (Swing Low)."""
        return self.pivot_type == PivotType.LOW

    def increment_touch(self) -> None:
        """
        Увеличивает счетчик касаний уровня ликвидности.
        Вызывается движком LiquidityEngine, когда цена тестирует этот уровень.
        """
        # Так как frozen=False, мы можем изменять атрибуты напрямую
        self.touched_count += 1

    def add_meta(self, key: str, value: Any) -> None:
        """Добавляет метаданные в словарь meta."""
        self.meta[key] = value

    def get_distance_to_price(self, price: Decimal) -> Decimal:
        """
        Вычисляет абсолютное расстояние между ценой пивота и заданной ценой.
        
        Args:
            price: Текущая цена для сравнения.
            
        Returns:
            Расстояние в виде Decimal.
        """
        p = Decimal(str(price))
        return abs(self.price - p)

    def is_within_tolerance(self, price: Decimal, tolerance_percent: Decimal) -> bool:
        """
        Проверяет, находится ли заданная цена в допустимом отклонении (tolerance) от цены пивота.
        Используется для определения касания уровней ликвидности.
        
        Args:
            price: Проверяемая цена.
            tolerance_percent: Допустимое отклонение в процентах (например, Decimal('0.001') для 0.1%).
            
        Returns:
            True, если цена попадает в диапазон допуска.
        """
        p = Decimal(str(price))
        tol = Decimal(str(tolerance_percent))
        
        diff = abs(self.price - p)
        threshold = self.price * tol
        
        return diff <= threshold

    def to_dict(self) -> Dict[str, Any]:
        """
        Сериализует объект в словарь.
        
        Returns:
            Словарь с данными пивота.
        """
        return {
            "id": self.id,
            "type": self.pivot_type.value,
            "timestamp": self.timestamp.isoformat(),
            "price": str(self.price),
            "candle_index": self.candle_index,
            "strength": self.strength,
            "is_confirmed": self.is_confirmed,
            "touched_count": self.touched_count,
            "meta": self.meta
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PivotPoint:
        """
        Десериализует объект из словаря.
        
        Args:
            data: Словарь с данными.
            
        Returns:
            Экземпляр PivotPoint.
        """
        try:
            # Парсинг типа
            pivot_type_str = data.get("type", "high")
            pivot_type = PivotType(pivot_type_str)

            # Парсинг времени
            ts_str = data.get("timestamp")
            if isinstance(ts_str, str):
                if ts_str.endswith('Z'):
                    ts_str = ts_str[:-1] + '+00:00'
                timestamp = datetime.fromisoformat(ts_str)
            elif isinstance(ts_str, datetime):
                timestamp = ts_str
            else:
                raise ValueError("Неверный формат timestamp")

            # Конвертация чисел
            price = Decimal(str(data["price"]))
            strength = int(data.get("strength", 5))
            candle_index = int(data["candle_index"])
            touched_count = int(data.get("touched_count", 0))
            is_confirmed = bool(data.get("is_confirmed", True))
            meta = data.get("meta", {})
            pivot_id = data.get("id", str(uuid.uuid4()))

            return cls(
                id=pivot_id,
                pivot_type=pivot_type,
                timestamp=timestamp,
                price=price,
                candle_index=candle_index,
                strength=strength,
                is_confirmed=is_confirmed,
                touched_count=touched_count,
                meta=meta
            )
        except KeyError as e:
            raise ValueError(f"Отсутствует обязательное поле в данных пивота: {e}")
        except Exception as e:
            raise ValueError(f"Ошибка парсинга данных пивота: {e}")

    def __str__(self) -> str:
        type_str = "HIGH" if self.is_high else "LOW"
        return f"Pivot({type_str} @ {self.price} | {self.timestamp.strftime('%Y-%m-%d %H:%M')} | Strength: {self.strength})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PivotPoint):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)