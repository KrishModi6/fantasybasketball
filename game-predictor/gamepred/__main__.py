"""Command line.

  python -m gamepred fetch                      download every season of box scores (once; then only this season)
  python -m gamepred train                      backtest, then train on everything and save the model
  python -m gamepred predict                    today's games
  python -m gamepred predict --date 2026-11-14
  python -m gamepred predict --home BOS --away LAL
  python -m gamepred predict --home BOS --away LAL --out "Jayson Tatum" --back "Kristaps Porzingis"
  python -m gamepred lineup BOS                 a team's rotation and each player's value
  python -m gamepred publish out.json           today's and tomorrow's predictions as one file (for Courtside)

Add --json to predict for the full numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import data, model, publish
from .features import build
from .predict import current_state, predict_day, predict_game


def _say(msg: str) -> None:
    print(msg, flush=True)


def cmd_fetch(args) -> int:
    teams, players = data.load_logs(refresh=args.refresh, progress=_say)
    _say(f"{len(teams) // 2:,} games and {len(players):,} player games in {data.DATA_DIR}")
    return 0


def _table(title: str, rows: dict[str, dict], order: list[str]) -> None:
    labels = {"home": "Home team always", "elo": "Elo ratings alone", "logistic": "Logistic regression",
              "random_forest": "Random forest", "xgboost": "XGBoost", "margin_model": "Margin model as odds",
              "blend": "Logistic + XGBoost (used)"}
    _say(f"\n{title}")
    _say(f"  {'method':<28}{'accuracy':>10}{'log loss':>10}{'Brier':>9}{'AUC':>8}")
    for name in order:
        r = rows[name]
        _say(f"  {labels[name]:<28}{r['accuracy']:>10.1%}{r['log_loss']:>10.4f}{r['brier']:>9.4f}{r['auc']:>8.3f}")


def cmd_train(args) -> int:
    teams, players = data.load_logs(refresh=args.refresh, progress=_say if args.verbose else None)
    games, _ = build(teams, players)
    done = [int(s) for s in sorted(games["season"].unique()) if s < data.current_season()]
    tests = done[-args.test_seasons:] + [int(s) for s in games["season"].unique() if s == data.current_season()]
    _say(f"{len(games):,} games, {data.season_label(int(games['season'].min()))} to {games['date'].max()}. Backtesting:")
    bundle = model.train(games, tests, progress=lambda m: _say("  " + m))
    bt = bundle["metrics"]["backtest"]
    order = ["home", "elo", "logistic", "random_forest", "xgboost", "margin_model", "blend"]
    _table(f"Out of sample, {bt['games']:,} games ({', '.join(data.season_label(s) for s in bt['test_seasons'])}):",
           bt["overall"], order)
    _say(f"\nScores: margin off by {bt['margin_mae']:.1f} points on average (always guessing the usual home edge: "
         f"{bt['margin_mae_naive']:.1f}); total off by {bt['total_mae']:.1f} (last season's average: {bt['total_mae_naive']:.1f}).")
    _say(f"A game lands {bt['sigma_margin']:.1f} points from its predicted margin (one standard deviation) and "
         f"{bt['sigma_total']:.1f} from its predicted total.")
    _say("\nWhen the model says the home team wins this often, it wins:")
    for row in bt["calibration"]:
        _say(f"  {row['range']:>9}: predicted {row['predicted']:.1%}, won {row['home_won']:.1%}  ({row['games']:,} games)")
    _say(f"\nSaved {model.MODEL_PATH}")
    return 0


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _print_game(g: dict, baseline: dict | None = None) -> None:
    home, away, sim = g["home"], g["away"], g["simulation"]
    p = g["home_win_probability"]
    where = " (neutral court)" if g["neutral"] else ""
    _say(f"\n{away} at {home}, {g['date']}{where}")
    line = f"  {home} {_pct(p)}   {away} {_pct(1 - p)}"
    if baseline is not None:
        line += f"   ({home} {100 * (p - baseline['home_win_probability']):+.1f} points from the lineup change)"
    _say(line)
    score = g["predicted_score"]
    _say(f"  Predicted score: {home} {score['home']}, {away} {score['away']}   "
         f"(8 games in 10: {home} {sim['home_range'][0]:.0f}-{sim['home_range'][1]:.0f}, "
         f"{away} {sim['away_range'][0]:.0f}-{sim['away_range'][1]:.0f})")
    _say(f"  {sim['sims']:,} simulated games: {home} by 10+ {_pct(sim['home_by_10'])}, {away} by 10+ {_pct(sim['away_by_10'])}, "
         f"within 5 points {_pct(sim['within_5'])}, total {sim['total']['p10']:.0f}-{sim['total']['p90']:.0f}")
    if g["factors"]:
        _say(f"  What moves {home}'s chance (percentage points):")
        for f in g["factors"][:6]:
            _say(f"    {f['points']:+5.1f}  {f['factor']}")
    for side, team in (("home", home), ("away", away)):
        lineup = g["lineups"][side]
        if lineup["out"]:
            _say(f"  {team} out: " + ", ".join(f"{p['name']} ({p['why']})" for p in lineup["out"]))
        if lineup["questionable"]:
            _say(f"  {team} listed but counted as playing: " + ", ".join(lineup["questionable"]))


def cmd_predict(args) -> int:
    bundle = model.load()
    state = current_state(refresh=args.refresh)
    day = date.fromisoformat(args.date) if args.date else data.today()
    injuries = {} if args.no_injuries else data.load_injuries()
    names = lambda s: [n.strip() for n in (s or "").split(",") if n.strip()]  # noqa: E731
    try:
        if args.home or args.away:
            if not (args.home and args.away):
                raise ValueError("Give both --home and --away.")
            games = [predict_game(bundle, state, args.home, args.away, day, args.neutral, names(args.out), names(args.back),
                                  injuries, args.sims)]
            baseline = (predict_game(bundle, state, args.home, args.away, day, args.neutral, injuries=injuries, sims=args.sims)
                        if args.out or args.back else None)
        else:
            if args.out or args.back:
                raise ValueError("--out and --back need one game: add --home and --away.")
            games, baseline = predict_day(bundle, state, day, injuries=injuries, sims=args.sims), None
    except (KeyError, ValueError) as exc:
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"games": games, "baseline": baseline, "data_through": str(state.last_day),
                          "trained_through": bundle["metrics"]["trained_through"]}, indent=2))
        return 0
    if not games:
        _say(f"No games on {day.isoformat()}.")
        return 0
    _say(f"Results through {state.last_day}; model trained through {bundle['metrics']['trained_through']}."
         + ("" if injuries or args.no_injuries else " ESPN's injury report couldn't be reached: nobody is counted out."))
    for g in games:
        _print_game(g, baseline)
    return 0


def cmd_publish(args) -> int:
    bundle = model.load()
    state = current_state(refresh=True)
    first = date.fromisoformat(args.date) if args.date else None
    # A game that has started keeps the prediction published before it did (the file written last time).
    previous = publish.read(Path(args.previous or args.out))
    predictions = publish.build(bundle, state, first, args.days, sims=args.sims, previous=previous)
    publish.write(Path(args.out), predictions)
    games = sum(len(d["games"]) for d in predictions["days"])
    _say(f"{games} games over {len(predictions['days'])} days, results through {predictions['data_through']}"
         f"{'' if predictions['injuries'] else ', no injury report'} -> {args.out}")
    return 0


def cmd_lineup(args) -> int:
    state = current_state(refresh=args.refresh)
    try:
        team = state.team_id(args.team)
    except KeyError as exc:
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2
    _say(f"{state.teams[team].abbrev} rotation (value = recent Game Score per game; the best nine count):")
    for i, pid in enumerate(state.rotation(team), 1):
        p = state.players[pid]
        _say(f"  {i:>2}. {p.name:<26}{p.value:>6.1f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gamepred", description="NBA game predictions.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download game logs from NBA.com")
    f.add_argument("--refresh", action="store_true", help="fetch this season again even if the copy is fresh")
    f.set_defaults(run=cmd_fetch)
    t = sub.add_parser("train", help="backtest and train")
    t.add_argument("--test-seasons", type=int, default=6, help="how many finished seasons to backtest on")
    t.add_argument("--refresh", action="store_true")
    t.add_argument("--verbose", action="store_true")
    t.set_defaults(run=cmd_train)
    p = sub.add_parser("predict", help="predict games")
    p.add_argument("--date", help="YYYY-MM-DD (today by default)")
    p.add_argument("--home", help="home team abbreviation, e.g. BOS")
    p.add_argument("--away", help="away team abbreviation")
    p.add_argument("--neutral", action="store_true", help="neutral court")
    p.add_argument("--out", help='players to sit, comma-separated: "Jayson Tatum, Jaylen Brown"')
    p.add_argument("--back", help="players to put back in the lineup, comma-separated")
    p.add_argument("--sims", type=int, default=10_000)
    p.add_argument("--no-injuries", action="store_true", help="ignore ESPN's injury report")
    p.add_argument("--refresh", action="store_true", help="fetch the latest results first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(run=cmd_predict)
    pb = sub.add_parser("publish", help="write today's and tomorrow's predictions to a file")
    pb.add_argument("out")
    pb.add_argument("--date", help="first day, YYYY-MM-DD (today by default)")
    pb.add_argument("--days", type=int, default=2)
    pb.add_argument("--sims", type=int, default=10_000)
    pb.add_argument("--previous", help="the file published last time, when it isn't the one being written")
    pb.set_defaults(run=cmd_publish)
    ln = sub.add_parser("lineup", help="a team's rotation and player values")
    ln.add_argument("team")
    ln.add_argument("--refresh", action="store_true")
    ln.set_defaults(run=cmd_lineup)
    args = ap.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
