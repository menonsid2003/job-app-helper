import { useState } from "react";

interface EditableCellProps {
  value: string;
  onSave: (value: string) => void;
  placeholder?: string;
}

/** Plain text input that saves on blur, only if the value actually changed —
 * same "click in, edit, click away" pattern already used for Notes, reused
 * here for Company/Location so a blank/wrong field scraped by a connector
 * can be fixed by hand wherever the job shows up (Jobs listing or Tracking —
 * both read/write the same underlying Job row). */
export function EditableCell({ value, onSave, placeholder }: EditableCellProps) {
  const [draft, setDraft] = useState(value);

  return (
    <input
      className="notes-input"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== value) onSave(draft);
      }}
      placeholder={placeholder}
    />
  );
}
