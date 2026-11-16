import { describe, expect, it } from "vitest";
import {
  count, fmt1, luckNote, pct, projRecord, projRecordNote, record, signed, signedPct, syncFailedText, syncLabel, untilTime,
} from "../lib/format";

describe("refresh status", () => {
  const now = Date.parse("2026-11-16T22:00:00Z");
  const inMin = (m: number) => new Date(now + m * 60_000).toISOString();
  it("says a failed refresh will be tried again, instead of a bare Refresh failed", () => {
    expect(syncLabel("error", inMin(-90), inMin(4), now)).toBe("Couldn't refresh · trying again in 4 min");
    expect(syncLabel("error", inMin(-90), inMin(0.2), now)).toBe("Couldn't refresh · trying again soon");
    expect(syncLabel("error", inMin(-90), null, now)).toBe("Couldn't refresh");
    expect(syncLabel("running", inMin(-90), inMin(4), now)).toBe("Refreshing");
    expect(syncLabel("ok", new Date(Date.now() - 20 * 60_000).toISOString())).toBe("Synced 20m ago");
  });
  it("counts down to the retry", () => {
    expect(untilTime(inMin(5), now)).toBe("in 5 min");
    expect(untilTime(inMin(60), now)).toBe("in 1h");
    expect(untilTime(inMin(-3), now)).toBe("soon");
    expect(untilTime("not a time", now)).toBe("soon");
  });
  it("tells someone who pressed Refresh what happened and when it's tried again", () => {
    expect(syncFailedText("Couldn't reach ESPN: it took too long to answer.", inMin(5), now))
      .toBe("Couldn't reach ESPN: it took too long to answer. Courtside will try again in 5 min.");
    expect(syncFailedText("Unexpected error while syncing: KeyError('x')", inMin(5), now))
      .toBe("Unexpected error while syncing: KeyError('x'). Courtside will try again in 5 min.");
    expect(syncFailedText(null, null, now)).toBe("The refresh didn't finish.");
  });
});

describe("format", () => {
  it("formats signed numbers with a true minus sign", () => {
    expect(signed(3.14)).toBe("+3.1");
    expect(signed(-2)).toBe("−2.0");
    expect(signed(0.01)).toBe("0.0");
  });
  it("formats percentages and records", () => {
    expect(pct(0.256)).toBe("26%");
    expect(signedPct(-0.0123)).toBe("−1.2%");
    expect(record(7, 3)).toBe("7–3");
    expect(record(7, 3, 1)).toBe("7–3–1");
    expect(fmt1(null)).toBe("–");
  });
  it("rounds a projected record so it adds up to the team's games", () => {
    // 8.5–8.5 would show 9–9 with wins and losses each rounded: one game too many.
    expect(projRecord({ proj_wins: 8.5, proj_ties: 0, proj_games: 17, byes: 2 })).toBe("9–8");
    expect(projRecord({ proj_wins: 12.49, proj_ties: 0, proj_games: 16, byes: 3 })).toBe("12–4");
    expect(projRecord({ proj_wins: 9.5, proj_ties: 1, proj_games: 16, byes: 3 })).toBe("10–5–1");
    expect(projRecordNote({ proj_wins: 12.49, proj_ties: 0, proj_games: 16, byes: 3 })).toBe("12.5 expected wins in 16 games (3 bye weeks)");
    expect(projRecordNote({ proj_wins: 10, proj_ties: 0, proj_games: 19, byes: 0 })).toBe("10.0 expected wins in 19 games");
  });
  it("names the players behind a team's luck", () => {
    const players = [{ name: "Jamal Murray", per_week: -24.3 }, { name: "Fred VanVleet", per_week: 18.6 }, { name: "Paul Reed", per_week: 0.2 }];
    expect(luckNote(players)).toBe("hottest Fred VanVleet +19, coldest Jamal Murray −24");
    expect(luckNote([{ name: "Paul Reed", per_week: 0.2 }])).toBe("");
    expect(luckNote([])).toBe("");
  });
  it("counts with a plural", () => {
    expect(count(1, "team")).toBe("1 team");
    expect(count(7, "team")).toBe("7 teams");
  });
});
