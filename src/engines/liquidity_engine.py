"""
SMC Trading Engine - Liquidity Engine.

Этот модуль отвечает за идентификацию зон ликвидности (Liquidity Pools) на основе
свинговых экстремумов (Pivot Points) и детектирование их пробоя (Sweep).

Ликвидность в SMC:
- BSL (Buy Side Liquidity): Стоп-лоссы шортистов над максимумами.
- SSL (Sell Side Liquidity): Стоп-лоссы быков под минимумами.

Алгоритм:
1. При появлении нового пивота создается соответствующий пул ликвидности.
2. При движении цены проверяется касание или пробой уровня пула.
3. Если цена пробивает уровень более чем на threshold (и закрывается там или уходит дальше),
   пул помечается как "Swept".
4. Старые пулы могут архивироваться, если они больше не актуальны для текущего тренда.
"""

import logging
from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import datetime

from src.models.candle import Candle
from src.models.pivot import PivotPoint, PivotType
from src.models.liquidity import LiquidityPool, LiquidityType, LiquidityStatus
from src.models.market_structure import MarketTrend

logger = logging.getLogger(__name__)


class LiquidityEngine:
    """
    Движок анализа ликвидности.
    
    Управляет коллекцией активных пулов ликвидности и определяет моменты их снятия.
    """

    def __init__(self, sweep_threshold: float = 0.001, lookback_bars: int = 50):
        """
        Инициализация движка.
        
        Args:
            sweep_threshold: Минимальный процент пробоя уровня для подтверждения сквиза (напр. 0.001 = 0.1%).
            lookback_bars: Количество баров назад для поиска актуальных свингов (если не переданы явно).
        """
        if sweep_threshold < 0:
            raise ValueError("sweep_threshold не может быть отрицательным")
        if lookback_bars <= 0:
            raise ValueError("lookback_bars должен быть положительным")
            
        self.sweep_threshold = Decimal(str(sweep_threshold))
        self.lookback_bars = lookback_bars
        
        # Внутренний реестр пулов (опционально, если не передаются извне)
        self._internal_pools: List[LiquidityPool] = []

    def update_pools(
        self, 
        pivots: List[PivotPoint], 
        existing_pools: List[LiquidityPool], 
        trend: MarketTrend
    ) -> List[LiquidityPool]:
        """
        Обновляет список пулов ликвидности на основе новых пивотов.
        
        Логика:
        - Новый Pivot High -> Создает BSL пул выше него.
        - Новый Pivot Low -> Создает SSL пул ниже него.
        - Пулы, противонаправленные сильному тренду, могут помечаться как менее приоритетные (но не удаляются).
        
        Args:
            pivots: Список новых подтвержденных пивотов.
            existing_pools: Текущий список активных пулов (ссылка на объект сервиса).
            trend: Текущий глобальный тренд.
            
        Returns:
            Список newly_created пулов.
        """
        new_pools = []
        
        for pivot in pivots:
            # Определяем тип ликвидности на основе типа пивота
            liq_type = LiquidityType.BSL if pivot.pivot_type == PivotType.HIGH else LiquidityType.SSL
            
            # Уровень ликвидности находится чуть за экстремумом пивота
            # Для BSL: уровень = High + epsilon (или просто High, так как стоп стоит ЗА хаем)
            # Для SSL: уровень = Low - epsilon
            # В данной реализации берем точное значение пивота как границу зоны.
            level = pivot.price
            
            # Проверка на дубликаты (если пивот уже был обработан ранее)
            exists = any(
                abs(p.level - level) < Decimal('0.00000001') and p.type == liq_type
                for p in existing_pools
            )
            
            if not exists:
                pool = LiquidityPool(
                    type=liq_type,
                    level=level,
                    source_pivot_id=pivot.id,
                    created_at=pivot.timestamp,
                    status=LiquidityStatus.ACTIVE
                )
                existing_pools.append(pool)
                new_pools.append(pool)
                logger.debug(f"Created new {liq_type.value} pool at {level} from pivot {pivot.id}")
        
        # Очистка старых неактивных пулов (оптимизация)
        # Оставляем только активные и недавно снятые
        active_count = sum(1 for p in existing_pools if p.status == LiquidityStatus.ACTIVE)
        if active_count > 100: # Лимит на количество отслеживаемых уровней
            # Удаляем самые старые снятые пулы
            swept_pools = [p for p in existing_pools if p.status == LiquidityStatus.SWEPT]
            if swept_pools:
                # Сортируем по времени обновления и удаляем старые
                swept_pools.sort(key=lambda x: x.updated_at or datetime.min)
                to_remove = swept_pools[:20]
                for p in to_remove:
                    existing_pools.remove(p)

        return new_pools

    def check_sweeps(
        self, 
        candle: Candle, 
        pools: List[LiquidityPool]
    ) -> List[LiquidityPool]:
        """
        Проверяет, были ли пробиты (сняты) какие-либо пулы ликвидности текущей свечой.
        
        Критерии Sweep:
        1. Цена High свечи превысила уровень BSL пула.
        2. Цена Low свечи опустилась ниже уровня SSL пула.
        3. Пробой должен быть значимым: (High - Level) / Level > threshold.
           ИЛИ цена закрылась за уровнем (подтверждение).
           
        В данной реализации используем комбинированный подход:
        - Касание фитилем + отскок часто считается сбором ликвидности.
        - Но для надежности требуем, чтобы цена ушла за уровень хотя бы на threshold 
          ИЛИ закрылась за ним.
        
        Args:
            candle: Текущая обрабатываемая свеча.
            pools: Список активных пулов ликвидности.
            
        Returns:
            Список пулов, которые были сняты этой свечой.
        """
        swept_pools = []
        
        high = candle.high
        low = candle.low
        close = candle.close
        
        for pool in pools:
            if pool.status != LiquidityStatus.ACTIVE:
                continue
            
            is_swept = False
            sweep_price = None
            
            if pool.type == LiquidityType.BSL:
                # Проверка Buy Side Liquidity (над максимумом)
                # Цена должна пойти выше уровня
                if high > pool.level:
                    # Вычисляем глубину пробоя
                    depth = (high - pool.level) / pool.level
                    
                    # Условие пробоя: либо глубина > threshold, либо закрытие выше уровня
                    if depth >= self.sweep_threshold or close > pool.level:
                        is_swept = True
                        sweep_price = high

            elif pool.type == LiquidityType.SSL:
                # Проверка Sell Side Liquidity (под минимумом)
                if low < pool.level:
                    depth = (pool.level - low) / pool.level
                    
                    if depth >= self.sweep_threshold or close < pool.level:
                        is_swept = True
                        sweep_price = low
            
            if is_swept:
                pool.mark_as_swept(price=sweep_price, timestamp=candle.timestamp)
                swept_pools.append(pool)
                logger.info(f"Liquidity Swept! Type: {pool.type.value}, Level: {pool.level}, Price: {sweep_price}")
        
        return swept_pools

    def get_nearest_liquidity(
        self, 
        current_price: Decimal, 
        pools: List[LiquidityPool], 
        limit: int = 5
    ) -> Tuple[List[LiquidityPool], List[LiquidityPool]]:
        """
        Возвращает ближайшие пулы ликвидности сверху и снизу от текущей цены.
        Полезно для определения целей движения цены (Take Profit).
        
        Args:
            current_price: Текущая рыночная цена.
            pools: Список пулов.
            limit: Максимальное количество возвращаемых пулов для каждой стороны.
            
        Returns:
            (nearest_above, nearest_below) - отсортированные списки.
        """
        above = []
        below = []
        
        for pool in pools:
            if pool.status != LiquidityStatus.ACTIVE:
                continue
                
            if pool.level > current_price:
                above.append(pool)
            elif pool.level < current_price:
                below.append(pool)
        
        # Сортировка: сверху - по возрастанию (ближайший первый), снизу - по убыванию
        above.sort(key=lambda x: x.level)
        below.sort(key=lambda x: x.level, reverse=True)
        
        return above[:limit], below[:limit]