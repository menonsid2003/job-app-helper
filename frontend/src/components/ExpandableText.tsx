import { useState } from "react";

export function ExpandableText({ text, className }: { text: string; className?: string }) {
  const [expanded, setExpanded] = useState(false);

  if (!text) {
    return <td className={className}>—</td>;
  }

  return (
    <td
      className={`${className ?? ""} ${expanded ? "expanded" : "truncated"}`}
      onClick={() => setExpanded((e) => !e)}
      role="button"
      tabIndex={0}
      title={expanded ? "Click to collapse" : "Click to read full text"}
    >
      {text}
    </td>
  );
}
