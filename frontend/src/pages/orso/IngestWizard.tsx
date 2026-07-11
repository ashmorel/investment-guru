import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, applyAllocation, ingestCsv, ingestErrorMessage, ingestScreenshot } from "../../lib/api";
import type { AllocationDraft, ApplyItem, ApplyNewFund, ApplyRequest, OrsoFundOut } from "../../lib/types";
import DraftReview from "./DraftReview";
import { draftRowToEditable, fundToEditableRow, type EditableRow } from "./draftModel";
import FundSearch from "./FundSearch";

type IngestMode = "csv" | "screenshot";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function buildApplyRequest(rows: EditableRow[]): ApplyRequest {
  const newFunds = new Map<string, ApplyNewFund>();

  const allocations: ApplyItem[] = rows.map((row) => {
    let fund_id: number | null = null;
    let new_fund_code: string | null = null;

    if (row.matchedFundId != null) {
      fund_id = row.matchedFundId;
    } else if (row.proposed) {
      new_fund_code = row.proposed.code;
      if (!newFunds.has(row.proposed.code)) {
        newFunds.set(row.proposed.code, {
          code: row.proposed.code,
          name: row.proposed.name,
          currency: row.currency || row.proposed.currency,
          asset_class: row.proposed.asset_class,
          risk_rating: row.proposed.risk_rating,
        });
      }
    }

    const value = row.value.trim();
    return {
      fund_id,
      new_fund_code,
      units: row.units.trim() || "0",
      contribution_pct: row.contributionPct.trim() || "0",
      price: value ? { market_value: value, as_of: todayIso() } : null,
    };
  });

  return { new_funds: Array.from(newFunds.values()), allocations };
}

export default function IngestWizard() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<IngestMode>("csv");
  const [draft, setDraft] = useState<AllocationDraft | null>(null);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const [showFundSearch, setShowFundSearch] = useState(false);
  const [savedFundCount, setSavedFundCount] = useState(0);

  const ingest = useMutation({
    mutationFn: (file: File) => (mode === "csv" ? ingestCsv(file) : ingestScreenshot(file)),
    onSuccess: (data) => {
      setDraft(data);
      setRows(data.rows.map((r, i) => draftRowToEditable(r, i)));
    },
  });

  const apply = useMutation({
    mutationFn: applyAllocation,
    onSuccess: () => {
      setSavedFundCount(rows.length);
      qc.invalidateQueries({ queryKey: ["orso", "overview"] });
      qc.invalidateQueries({ queryKey: ["orso", "switchlog"] });
    },
  });

  function handleFile(file: File) {
    ingest.mutate(file);
  }

  function handleDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  }

  function handleBack() {
    setDraft(null);
    setRows([]);
    ingest.reset();
  }

  function handleConfirm() {
    apply.mutate(buildApplyRequest(rows));
  }

  function handleFundSelect(fund: OrsoFundOut) {
    setRows((r) => [...r, fundToEditableRow(fund, r.length)]);
    setShowFundSearch(false);
  }

  const ingestErr = ingest.isError ? (ingestErrorMessage(ingest.error) ?? "Could not read that file — please try again.") : null;
  const confirmErr = apply.isError ? applyErrorMessage(apply.error) : null;

  const saved = apply.isSuccess;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text">Import ORSO statement</h1>
        <p className="text-sm text-muted">HSBC ORSO · review before saving</p>
      </div>

      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <p className="text-xs font-semibold uppercase tracking-wide text-accent">Step 1 of 3</p>
        <h2 className="mt-1 text-lg font-medium text-text">Upload</h2>
        <p className="mt-1 text-sm text-muted">
          Upload your HSBC ORSO statement — we'll read it and let you review before saving.
        </p>

        <fieldset className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <legend className="sr-only">Import method</legend>
          <label
            className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 ${
              mode === "csv" ? "border-accent bg-accent-subtle" : "border-border"
            }`}
          >
            <input
              type="radio"
              name="ingest-mode"
              checked={mode === "csv"}
              onChange={() => setMode("csv")}
              className="mt-1"
            />
            <span>
              <span className="block text-sm font-medium text-text">CSV file</span>
              <span className="block text-xs text-muted">Exported from HSBC ORSO member portal</span>
            </span>
          </label>
          <label
            className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 ${
              mode === "screenshot" ? "border-accent bg-accent-subtle" : "border-border"
            }`}
          >
            <input
              type="radio"
              name="ingest-mode"
              checked={mode === "screenshot"}
              onChange={() => setMode("screenshot")}
              className="mt-1"
            />
            <span>
              <span className="block text-sm font-medium text-text">Statement screenshot</span>
              <span className="block text-xs text-muted">A photo of your statement (PNG or JPG)</span>
            </span>
          </label>
        </fieldset>

        <label
          htmlFor="orso-ingest-file"
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="mt-4 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed border-border p-8 text-center"
        >
          <input
            id="orso-ingest-file"
            type="file"
            accept={mode === "csv" ? ".csv,text/csv" : "image/png,image/jpeg,image/webp,image/gif"}
            onChange={handleFileChange}
            className="sr-only"
          />
          <p className="text-sm font-medium text-text">
            {ingest.isPending
              ? "Reading your statement…"
              : `Drag & drop your ${mode === "csv" ? "CSV" : "screenshot"} here, or click to browse`}
          </p>
          <p className="text-xs text-muted">CSV, PNG or JPG · max 2MB</p>
        </label>
        {ingestErr && (
          <p role="alert" className="mt-2 text-sm text-loss">
            {ingestErr}
          </p>
        )}
      </section>

      {draft && !saved && (
        <>
          <DraftReview
            rows={rows}
            onRowsChange={setRows}
            serverWarnings={draft.warnings}
            onBack={handleBack}
            onConfirm={handleConfirm}
            confirmPending={apply.isPending}
            confirmError={confirmErr}
          />

          <section className="rounded-xl border border-border bg-surface p-5 shadow">
            <button
              type="button"
              onClick={() => setShowFundSearch((s) => !s)}
              className="text-sm text-accent underline"
            >
              {showFundSearch ? "Hide fund search" : "+ Add another fund"}
            </button>
            {showFundSearch && (
              <div className="mt-3">
                <FundSearch onSelect={handleFundSelect} />
              </div>
            )}
          </section>
        </>
      )}

      {saved && (
        <section className="rounded-xl border border-border bg-surface p-5 shadow">
          <p className="text-xs font-semibold uppercase tracking-wide text-accent">Step 3 of 3</p>
          <div className="mt-2 flex items-start gap-2">
            <span aria-hidden="true" className="text-gain">
              ✓
            </span>
            <div>
              <h2 className="font-medium text-text">Allocation updated · switch logged</h2>
              <p className="mt-1 text-sm text-muted">
                {savedFundCount} fund{savedFundCount === 1 ? "" : "s"} updated. Contribution split saved
                as entered — you can adjust anytime in Review &amp; edit.
              </p>
            </div>
          </div>
          <Link
            to="/orso"
            className="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm text-white"
          >
            View ORSO overview
          </Link>
        </section>
      )}
    </div>
  );
}

function applyErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Could not save the allocation — please try again.";
  try {
    const body = JSON.parse(error.message) as { detail?: string };
    if (body.detail) return `Could not save: ${body.detail.replace(/_/g, " ")}.`;
  } catch {
    /* fall through */
  }
  return "Could not save the allocation — please review the rows and try again.";
}
