import type { SortDir, SortKey } from "../hooks/useJobFilters";

interface SortableHeaderProps {
  label: string;
  sortKey: SortKey;
  currentSortKey: SortKey;
  currentSortDir: SortDir;
  onClick: (key: SortKey) => void;
}

export function SortableHeader({ label, sortKey, currentSortKey, currentSortDir, onClick }: SortableHeaderProps) {
  return (
    <th className="sortable" onClick={() => onClick(sortKey)}>
      {label}
      {currentSortKey === sortKey && <span className="sort-indicator">{currentSortDir === "asc" ? " ▲" : " ▼"}</span>}
    </th>
  );
}
