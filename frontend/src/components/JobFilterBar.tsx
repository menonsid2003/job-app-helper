interface JobFilterBarProps {
  roleCategories: string[];
  roleCategoryFilter: string;
  onRoleCategoryChange: (value: string) => void;
  sponsorshipFilter: string;
  onSponsorshipChange: (value: string) => void;
  sources: string[];
  sourceFilter: string;
  onSourceChange: (value: string) => void;
  locations: string[];
  locationFilter: string;
  onLocationChange: (value: string) => void;
  remoteFilter: string;
  onRemoteChange: (value: string) => void;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  visibleCount: number;
  totalCount: number;
}

export function JobFilterBar({
  roleCategories, roleCategoryFilter, onRoleCategoryChange,
  sponsorshipFilter, onSponsorshipChange,
  sources, sourceFilter, onSourceChange,
  locations, locationFilter, onLocationChange,
  remoteFilter, onRemoteChange,
  searchQuery, onSearchChange,
  visibleCount, totalCount,
}: JobFilterBarProps) {
  const hasFilters =
    roleCategoryFilter !== "" || sponsorshipFilter !== "" || sourceFilter !== "" || locationFilter !== "" ||
    remoteFilter !== "" || searchQuery !== "";

  return (
    <div className="jobs-table-filters">
      <label className="field-inline">
        <span className="field-label">Search</span>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Company or title…"
        />
      </label>
      <label className="field-inline">
        <span className="field-label">Role category</span>
        <select value={roleCategoryFilter} onChange={(e) => onRoleCategoryChange(e.target.value)}>
          <option value="">All</option>
          {roleCategories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <label className="field-inline">
        <span className="field-label">Sponsorship</span>
        <select value={sponsorshipFilter} onChange={(e) => onSponsorshipChange(e.target.value)}>
          <option value="">All</option>
          <option value="yes">Sponsors</option>
          <option value="no">Won't sponsor</option>
          <option value="not_mentioned">Not mentioned</option>
        </select>
      </label>
      <label className="field-inline">
        <span className="field-label">Source</span>
        <select value={sourceFilter} onChange={(e) => onSourceChange(e.target.value)}>
          <option value="">All</option>
          {sources.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="field-inline">
        <span className="field-label">Location</span>
        <select value={locationFilter} onChange={(e) => onLocationChange(e.target.value)}>
          <option value="">All</option>
          {locations.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label className="field-inline">
        <span className="field-label">Remote</span>
        <select value={remoteFilter} onChange={(e) => onRemoteChange(e.target.value)}>
          <option value="">All</option>
          <option value="remote">Remote</option>
          <option value="not_remote">Not remote</option>
          <option value="unknown">Unknown</option>
        </select>
      </label>
      {hasFilters && (
        <button
          className="filters-clear"
          onClick={() => {
            onRoleCategoryChange("");
            onSponsorshipChange("");
            onSourceChange("");
            onLocationChange("");
            onRemoteChange("");
            onSearchChange("");
          }}
        >
          Clear filters
        </button>
      )}
      <span className="jobs-table-count">
        {visibleCount} of {totalCount}
      </span>
    </div>
  );
}
