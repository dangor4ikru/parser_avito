import random


def random_pause(base: float, spread: float = 0.5) -> float:
    """Случайная пауза от base до base * (1 + spread) сек.

    Пауза никогда не короче заданной в настройках, но и не повторяется
    с одинаковым шагом — ровный интервал выдаёт бота.
    """
    if not base or base <= 0:
        return 0.0
    return random.uniform(base, base * (1 + spread))


def random_delay(delay_min: float, delay_max: float) -> float:
    """Случайная пауза в диапазоне [delay_min, delay_max] сек."""
    delay_min = max(0.0, delay_min or 0.0)
    delay_max = max(delay_min, delay_max or 0.0)
    return random.uniform(delay_min, delay_max)
