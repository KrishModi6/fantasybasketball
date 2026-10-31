import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WhatsNew } from "../components/WhatsNew";
import { openWhatsNew, parseReleaseNotes } from "../lib/desktop";

const NOTES = `Courtside runs on your computer.

## What's new in 0.5.3

- **Title bar.** Matches the theme.

## In 0.5.2

- **Luck.** On your roster.

## In 0.3.10

- Older.

## Install on Windows

1. Download it.
`;

function desktop(version: string) {
  let menu: () => void = () => undefined;
  window.courtside = {
    version,
    releaseNotes: vi.fn(() => Promise.resolve(NOTES)),
    onWhatsNew: (cb) => {
      menu = cb;
      return () => undefined;
    },
  };
  return { fromMenu: () => act(() => menu()) };
}

describe("release notes", () => {
  it("reads each version's section, newest first, and stops at the install guide", () => {
    const notes = parseReleaseNotes(NOTES);
    expect(notes.map((n) => n.version)).toEqual(["0.5.3", "0.5.2", "0.3.10"]);
    expect(notes[0]!.body).toBe("- **Title bar.** Matches the theme.");
    expect(notes[2]!.body).toBe("- Older.");
  });
});

describe("what's new", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    cleanup();
    delete window.courtside;
  });

  it("is hidden on the website", () => {
    const { container } = render(<WhatsNew />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows every version since the last one opened, after an update", async () => {
    localStorage.setItem("cs.seen-version", "0.3.10");
    desktop("0.5.3");
    render(<WhatsNew />);
    expect(await screen.findByRole("dialog", { name: "What's new in Courtside 0.5.3" })).toBeInTheDocument();
    expect(screen.getByText("You updated from 0.3.10. Here's everything since then.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Courtside 0.5.3" })).toHaveTextContent("Title bar.");
    expect(screen.getByRole("region", { name: "Courtside 0.5.2" })).toHaveTextContent("Luck.");
    expect(screen.queryByRole("region", { name: "Courtside 0.3.10" })).not.toBeInTheDocument();
    expect(localStorage.getItem("cs.seen-version")).toBe("0.5.3");

    fireEvent.click(screen.getByRole("button", { name: "Earlier versions" }));
    expect(screen.getByRole("dialog", { name: "What's new in Courtside" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Courtside 0.3.10" })).toHaveTextContent("Older.");
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays closed on the version already seen, until asked", async () => {
    localStorage.setItem("cs.seen-version", "0.5.3");
    const { fromMenu } = desktop("0.5.3");
    render(<WhatsNew />);
    await act(() => Promise.resolve());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fromMenu();
    expect(screen.getByRole("dialog", { name: "What's new in Courtside" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    act(() => openWhatsNew());
    expect(screen.getByRole("region", { name: "Courtside 0.5.2" })).toBeInTheDocument();
  });

  it("shows this version's notes to someone updating from before it remembered versions", async () => {
    localStorage.setItem("cs.last-path", "/l/abc");
    desktop("0.5.3");
    render(<WhatsNew />);
    const dialog = await screen.findByRole("dialog", { name: "What's new in Courtside 0.5.3" });
    expect(dialog).toHaveTextContent("Title bar.");
    expect(dialog).not.toHaveTextContent("Luck.");
  });

  it("stays closed on a brand-new copy", async () => {
    desktop("0.5.3");
    render(<WhatsNew />);
    await act(() => Promise.resolve());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(localStorage.getItem("cs.seen-version")).toBe("0.5.3");
  });
});
