"""
SMC Trading Engine - Premium & Discount Zone Engine.

Этот модуль отвечает за разделение ценового диапазона текущего свинга (Swing High/Low)
на зоны Premium (дорого) и Discount (дешево) с использованием уровней Фибоначчи.

Логика работы:
1. Определение текущего свинга (High и Low тренда).
2. Расчет уровней Фибоначчи (0, 0.5, 1 и др.).
3. Классификация цены:
   - Выше 0.5 -> Premium (зона для продаж/шортов).
   - Ниже 0.5 -> Discount (зона для покупок/лонгов).
   - Равно 0.5 -> Equilibrium (равновесие).
4. Генерация объектов зон для использования в стратегии.

Гарантии:
- Использование Decimal для всех ценовых расчетов.
- Строгая типизация.
- Обработка инверсии тренда (смена High/Low местами).
"""

import logging
from typing import List, Optional, Dict, Any, Tuple
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass

# Import Models
from src.models.premium_discount import PremiumDiscountZone, PDZoneType, FibonacciLevel

# Import Utils
from src.utils.helpers import to_decimal, round_price

logger = logging.getLogger(__name__)


@dataclass
class PDAnalysisResult:
    """Результат анализа зон Premium/Discount."""
    swing_high: Decimal
    swing_low: Decimal
    current_price: Decimal
    zone_type: PDZoneType
    fib_levels: List[FibonacciLevel]
    active_zone: Optional[PremiumDiscountZone]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "swing_high": str(self.swing_high),
            "swing_low": str(self.swing_low),
            "current_price": str(self.current_price),
            "zone_type": self.zone_type.value,
            "fib_levels": [lvl.to_dict() for lvl in self.fib_levels],
            "active_zone": self.active_zone.to_dict() if self.active_zone else None
        }


class PremiumDiscountEngine:
    """
    Движок для расчета и анализа зон Premium и Discount.
    
    Основные задачи:
    - Расчет сетки Фибоначчи на заданном диапазоне.
    - Определение текущей позиции цены относительно зон.
    - Создание объектов зон для визуализации и торговли.
    """

    def __init__(self, fib_levels: List[float], pd_threshold: float = 0.5):
        """
        Инициализация движка.
        
        Args:
            fib_levels: Список уровней Фибоначчи (например, [0.0, 0.382, 0.5, 0.618, 1.0]).
                        Значения должны быть в диапазоне [0, 1].
            pd_threshold: Порог разделения на Premium/Discount. Обычно 0.5 (50%).
        """
        if not fib_levels:
            raise ValueError("Список уровней Фибоначчи не может быть пустым")
        
        # Валидация уровней
        validated_levels = []
        for level in fib_levels:
            if not (0.0 <= level <= 1.0):
                raise ValueError(f"Уровень Фибоначчи {level} выходит за пределы [0, 1]")
            validated_levels.append(level)
        
        # Сортировка и удаление дубликатов
        self.fib_levels = sorted(list(set(validated_levels)))
        
        if not (0.0 <= pd_threshold <= 1.0):
            raise ValueError("Порог PD зоны должен быть между 0.0 и 1.0")
            
        self.pd_threshold = Decimal(str(pd_threshold))
        
        logger.info(f"PremiumDiscountEngine initialized: levels={self.fib_levels}, threshold={pd_threshold}")

    # -------------------------------------------------------------------------
    # Основной метод расчета
    # -------------------------------------------------------------------------

    def calculate_zone(
        self,
        high: Decimal,
        low: Decimal,
        current_price: Decimal
    ) -> PremiumDiscountZone:
        """
        Рассчитывает зону Premium/Discount на основе заданного диапазона и текущей цены.
        
        Логика:
        - Если цена > 50% диапазона -> Premium.
        - Если цена < 50% диапазона -> Discount.
        - Возвращает объект зоны, в которой находится цена.
        
        Args:
            high: Максимум свинга (Swing High).
            low: Минимум свинга (Swing Low).
            current_price: Текущая цена рынка.
            
        Returns:
            Объект PremiumDiscountZone с деталями о текущей зоне.
        """
        high = to_decimal(high)
        low = to_decimal(low)
        current_price = to_decimal(current_price)
        
        if high == low:
            logger.warning("High и Low равны, невозможно рассчитать зоны. Возвращаем Neutral.")
            return PremiumDiscountZone(
                high=high,
                low=low,
                zone_type=PDZoneType.EQUILIBRIUM,
                start_price=high,
                end_price=low,
                fibonacci_level=0.5,
                created_at=datetime.utcnow()
            )
        
        # Нормализация: high всегда должен быть больше low
        if high < low:
            high, low = low, high
            
        range_size = high - low
        
        # Расчет текущей позиции в процентах (0.0 to 1.0)
        # Формула: (Price - Low) / (High - Low)
        position_ratio = (current_price - low) / range_size
        
        # Определение типа зоны
        zone_type = PDZoneType.EQUILIBRIUM
        if position_ratio > self.pd_threshold:
            zone_type = PDZoneType.PREMIUM
        elif position_ratio < self.pd_threshold:
            zone_type = PDZoneType.DISCOUNT
            
        # Находим ближайший уровень Фибоначчи для точности
        closest_level = self._find_closest_fib_level(float(position_ratio))
        
        # Создаем объект зоны
        # Для Premium: зона от 0.5 до 1.0 (или до следующего уровня выше)
        # Для Discount: зона от 0.0 до 0.5 (или до следующего уровня ниже)
        # Здесь мы возвращаем зону, соответствующую текущему положению цены относительно порога
        
        zone_start = low
        zone_end = high
        
        if zone_type == PDZoneType.PREMIUM:
            # Зона Premium начинается от порога (0.5) и идет до High
            threshold_price = low + (range_size * self.pd_threshold)
            zone_start = threshold_price
            zone_end = high
        elif zone_type == PDZoneType.DISCOUNT:
            # Зона Discount идет от Low до порога (0.5)
            threshold_price = low + (range_size * self.pd_threshold)
            zone_start = low
            zone_end = threshold_price
            
        return PremiumDiscountZone(
            high=zone_end,
            low=zone_start,
            zone_type=zone_type,
            start_price=zone_start,
            end_price=zone_end,
            fibonacci_level=float(closest_level),
            created_at=datetime.utcnow()
        )

    def get_full_fibonacci_grid(
        self,
        high: Decimal,
        low: Decimal
    ) -> List[FibonacciLevel]:
        """
        Генерирует полную сетку уровней Фибоначчи для заданного диапазона.
        
        Args:
            high: Максимум свинга.
            low: Минимум свинга.
            
        Returns:
            Список объектов FibonacciLevel с рассчитанными ценами.
        """
        high = to_decimal(high)
        low = to_decimal(low)
        
        if high < low:
            high, low = low, high
            
        range_size = high - low
        levels = []
        
        for ratio in self.fib_levels:
            price = low + (range_size * Decimal(str(ratio)))
            level_obj = FibonacciLevel(
                ratio=ratio,
                price=float(price),
                label=f"{int(ratio * 100)}%"
            )
            levels.append(level_obj)
            
        return levels

    def analyze(
        self,
        high: Decimal,
        low: Decimal,
        current_price: Decimal
    ) -> PDAnalysisResult:
        """
        Полный анализ состояния рынка относительно зон PD.
        Возвращает детальную структуру с текущей зоной и всеми уровнями.
        """
        high = to_decimal(high)
        low = to_decimal(low)
        current_price = to_decimal(current_price)
        
        if high < low:
            high, low = low, high
            
        # Расчет текущей активной зоны
        active_zone = self.calculate_zone(high, low, current_price)
        
        # Генерация полной сетки
        fib_grid = self.get_full_fibonacci_grid(high, low)
        
        # Определение общего типа зоны для текущей цены
        zone_type = active_zone.zone_type
        
        return PDAnalysisResult(
            swing_high=high,
            swing_low=low,
            current_price=current_price,
            zone_type=zone_type,
            fib_levels=fib_grid,
            active_zone=active_zone
        )

    # -------------------------------------------------------------------------
    # Вспомогательные методы
    # -------------------------------------------------------------------------

    def _find_closest_fib_level(self, ratio: float) -> float:
        """Находит ближайший уровень Фибоначчи к заданному соотношению."""
        if not self.fib_levels:
            return 0.5
            
        return min(self.fib_levels, key=lambda x: abs(x - ratio))

    def is_in_premium(self, price: Decimal, high: Decimal, low: Decimal) -> bool:
        """Проверяет, находится ли цена в зоне Premium."""
        zone = self.calculate_zone(high, low, price)
        return zone.zone_type == PDZoneType.PREMIUM

    def is_in_discount(self, price: Decimal, high: Decimal, low: Decimal) -> bool:
        """Проверяет, находится ли цена в зоне Discount."""
        zone = self.calculate_zone(high, low, price)
        return zone.zone_type == PDZoneType.DISCOUNT

    def get_equilibrium_price(self, high: Decimal, low: Decimal) -> Decimal:
        """Возвращает цену равновесия (50% уровня)."""
        high = to_decimal(high)
        low = to_decimal(low)
        if high < low:
            high, low = low, high
        return low + (high - low) * self.pd_threshold

    def reset(self):
        """Сброс состояния (движок stateless, метод для совместимости)."""
        logger.debug("PremiumDiscountEngine reset called.")