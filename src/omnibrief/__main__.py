"""CLI: python -m omnibrief {run, validate-config, render-only}."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from omnibrief.config import ConfigError, check_notice, load_config
from omnibrief.pipeline import HISTORY_DIGESTS, RunOptions, render_site
from omnibrief.pipeline import run as run_pipeline
from omnibrief.state import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO. Logs are public; keep URLs (and any tokens in them) out.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omnibrief")
    parser.add_argument("--config", default="sources.yaml", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="collect, summarise, publish static outputs")
    p_run.add_argument("--data", required=True, type=Path, help="checkout of the data branch")
    p_run.add_argument("--site", required=True, type=Path, help="output dir for the Pages site")
    p_run.add_argument("--raw", type=Path, default=None, help="output dir for raw merged records")
    p_run.add_argument("--no-llm", action="store_true", help="skip the LLM; publish fallback digests")

    sub.add_parser("validate-config", help="validate sources.yaml and NOTICE.md coverage")

    p_deliver = sub.add_parser("deliver", help="send pending messages via push publishers (Telegram)")
    p_deliver.add_argument("--data", required=True, type=Path)
    p_deliver.add_argument("--test", action="store_true", help="also send a test message to each channel")

    p_render = sub.add_parser("render-only", help="rebuild the site from existing data, no fetching")
    p_render.add_argument("--data", required=True, type=Path)
    p_render.add_argument("--site", required=True, type=Path)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("omnibrief")

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("invalid config: %s", e)
        return 2
    missing = check_notice(cfg)
    if missing:
        log.error("NOTICE.md is missing attribution for enabled sources: %s", "; ".join(missing))
        return 2

    if args.cmd == "validate-config":
        log.info("config OK: %d topic(s)", len(cfg.topics))
        return 0

    if args.cmd == "deliver":
        from omnibrief.pipeline import deliver

        for pid, status in deliver(cfg, args.data, dict(os.environ), test=args.test).items():
            log.info("deliver %s: %s", pid, status)
        return 0

    if args.cmd == "render-only":
        from omnibrief.publishers.base import PublisherContext
        from omnibrief.publishers.registry import build_publishers

        store = Store(args.data)
        args.site.mkdir(parents=True, exist_ok=True)
        for topic in cfg.topics:
            pubs, _ = build_publishers(PublisherContext(cfg, topic, args.site, dict(os.environ)))
            digests = store.recent_digests(topic.id, HISTORY_DIGESTS)
            for pub in pubs:
                if pub.kind == "static":
                    pub.publish(digests)
        render_site(cfg, store, args.site, env=dict(os.environ))
        return 0

    runs = run_pipeline(
        cfg,
        RunOptions(
            data_dir=args.data,
            site_dir=args.site,
            raw_dir=args.raw,
            use_llm=not args.no_llm,
            env=dict(os.environ),
        ),
    )
    for r in runs:
        log.info("run %s topic=%s status=%s llm=%s", r.run_id, r.topic, r.status, r.llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
