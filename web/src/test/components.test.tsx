import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Delta, Injury, Meter } from "../components/ui";

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
});
