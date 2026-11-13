import { describe, expect, it } from "vitest";
// @ts-expect-error The web project has no Node types, but tests run in Node (and the test config stubs CSS imports).
import { readFileSync } from "node:fs";

const source: string = readFileSync("src/styles.css", "utf8");

// The desktop window has no system title bar: the top bar is the title bar, Windows and Linux draw the window
// buttons over its right end, and the whole strip drags the window. jsdom has no layout, so these read the
// stylesheet's rules; the geometry itself was checked in a real window when the rules were written.
const css = source.replace(/\r\n/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");

/** The declarations of the rule whose selector is exactly this (it starts a line). */
function rule(selector: string): string {
  const at = `\n${css}`.indexOf(`\n${selector} {`);
  expect(at, `no rule for ${selector}`).toBeGreaterThanOrEqual(0);
  return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
}

describe("the title-bar strip in the desktop app", () => {
  it("has the player drawer start below it", () => {
    // It started at the top of the window, with "What to ask for" and its close button under the window buttons.
    expect(rule(":root[data-platform] .drawer")).toContain("top: var(--topbar)");
    expect(rule(".drawer")).toContain("top: 0"); // a browser has no window buttons to avoid
  });

  it("keeps a tall dialog out of it", () => {
    expect(rule(":root[data-platform] .dialog")).toContain("max-height: calc(100vh - 2 * var(--topbar))");
  });

  it("lets panels shown over the page take clicks", () => {
    const selector = ":root[data-platform] :is(.drawer, .dialog, .update-popup, .toasts)";
    expect(rule(selector)).toContain("-webkit-app-region: no-drag");
  });

  it("keeps the top bar's own controls clear of the window buttons", () => {
    const pad = rule(':root[data-platform="win32"] .topbar,\n:root[data-platform="linux"] .topbar');
    expect(pad).toContain("env(titlebar-area-width");
    expect(rule(":root[data-platform] .topbar")).toContain("-webkit-app-region: drag");
    expect(rule(":root[data-platform] .topbar :is(a, button, select, input, label)")).toContain("no-drag");
  });

  it("puts nothing else at the top of the window", () => {
    // Every fixed panel: the drawer and dialog are handled above, the rest sit below the bars or at the bottom.
    const fixed = [...css.matchAll(/([^{}]+)\{([^{}]*position: fixed[^{}]*)\}/g)].map((m) => [m[1]!.trim(), m[2]!] as const);
    const names = fixed.map(([selector]) => selector).sort();
    expect(names).toEqual([".dialog", ".drawer", ".loading-line", ".scrim", ".term-tip", ".toasts", ".update-popup"]);
    const body = Object.fromEntries(fixed);
    expect(body[".update-popup"]).toContain("top: calc(var(--topbar) + var(--tabs) + 10px)");
    expect(body[".toasts"]).toContain("bottom: calc(var(--status) + 10px)");
  });
});
