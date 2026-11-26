import { memo, useCallback, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

// react-markdown builds a React element tree and never produces an HTML string,
// so every text node is escaped by React. Raw HTML inside an answer is dropped
// because the plugin that would re-enable it is deliberately not installed.
// scripts/security_audit.py fails the build if any raw-HTML path reappears here.

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const block = useRef<HTMLPreElement>(null);
  // Read the rendered text rather than walking React children: the highlighter
  // nests the code in spans, and this copies exactly what is on screen.
  const copy = useCallback(() => {
    const text = block.current?.textContent ?? "";
    if (!text) return;
    void navigator.clipboard
      ?.writeText(text)
      .then(() => setCopied(true))
      .catch(() => undefined);
  }, []);
  return (
    <div className="code-block">
      <button className="code-copy" onClick={copy} type="button">
        {copied ? "Copied" : "Copy"}
      </button>
      <pre ref={block}>{children}</pre>
      <span className="sr-only" role="status">
        {copied ? "Code copied to clipboard" : ""}
      </span>
    </div>
  );
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="answer-text">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{ pre: CodeBlock }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
