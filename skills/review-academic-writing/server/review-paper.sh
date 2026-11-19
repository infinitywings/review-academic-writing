#!/usr/bin/env bash
# One command for a whole server review: PDF -> MinerU -> cleaned edition -> staged review -> report.md.
# Runs in the background, so it survives the SSH session ending.
#
# Usage: review-paper.sh --config llm.json PAPER.pdf [--pages 1-10] [-- other orchestrator.py run options]
#   --config  editor endpoint config (dispatch_reader.py --runtime http format); also used for the readers, the
#             auditor and the challenger unless --reader-config is passed after `--`
#   --pages   PDF pages to review (MinerU page syntax), default all
# Environment: MINERU (MinerU 4 executable, default `mineru` on PATH), PDFTOTEXT (poppler's pdftotext for the
#   text-layer word repair, default `pdftotext` on PATH; set it empty to skip the repair), REVIEW_RUNS (default
#   ~/review-runs).
# Each run gets REVIEW_RUNS/<date-time>-<name>/ with pipeline.log, extract/, manuscript.md and review/.
# Unpublished manuscripts are parsed locally and stay on this machine.
set -euo pipefail
PKG=$(cd "$(dirname "$(realpath "$0")")/.." && pwd)
MINERU=${MINERU:-$(command -v mineru || true)}
PDFTOTEXT=${PDFTOTEXT-$(command -v pdftotext || true)}
RUNS=${REVIEW_RUNS:-$HOME/review-runs}

usage() { sed -n '5,13p' "$(realpath "$0")" >&2; exit 2; }

if [ "${REVIEW_PAPER_RUN:-}" = "" ]; then
  config=""; args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --config) [ $# -ge 2 ] || usage; config=$(realpath "$2"); shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  [ -n "$config" ] && [ -f "$config" ] && [ ${#args[@]} -ge 1 ] && [ -f "${args[0]}" ] || usage
  [ -n "$MINERU" ] || { echo "MinerU executable not found: set MINERU" >&2; exit 2; }
  pdf=$(realpath "${args[0]}")
  run=$RUNS/$(date +%Y%m%d-%H%M)-$(basename "$pdf" .pdf | tr -c 'A-Za-z0-9._\n-' '_')
  mkdir -p "$run"
  # setsid detaches from the SSH session on Linux; where it is missing, nohup alone ignores the hangup.
  detach=$(command -v setsid || true)
  REVIEW_PAPER_RUN=$run REVIEW_PAPER_CONFIG=$config MINERU=$MINERU PDFTOTEXT=$PDFTOTEXT \
    $detach nohup "$(realpath "$0")" "$pdf" ${args[@]+"${args[@]:1}"} > "$run/pipeline.log" 2>&1 < /dev/null &
  echo "started: $run"
  echo "progress: tail -f $run/pipeline.log"
  echo "report when done: $run/review/report.md"
  exit 0
fi

run=$REVIEW_PAPER_RUN
pdf=$1; shift
pages=all; extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --pages) pages=$2; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
name=$(basename "$pdf" .pdf)
cd "$PKG"

# MinerU's local service is a user process that a reboot does not bring back; start is a no-op when it runs.
"$MINERU" server start
echo "$(date -u +%FT%TZ) extract ($pages)"
python3 scripts/extract_pdf.py extract --pdf "$pdf" --pages "$pages" --out-dir "$run/extract" --mineru "$MINERU" --timeout 3600
# With the PDF's text layer, postprocess restores words that layout parsing split or joined ("as sume"), so they
# do not reach the review as the author's typos.
textlayer=()
[ -z "$PDFTOTEXT" ] || textlayer=(--pdf "$pdf" --pdftotext "$PDFTOTEXT")
python3 scripts/extract_pdf.py postprocess --input "$run/extract/$name.md" --out "$run/manuscript.md" \
  --pages-out "$run/pages.json" --notes-out "$run/extraction-notes.txt" ${textlayer[@]+"${textlayer[@]}"}
echo "$(date -u +%FT%TZ) review"
python3 server/orchestrator.py run --manuscript "$run/manuscript.md" --out "$run/review" --config "$REVIEW_PAPER_CONFIG" \
  --extraction-notes "$run/extraction-notes.txt" ${extra[@]+"${extra[@]}"}
echo "$(date -u +%FT%TZ) done: $run/review/report.md"
