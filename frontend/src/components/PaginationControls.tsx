import { PAGE_SIZE_OPTIONS } from "../hooks/usePagination";

interface PaginationControlsProps {
  page: number;
  pageCount: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  rangeStart: number;
  rangeEnd: number;
  totalCount: number;
}

export function PaginationControls({
  page, pageCount, pageSize, onPageChange, onPageSizeChange, rangeStart, rangeEnd, totalCount,
}: PaginationControlsProps) {
  return (
    <div className="pagination-controls">
      <label className="field-inline">
        <span className="field-label">Per page</span>
        <select value={pageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <button onClick={() => onPageChange(page - 1)} disabled={page <= 1}>
        ‹ Prev
      </button>
      <span className="pagination-status">
        {totalCount === 0 ? "0 of 0" : `${rangeStart}–${rangeEnd} of ${totalCount}`} · page {page} of {pageCount}
      </span>
      <button onClick={() => onPageChange(page + 1)} disabled={page >= pageCount}>
        Next ›
      </button>
    </div>
  );
}
