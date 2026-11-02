import importlib.util
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("scheduler", Path(__file__).parent.parent / "scripts" / "scheduler.py")
sch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sch)


def test_next_weekday_run():
    days = sch.parse_days("1-5")
    assert days == {1, 2, 3, 4, 5} and sch.parse_days("1,3,6-7") == {1, 3, 6, 7}
    fri_eve = datetime(2026, 11, 6, 18, 0)
    assert sch.next_run(fri_eve, "06:30", days) == datetime(2026, 11, 9, 6, 30)  # skips the weekend
    mon_early = datetime(2026, 11, 9, 6, 0)
    assert sch.next_run(mon_early, "06:30", days) == datetime(2026, 11, 9, 6, 30)
    assert sch.next_run(datetime(2026, 11, 9, 6, 30), "06:30", days) == datetime(2026, 11, 10, 6, 30)
