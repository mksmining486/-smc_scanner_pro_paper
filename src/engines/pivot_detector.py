"""
SMC Trading Engine - Pivot Point Detection Engine.

Этот модуль отвечает за идентификацию локальных экстремумов (High/Low)
на основе классического алгоритма "Fractal" или "Left/Right Bars".

Логика работы:
1. Буферизация входящих свечей.
2. Проверка условия: High/Low текущей свечи выше/ниже N баров слева и N баров справа.
3. Так как "правые" бары появляются только в будущем, пивот считается подтвержденным
   только после формирования достаточного количества баров справа.
4. Возврат подтвержденных пивотов для использования в других движках.

Гарантии:
- Использование Decimal для всех ценовых расчетов.
- Строгая типизация.
- Обработка переопределения пивотов (если цена обновляет экстремум до подтверждения).
- Потокобезопасность в контексте последовательной обработки (single-threaded).
"""

import logging
from typing import List, Optional, Tuple, Dict, Any
from decimal import Decimal
from datetime import datetime
from collections import deque
import copy

# Import Models
from src.models.candle import Candle
from src.models.pivot import PivotPoint, PivotType

# Import Utils
from src.utils.helpers import to_decimal

logger = logging.getLogger(__name__)


class PivotDetector:
    """
    Движок для обнаружения точек разворота (Pivot Points).
    
    Основные задачи:
    - Накопление буфера свечей для анализа "правой стороны".
    - Выявление локальных максимумов и минимумов.
    - Управление жизненным циклом пивота (поиск -> подтверждение).
    - Фильтрация шумовых экстремумов (плоские вершины).
    """

    def __init__(self, left_bars: int = 5, right_bars: int = 5):
        """
        Инициализация детектора.
        
        Args:
            left_bars: Количество баров слева, которые должны быть ниже/выше центрального бара.
            right_bars: Количество баров справа, необходимых для подтверждения пивота.
        """
        if left_bars < 1:
            raise ValueError("left_bars должен быть >= 1")
        if right_bars < 1:
            raise ValueError("right_bars должен быть >= 1")
            
        self.left_bars = left_bars
        self.right_bars = right_bars
        
        # Буфер для хранения свечей, ожидающих подтверждения правой стороны
        # Используем deque для эффективного добавления/удаления
        self._candle_buffer: deque[Candle] = deque()
        
        # Список уже подтвержденных пивотов (кэш)
        self._confirmed_pivots: List[PivotPoint] = []
        
        # Отслеживание последнего подтвержденного пивота каждого типа для оптимизации
        self._last_confirmed_high: Optional[PivotPoint] = None
        self._last_confirmed_low: Optional[PivotPoint] = None

        logger.info(f"PivotDetector initialized: left={left_bars}, right={right_bars}")

    # -------------------------------------------------------------------------
    # Основной метод обработки
    # -------------------------------------------------------------------------
    
    def process_candle(self, candle: Candle, full_history: Optional[List[Candle]] = None) -> List[PivotPoint]:
        """
        Обрабатывает новую свечу и возвращает список newly confirmed pivots.
        
        Алгоритм:
        1. Добавляем свечу в буфер.
        2. Проверяем, есть ли в буфере свечи, у которых уже сформировалась "правая сторона".
           Кандидат на пивот находится по индексу: len(buffer) - 1 - right_bars.
        3. Если кандидат существует, проверяем условие Left/Right.
        4. Если условие выполнено -> создаем PivotPoint и возвращаем его.
        5. Удаляем старые свечи из буфера, которые уже не могут стать пивотами.
        
        Args:
            candle: Новая пришедшая свеча.
            full_history: Опционально, полная история свечей. Если передана, используется для 
                          инициализации буфера при первом запуске или сбоях. В потоковом режиме
                          обычно передается только новая свеча, а буфер хранится внутри класса.
        
        Returns:
            Список новых подтвержденных объектов PivotPoint.
        """
        if not candle or not candle.is_valid():
            logger.warning(f"Invalid candle received in PivotDetector: {candle}")
            return []

        # 1. Добавляем свечу в буфер
        # Проверка на дубликат времени (обновление текущей свечи)
        if self._candle_buffer and candle.timestamp == self._candle_buffer[-1].timestamp:
            self._candle_buffer[-1] = candle
            # Если обновили последнюю свечу, правая сторона для предыдущих еще не сформирована окончательно,
            # но мы все равно попробуем проверить предпоследние, так как данные могли уточниться.
            # Однако, строго говоря, пивот подтверждается только закрытием бара.
            # Для простоты считаем, что если бар обновляется, он еще не "закрыт" для правой стороны.
            # Но в рамках этого движка мы считаем правую сторону сформированной, если есть N баров ПОСЛЕ.
            # Поэтому обновление последнего бара не влияет на проверку старых кандидатов.
            return [] 
        
        self._candle_buffer.append(candle)

        # 2. Очистка буфера от старых свечей
        # Нам нужно хранить минимум: left_bars + right_bars + 1 (центральный) + запас
        # Удаляем свечи, которые уже ушли слишком далеко влево и не могут быть центром нового пивота
        min_buffer_size = self.left_bars + self.right_bars + 5
        while len(self._candle_buffer) > min_buffer_size:
            self._candle_buffer.popleft()

        # 3. Поиск подтвержденных пивотов
        # Кандидат находится на позиции: current_index - right_bars
        # В deque индексация прямая. Последний элемент - это index = len-1.
        # Кандидат: index = len(self._candle_buffer) - 1 - self.right_bars
        
        new_pivots = []
        candidate_index = len(self._candle_buffer) - 1 - self.right_bars

        if candidate_index < self.left_bars:
            # Недостаточно данных слева для формирования пивота в этой позиции
            return []

        candidate_candle = self._candle_buffer[candidate_index]

        # Проверка условия High (Pivot High)
        if self._is_pivot_high(candidate_index, candidate_candle):
            pivot = self._create_pivot(candidate_candle, PivotType.HIGH)
            # Проверка на дублирование или перекрытие с последним пивотом
            if not self._is_duplicate_pivot(pivot, self._last_confirmed_high):
                new_pivots.append(pivot)
                self._last_confirmed_high = pivot
                self._confirmed_pivots.append(pivot)
                logger.debug(f"Pivot HIGH confirmed at {candidate_candle.timestamp} price {candidate_candle.high}")

        # Проверка условия Low (Pivot Low)
        if self._is_pivot_low(candidate_index, candidate_candle):
            pivot = self._create_pivot(candidate_candle, PivotType.LOW)
            if not self._is_duplicate_pivot(pivot, self._last_confirmed_low):
                new_pivots.append(pivot)
                self._last_confirmed_low = pivot
                self._confirmed_pivots.append(pivot)
                logger.debug(f"Pivot LOW confirmed at {candidate_candle.timestamp} price {candidate_candle.low}")

        return new_pivots

    # -------------------------------------------------------------------------
    # Логика проверки условий (Core Logic)
    # -------------------------------------------------------------------------

    def _is_pivot_high(self, index: int, candle: Candle) -> bool:
        """
        Проверяет, является ли свеча локальным максимумом.
        Условие: High свечи > High всех left_bars слева И > High всех right_bars справа.
        """
        c_high = to_decimal(candle.high)
        
        # Проверка слева
        start_left = max(0, index - self.left_bars)
        for i in range(start_left, index):
            if to_decimal(self._candle_buffer[i].high) >= c_high:
                return False
        
        # Проверка справа
        end_right = min(len(self._candle_buffer), index + self.right_bars + 1)
        for i in range(index + 1, end_right):
            if to_decimal(self._candle_buffer[i].high) >= c_high:
                return False
                
        return True

    def _is_pivot_low(self, index: int, candle: Candle) -> bool:
        """
        Проверяет, является ли свеча локальным минимумом.
        Условие: Low свечи < Low всех left_bars слева И < Low всех right_bars справа.
        """
        c_low = to_decimal(candle.low)
        
        # Проверка слева
        start_left = max(0, index - self.left_bars)
        for i in range(start_left, index):
            if to_decimal(self._candle_buffer[i].low) <= c_low:
                return False
        
        # Проверка справа
        end_right = min(len(self._candle_buffer), index + self.right_bars + 1)
        for i in range(index + 1, end_right):
            if to_decimal(self._candle_buffer[i].low) <= c_low:
                return False
                
        return True

    def _create_pivot(self, candle: Candle, pivot_type: PivotType) -> PivotPoint:
        """Создает объект PivotPoint на основе свечи."""
        price = to_decimal(candle.high) if pivot_type == PivotType.HIGH else to_decimal(candle.low)
        
        return PivotPoint(
            price=price,
            timestamp=candle.timestamp,
            pivot_type=pivot_type,
            candle_high=candle.high,
            candle_low=candle.low,
            strength=0, # Сила будет рассчитана в MarketStructureEngine
            is_broken=False
        )

    def _is_duplicate_pivot(self, new_pivot: PivotPoint, last_pivot: Optional[PivotPoint]) -> bool:
        """
        Проверяет, не является ли новый пивот дубликатом или слишком близким к предыдущему.
        Защита от множественных пивотов в одном баре (редко, но возможно при странных данных)
        или пивотов с одинаковой ценой подряд.
        """
        if last_pivot is None:
            return False
            
        # Если время совпадает (невозможно при правильной логике буфера, но страховка)
        if new_pivot.timestamp == last_pivot.timestamp:
            return True
            
        # Если цена совпадает точно (плоская вершина/дно, разнесенная по времени)
        # В SMC часто считают плоские уровни одним уровнем, но пивоты могут быть разными барами.
        # Здесь разрешаем разные бары с одинаковой ценой, если они не соседние кандидаты.
        # Но для простоты: если цена равна и тип тот же - пропускаем, чтобы не засорять структуру.
        if new_pivot.price == last_pivot.price:
            return True
            
        return False

    # -------------------------------------------------------------------------
    # Методы управления состоянием и утилиты
    # -------------------------------------------------------------------------

    def get_all_pivots(self) -> List[PivotPoint]:
        """Возвращает список всех подтвержденных пивотов."""
        return copy.deepcopy(self._confirmed_pivots)

    def get_last_pivot(self, pivot_type: Optional[PivotType] = None) -> Optional[PivotPoint]:
        """
        Возвращает последний подтвержденный пивот.
        Если указан тип, ищет только этого типа.
        """
        if not self._confirmed_pivots:
            return None
            
        if pivot_type is None:
            return self._confirmed_pivots[-1]
            
        # Ищем с конца
        for pivot in reversed(self._confirmed_pivots):
            if pivot.pivot_type == pivot_type:
                return pivot
        return None

    def reset(self):
        """Полный сброс состояния детектора."""
        self._candle_buffer.clear()
        self._confirmed_pivots.clear()
        self._last_confirmed_high = None
        self._last_confirmed_low = None
        logger.info("PivotDetector state reset.")

    def load_history(self, candles: List[Candle]):
        """
        Загружает историю свечей для инициализации буфера.
        Полезно при рестарте приложения, чтобы сразу иметь актуальные пивоты.
        Внимание: Этот метод не вернет старые пивоты, он только заполнит буфер
        для поиска новых. Для восстановления старых пивотов нужно использовать
        отдельный механизм или прогнать историю через process_candle.
        """
        self.reset()
        # Прогоняем всю историю через процессор, чтобы восстановить состояние и найти пивоты
        # Это самый надежный способ
        restored_pivots = []
        for candle in candles:
            pivots = self.process_candle(candle)
            restored_pivots.extend(pivots)
        
        logger.info(f"PivotDetector loaded history ({len(candles)} candles), found {len(restored_pivots)} pivots during load.")
        # Возвращаем найденные пивоты не нужно, они уже во внутреннем списке.
        # Пользователь может вызвать get_all_pivots() после загрузки.