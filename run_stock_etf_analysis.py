import logging
import os
from datetime import datetime
from pathlib import Path

from stock_etf.config import Settings
from stock_etf.engine import run
from stock_etf.profile_store import load_profile


def _prune_engine_logs(log_dir: Path, keep: int = 5) -> None:
    """Keep only the newest five timestamped engine logs."""
    candidates = sorted(
        (
            path for path in log_dir.glob("stock_etf_*.log")
            if path.is_file() and path.name not in {
                "stock_etf_engine_latest.log", "stock_etf_latest.log"
            }
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for old_path in candidates[keep:]:
        try:
            old_path.unlink()
        except OSError:
            logging.getLogger("stock_etf").warning(
                "Could not remove expired engine log %s", old_path, exc_info=True
            )


def configure_engine_logging(log_dir: Path) -> Path:
    """Configure console and durable per-run/latest engine logs."""
    log_dir.mkdir(parents=True, exist_ok=True)
    _prune_engine_logs(log_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_log = log_dir / f"stock_etf_{stamp}.log"
    latest_log = log_dir / "stock_etf_engine_latest.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # This script is a fresh subprocess, but remove any handlers installed by
    # imported libraries so records are written exactly once.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    for destination in (None, run_log, latest_log):
        handler = logging.StreamHandler() if destination is None else logging.FileHandler(
            destination, mode="w" if destination == latest_log else "a", encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)

    return run_log


def main() -> None:
    settings = Settings()
    log_dir = Path(settings.cache_dir).parent / "logs"
    run_log = configure_engine_logging(log_dir)
    logger = logging.getLogger("stock_etf")

    try:
        horizon = os.getenv("STOCK_HORIZON", "LONG_TERM").upper()
        profile = load_profile(
            settings.profile_path,
            int(os.getenv("INVESTOR_AGE", "38")),
            float(os.getenv("INVESTMENT_CAPITAL_INR", "1000000")),
        )
        logger.info(
            "Backend profile loaded: age=%s capital=₹%.2f updated=%s",
            profile["age"],
            profile["capital_inr"],
            profile.get("updated_at_utc"),
        )
        logger.info("Starting engine horizon=%s run_log=%s", horizon, run_log)
        run(horizon, settings, age=profile["age"], capital_inr=profile["capital_inr"])
        logger.info("Engine completed successfully")
    except Exception:
        logger.exception("Stock/ETF analysis crashed. Full traceback follows.")
        raise


if __name__ == "__main__":
    main()
