import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { UpdatePopup } from "../components/UpdatePopup";
import { VersionBadge } from "../components/VersionBadge";
import type { UpdateState } from "../lib/api";

function desktop(initial: UpdateState) {
  let push: (s: UpdateState) => void = () => undefined;
  const updates = {
    state: vi.fn(() => Promise.resolve(initial)),
    install: vi.fn(() => Promise.resolve()),
    download: vi.fn(() => Promise.resolve()),
    check: vi.fn(() => Promise.resolve()),
    onChange: vi.fn((cb: (s: UpdateState) => void) => {
      push = cb;
      return () => undefined;
    }),
  };
  window.courtside = { version: "0.2.3", updates };
  return { updates, push: (s: UpdateState) => act(() => push(s)) };
}

describe("version label", () => {
  afterEach(() => {
    delete window.courtside;
  });

  it("is hidden on the website", () => {
    const { container } = render(<VersionBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the version and checks for updates when clicked", async () => {
    const { updates } = desktop({ status: "current", current: "0.2.3" });
    render(<VersionBadge />);
    const badge = await screen.findByRole("button", { name: "Courtside version 0.2.3, Up to date" });
    expect(badge).toHaveTextContent("v0.2.3");
    fireEvent.click(badge);
    expect(updates.check).toHaveBeenCalledOnce();
  });

  it("offers the new version once one is out", async () => {
    const { updates, push } = desktop({ status: "idle" });
    render(<VersionBadge />);
    await screen.findByRole("button", { name: "Courtside version 0.2.3" });
    push({ status: "available", version: "0.2.4", current: "0.2.3" });
    fireEvent.click(screen.getByRole("button", { name: "Courtside version 0.2.3, 0.2.4 available" }));
    expect(updates.download).toHaveBeenCalledOnce();
    expect(updates.check).not.toHaveBeenCalled();
  });
});

describe("update popup", () => {
  afterEach(() => {
    delete window.courtside;
  });

  it("pops up when a new version is out and downloads it", async () => {
    const { updates, push } = desktop({ status: "current", current: "0.2.3" });
    render(<UpdatePopup />);
    expect(screen.queryByRole("dialog")).toBeNull();
    push({ status: "available", version: "0.2.4", current: "0.2.3" });
    expect(screen.getByRole("dialog", { name: "New update released" })).toHaveTextContent("Courtside 0.2.4 is out (you have 0.2.3)");
    fireEvent.click(screen.getByRole("button", { name: "Download now" }));
    expect(updates.download).toHaveBeenCalledOnce();
  });

  it("asks again once a dismissed download is ready, then restarts into it", async () => {
    const { updates, push } = desktop({ status: "downloading", version: "0.2.4", current: "0.2.3", percent: 40 });
    render(<UpdatePopup />);
    fireEvent.click(await screen.findByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    push({ status: "ready", version: "0.2.4", current: "0.2.3" });
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    expect(updates.install).toHaveBeenCalledOnce();
  });
});
