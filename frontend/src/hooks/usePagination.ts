import { useEffect, useMemo, useState } from "react";

export const PAGE_SIZE_OPTIONS = [20, 30, 50, 100] as const;

/** Slices `items` into pages, resetting to page 1 whenever the underlying
 * item count changes (e.g. a filter/sort narrows the list) so you never end
 * up stranded on a now-empty page. */
export function usePagination<T>(items: T[], defaultPageSize: number = 50) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(defaultPageSize);

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  useEffect(() => {
    setPage(1);
  }, [items.length, pageSize]);

  const pageItems = useMemo(() => {
    const start = (page - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, page, pageSize]);

  return {
    page, setPage, pageSize, setPageSize, pageCount, pageItems,
    rangeStart: items.length === 0 ? 0 : (page - 1) * pageSize + 1,
    rangeEnd: Math.min(page * pageSize, items.length),
  };
}
