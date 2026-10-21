import { expect, test } from "bun:test";
import { tags } from "../../scripts/ts/fe_a11y_check.ts";

const names = (text: string, jsx = false) => [...tags(text, jsx)].map(([n]) => n);

test("elements nested in JSX attribute expressions are visited, in source order", () => {
  expect(names('<Toolbar icon={<img src="logo.png" />} id="t" /><p>x</p>')).toEqual(["toolbar", "img", "p"]);
  expect(names("<Section action={<input/>} />")).toEqual(["section", "input"]);
  expect(names("<Menu trigger={<button></button>} />")).toEqual(["menu", "button"]);
  expect(names('<A p={<B q={<img src="x" />} />} />')).toEqual(["a", "b", "img"]);
});

test("the enclosing tag keeps its whole attribute text; the nested tag gets its own", () => {
  const [outer, inner] = [...tags('<Toolbar icon={<img src="logo.png" />} id="t" />')];
  expect(outer![1]).toContain('id="t"');
  expect(inner![0]).toBe("img");
  expect(inner![1].trim()).toBe('src="logo.png" /');
});

test("a comparison inside an expression is not an element", () => {
  expect(names('<div role="button" onClick={() => a<b && c} onKeyDown={k}>x</div>')).toEqual(["div"]);
  expect(names("<A p={a<b && c>d} />")).toEqual(["a", "b"]);
});

const apostrophe = `<div
// Keep Atlas's footer: it must not swallow the markup below
className="x"
/>
<img src="a.png" />
<button></button>
<p>it's done</p>
`;

test("a comment between attributes does not swallow markup in .jsx/.tsx", () => {
  expect(names(apostrophe, true)).toEqual(["div", "img", "button", "p"]);
  expect(names(apostrophe.replace(/\/\/ .*/, "/* Keep Atlas's footer */"), true)).toEqual(["div", "img", "button", "p"]);
});

test("comments inside expressions and a lone apostrophe in JSX text", () => {
  const inExpr = `<div className={cn(\n  // can't drop this\n  "a",\n  /* it's */ "b"\n)} />\n<img src="a.png" />\n`;
  expect(names(inExpr, true)).toEqual(["div", "img"]);
  expect(names(`<Dialog body={<>This can't be undone.</>} />\n<img src="a.png" />\n`, true)).toEqual(["dialog", "img"]);
});

test("comment skipping is JSX-only and needs whitespace before the comment", () => {
  expect(names('<a href="https://example.com/a" aria-label="x">y</a><img src="a.png" />', true)).toEqual(["a", "img"]);
});

test("scanning stays linear on pathological 200 KB inputs", () => {
  const n = 200_000;
  const cases = [
    "<A p={".repeat(n / 6), "<A p={<img/>".repeat(n / 12), "<a p={" + "x<y ".repeat(n / 4), "<a " + "// it's\n".repeat(n / 8),
    "<a " + "/* ' */ ".repeat(n / 8), "<a p={" + "'\\'".repeat(n / 3), "<a p={" + "can't\n".repeat(n / 6),
    "<a p={" + "a<b && c}".repeat(n / 9), '<A p={<img src="x" />}>'.repeat(n / 23), "<a {".repeat(n / 4), '<a "'.repeat(n / 4),
  ];
  for (const text of cases) {
    for (const jsx of [true, false]) {
      const t = performance.now();
      [...tags(text, jsx)];
      expect(performance.now() - t).toBeLessThan(5000);
    }
  }
});
