"""
Защита от запуска двух парсеров одновременно (по мотивам eminsk/avito-sdk process_guard).

Два экземпляра с одним прокси удваивают частоту запросов и дёргают смену IP
друг у друга — это прямой путь к блокировке. Блокировка файла снимается ОС
автоматически, даже если процесс упал.
"""
import os
import sys
from pathlib import Path

from loguru import logger


class SingleInstanceLock:
    def __init__(self, path: str | Path = "storage/parser.lock"):
        self.path = Path(path)
        self._fd = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            logger.error(
                "Парсер уже запущен в другом окне/процессе. Два экземпляра "
                "одновременно быстрее получают блокировку — закройте второй."
            )
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None
