interface RemoteSelectProps {
  value: boolean | null;
  onSave: (value: boolean | null) => void;
}

/** Tri-state Remote/Not remote/Unknown editor — location text and jobspy's
 * own signal get this wrong often enough (see app/location_parse.py and
 * is_remote_hint) that a manual override belongs right on the table, same
 * as Company/Location already have via EditableCell. */
export function RemoteSelect({ value, onSave }: RemoteSelectProps) {
  const current = value === null ? "" : value ? "true" : "false";

  return (
    <select
      className={`remote-select remote-select-${current || "unknown"}`}
      value={current}
      onChange={(e) => {
        const raw = e.target.value;
        onSave(raw === "" ? null : raw === "true");
      }}
    >
      <option value="">Unknown</option>
      <option value="true">Remote</option>
      <option value="false">Not remote</option>
    </select>
  );
}
