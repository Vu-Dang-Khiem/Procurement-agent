import sqlite3
from datetime import datetime, timedelta


def quarter_of(moment: datetime) -> str:
    return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"


class SimClock:
    """Đồng hồ của công ty giả lập, không bao giờ đọc giờ thật.

    Thời gian lưu trong database để các MCP server chạy ở tiến trình khác thấy
    cùng một giờ, và vẫn giữ nguyên khi tắt chương trình rồi tiếp tục.
    Thời gian không có múi giờ, hiểu là giờ công ty.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is not None:
            raise ValueError("Đồng hồ giả lập không dùng múi giờ")
        with self._conn:
            self._conn.execute(
                "INSERT INTO sim_clock (id, now) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET now = excluded.now",
                (moment.isoformat(timespec="seconds"),),
            )

    def now(self) -> datetime:
        row = self._conn.execute("SELECT now FROM sim_clock WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("Đồng hồ giả lập chưa được đặt giờ")
        return datetime.fromisoformat(row["now"])

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("Đồng hồ giả lập không được chạy lùi")
        moment = self.now() + delta
        self.set(moment)
        return moment
