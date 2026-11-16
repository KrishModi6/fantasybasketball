import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TeammateNote } from "../components/PlayerDrawer";
import { lastName, lastNames, teammateLabel } from "../lib/format";

const embiid = { id: "1", name: "Joel Embiid", games_out: 3, injury_status: "OUT" };
const george = { id: "2", name: "Paul George", games_out: 0.4, injury_status: "QUESTIONABLE" };

describe("teammate out", () => {
  it("names teammates by last name", () => {
    expect(lastNames(["Joel Embiid"])).toBe("Embiid");
    expect(lastNames(["Joel Embiid", "Paul George"])).toBe("Embiid and George");
    expect(lastNames(["A B", "C D", "E F"])).toBe("B, D and F");
    expect(lastName("Jaren Jackson Jr.")).toBe("Jackson"); // a suffix isn't a name
    expect(lastName("Gary Trent Jr")).toBe("Trent");
    expect(lastName("Robert Williams III")).toBe("Williams");
    expect(lastName("Nene")).toBe("Nene");
  });
  it("labels a lift and a drop for the Players list", () => {
    expect(teammateLabel({ pct: 12.4, out: [embiid], back: [] })).toBe("+12% · Embiid out");
    expect(teammateLabel({ pct: -5.2, out: [], back: [embiid] })).toBe("−5% · Embiid back");
    expect(teammateLabel(null)).toBeUndefined();
    expect(teammateLabel({ pct: 4, out: [], back: [] })).toBeUndefined(); // nobody to name: no label
  });
  it("explains a lift on the player card, with the games a teammate misses", () => {
    render(<TeammateNote t={{ pct: 12.4, out: [embiid, george], back: [] }} name="Kelly Oubre Jr." />);
    expect(screen.getByText(/Teammate out: \+12% for the next 7 days/)).toBeInTheDocument();
    expect(screen.getByText(/Joel Embiid \(about 3 games out\)/)).toBeInTheDocument();
    expect(screen.getByText(/^and Paul George$/)).toBeInTheDocument(); // under a game out: no count
    expect(screen.getByText("How this works")).toBeInTheDocument();
    expect(screen.getByText(/Oubre should get more shots/)).toBeInTheDocument(); // not "Jr. should"
  });
  it("explains a drop once a star is back", () => {
    render(<TeammateNote t={{ pct: -5, out: [], back: [embiid] }} name="Guerschon Yabusele" />);
    expect(screen.getByText(/Teammate back: −5% for the next 7 days/)).toBeInTheDocument();
    expect(screen.getByText(/Embiid is expected back/)).toBeInTheDocument();
  });
});
