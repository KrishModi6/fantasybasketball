import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown, parseBlocks } from "../components/Markdown";
import { askPath } from "../lib/ai";

describe("assistant markdown", () => {
  it("renders the subset the assistant uses", () => {
    const text = [
      "**Add Free 30.** It adds `+6.1` FP/week.",
      "",
      "## Moves",
      "- Drop Player 9",
      "  - keeps 2 centres",
      "1. Stream on Thursday",
      "",
      "| Player | FP/wk |",
      "|---|---:|",
      "| Free 30 | 41.2 |",
    ].join("\n");
    const { container } = render(<Markdown text={text} />);
    expect(screen.getByText("Add Free 30.").tagName).toBe("STRONG");
    expect(screen.getByText("+6.1").tagName).toBe("CODE");
    expect(screen.getByText("Moves").tagName).toBe("H3");
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelector("li.d1")).toHaveTextContent("keeps 2 centres");
    expect(container.querySelector("ol > li")).toHaveTextContent("Stream on Thursday");
    expect(screen.getByText("41.2")).toHaveClass("n");
  });

  it("never renders model output as HTML", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> [bad](javascript:alert(1))'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent("<img src=x");
  });

  it("only links to https pages, in a new window", () => {
    render(<Markdown text="See [ESPN](https://espn.com/nba)." />);
    const link = screen.getByRole("link", { name: "ESPN" });
    expect(link).toHaveAttribute("href", "https://espn.com/nba");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("joins wrapped list lines and keeps paragraphs apart", () => {
    const blocks = parseBlocks("- first\n  continues here\n\nNext paragraph");
    expect(blocks[0]).toEqual({ kind: "ul", items: [{ text: "first continues here", depth: 0 }] });
    expect(blocks[1]).toEqual({ kind: "p", lines: ["Next paragraph"] });
  });
});

describe("askPath", () => {
  it("encodes prompts and presets", () => {
    expect(askPath("abc", { preset: "waivers" })).toBe("/l/abc/assistant?preset=waivers");
    expect(askPath("abc", { prompt: "Should I add A&B?" })).toBe("/l/abc/assistant?ask=Should+I+add+A%26B%3F");
  });
});
