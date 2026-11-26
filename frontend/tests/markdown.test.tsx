import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Markdown } from "../src/components/Markdown";

describe("Markdown rendering", () => {
  it("renders fenced code as a highlighted block", () => {
    render(<Markdown text={"Here:\n\n```python\nprint('hi')\n```"} />);
    const code = document.querySelector("pre code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toContain("print('hi')");
    // The literal fence must not survive into the output.
    expect(document.body.textContent).not.toContain("```");
  });

  it("renders headings, lists and tables rather than raw syntax", () => {
    render(<Markdown text={"# Title\n\n- one\n- two\n"} />);
    expect(screen.getByRole("heading", { name: "Title" })).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(document.body.textContent).not.toContain("# Title");
  });

  it("never executes HTML embedded in an answer", () => {
    // Model output is untrusted. The renderer must not create these elements.
    render(
      <Markdown
        text={
          '<script>window.pwned=1</script><img src=x onerror="window.pwned=1">'
        }
      />,
    );
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("escapes an HTML-looking answer into visible text", () => {
    render(<Markdown text={"<b>not bold</b>"} />);
    expect(document.querySelector("b")).toBeNull();
    expect(document.body.textContent).toContain("<b>not bold</b>");
  });

  it("copies the code block to the clipboard", async () => {
    // userEvent.setup() installs its own clipboard stub, so override after it.
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<Markdown text={"```js\nconst a = 1;\n```"} />);
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("const a = 1;"),
    );
    expect(await screen.findByRole("button", { name: "Copied" })).toBeVisible();
  });
});
