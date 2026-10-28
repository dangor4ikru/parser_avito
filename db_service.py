import sqlite3

from models import Item


class SQLiteDBHandler:
    """Работа с БД sqlite"""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SQLiteDBHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self, db_name="database.db"):
        if not hasattr(self, "_initialized"):
            self.db_name = db_name
            self._create_table()
            self._initialized = True

    def _create_table(self):
        """Создает таблицу viewed, если она не существует."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS viewed (
                    id INTEGER,
                    price INTEGER
                )
                """
            )
            # последняя известная цена каждого встреченного объявления
            # (для отслеживания снижения цены, PR #334 avito-sdk)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY,
                    price INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def add_record(self, ad: Item):
        """Добавляет новую запись в таблицу viewed."""
        price = (
            ad.priceDetailed.value
            if (ad.priceDetailed and getattr(ad.priceDetailed, "value", None) is not None)
            else 0
        )
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO viewed (id, price) VALUES (?, ?)",
                (ad.id, price),
            )
            conn.commit()

    def add_record_from_page(self, ads: list[Item]):
        """Добавляет несколько записей в таблицу viewed."""
        records = [
            (
                ad.id,
                ad.priceDetailed.value
                if (ad.priceDetailed and getattr(ad.priceDetailed, "value", None) is not None)
                else 0,
            )
            for ad in ads
        ]

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO viewed (id, price)
                VALUES (?, ?)
                """,
                records,
            )
            conn.commit()

    def get_last_prices(self, ids: list[int]) -> dict[int, int]:
        """Последние известные цены объявлений.

        Если объявления нет в prices (база от старой версии парсера),
        берём последнюю запись из viewed.
        """
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT id, price FROM viewed WHERE id IN ({placeholders}) ORDER BY rowid",
                ids,
            )
            prices = dict(cursor.fetchall())
            cursor.execute(
                f"SELECT id, price FROM prices WHERE id IN ({placeholders})",
                ids,
            )
            prices.update(cursor.fetchall())
        return prices

    def update_prices(self, ads: list[Item]) -> None:
        """Запомнить текущие цены объявлений."""
        records = [
            (ad.id, ad.price_value)
            for ad in ads
            if isinstance(ad.id, int) and not isinstance(ad.id, bool)
        ]
        if not records:
            return
        with sqlite3.connect(self.db_name) as conn:
            conn.executemany(
                """
                INSERT INTO prices (id, price, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    price = excluded.price,
                    updated_at = CASE WHEN prices.price != excluded.price
                                      THEN CURRENT_TIMESTAMP ELSE prices.updated_at END
                """,
                records,
            )
            conn.commit()

    def record_exists(self, record_id, price):
        """Проверяет, существует ли запись с заданными id и price."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM viewed WHERE id = ? AND price = ?",
                (record_id, price),
            )
            return cursor.fetchone() is not None
