"""
SMC Trading Engine - Fair Value Gap (FVG) Detection Engine.

Этот модуль отвечает за идентификацию зон дисбаланса цены (Fair Value Gaps),
также известных как Imbalance или Inefficiency.

Логика работы:
1. Анализ паттерна из 3 свечей.
2. Бычий FVG: Low 3-й свечи > High 1-й свечи. Разрыв между ними - зона покупки.
3. Медвежий FVG: High 3-й свечи < Low 1-й свечи. Разрыв между ними - зона продажи.
4. Отслеживание статуса: Pending -> Filled (частично или полностью) -> Inverted (опционально).
5. Расчет точки равновесия (Consequent Encroachment - 50% уровня).

Гарантии:
- Использование Decimal для всех ценовых расчетов.
- Строгая типизация.
- Обработка частичного заполнения (Partial Fill).
- Поддержка инверсии FVG при сильном пробое.
"""

import logging
from typing import List, Optional, Tuple, Dict, Any
from decimal import Decimal
from datetime import datetime
import copy

# Import Models
from src.models.candle import Candle
from src.models.fvg import FairValueGap, FVGStatus, FVGType
from src.models.market_structure import MarketTrend

# Import Utils
from src.utils.helpers import to_decimal, is_within_tolerance

logger = logging.getLogger(__name__)


class FVGEngine:
    """
    Движок для обнаружения и управления Fair Value Gaps.
    
    Основные задачи:
    - Сканирование свечей на наличие 3-барных паттернов дисбаланса.
    - Фильтрация слабых FVG по размеру.
    - Мониторинг цены на предмет заполнения (Fill) зоны.
    - Логика инверсии (если цена пробивает FVG и закрепляется за ним).
    """

    def __init__(self, min_size_pct: float = 0.0005, inversion_enabled: bool = True):
        """
        Инициализация движка.
        
        Args:
            min_size_pct: Минимальный размер FVG в процентах от цены (фильтр шума).
                          Например, 0.0005 = 0.05%.
            inversion_enabled: Если True, FVG может изменить направление при сильном пробое.
        """
        if min_size_pct < 0:
            raise ValueError("min_size_pct не может быть отрицательным")
            
        self.min_size_pct = min_size_pct
        self.inversion_enabled = inversion_enabled
        
        # Кэш активных FVG для оптимизации (чтобы не искать каждый раз заново, если не нужно)
        # Но в потоковом режиме мы обычно добавляем новые и проверяем старые.
        self._active_fvgs: List[FairValueGap] = []

        logger.info(f"FVGEngine initialized: min_size={min_size_pct}, inversion={inversion_enabled}")

    # -------------------------------------------------------------------------
    # Основной метод обнаружения (Detection)
    # -------------------------------------------------------------------------
    
    def detect_fvgs(self, candles: List[Candle]) -> List[FairValueGap]:
        """
        Сканирует список свечей на наличие новых Fair Value Gaps.
        
        Алгоритм:
        - Проход по свечам начиная с индекса 2 (нужно минимум 3 свечи: i-2, i-1, i).
        - Проверка условия разрыва между i-2 и i.
        - Валидация размера разрыва.
        - Создание объекта FVG.
        
        Args:
            candles: Список свечей (минимум 3). Рекомендуется передавать последние N свечей.
            
        Returns:
            Список новых объектов FairValueGap.
        """
        if len(candles) < 3:
            return []

        new_fvgs = []
        
        # Проверяем только последнюю сформировавшуюся тройку свечей, 
        # так как предыдущие уже были проверены в прошлых итерациях.
        # Индекс последней свечи: len(candles) - 1
        # Нас интересует паттерн, где "текущая" свеча (i) замыкает разрыв с (i-2).
        
        i = len(candles) - 1
        candle_current = candles[i]
        candle_prev = candles[i-1]
        candle_prev2 = candles[i-2]
        
        # Проверка на валидность данных
        if not all([candle_current.is_valid(), candle_prev.is_valid(), candle_prev2.is_valid()]):
            return []

        # Преобразование в Decimal для точности
        c1_high = to_decimal(candle_prev2.high)
        c1_low = to_decimal(candle_prev2.low)
        
        # c2 (middle) нам нужен только для направления тренда внутри паттерна, но формально FVG определяется c1 и c3
        c2_open = to_decimal(candle_prev.open)
        c2_close = to_decimal(candle_prev.close)
        
        c3_high = to_decimal(candle_current.high)
        c3_low = to_decimal(candle_current.low)

        # --- Проверка на Бычий FVG (Bullish Imbalance) ---
        # Условие: Low 3-й свечи > High 1-й свечи
        # Цена резко ушла вверх, оставив гэп внизу.
        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            gap_size_pct = gap_size / c1_high
            
            if gap_size_pct >= self.min_size_pct:
                # Расчет зоны
                zone_low = c1_high
                zone_high = c3_low
                
                # Consequent Encroachment (CE) - 50% уровня
                ce_price = zone_low + (gap_size / Decimal('2'))
                
                fvg = FairValueGap(
                    high=zone_high,
                    low=zone_low,
                    timestamp=candle_current.timestamp, # Время формирования (закрытия 3-й свечи)
                    direction='bullish',
                    status=FVGStatus.PENDING,
                    consequential_encroachment=ce_price,
                    mitigation_price=None,
                    filled_at=None
                )
                
                # Проверка на дублирование (вдруг мы уже создали такой же в прошлый тик, если свеча обновлялась)
                if not self._is_duplicate_fvg(fvg, new_fvgs):
                    new_fvgs.append(fvg)
                    logger.debug(f"Bullish FVG detected at {candle_current.timestamp} [{zone_low} - {zone_high}]")

        # --- Проверка на Медвежий FVG (Bearish Imbalance) ---
        # Условие: High 3-й свечи < Low 1-й свечи
        # Цена резко ушла вниз, оставив гэп вверху.
        elif c3_high < c1_low:
            gap_size = c1_low - c3_high
            gap_size_pct = gap_size / c1_low
            
            if gap_size_pct >= self.min_size_pct:
                # Расчет зоны
                zone_high = c1_low
                zone_low = c3_high
                
                # Consequent Encroachment (CE) - 50% уровня
                ce_price = zone_high - (gap_size / Decimal('2'))
                
                fvg = FairValueGap(
                    high=zone_high,
                    low=zone_low,
                    timestamp=candle_current.timestamp,
                    direction='bearish',
                    status=FVGStatus.PENDING,
                    consequential_encroachment=ce_price,
                    mitigation_price=None,
                    filled_at=None
                )
                
                if not self._is_duplicate_fvg(fvg, new_fvgs):
                    new_fvgs.append(fvg)
                    logger.debug(f"Bearish FVG detected at {candle_current.timestamp} [{zone_low} - {zone_high}]")

        return new_fvgs

    # -------------------------------------------------------------------------
    # Метод проверки заполнения (Monitoring)
    # -------------------------------------------------------------------------
    
    def check_fills(
        self,
        candle: Candle,
        fvgs: List[FairValueGap]
    ) -> List[FairValueGap]:
        """
        Проверяет текущую свечу на взаимодействие с активными FVG.
        
        Логика:
        - Если цена коснулась зоны FVG -> статус меняется на FILLED (или PARTIALLY_FILLED).
        - Если цена полностью прошла сквозь FVG и закрылась за ним -> возможна инверсия.
        
        Args:
            candle: Текущая обрабатываемая свеча.
            fvgs: Список всех известных FVG (метод вернет только те, чей статус изменился).
            
        Returns:
            Список FVG, которые были заполнены или изменили статус.
        """
        updated_fvgs = []
        current_high = to_decimal(candle.high)
        current_low = to_decimal(candle.low)
        current_close = to_decimal(candle.close)

        for fvg in fvgs:
            # Пропускаем уже полностью обработанные, если они не поддерживают инверсию
            if fvg.status == FVGStatus.FILLED and not self.inversion_enabled:
                continue
            if fvg.status == FVGStatus.INVALIDATED:
                continue

            fvg_high = to_decimal(fvg.high)
            fvg_low = to_decimal(fvg.low)
            fvg_ce = to_decimal(fvg.consequential_encroachment)

            is_touched = False
            is_filled_completely = False
            is_inverted = False
            
            touch_price = None

            # --- Логика для Бычьего FVG ---
            if fvg.direction == 'bullish':
                # Ожидаем снижения цены в зону.
                # Касание: Low свечи <= High FVG (зашли в зону сверху)
                if current_low <= fvg_high:
                    is_touched = True
                    touch_price = min(fvg_high, current_high) # Фактическая точка входа/касания

                    # Проверка на полное прохождение (закрытие ниже Low FVG)
                    if current_close < fvg_low:
                        is_filled_completely = True
                        
                        # Проверка на инверсию (сильное движение вниз сквозь поддержку)
                        if self.inversion_enabled and fvg.status != FVGStatus.INVERTED:
                            # Если цена закрылась значительно ниже FVG, он может стать медвежьим
                            # Критерий инверсии: закрытие ниже FVG Low
                            is_inverted = True

            # --- Логика для Медвежьего FVG ---
            elif fvg.direction == 'bearish':
                # Ожидаем роста цены в зону.
                # Касание: High свечи >= Low FVG (зашли в зону снизу)
                if current_high >= fvg_low:
                    is_touched = True
                    touch_price = max(fvg_low, current_low)

                    # Проверка на полное прохождение (закрытие выше High FVG)
                    if current_close > fvg_high:
                        is_filled_completely = True
                        
                        # Проверка на инверсию
                        if self.inversion_enabled and fvg.status != FVGStatus.INVERTED:
                            is_inverted = True

            # --- Обновление статусов ---
            
            if is_inverted:
                fvg.status = FVGStatus.INVERTED
                fvg.mitigation_price = current_close
                fvg.filled_at = candle.timestamp
                updated_fvgs.append(fvg)
                logger.info(f"FVG {fvg.id} INVERTED at {candle.timestamp}. New trend opposite.")
                
            elif is_filled_completely:
                # Если еще не был заполнен полностью
                if fvg.status != FVGStatus.FILLED:
                    fvg.status = FVGStatus.FILLED
                    fvg.mitigation_price = touch_price
                    fvg.filled_at = candle.timestamp
                    updated_fvgs.append(fvg)
                    logger.debug(f"FVG {fvg.id} FILLED completely at {candle.timestamp}.")
                    
            elif is_touched:
                # Частичное касание. В некоторых стратегиях это триггер на вход.
                # Статус можно оставить PENDING или поменять на TOUCHED, если бы такой статус был.
                # В нашей модели PENDING означает "ожидает касания". После касания он считается "отработанным" (Filled).
                # Для строгости: если коснулись, считаем его сработавшим (Filled), даже если не прошли насквозь.
                # Трейдеры часто входят именно при первом касании (Limit Order).
                if fvg.status == FVGStatus.PENDING:
                    fvg.status = FVGStatus.FILLED
                    fvg.mitigation_price = touch_price
                    fvg.filled_at = candle.timestamp
                    updated_fvgs.append(fvg)
                    logger.debug(f"FVG {fvg.id} TOUCHED/FILLED at {candle.timestamp}. Price: {touch_price}")

        return updated_fvgs
    
    # -------------------------------------------------------------------------
    # Вспомогательные методы (Helpers & Utils)
    # -------------------------------------------------------------------------

    def _is_duplicate_fvg(self, new_fvg: FairValueGap, existing_fvgs: List[FairValueGap]) -> bool:
        """
        Проверяет, не является ли новый FVG дубликатом уже найденного в текущем батче.
        Дубликатом считается FVG с тем же временем и направлением.
        """
        for fvg in existing_fvgs:
            if fvg.timestamp == new_fvg.timestamp and fvg.direction == new_fvg.direction:
                return True
            # Также проверяем перекрытие зон на том же баре (редкий кейс)
            if fvg.timestamp == new_fvg.timestamp:
                if fvg.low == new_fvg.low and fvg.high == new_fvg.high:
                    return True
        return False

    def get_active_fvgs(self, fvgs: List[FairValueGap]) -> List[FairValueGap]:
        """
        Фильтрует список, оставляя только активные (PENDING) FVG.
        """
        return [f for f in fvgs if f.status == FVGStatus.PENDING]

    def get_nearest_fvg(
        self, 
        fvgs: List[FairValueGap], 
        current_price: Decimal, 
        direction: str
    ) -> Optional[FairValueGap]:
        """
        Находит ближайший активный FVG в направлении движения цены.
        
        Args:
            fvgs: Список FVG.
            current_price: Текущая цена.
            direction: 'long' (ищем снизу) или 'short' (ищем сверху).
            
        Returns:
            Ближайший объект FVG или None.
        """
        active_fvgs = self.get_active_fvgs(fvgs)
        if not active_fvgs:
            return None
            
        nearest = None
        min_distance = Decimal('Infinity')

        for fvg in active_fvgs:
            distance = Decimal('Infinity')
            
            if direction == 'long':
                # Ищем FVG ниже текущей цены (поддержка)
                if to_decimal(fvg.high) < current_price:
                    distance = current_price - to_decimal(fvg.high)
            elif direction == 'short':
                # Ищем FVG выше текущей цены (сопротивление)
                if to_decimal(fvg.low) > current_price:
                    distance = to_decimal(fvg.low) - current_price
            
            if distance < min_distance:
                min_distance = distance
                nearest = fvg
                
        return nearest

    def calculate_fvg_refinements(self, fvg: FairValueGap) -> Dict[str, Decimal]:
        """
        Рассчитывает дополнительные уровни внутри FVG для точного входа.
        - 0% (Open): Граница зоны.
        - 50% (CE): Consequent Encroachment.
        - 100% (Close): Дальняя граница.
        
        Returns:
            Словарь с уровнями.
        """
        low = to_decimal(fvg.low)
        high = to_decimal(fvg.high)
        range_size = abs(high - low)
        
        return {
            "boundary_0": low if fvg.direction == 'bullish' else high,
            "consequent_encroachment_50": fvg.consequential_encroachment,
            "boundary_100": high if fvg.direction == 'bullish' else low,
            "range_size": range_size
        }

    def reset(self):
        """Сброс внутреннего кэша (если используется)."""
        self._active_fvgs.clear()
        logger.info("FVGEngine internal cache reset.")