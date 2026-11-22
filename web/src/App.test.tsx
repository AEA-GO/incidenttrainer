import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

afterEach(cleanup);

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("the site", () => {
  it("home page has the promise and the invite box", () => {
    at("/");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Your first on-call shift");
    expect(screen.getByLabelText("Have an invite code?")).toBeTruthy();
  });

  it("a malformed code is caught before any request", () => {
    at("/");
    fireEvent.change(screen.getByLabelText("Have an invite code?"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(screen.getByText(/8 letters and digits/)).toBeTruthy();
  });

  it("an invite link without a code says so", () => {
    at("/play");
    expect(screen.getByText("This link has no valid invite code.")).toBeTruthy();
  });

  it("unknown pages get a 404 page", () => {
    at("/no/such/page");
    expect(screen.getByText("Nothing on call here.")).toBeTruthy();
  });
});
