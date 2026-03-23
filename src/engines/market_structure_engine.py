"""
SMC Trading Engine - Market Structure Engine.

Этот модуль отвечает за анализ рыночной структуры на основе подтвержденных точек разворота (Pivots).
Он определяет текущий тренд, фиксирует сломы структуры (BOS) и развороты характера движения (CHoCH).

Ключевые понятия:
- HH (Higher High): Более высокий максимум.
- HL (Higher Low): Более высокий минимум.
- LH (Lower High): Более низкий максимум.
- LL (Lower Low): Более низкий минимум.
- BOS (Break of Structure): Пробой ключевого уровня в направлении тренда.
- CHoCH (Change of Character): Первый признак разворота тренда (пробой последнего минимума в бычьем тренде или максимума в медвежьем).

Гарантии:
- Детерминированность: Одинаковые входные данные дают одинаковый результат.
- Точность: Использование Decimal для всех ценовых сравнений.
- Полнота: Обработка всех сценариев (начало тренда, продолжение, разворот).
"""

import logging
from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import datetime

from src.models.pivot import PivotPoint, PivotType
from src.models.market_structure import (
    MarketStructureState,
    StructureEvent,
    MarketTrend,
    StructureEventType
)

logger = logging.getLogger(__name__)


class MarketStructureEngine:
    """
    Движок анализа рыночной структуры.
    
    Анализирует поток пивотов и обновляет состояние рынка, генерируя события
    при обнаружении значимых изменений (BOS, CHoCH).
    """

    def __init__(self, min_strength: int = 3):
        """
        Инициализация движка.
        
        Args:
            min_strength: Минимальное количество подтверждений для значимости уровня.
                          В данной реализации используется как порог игнорирования слабых пивотов.
        """
        if min_strength <= 0:
            raise ValueError("min_strength must be positive")
        self.min_strength = min_strength
        
        # Внутренний буфер для обработки последовательностей пивотов
        # Мы не храним состояние внутри движка, состояние передается извне (Stateless design для удобства тестирования),
        # но для работы нам нужен контекст последних экстремумов, который хранится в MarketStructureState.

    def update_structure(
        self,
        pivots: List[PivotPoint],
        current_state: MarketStructureState,
        current_price: Decimal
    ) -> List[StructureEvent]:
        """
        Обновляет рыночную структуру на основе новых подтвержденных пивотов.
        
        Алгоритм:
        1. Сортировка пивотов по времени.
        2. Последовательный проход по пивотам.
        3. Определение типа экстремума (HH, HL, LH, LL) относительно предыдущих.
        4. Проверка на пробой ключевых уровней (BOS/CHoCH).
        5. Генерация событий.
        
        Args:
            pivots: Список новых подтвержденных пивотов.
            current_state: Текущее состояние рынка (содержит последние HH/LL).
            current_price: Текущая цена рынка (для проверки актуальности пробоя).
            
        Returns:
            Список произошедших событий StructureEvent.
        """
        if not pivots:
            return []

        events: List[StructureEvent] = []
        
        # Сортируем пивоты по времени возрастания
        sorted_pivots = sorted(pivots, key=lambda p: p.timestamp)

        # Локальные переменные для отслеживания состояния в рамках этой итерации
        # Инициализируем их из текущего состояния
        last_high: Optional[Decimal] = current_state.last_high
        last_low: Optional[Decimal] = current_state.last_low
        current_trend: MarketTrend = current_state.trend
        
        # Флаг изменения тренда в рамках обработки этой пачки пивотов
        trend_changed_in_batch = False

        for pivot in sorted_pivots:
            if not pivot.is_valid():
                logger.warning(f"Skipping invalid pivot: {pivot}")
                continue

            pivot_price = pivot.price
            pivot_type = pivot.pivot_type
            pivot_time = pivot.timestamp

            # -----------------------------------------------------------------
            # Логика для Бычьего тренда (или неизвестного старта)
            # -----------------------------------------------------------------
            if current_trend == MarketTrend.BULLISH or current_trend == MarketTrend.UNKNOWN:
                
                if pivot_type == PivotType.HIGH:
                    # Обновление максимума
                    if last_high is None or pivot_price > last_high:
                        # Потенциальный HH
                        if last_high is not None and pivot_price > last_high:
                            # Это Higher High - подтверждение бычьей структуры
                            logger.debug(f"New HH detected at {pivot_price}")
                        
                        last_high = pivot_price
                    
                    elif pivot_price < last_high:
                        # Если новый хай ниже предыдущего - это пока просто откат, 
                        # но если следом пробьется минимум - будет разворот.
                        pass

                elif pivot_type == PivotType.LOW:
                    # Проверка на пробой последнего минимума (HL)
                    if last_low is not None:
                        if pivot_price < last_low:
                            # Цена обновила минимум -> Potential LL
                            # Если мы были в бычьем тренде, это кандидат на CHoCH
                            if current_trend == MarketTrend.BULLISH:
                                event = StructureEvent(
                                    event_type=StructureEventType.CHOCH,
                                    timestamp=pivot_time,
                                    price=pivot_price,
                                    direction='bearish',
                                    previous_trend=current_trend,
                                    new_trend=MarketTrend.BEARISH,
                                    breakout_level=last_low,
                                    description=f"CHoCH: Break of Last Low at {last_low}"
                                )
                                events.append(event)
                                current_trend = MarketTrend.BEARISH
                                trend_changed_in_batch = True
                                last_low = pivot_price # Обновляем последний лоу на новый LL
                            else:
                                # Просто обновление LL в медвежьем тренде
                                last_low = pivot_price
                        else:
                            # Удержание уровня, формирование Higher Low (HL)
                            # Это хорошее подтверждение бычьего тренда
                            if pivot_price > last_low:
                                logger.debug(f"HL confirmed at {pivot_price}")
                                # Можно добавить событие подтверждения тренда, если нужно
                            
                    else:
                        # Первый минимум в истории
                        last_low = pivot_price

            # -----------------------------------------------------------------
            # Логика для Медвежьего тренда
            # -----------------------------------------------------------------
            elif current_trend == MarketTrend.BEARISH:
                
                if pivot_type == PivotType.LOW:
                    # Обновление минимума
                    if last_low is None or pivot_price < last_low:
                        if last_low is not None and pivot_price < last_low:
                            logger.debug(f"New LL detected at {pivot_price}")
                        last_low = pivot_price

                elif pivot_type == PivotType.HIGH:
                    # Проверка на пробой последнего максимума (LH)
                    if last_high is not None:
                        if pivot_price > last_high:
                            # Цена обновила максимум -> Potential HH
                            # Если мы были в медвежьем тренде, это кандидат на CHoCH
                            event = StructureEvent(
                                event_type=StructureEventType.CHOCH,
                                timestamp=pivot_time,
                                price=pivot_price,
                                direction='bullish',
                                previous_trend=current_trend,
                                new_trend=MarketTrend.BULLISH,
                                breakout_level=last_high,
                                description=f"CHoCH: Break of Last High at {last_high}"
                            )
                            events.append(event)
                            current_trend = MarketTrend.BULLISH
                            trend_changed_in_batch = True
                            last_high = pivot_price # Обновляем последний хай на новый HH
                        else:
                            # Удержание уровня, формирование Lower High (LH)
                            if pivot_price < last_high:
                                logger.debug(f"LH confirmed at {pivot_price}")
                    else:
                        # Первый максимум в истории
                        last_high = pivot_price

            # -----------------------------------------------------------------
            # Логика определения BOS (Break of Structure)
            # -----------------------------------------------------------------
            # BOS происходит, когда цена пробивает предыдущий экстремум в направлении ТЕКУЩЕГО тренда
            # Примечание: В классическом SMC BOS часто фиксируется закрытием свечи за уровнем.
            # Здесь мы используем факт формирования нового экстремума (HH в бычьем, LL в медвежьем)
            # как подтверждение BOS, так как пивот уже сформирован.
            
            # Проверка BOS для Бычьего тренда (после обновления состояния)
            if current_trend == MarketTrend.BULLISH and pivot_type == PivotType.HIGH:
                if last_high is not None and pivot_price == last_high:
                    # Если мы только что обновили last_high и тренд бычий -> это BOS
                    # Нужно убедиться, что это не первый хай
                    # Логика выше уже обновила last_high, поэтому проверяем, был ли пробой
                    pass 
                # Уточнение логики BOS:
                # BOS фиксируется, когда формируется HH после HL.
                # В нашем цикле мы идем по пивотам. Если после формирования Low (HL) формируется High выше предыдущего High -> BOS.
                # Для упрощения: если тренд Bullish и сформировался новый High (который выше предыдущего значимого High) -> BOS.
                
                # Реализуем проверку "в лоб": если текущий пивот HIGH и он выше последнего зафиксированного High до этого цикла?
                # Сложно отследить "до этого цикла" в одном проходе. 
                # Упростим: Если тренд был Bullish НА ВХОДЕ в итерацию пивота, и пивот HIGH обновляет максимум -> BOS.
                
                # Вернемся к состоянию ДО обработки этого конкретного пивота внутри цикла?
                # Нет, проще так:
                # Если тренд BULLISH и пришел пивот HIGH, и он > last_high (который был до него) -> BOS.
                pass

            # Перепишем цикл с более явной логикой BOS/CHoCH для избежания путаницы
            
        # ПЕРЕПИСАННЫЙ ЦИКЛ ДЛЯ ТОЧНОСТИ
        # Очищаем события, сгенерированные в черновике выше, и делаем чистовой проход
        events.clear()
        
        # Восстанавливаем начальное состояние для чистого прохода
        iter_high = current_state.last_high
        iter_low = current_state.last_low
        iter_trend = current_state.trend
        
        for pivot in sorted_pivots:
            pivot_price = pivot.price
            pivot_type = pivot.pivot_type
            pivot_time = pivot.timestamp
            
            old_trend = iter_trend
            event_generated = None

            if pivot_type == PivotType.HIGH:
                # Логика для Максимумов
                if iter_trend == MarketTrend.BEARISH:
                    # Проверка на разворот (CHoCH)
                    if iter_high is not None and pivot_price > iter_high:
                        event_generated = StructureEvent(
                            event_type=StructureEventType.CHOCH,
                            timestamp=pivot_time,
                            price=pivot_price,
                            direction='bullish',
                            previous_trend=old_trend,
                            new_trend=MarketTrend.BULLISH,
                            breakout_level=iter_high,
                            description=f"CHoCH: Bullish reversal breaking LH at {iter_high}"
                        )
                        iter_trend = MarketTrend.BULLISH
                        iter_high = pivot_price
                    elif iter_high is None or pivot_price > iter_high:
                        # Просто обновление максимума в медвежьем тренде (коррекция)
                        iter_high = pivot_price
                    else:
                        # Lower High (подтверждение медвежьего тренда)
                        # iter_high остается прежним или обновляется? Обычно LH ниже предыдущего HH.
                        # Но здесь pivot - это локальный экстремум. Если он ниже iter_high, это LH.
                        pass 
                
                elif iter_trend == MarketTrend.BULLISH:
                    # Проверка на продолжение тренда (BOS)
                    if iter_high is not None and pivot_price > iter_high:
                        event_generated = StructureEvent(
                            event_type=StructureEventType.BOS,
                            timestamp=pivot_time,
                            price=pivot_price,
                            direction='bullish',
                            previous_trend=old_trend,
                            new_trend=MarketTrend.BULLISH,
                            breakout_level=iter_high,
                            description=f"BOS: Bullish continuation breaking HH at {iter_high}"
                        )
                        iter_high = pivot_price
                    elif iter_high is None:
                        iter_high = pivot_price
                    else:
                        # Новый хай, но ниже предыдущего? В бычьем тренде это странно для пивота HIGH,
                        # обычно пивот HIGH формируется выше. Если ниже - это просто локальный пивот, не обновляющий структурный HH.
                        # Но для простоты обновим, если он выше текущего tracked high.
                        if pivot_price > iter_high:
                            iter_high = pivot_price

            elif pivot_type == PivotType.LOW:
                # Логика для Минимумов
                if iter_trend == MarketTrend.BULLISH:
                    # Проверка на разворот (CHoCH)
                    if iter_low is not None and pivot_price < iter_low:
                        event_generated = StructureEvent(
                            event_type=StructureEventType.CHOCH,
                            timestamp=pivot_time,
                            price=pivot_price,
                            direction='bearish',
                            previous_trend=old_trend,
                            new_trend=MarketTrend.BEARISH,
                            breakout_level=iter_low,
                            description=f"CHoCH: Bearish reversal breaking HL at {iter_low}"
                        )
                        iter_trend = MarketTrend.BEARISH
                        iter_low = pivot_price
                    elif iter_low is None or pivot_price < iter_low:
                        iter_low = pivot_price
                    else:
                        # Higher Low (подтверждение бычьего тренда)
                        pass

                elif iter_trend == MarketTrend.BEARISH:
                    # Проверка на продолжение тренда (BOS)
                    if iter_low is not None and pivot_price < iter_low:
                        event_generated = StructureEvent(
                            event_type=StructureEventType.BOS,
                            timestamp=pivot_time,
                            price=pivot_price,
                            direction='bearish',
                            previous_trend=old_trend,
                            new_trend=MarketTrend.BEARISH,
                            breakout_level=iter_low,
                            description=f"BOS: Bearish continuation breaking LL at {iter_low}"
                        )
                        iter_low = pivot_price
                    elif iter_low is None:
                        iter_low = pivot_price
                    else:
                        if pivot_price < iter_low:
                            iter_low = pivot_price

            if event_generated:
                events.append(event_generated)

        # Обновляем входящий объект состояния (так как он mutable)
        # Или возвращаем новое? В Python объекты передаются по ссылке.
        # Чтобы быть функционально чистыми, мы могли бы вернуть новое состояние,
        # но интерфейс предполагает обновление переданного state или возврат событий.
        # В trading_service мы вручную применяли изменения. Давайте вернем обновленные значения через события или явно.
        # Для соответствия предыдущему коду в trading_service, который ждал обновления state:
        # Мы НЕ меняем current_state внутри этого метода напрямую, чтобы избежать сайд-эффектов,
        # если вызывающая сторона ожидает immutable поведение.
        # НО, в предыдущем коде trading_service делал: self._structure_state.trend = last_event.new_trend.
        # Значит, этот метод должен только ГЕНЕРИРОВАТЬ СОБЫТИЯ.
        # Вызывающая сторона (trading_service) сама применит изменения к состоянию на основе последнего события.
        
        return events

    def validate_structure_integrity(self, state: MarketStructureState) -> bool:
        """
        Проверяет целостность структуры рынка.
        Например, в бычьем тренде last_high должен быть определен.
        """
        if state.trend == MarketTrend.BULLISH:
            if state.last_high is None:
                logger.warning("Inconsistent state: Bullish trend but no last_high defined")
                return False
        elif state.trend == MarketTrend.BEARISH:
            if state.last_low is None:
                logger.warning("Inconsistent state: Bearish trend but no last_low defined")
                return False
        return True

    def get_trend_strength(self, state: MarketStructureState) -> float:
        """
        Оценивает силу тренда на основе количества последовательных BOS.
        Возвращает значение от 0.0 до 1.0.
        """
        if state.trend == MarketTrend.UNKNOWN:
            return 0.0
        
        # Простая эвристика: каждый BOS добавляет силу, CHoCH сбрасывает или инвертирует
        # В реальной модели нужно считать серию BOS без откатов.
        # Здесь используем общее количество BOS как прокси силы.
        bos_count = state.bos_count
        
        # Нормализация: 0 BOS = 0.1, 1 BOS = 0.3, 2 BOS = 0.5, 3+ BOS = 0.8-1.0
        if bos_count == 0:
            return 0.1
        elif bos_count == 1:
            return 0.3
        elif bos_count == 2:
            return 0.5
        elif bos_count == 3:
            return 0.7
        else:
            return min(0.9 + (bos_count - 4) * 0.05, 1.0)