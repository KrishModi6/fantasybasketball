import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Delta, Injury, Meter, Term } from "../components/ui";
import { GLOSSARY, GLOSSARY_SECTIONS } from "../lib/glossary";

describe("ui primitives", () => {
  it("colours deltas by sign", () => {
    render(<Delta value={-4.2} />);
    expect(screen.getByText("−4.2")).toHaveClass("neg");
  });
  it("labels injuries accessibly", () => {
    render(<Injury status="DAY_TO_DAY" />);
    expect(screen.getByTitle("day to day")).toHaveTextContent("DTD");
  });
  it("renders nothing for healthy players", () => {
    const { container } = render(<Injury status="ACTIVE" />);
    expect(container).toBeEmptyDOMElement();
  });
  it("shows meter percentage", () => {
    render(<Meter value={0.73} />);
    expect(screen.getByText("73%")).toBeInTheDocument();
  });
  it("shows a term's definition on hover and focus", () => {
    render(<Term t="range80" />);
    const term = screen.getByText("80% range");
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseEnter(term);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Where the score lands in 80% of the simulations");
    fireEvent.mouseLeave(term);
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.focus(term);
    expect(term).toHaveAttribute("aria-describedby", screen.getByRole("tooltip").id);
  });
  it("sorts when a term is a column button", () => {
    let sorted = 0;
    render(<Term t="ros" onClick={() => sorted++}>ROS ▾</Term>);
    fireEvent.click(screen.getByRole("button", { name: "ROS ▾" }));
    expect(sorted).toBe(1);
  });
  it("lists every glossary term on the glossary page exactly once", () => {
    const listed = GLOSSARY_SECTIONS.flatMap((s) => s.keys);
    expect(new Set(listed).size).toBe(listed.length);
    expect([...listed].sort()).toEqual(Object.keys(GLOSSARY).sort());
  });
});
