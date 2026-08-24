import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchBaseResume,
  fetchCriteria,
  fetchExperienceBank,
  fetchLocationBackfillStatus,
  rescoreAll,
  runLocationBackfill,
  saveBaseResume,
  saveCriteria,
  saveExperienceBank,
  stopLocationBackfill,
  type CriteriaConfig,
} from "../api/client";
import { ApplyRunPanel } from "../components/ApplyRunPanel";
import { ListEditor } from "../components/ListEditor";

function LocationBackfillPanel() {
  const queryClient = useQueryClient();
  const wasRunning = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["location-backfill-status"],
    queryFn: fetchLocationBackfillStatus,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : 5000),
  });
  const isRunning = statusQuery.data?.status === "running";

  useEffect(() => {
    if (wasRunning.current && !isRunning) {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["tracking-jobs"] });
    }
    wasRunning.current = isRunning;
  }, [isRunning, queryClient]);

  const runMutation = useMutation({
    mutationFn: runLocationBackfill,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["location-backfill-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopLocationBackfill,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["location-backfill-status"] }),
  });

  return (
    <ApplyRunPanel
      status={statusQuery.data}
      isRunning={isRunning}
      onRun={() => runMutation.mutate()}
      onStop={() => stopMutation.mutate()}
      runPending={runMutation.isPending}
      stopPending={stopMutation.isPending}
      runLabel="Backfill Empty Locations"
      runError={runMutation.error as Error | null}
      counts={[
        { label: "Updated", value: statusQuery.data?.updated_count ?? 0 },
        { label: "Still not found", value: statusQuery.data?.not_found_count ?? 0 },
        { label: "Skipped (jobspy disabled)", value: statusQuery.data?.skipped_count ?? 0 },
      ]}
    />
  );
}

export function Settings() {
  const queryClient = useQueryClient();
  const criteriaQuery = useQuery({ queryKey: ["criteria"], queryFn: fetchCriteria });
  const [draft, setDraft] = useState<CriteriaConfig | null>(null);

  const baseResumeQuery = useQuery({ queryKey: ["base-resume"], queryFn: fetchBaseResume });
  const [resumeDraft, setResumeDraft] = useState<string | null>(null);

  useEffect(() => {
    if (baseResumeQuery.data && resumeDraft === null) {
      setResumeDraft(baseResumeQuery.data.text);
    }
  }, [baseResumeQuery.data, resumeDraft]);

  const saveResumeMutation = useMutation({
    mutationFn: saveBaseResume,
    onSuccess: (saved) => {
      setResumeDraft(saved.text);
      queryClient.invalidateQueries({ queryKey: ["base-resume"] });
    },
  });

  const experienceBankQuery = useQuery({ queryKey: ["experience-bank"], queryFn: fetchExperienceBank });
  const [experienceBankDraft, setExperienceBankDraft] = useState<string | null>(null);

  useEffect(() => {
    if (experienceBankQuery.data && experienceBankDraft === null) {
      setExperienceBankDraft(experienceBankQuery.data.text);
    }
  }, [experienceBankQuery.data, experienceBankDraft]);

  const saveExperienceBankMutation = useMutation({
    mutationFn: saveExperienceBank,
    onSuccess: (saved) => {
      setExperienceBankDraft(saved.text);
      queryClient.invalidateQueries({ queryKey: ["experience-bank"] });
    },
  });
  // Defaults match the dashboard's "yellow" (score-mid) band in JobsTable.tsx —
  // borderline scores are exactly where a scoring-logic fix is likely to move
  // the outcome; clear-cut low scores usually aren't worth re-running the LLM on.
  const [forceMinScore, setForceMinScore] = useState("40");
  const [forceMaxScore, setForceMaxScore] = useState("69");

  useEffect(() => {
    if (criteriaQuery.data && !draft) {
      setDraft(criteriaQuery.data);
    }
  }, [criteriaQuery.data, draft]);

  const saveMutation = useMutation({
    mutationFn: saveCriteria,
    onSuccess: (saved) => {
      setDraft(saved);
      queryClient.invalidateQueries({ queryKey: ["criteria"] });
    },
  });

  const rescoreMutation = useMutation({
    mutationFn: rescoreAll,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-status"] }),
  });

  if (criteriaQuery.isLoading) return <p>Loading…</p>;
  if (criteriaQuery.isError) return <p className="error-banner">{(criteriaQuery.error as Error).message}</p>;
  if (!draft) return null;

  const update = <K extends keyof CriteriaConfig>(key: K, value: CriteriaConfig[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  return (
    <div className="settings">
      <div className="settings-actions">
        <button onClick={() => saveMutation.mutate(draft)} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? "Saving…" : "Save Criteria"}
        </button>
        <button
          onClick={() => rescoreMutation.mutate({ force: false })}
          disabled={rescoreMutation.isPending}
          title="Re-score every job whose latest score was computed under an older criteria version"
        >
          {rescoreMutation.isPending ? "Starting…" : "Rescore All (stale scores)"}
        </button>
        <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
          <span className="field-label">score</span>
          <input
            type="number"
            value={forceMinScore}
            onChange={(e) => setForceMinScore(e.target.value)}
            style={{ width: "4em" }}
          />
          <span>–</span>
          <input
            type="number"
            value={forceMaxScore}
            onChange={(e) => setForceMaxScore(e.target.value)}
            style={{ width: "4em" }}
          />
        </span>
        <button
          onClick={() => {
            const minScore = forceMinScore === "" ? undefined : Number(forceMinScore);
            const maxScore = forceMaxScore === "" ? undefined : Number(forceMaxScore);
            const range =
              minScore !== undefined || maxScore !== undefined
                ? ` with a score between ${minScore ?? "-inf"} and ${maxScore ?? "+inf"}`
                : "";
            if (
              window.confirm(
                `Force-rescore every scored/excluded job${range}, regardless of criteria version? This re-runs ` +
                  "the LLM on all of them (time and, with a paid provider, cost) — use this after a scoring-logic " +
                  "or prompt change, not a criteria.yaml edit (the plain Rescore All above already covers that)."
              )
            ) {
              rescoreMutation.mutate({ force: true, minScore, maxScore });
            }
          }}
          disabled={rescoreMutation.isPending}
          title="Ignores criteria version entirely — rescores every scored/excluded job in the score range above. Use after a scoring-logic/prompt change."
        >
          {rescoreMutation.isPending ? "Starting…" : "Force Rescore All (ignore criteria version)"}
        </button>
        {saveMutation.isSuccess && <span className="status-banner inline">Saved.</span>}
        {saveMutation.isError && <span className="error-banner inline">{(saveMutation.error as Error).message}</span>}
        {rescoreMutation.isSuccess && (
          <span className="status-banner inline">Rescore started — watch the progress panel above.</span>
        )}
      </div>

      <fieldset className="base-resume-fieldset">
        <legend>Location Data</legend>
        <p className="field-hint">
          Re-fetches every job with a blank location and fills it in if it's found again. Greenhouse/Lever/Workday
          jobs are re-checked against their company's own board; Indeed/LinkedIn/etc. (via JobSpy) are re-checked
          with a fresh search — lower odds of finding the same posting again since those boards churn faster, but
          still attempted rather than skipped.
        </p>
        <LocationBackfillPanel />
      </fieldset>

      <fieldset className="base-resume-fieldset">
        <legend>Base Resume</legend>
        <p className="field-hint">
          Paste your resume as plain text (ATS-friendly — no tables/columns/graphics). This is what gets sent to
          the LLM for scoring every job and as the starting point for per-job tailoring, so it needs to actually
          be here before you run the pipeline.
        </p>
        {baseResumeQuery.isLoading && <p>Loading…</p>}
        {baseResumeQuery.isError && (
          <p className="error-banner">{(baseResumeQuery.error as Error).message}</p>
        )}
        {resumeDraft !== null && (
          <>
            <textarea
              className="base-resume-textarea"
              rows={18}
              value={resumeDraft}
              onChange={(e) => setResumeDraft(e.target.value)}
              placeholder="Paste your ATS-plain-text resume here…"
            />
            <div className="settings-actions">
              <button
                onClick={() => saveResumeMutation.mutate(resumeDraft)}
                disabled={saveResumeMutation.isPending || resumeDraft.trim() === ""}
              >
                {saveResumeMutation.isPending ? "Saving…" : "Save Resume"}
              </button>
              {saveResumeMutation.isSuccess && <span className="status-banner inline">Saved.</span>}
              {saveResumeMutation.isError && (
                <span className="error-banner inline">{(saveResumeMutation.error as Error).message}</span>
              )}
            </div>
          </>
        )}
      </fieldset>

      <fieldset className="base-resume-fieldset">
        <legend>Experience Bank (optional)</legend>
        <p className="field-hint">
          Everything that doesn't fit the Base Resume above without breaking its one-page layout — older
          roles, one-off projects, retired skills. Tailoring reads this alongside the base resume and can
          swap an entry in from here when it's a better fit for a specific job than what's in the base resume
          (never just tacked on — the result still has to fit one page). Leave blank to tailor from the base
          resume alone.
        </p>
        {experienceBankQuery.isLoading && <p>Loading…</p>}
        {experienceBankQuery.isError && (
          <p className="error-banner">{(experienceBankQuery.error as Error).message}</p>
        )}
        {experienceBankDraft !== null && (
          <>
            <textarea
              className="base-resume-textarea"
              rows={18}
              value={experienceBankDraft}
              onChange={(e) => setExperienceBankDraft(e.target.value)}
              placeholder="Paste additional job history, older roles, and projects here — plain text, same as the base resume…"
            />
            <div className="settings-actions">
              <button
                onClick={() => saveExperienceBankMutation.mutate(experienceBankDraft)}
                disabled={saveExperienceBankMutation.isPending}
              >
                {saveExperienceBankMutation.isPending ? "Saving…" : "Save Experience Bank"}
              </button>
              {saveExperienceBankMutation.isSuccess && <span className="status-banner inline">Saved.</span>}
              {saveExperienceBankMutation.isError && (
                <span className="error-banner inline">{(saveExperienceBankMutation.error as Error).message}</span>
              )}
            </div>
          </>
        )}
      </fieldset>

      <fieldset className="auto-apply-fieldset">
        <legend>⚠ Auto-Apply</legend>
        <label className="field field-checkbox">
          <input
            type="checkbox"
            checked={draft.auto_apply_enabled}
            onChange={(e) => update("auto_apply_enabled", e.target.checked)}
          />
          <span>
            <strong>Enable auto-apply.</strong> When on, running auto-apply submits real applications with no
            per-job approval step — only for platforms with a built-in adapter (Greenhouse only right now); anything
            else is left for you to apply to manually. Off by default. See the Auto-Apply Log tab for what's
            actually been submitted.
          </span>
        </label>
        <p className="field-hint">
          This profile is also what the Agent Apply tab's AI agent uses to fill out forms and answer screening
          questions — the fields below (city through work authorization note) exist for its benefit; the
          Greenhouse/Lever auto-apply adapters above only use name/email/phone/LinkedIn/sponsorship.
        </p>
        <div className="applicant-profile-fields">
          <label className="field">
            <span className="field-label">Full name</span>
            <input
              type="text"
              value={draft.applicant_profile.full_name}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, full_name: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Email</span>
            <input
              type="text"
              value={draft.applicant_profile.email}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, email: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Phone</span>
            <input
              type="text"
              value={draft.applicant_profile.phone}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, phone: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">LinkedIn URL</span>
            <input
              type="text"
              value={draft.applicant_profile.linkedin_url}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, linkedin_url: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Requires visa sponsorship?</span>
            <span className="field-hint">
              Answers the common "will you require sponsorship" screening question consistently. Leave unset and a
              form asking this will be flagged for manual completion rather than guessed.
            </span>
            <select
              value={draft.applicant_profile.requires_visa_sponsorship === null ? "" : String(draft.applicant_profile.requires_visa_sponsorship)}
              onChange={(e) =>
                update("applicant_profile", {
                  ...draft.applicant_profile,
                  requires_visa_sponsorship: e.target.value === "" ? null : e.target.value === "true",
                })
              }
            >
              <option value="">Not set</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">City (Agent Apply)</span>
            <input
              type="text"
              value={draft.applicant_profile.city}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, city: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Target role (Agent Apply)</span>
            <span className="field-hint">Falls back to each job's own title when blank.</span>
            <input
              type="text"
              value={draft.applicant_profile.target_role}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, target_role: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Years of experience (Agent Apply)</span>
            <input
              type="number"
              min="0"
              value={draft.applicant_profile.years_of_experience ?? ""}
              onChange={(e) =>
                update("applicant_profile", {
                  ...draft.applicant_profile,
                  years_of_experience: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </label>
          <label className="field">
            <span className="field-label">Salary expectation, USD (Agent Apply)</span>
            <span className="field-hint">Falls back to the salary floor under Preferences below when blank.</span>
            <input
              type="number"
              min="0"
              value={draft.applicant_profile.salary_expectation ?? ""}
              onChange={(e) =>
                update("applicant_profile", {
                  ...draft.applicant_profile,
                  salary_expectation: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </label>
          <label className="field">
            <span className="field-label">Work authorization note (Agent Apply)</span>
            <span className="field-hint">
              Free text the agent uses verbatim for work-authorization screening questions, e.g. "US citizen, no
              sponsorship ever needed". Falls back to a generic phrasing from the sponsorship answer above when
              blank.
            </span>
            <input
              type="text"
              value={draft.applicant_profile.work_authorization_note}
              onChange={(e) =>
                update("applicant_profile", { ...draft.applicant_profile, work_authorization_note: e.target.value })
              }
            />
          </label>
          <label className="field">
            <span className="field-label">Reusable signup email (Agent Apply)</span>
            <span className="field-hint">
              Some ATSes (Workday especially) require creating a site account before you can even see the
              application form, with no guest option. Leave blank and the agent keeps stopping on those
              (RESULT:FAILED:login_issue, shown as "unsupported / login_issue" in the log) same as before. Fill
              this in and it will create — or sign into, if that email is already registered — an account with
              this email/password when a site demands one and there's no guest option or usable Google SSO. Stored
              in plaintext in criteria.yaml, so use a password dedicated to this, not one reused elsewhere.
            </span>
            <input
              type="text"
              value={draft.applicant_profile.signup_email}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, signup_email: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Reusable signup password (Agent Apply)</span>
            <input
              type="password"
              value={draft.applicant_profile.signup_password}
              onChange={(e) => update("applicant_profile", { ...draft.applicant_profile, signup_password: e.target.value })}
            />
          </label>
        </div>
      </fieldset>

      <div className="settings-grid">
        <fieldset>
          <legend>Target roles &amp; relevance</legend>
          <ListEditor
            label="Role categories"
            value={draft.role_categories}
            onChange={(v) => update("role_categories", v)}
            rows={4}
            hint="Buckets the LLM sorts each job into (shown in the Role category column) — replace these with ones that fit your field (e.g. ICU, ER, Med-Surg for nursing). 'Other' is always available as a fallback, no need to list it."
          />
          <ListEditor
            label="Target roles"
            value={draft.target_roles}
            onChange={(v) => update("target_roles", v)}
            hint="Title must contain the last word of one of these (e.g. 'Engineer', 'Developer') to reach scoring."
          />
          <ListEditor
            label="Must-have keywords"
            value={draft.must_have_keywords}
            onChange={(v) => update("must_have_keywords", v)}
            hint="Description must contain at least one of these, if any are set."
          />
          <ListEditor
            label="Nice-to-have keywords"
            value={draft.nice_to_have_keywords}
            onChange={(v) => update("nice_to_have_keywords", v)}
            hint="Only influence LLM scoring — never gate relevance."
          />
          <ListEditor
            label="Exclude keywords"
            value={draft.exclude_keywords}
            onChange={(v) => update("exclude_keywords", v)}
            hint="Word-boundary matched against title/description (e.g. 'intern' won't match 'international')."
          />
          <ListEditor label="Exclude companies" value={draft.exclude_companies} onChange={(v) => update("exclude_companies", v)} />
        </fieldset>

        <fieldset>
          <legend>Preferences</legend>
          <ListEditor
            label="Preferred locations"
            value={draft.locations}
            onChange={(v) => update("locations", v)}
            hint="Scoring signal only — US-only eligibility is a separate hard constraint below."
          />
          <ListEditor label="Seniority" value={draft.seniority} onChange={(v) => update("seniority", v)} rows={2} />
          <label className="field">
            <span className="field-label">Salary floor (USD, blank = none)</span>
            <input
              type="number"
              value={draft.salary_min ?? ""}
              onChange={(e) => update("salary_min", e.target.value === "" ? null : Number(e.target.value))}
            />
          </label>
          <label className="field field-checkbox">
            <input
              type="checkbox"
              checked={draft.prefer_full_time}
              onChange={(e) => update("prefer_full_time", e.target.checked)}
            />
            <span>Prefer full-time (contract still scored, just slightly lower all else equal)</span>
          </label>
          <label className="field">
            <span className="field-label">Canonical link resolution score threshold</span>
            <span className="field-hint">
              Only attempted for scored jobs at/above this score. Currently a no-op — Greenhouse/Lever/Workday
              links are already the company's own — but ready for a future non-canonical source.
            </span>
            <input
              type="number"
              min="0"
              max="100"
              value={draft.canonical_link_score_threshold}
              onChange={(e) => update("canonical_link_score_threshold", Number(e.target.value))}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Work authorization (hard constraint)</legend>
          <ListEditor
            label="Prefilter exclusion keywords"
            value={draft.work_authorization.hard_exclude_prefilter_keywords}
            onChange={(v) => update("work_authorization", { ...draft.work_authorization, hard_exclude_prefilter_keywords: v })}
            rows={5}
            hint="Substring-matched against the full description, before any LLM call."
          />
        </fieldset>

        <fieldset>
          <legend>Location (US-only hard constraint)</legend>
          <details>
            <summary>Non-US location keywords ({draft.exclude_location_keywords.length})</summary>
            <ListEditor
              label=""
              value={draft.exclude_location_keywords}
              onChange={(v) => update("exclude_location_keywords", v)}
              rows={10}
              hint="Never triggers if the location also contains an explicit US signal (e.g. 'Dublin, US-Remote')."
            />
          </details>
        </fieldset>

        <fieldset>
          <legend>Target companies to poll</legend>
          <label className="field field-checkbox">
            <input
              type="checkbox"
              checked={draft.company_board_connectors_enabled}
              onChange={(e) => update("company_board_connectors_enabled", e.target.checked)}
            />
            <span>
              Enable Greenhouse/Lever/Workday (the company-list connectors below). Turn off to run JobSpy-only
              discovery without losing this list — it's just not polled while off.
            </span>
          </label>
          <ListEditor
            label="Greenhouse board tokens"
            value={draft.target_companies.greenhouse ?? []}
            onChange={(v) => update("target_companies", { ...draft.target_companies, greenhouse: v })}
            rows={6}
            hint="Slug in https://boards.greenhouse.io/<token>"
          />
          <ListEditor
            label="Lever board tokens"
            value={draft.target_companies.lever ?? []}
            onChange={(v) => update("target_companies", { ...draft.target_companies, lever: v })}
            rows={6}
            hint="Slug in https://jobs.lever.co/<token>"
          />
          <ListEditor
            label="Workday career site URLs"
            value={draft.target_companies.workday ?? []}
            onChange={(v) => update("target_companies", { ...draft.target_companies, workday: v })}
            rows={6}
            hint="Full career site URL, e.g. https://redhat.wd5.myworkdayjobs.com/Jobs"
          />
        </fieldset>

        <fieldset>
          <legend>JobSpy (Indeed / LinkedIn / ZipRecruiter / ...)</legend>
          <label className="field field-checkbox">
            <input
              type="checkbox"
              checked={draft.jobspy.enabled}
              onChange={(e) => update("jobspy", { ...draft.jobspy, enabled: e.target.checked })}
            />
            <span>
              <strong>Enable JobSpy search.</strong> Searches general job boards for each target role x preferred
              location combination, instead of a per-company API. Off by default — scraping these sites carries a
              different reliability/blocking profile than Greenhouse/Lever/Workday.
            </span>
          </label>
          <ListEditor
            label="Sites"
            value={draft.jobspy.sites}
            onChange={(v) => update("jobspy", { ...draft.jobspy, sites: v })}
            rows={3}
            hint="indeed, linkedin, zip_recruiter, glassdoor, google, bayt, naukri"
          />
          <label className="field field-inline">
            <span className="field-label">Results wanted per search</span>
            <input
              type="number"
              min="1"
              value={draft.jobspy.results_wanted}
              onChange={(e) => update("jobspy", { ...draft.jobspy, results_wanted: Number(e.target.value) })}
            />
          </label>
          <label className="field field-inline">
            <span className="field-label">Max posting age (hours)</span>
            <input
              type="number"
              min="1"
              value={draft.jobspy.hours_old}
              onChange={(e) => update("jobspy", { ...draft.jobspy, hours_old: Number(e.target.value) })}
            />
          </label>
          <label className="field field-inline">
            <span className="field-label">Country (Indeed/Glassdoor)</span>
            <input
              type="text"
              value={draft.jobspy.country_indeed}
              onChange={(e) => update("jobspy", { ...draft.jobspy, country_indeed: e.target.value })}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Google Sheets Tracking</legend>
          <label className="field field-checkbox">
            <input
              type="checkbox"
              checked={draft.google_sheets.enabled}
              onChange={(e) => update("google_sheets", { ...draft.google_sheets, enabled: e.target.checked })}
            />
            <span>
              <strong>Push applied jobs to a Google Sheet.</strong> When a job's status becomes "Applied" — from the
              Tracking table, Auto-Apply, or Agent Apply alike — appends one row to the sheet below. Off by default.
            </span>
          </label>
          <p className="field-hint">
            One-time setup: in{" "}
            <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">
              Google Cloud Console
            </a>
            , create a service account and download its JSON key, save it as{" "}
            <code>backend/google/service_account.json</code>, then share your target sheet with that key's{" "}
            <code>client_email</code> as Editor. Rows are matched to your sheet's own header row by name — Company,
            Role, Location, Location Type, Salary Range, Application date, Application Type, Status, and Notes are
            recognized (case-insensitive); any other column is left blank.
          </p>
          <label className="field">
            <span className="field-label">Spreadsheet URL</span>
            <input
              type="text"
              value={draft.google_sheets.spreadsheet_url}
              onChange={(e) => update("google_sheets", { ...draft.google_sheets, spreadsheet_url: e.target.value })}
              placeholder="https://docs.google.com/spreadsheets/d/…/edit"
            />
          </label>
          <label className="field">
            <span className="field-label">Sheet/tab name</span>
            <input
              type="text"
              value={draft.google_sheets.sheet_name}
              onChange={(e) => update("google_sheets", { ...draft.google_sheets, sheet_name: e.target.value })}
              placeholder="(blank = first sheet)"
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Scoring weights</legend>
          {(
            [
              ["title_match", "Title match"],
              ["skills_overlap", "Skills overlap"],
              ["location_fit", "Location fit"],
              ["seniority_fit", "Seniority fit"],
              ["salary_fit", "Salary fit"],
            ] as const
          ).map(([key, labelText]) => (
            <label className="field field-inline" key={key}>
              <span className="field-label">{labelText}</span>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={draft.scoring_weights[key]}
                onChange={(e) => update("scoring_weights", { ...draft.scoring_weights, [key]: Number(e.target.value) })}
              />
            </label>
          ))}
        </fieldset>

        <fieldset>
          <legend>Scheduling</legend>
          <label className="field">
            <span className="field-label">GPU schedule window (local time, HH:MM-HH:MM)</span>
            <span className="field-hint">
              No GPU-load probing is done (Ollama runs on a separate LAN machine) — this is purely a time
              window, e.g. "22:00-07:00" for overnight.
            </span>
            <input
              type="text"
              value={draft.gpu_schedule_window ?? ""}
              onChange={(e) => update("gpu_schedule_window", e.target.value || null)}
              placeholder="22:00-07:00"
            />
          </label>
          <label className="field field-checkbox">
            <input
              type="checkbox"
              checked={draft.auto_schedule_enabled}
              onChange={(e) => update("auto_schedule_enabled", e.target.checked)}
            />
            <span>Automatically run the pipeline once per night within the window above</span>
          </label>
        </fieldset>
      </div>
    </div>
  );
}
