"""CLI: python -m omnibrief {validate-config, plan, run, deliver, render-only}  (v2 engine).

The engine is inert without an instance: `run`, `deliver` and `render-only` exit 0 doing nothing
if `<instance>/omnibrief.yaml` doesn't exist (design-v2 §3).

Secrets/variables: besides normal env vars, the workflow may pass all repository secrets and
variables as JSON (OMNIBRIEF_SECRETS_JSON / OMNIBRIEF_VARS_JSON), so instance YAML can name any
env var without editing the engine-owned workflow. Explicit env vars win.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from omnibrief.engine.config import ConfigError, load_engine


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO (Telegram puts the bot token in the URL). Logs are public.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_env() -> dict[str, str]:
    env: dict[str, str] = {}
    sources = [os.environ.get("OMNIBRIEF_VARS_JSON"), os.environ.get("OMNIBRIEF_SECRETS_JSON")]
    if (path := os.environ.get("OMNIBRIEF_SECRETS_FILE")) and Path(path).is_file():
        sources.append(Path(path).read_text(encoding="utf-8"))
    for name, raw in zip(("OMNIBRIEF_VARS_JSON", "OMNIBRIEF_SECRETS_JSON", "OMNIBRIEF_SECRETS_FILE"), sources,
                         strict=False):
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            logging.getLogger("omnibrief").warning("%s is not valid JSON; ignored", name)
            continue
        env.update({str(k): str(v) for k, v in data.items() if isinstance(v, str | int | float)})
    env.update({k: v for k, v in os.environ.items() if k not in ("OMNIBRIEF_VARS_JSON", "OMNIBRIEF_SECRETS_JSON")})
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omnibrief")
    parser.add_argument("--instance", default="instance", type=Path, help="instance directory (default: instance/)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate-config", help="validate the instance + catalogue (allowlist, routing, NOTICE)")
    p_plan = sub.add_parser("plan", help="show which topics are due now")
    p_plan.add_argument("--data", required=True, type=Path)

    p_run = sub.add_parser("run", help="collect due topics, store, publish feeds and site")
    p_run.add_argument("--data", required=True, type=Path, help="checkout of the data branch")
    p_run.add_argument("--site", required=True, type=Path, help="output dir for the Pages site")
    p_run.add_argument("--out", type=Path, default=None, help="artifact sink output dir")
    p_run.add_argument("--archive", type=Path, default=None, help="checkout of the archive branch (jsonl_git sink)")
    p_run.add_argument("--topics", default="", help="force these topics (comma-separated, or 'all')")
    p_run.add_argument("--no-llm", action="store_true", help="skip the LLM; digests are un-summarised")

    p_deliver = sub.add_parser("deliver", help="send pending messages on push channels (Telegram)")
    p_deliver.add_argument("--data", required=True, type=Path)
    p_deliver.add_argument("--test", action="store_true", help="also send a test message to each channel")

    p_render = sub.add_parser("render-only", help="rebuild feeds + site from stored state, no fetching")
    p_render.add_argument("--data", required=True, type=Path)
    p_render.add_argument("--site", required=True, type=Path)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("omnibrief")

    if not (args.instance / "omnibrief.yaml").is_file():
        if args.cmd == "validate-config":
            log.error("no instance config at %s", args.instance / "omnibrief.yaml")
            return 2
        log.info("no %s — this is the engine repo (inert); nothing to do", args.instance / "omnibrief.yaml")
        return 0
    try:
        engine = load_engine(args.instance)
    except ConfigError as e:
        for line in str(e).splitlines():
            log.error("invalid config: %s", line)
        return 2

    if args.cmd == "validate-config":
        log.info("config OK: %d enabled topic(s): %s", len(engine.enabled_topics()),
                 ", ".join(s.id for s in engine.enabled_topics()))
        return 0

    from omnibrief.engine import runner
    from omnibrief.engine.planner import plan
    from omnibrief.engine.store import EngineStore
    from omnibrief.models import utcnow

    env = build_env()
    if args.cmd == "plan":
        decisions = plan(engine.enabled_topics(), EngineStore(args.data).load_schedule(), utcnow(),
                         engine.site.timezone)
        for tid, (due, why) in decisions.items():
            print(f"{tid:16} {'DUE ' if due else 'skip'}  {why}")
        return 0
    if args.cmd == "deliver":
        for cid, status in runner.deliver(engine, args.data, env, test=args.test).items():
            log.info("deliver %s: %s", cid, status)
        return 0
    if args.cmd == "render-only":
        runner.run(engine, runner.RunPaths(data_dir=args.data, site_dir=args.site), env, collect=False,
                   use_llm=False)
        return 0
    force = {t.strip() for t in args.topics.split(",") if t.strip()}
    rec = runner.run(engine, runner.RunPaths(data_dir=args.data, site_dir=args.site, out_dir=args.out,
                                             archive_dir=args.archive), env, force=force, use_llm=not args.no_llm)
    log.info("run %s: %s · %s", rec.run_id, rec.status,
             ", ".join(f"{t}={r['status']}" for t, r in rec.topics.items()) or "no topics due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
