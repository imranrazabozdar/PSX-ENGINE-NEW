"""scheduler.py — APScheduler-driven automation.

  * Every RUN_INTERVAL_MINUTES during market hours (Mon-Fri) -> full run
  * 09:00 -> morning report
  * 21:00 -> evening report + outcome update
Falls back to the lightweight `schedule` library loop if APScheduler is
unavailable.
"""

import logging
from datetime import datetime

import config
import reports
import backtester

log = logging.getLogger("scheduler")


def _in_market_hours(now=None):
    now = now or datetime.now()
    if now.weekday() not in config.MARKET_DAYS:
        return False
    hm = now.strftime("%H:%M")
    return config.MARKET_OPEN <= hm <= config.MARKET_CLOSE


def _tick():
    import main
    if _in_market_hours():
        main.full_run()
    else:
        log.info("Outside market hours — skipping 10-min run.")


def _morning():
    text = reports.morning_report()
    print(text)
    reports.save_report(text, "morning")


def _evening():
    backtester.update_outcomes()
    text = reports.evening_report()
    print(text)
    reports.save_report(text, "evening")


def start():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        sched = BlockingScheduler(timezone=config.TIMEZONE)
        sched.add_job(_tick, "interval",
                      minutes=config.RUN_INTERVAL_MINUTES,
                      next_run_time=datetime.now())
        h, m = config.MORNING_REPORT_TIME.split(":")
        sched.add_job(_morning, "cron", hour=int(h), minute=int(m))
        h, m = config.EVENING_REPORT_TIME.split(":")
        sched.add_job(_evening, "cron", hour=int(h), minute=int(m))
        log.info("APScheduler started — Ctrl+C to stop.")
        sched.start()
    except ImportError:
        log.warning("APScheduler missing — using `schedule` fallback.")
        import time
        import schedule as sch
        sch.every(config.RUN_INTERVAL_MINUTES).minutes.do(_tick)
        sch.every().day.at(config.MORNING_REPORT_TIME).do(_morning)
        sch.every().day.at(config.EVENING_REPORT_TIME).do(_evening)
        _tick()
        while True:
            sch.run_pending()
            time.sleep(20)
