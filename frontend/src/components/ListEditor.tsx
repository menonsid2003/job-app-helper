import { useEffect, useState } from "react";

export function ListEditor({
  label,
  value,
  onChange,
  rows = 3,
  hint,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  rows?: number;
  hint?: string;
}) {
  const [text, setText] = useState(value.join("\n"));

  useEffect(() => {
    setText(value.join("\n"));
    // Only re-sync when the *identity* of the parent value changes (e.g. after
    // loading criteria) — not on every keystroke, which is handled locally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.join("\n")]);

  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {hint && <span className="field-hint">{hint}</span>}
      <textarea
        rows={rows}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => onChange(text.split("\n").map((line) => line.trim()).filter(Boolean))}
        placeholder="One per line"
      />
    </label>
  );
}
