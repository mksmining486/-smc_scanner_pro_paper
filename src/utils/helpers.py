"""
SMC Trading Engine - Utility Helpers.

Этот модуль содержит набор низкоуровневых вспомогательных функций,
используемых во всем проекте для обеспечения точности вычислений,
работы с данными и времени.

Ключевые особенности:
- Все финансовые вычисления используют decimal.Decimal для исключения ошибок float.
- Функции чистые (pure) там, где это возможно, без скрытого состояния.
- Строгая типизация и обработка краевых случаев (division by zero, empty lists).
- Поддержка работы с часовыми поясами и торговыми сессиями.
"""

import math
import logging
from typing import List, Dict, Any, Optional, Tuple, Union, Iterable
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, time, timezone
import pytz

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Константы и Типы
# -------------------------------------------------------------------------

# Допуск для сравнения чисел с плавающей точкой (если вдруг придется использовать float)
FLOAT_EPSILON = 1e-9

# Стандартные уровни Фибоначчи по умолчанию
DEFAULT_FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

# Торговые сессии (время UTC)
SESSIONS = {
    "sydney": (time(21, 0), time(6, 0)),
    "tokyo": (time(0, 0), time(9, 0)),
    "london": (time(7, 0), time(16, 0)),
    "new_york": (time(13, 0), time(22, 0)),
}

# -------------------------------------------------------------------------
# Работа с Decimal (Точная арифметика)
# -------------------------------------------------------------------------

def to_decimal(value: Union[int, float, str, Decimal, None], default: Decimal = Decimal('0')) -> Decimal:
    """
    Безопасно конвертирует значение в Decimal.
    
    Args:
        value: Входное значение (int, float, str, Decimal).
        default: Значение по умолчанию, если input None или невалиден.
        
    Returns:
        Decimal значение.
    """
    if value is None:
        return default
    
    if isinstance(value, Decimal):
        return value
        
    try:
        if isinstance(value, float):
            # Конвертация float в str перед Decimal для сохранения точности
            return Decimal(str(value))
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as e:
        logger.warning(f"Failed to convert {value} to Decimal: {e}. Using default {default}")
        return default


def round_price(price: Union[float, Decimal], precision: int = 8) -> Decimal:
    """
    Округляет цену до заданного количества знаков после запятой.
    Использует банковское округление (ROUND_HALF_UP).
    
    Args:
        price: Цена для округления.
        precision: Количество знаков после запятой.
        
    Returns:
        Округленное Decimal значение.
    """
    d_price = to_decimal(price)
    quantize_str = '0.' + '0' * precision if precision > 0 else '1'
    return d_price.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)


def safe_divide(numerator: Union[float, Decimal], denominator: Union[float, Decimal], default: Decimal = Decimal('0')) -> Decimal:
    """
    Безопасное деление. Возвращает default при делении на ноль.
    
    Args:
        numerator: Числитель.
        denominator: Знаменатель.
        default: Значение при ошибке деления.
        
    Returns:
        Результат деления или default.
    """
    num = to_decimal(numerator)
    den = to_decimal(denominator)
    
    if den == 0:
        return default
        
    try:
        return num / den
    except InvalidOperation:
        return default


def calculate_percentage_change(old_value: Union[float, Decimal], new_value: Union[float, Decimal]) -> Decimal:
    """
    Рассчитывает процентное изменение между двумя значениями.
    Формула: ((New - Old) / Old) * 100
    
    Returns:
        Процентное изменение (Decimal). 0 если старое значение 0.
    """
    old = to_decimal(old_value)
    new = to_decimal(new_value)
    
    if old == 0:
        return Decimal('0')
        
    change = new - old
    pct = (change / old) * Decimal('100')
    return pct


def clamp(value: Union[float, Decimal], min_val: Union[float, Decimal], max_val: Union[float, Decimal]) -> Decimal:
    """
    Ограничивает значение диапазоном [min_val, max_val].
    """
    v = to_decimal(value)
    mn = to_decimal(min_val)
    mx = to_decimal(max_val)
    
    if mn > mx:
        mn, mx = mx, mn
        
    if v < mn:
        return mn
    if v > mx:
        return mx
    return v

# -------------------------------------------------------------------------
# Сравнение и Валидация
# -------------------------------------------------------------------------

def is_within_tolerance(val1: Union[float, Decimal], val2: Union[float, Decimal], tolerance: Union[float, Decimal, str] = "0.0001") -> bool:
    """
    Проверяет, находятся ли два значения в пределах допустимого отклонения (tolerance).
    Полезно для сравнения цен, где float ошибки недопустимы.
    
    Args:
        val1: Первое значение.
        val2: Второе значение.
        tolerance: Допустимая разница (абсолютная).
        
    Returns:
        True если |val1 - val2| <= tolerance.
    """
    v1 = to_decimal(val1)
    v2 = to_decimal(val2)
    tol = to_decimal(tolerance)
    
    return abs(v1 - v2) <= tol


def is_price_near_level(price: Union[float, Decimal], level: Union[float, Decimal], tolerance_pct: float = 0.001) -> bool:
    """
    Проверяет, находится ли цена близко к уровню в процентах от цены.
    
    Args:
        price: Текущая цена.
        level: Целевой уровень.
        tolerance_pct: Допуск в процентах (например, 0.001 = 0.1%).
        
    Returns:
        True если цена в пределах допуска от уровня.
    """
    p = to_decimal(price)
    l = to_decimal(level)
    
    if p == 0 or l == 0:
        return is_within_tolerance(p, l, Decimal('0.00000001'))
        
    diff = abs(p - l)
    threshold = p * to_decimal(tolerance_pct)
    
    return diff <= threshold


def validate_candle_data(open_p: float, high: float, low: float, close: float) -> bool:
    """
    Валидирует базовую логику OHLC свечи.
    Условия:
    - High >= Low
    - High >= Open и High >= Close
    - Low <= Open и Low <= Close
    - Цены >= 0
    
    Returns:
        True если данные валидны.
    """
    if any(x < 0 for x in [open_p, high, low, close]):
        return False
        
    h = to_decimal(high)
    l = to_decimal(low)
    o = to_decimal(open_p)
    c = to_decimal(close)
    
    if h < l:
        return False
    if h < o or h < c:
        return False
    if l > o or l > c:
        return False
        
    return True

# -------------------------------------------------------------------------
# Математические и Финансовые Утилиты
# -------------------------------------------------------------------------

def fibonacci_levels(start: Union[float, Decimal], end: Union[float, Decimal], levels: List[float] = DEFAULT_FIB_LEVELS) -> Dict[float, Decimal]:
    """
    Рассчитывает уровни Фибоначчи между двумя точками.
    
    Args:
        start: Начальное значение (обычно минимум).
        end: Конечное значение (обычно максимум).
        levels: Список уровней Фибоначчи (например, [0.0, 0.382, 0.618, 1.0]).
        
    Returns:
        Словарь {уровень: значение}.
    """
    s = to_decimal(start)
    e = to_decimal(end)
    diff = e - s
    
    result = {}
    for level in levels:
        fib_decimal = to_decimal(level)
        value = s + diff * fib_decimal
        result[level] = value
        
    return result


def calculate_true_range(high: Union[float, Decimal], low: Union[float, Decimal], close: Union[float, Decimal], prev_close: Optional[Union[float, Decimal]]) -> Decimal:
    """
    Рассчитывает True Range (TR) для одной свечи.
    TR = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
    """
    h = to_decimal(high)
    l = to_decimal(low)
    c = to_decimal(close)
    
    tr1 = h - l
    
    if prev_close is None:
        return tr1
        
    pc = to_decimal(prev_close)
    tr2 = abs(h - pc)
    tr3 = abs(l - pc)
    
    return max(tr1, tr2, tr3)


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Decimal:
    """
    Рассчитывает Average True Range (ATR) за указанный период.
    ATR = SMA(TrueRange)
    
    Args:
        highs: Список максимальных цен.
        lows: Список минимальных цен.
        closes: Список закрытий.
        period: Период для расчета.
        
    Returns:
        Среднее значение ATR.
    """
    if len(highs) != len(lows) or len(lows) != len(closes):
        raise ValueError("Длины списков должны совпадать.")
        
    if len(closes) < period:
        return Decimal('0')
    
    tr_values = []
    prev_close = None
    for i in range(len(highs)):
        high = to_decimal(highs[i])
        low = to_decimal(lows[i])
        close = to_decimal(closes[i])
        
        tr = calculate_true_range(high, low, close, prev_close)
        tr_values.append(tr)
        prev_close = close
    
    # Выбираем последние N значений TR
    last_tr = tr_values[-period:]
    atr = sum(last_tr) / len(last_tr)
    return atr


def calculate_drawdown(peak: Union[float, Decimal], current: Union[float, Decimal]) -> Decimal:
    """
    Рассчитывает просадку в процентах.
    Drawdown = ((Peak - Current) / Peak) * 100
    
    Args:
        peak: Пиковая цена.
        current: Текущая цена.
        
    Returns:
        Процент просадки (всегда положительный).
    """
    p = to_decimal(peak)
    c = to_decimal(current)
    
    if p <= 0:
        return Decimal('0')
        
    dd = (p - c) / p * Decimal('100')
    return max(dd, Decimal('0'))


def calculate_risk_reward(entry: Union[float, Decimal], stop_loss: Union[float, Decimal], take_profit: Union[float, Decimal]) -> Optional[Decimal]:
    """
    Рассчитывает соотношение риск/прибыль.
    RR = (TakeProfit - Entry) / (Entry - StopLoss)
    
    Args:
        entry: Цена входа.
        stop_loss: Цена стоп-лосса.
        take_profit: Цена тейк-профита.
        
    Returns:
        Соотношение риск/прибыль или None если деление на ноль.
    """
    e = to_decimal(entry)
    sl = to_decimal(stop_loss)
    tp = to_decimal(take_profit)
    
    risk = abs(e - sl)
    reward = abs(tp - e)
    
    if risk == 0:
        return None
        
    return reward / risk

# -------------------------------------------------------------------------
# Работа со Временем
# -------------------------------------------------------------------------

def ensure_utc(dt: datetime) -> datetime:
    """
    Приводит datetime к UTC, если не имеет часового пояса.
    """
    if dt.tzinfo is None:
        # Предполагаем, что время в UTC, если tzinfo нет
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Конвертируем в UTC
        return dt.astimezone(timezone.utc)


def get_current_session(dt: datetime) -> Optional[str]:
    """
    Определяет текущую торговую сессию по времени (UTC).
    
    Args:
        dt: Время в любом часовом поясе (будет приведено к UTC).
        
    Returns:
        Название сессии ('sydney', 'tokyo', 'london', 'new_york') или None.
    """
    utc_dt = ensure_utc(dt)
    current_time = utc_dt.time()
    
    # Для сессий, которые пересекают полночь UTC (например, sydney)
    # нужно проверить, находится ли время в интервале, который охватывает 2 дня
    for session_name, (start, end) in SESSIONS.items():
        if start <= end:
            # Обычный случай: начало < конец
            if start <= current_time <= end:
                return session_name
        else:
            # Случай, когда сессия пересекает полночь UTC (start > end)
            if current_time >= start or current_time <= end:
                return session_name
                
    return None


def is_market_open(dt: datetime, market_timezone: str = 'UTC') -> bool:
    """
    Простая проверка, открыт ли рынок в определенное время.
    Использует фиктивные сессии из SESSIONS.
    """
    # В реальном проекте здесь будет проверка по конкретным инструментам и биржам
    # Пока просто проверяем, находится ли время в одной из сессий
    return get_current_session(dt) is not None

# -------------------------------------------------------------------------
# Работа с Коллекциями
# -------------------------------------------------------------------------

def batch_list(lst: List[Any], batch_size: int) -> List[List[Any]]:
    """
    Разбивает список на подсписки заданного размера.
    
    Args:
        lst: Исходный список.
        batch_size: Размер подсписка.
        
    Returns:
        Список подсписков.
    """
    if batch_size <= 0:
        raise ValueError("Batch size must be positive")
        
    batches = []
    for i in range(0, len(lst), batch_size):
        batches.append(lst[i:i + batch_size])
        
    return batches


def merge_dicts(*dicts: Dict[str, Any], overwrite: bool = True) -> Dict[str, Any]:
    """
    Объединяет несколько словарей в один.
    
    Args:
        *dicts: Словари для объединения.
        overwrite: Если True, то ключи из последующих словарей заменяют предыдущие.
                   Если False, то первое найденное значение сохраняется.
                   
    Returns:
        Объединенный словарь.
    """
    result = {}
    for d in dicts:
        for k, v in d.items():
            if overwrite or k not in result:
                result[k] = v
    return result


def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
    """
    Преобразует вложенный словарь в плоский.
    {'a': {'b': 1}} -> {'a.b': 1}
    
    Args:
        d: Входной словарь.
        parent_key: Префикс для ключей.
        sep: Разделитель для ключей.
        
    Returns:
        Плоский словарь.
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def chunk_iterable(iterable: Iterable[Any], chunk_size: int):
    """
    Генератор, разбивающий итерируемый объект на чанки.
    """
    iterator = iter(iterable)
    while True:
        chunk = []
        try:
            for _ in range(chunk_size):
                chunk.append(next(iterator))
            yield chunk
        except StopIteration:
            if chunk:
                yield chunk
            break

# -------------------------------------------------------------------------
# Валидация и Проверки
# -------------------------------------------------------------------------

def validate_positive_number(value: Union[int, float, Decimal], name: str = "Value"):
    """
    Проверяет, является ли значение положительным числом.
    
    Raises:
        ValueError: Если значение <= 0 или не число.
    """
    d_value = to_decimal(value)
    if d_value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def validate_probability(probability: Union[float, Decimal], name: str = "Probability"):
    """
    Проверяет, является ли значение вероятностью (0 <= prob <= 1).
    
    Raises:
        ValueError: Если значение вне диапазона [0, 1].
    """
    p = to_decimal(probability)
    if p < 0 or p > 1:
        raise ValueError(f"{name} must be between 0 and 1, got {probability}")


def validate_percentage(percentage: Union[float, Decimal], name: str = "Percentage", lower_bound: float = 0.0):
    """
    Проверяет, является ли значение процентом (например, от 0 до 100).
    По умолчанию проверяет 0 <= percentage <= 100.
    
    Args:
        percentage: Значение в процентах.
        name: Имя параметра для сообщения об ошибке.
        lower_bound: Нижняя граница (по умолчанию 0).
    """
    p = to_decimal(percentage)
    lower = to_decimal(lower_bound)
    upper = Decimal('100')
    if p < lower or p > upper:
        raise ValueError(f"{name} must be between {lower_bound}% and 100%, got {percentage}%")

# -------------------------------------------------------------------------
# Завершение модуля
# -------------------------------------------------------------------------

__all__ = [
    # Decimal
    'to_decimal', 'round_price', 'safe_divide', 'calculate_percentage_change', 'clamp',
    # Сравнение
    'is_within_tolerance', 'is_price_near_level', 'validate_candle_data',
    # Математика
    'fibonacci_levels', 'calculate_atr', 'calculate_true_range', 'calculate_drawdown', 'calculate_risk_reward',
    # Время
    'ensure_utc', 'get_current_session', 'is_market_open',
    # Коллекции
    'batch_list', 'merge_dicts', 'flatten_dict', 'chunk_iterable',
    # Валидация
    'validate_positive_number', 'validate_probability', 'validate_percentage',
    # Константы
    'DEFAULT_FIB_LEVELS', 'SESSIONS'
]