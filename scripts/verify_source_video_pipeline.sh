#!/usr/bin/env bash
# Verifies the full downstream pipeline (transcribe -> AI selects clips ->
# real ffmpeg render -> review -> publish) against EXISTING source videos
# already registered in a live deployment - unlike
# verify_production_pipeline.sh, which uploads its own fresh synthetic
# video, this targets specific source_video_ids (e.g. ones just ingested
# via the Content Rewards connector) so the real production data can be
# verified without creating throwaway videos.
#
# Never stops at the first failing video: each id is verified
# independently and every stage's outcome is recorded, so one video
# failing at (say) render still lets every other video's real result be
# seen, and a single final per-video summary table is printed at the end.
#
# Usage:
#   BASE_URL=https://your-app.onrender.com \
#   AUTH_CLIENT_ID=pilot-operator \
#   AUTH_CLIENT_SECRET=<real value from Render dashboard> \
#   SOURCE_VIDEO_IDS="26 28 29" \
#   bash scripts/verify_source_video_pipeline.sh
set -uo pipefail

BASE_URL="${BASE_URL:-}"
AUTH_CLIENT_ID="${AUTH_CLIENT_ID:-pilot-operator}"
AUTH_CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"
SOURCE_VIDEO_IDS="${SOURCE_VIDEO_IDS:-}"

if [[ -z "$BASE_URL" || -z "$AUTH_CLIENT_SECRET" || -z "$SOURCE_VIDEO_IDS" ]]; then
  echo "Set BASE_URL, AUTH_CLIENT_SECRET, and SOURCE_VIDEO_IDS (space/comma-separated) before running this script." >&2
  exit 2
fi

echo "== auth =="
TOKEN=$(curl -sS --max-time 20 -X POST "$BASE_URL/auth/token" \
  -H "content-type: application/json" \
  -d "{\"client_id\":\"$AUTH_CLIENT_ID\",\"client_secret\":\"$AUTH_CLIENT_SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
  echo "FAIL: could not obtain an auth token - check AUTH_CLIENT_ID/AUTH_CLIENT_SECRET" >&2
  exit 1
fi
echo "PASS: obtained a real auth token"
AUTH=(-H "Authorization: Bearer $TOKEN")

declare -a SUMMARY=()
OVERALL_FAILED=0

verify_one_video() {
  local sv_id="$1"
  local transcription="not run" clip_selection="not run" render="not run" publish="not run"

  echo
  echo "=================== source_video_id=$sv_id ==================="

  echo "-- transcribe --"
  local transcribe
  transcribe=$(curl -sS --max-time 300 -X POST "$BASE_URL/source-videos/$sv_id/transcribe" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{}')
  echo "$transcribe"
  local tr_status tr_text
  tr_status=$(echo "$transcribe" | python3 -c "import sys,json;print(json.load(sys.stdin).get('transcription_status',''))" 2>/dev/null)
  tr_text=$(echo "$transcribe" | python3 -c "import sys,json;print(json.load(sys.stdin).get('transcript_text') or '')" 2>/dev/null)
  if [[ "$tr_status" == "completed" && -n "$tr_text" ]]; then
    transcription="PASS (transcript: $(echo -n "$tr_text" | wc -c) chars)"
  else
    transcription="FAIL (transcription_status='$tr_status', transcript_text empty)"
    SUMMARY+=("$sv_id | transcription=$transcription | clip_selection=$clip_selection | render=$render | publish=$publish")
    OVERALL_FAILED=1
    return
  fi

  echo "-- analyze (clip selection) --"
  local clips
  clips=$(curl -sS --max-time 120 -X POST "$BASE_URL/source-videos/$sv_id/analyze" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{"max_clips": 3}')
  echo "$clips"
  local clip_count first_clip_id
  clip_count=$(echo "$clips" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
  if [[ "$clip_count" -gt 0 ]]; then
    clip_selection="PASS ($clip_count clip(s) suggested)"
    first_clip_id=$(echo "$clips" | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
  else
    clip_selection="FAIL (zero clips suggested)"
    SUMMARY+=("$sv_id | transcription=$transcription | clip_selection=$clip_selection | render=$render | publish=$publish")
    OVERALL_FAILED=1
    return
  fi

  echo "-- render clip $first_clip_id --"
  local video
  video=$(curl -sS --max-time 180 -X POST "$BASE_URL/clips/$first_clip_id/render" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{}')
  echo "$video"
  local video_id qc_status asset_url
  video_id=$(echo "$video" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  qc_status=$(echo "$video" | python3 -c "import sys,json;print(json.load(sys.stdin).get('qc_status',''))" 2>/dev/null)
  asset_url=$(echo "$video" | python3 -c "import sys,json;print(json.load(sys.stdin).get('asset_url') or '')" 2>/dev/null)
  if [[ -n "$video_id" && "$qc_status" == "passed" && "$asset_url" == *.mp4 ]]; then
    render="PASS (video_id=$video_id, asset_url=$asset_url)"
  else
    render="FAIL (video_id='$video_id', qc_status='$qc_status', asset_url='$asset_url')"
    SUMMARY+=("$sv_id | transcription=$transcription | clip_selection=$clip_selection | render=$render | publish=$publish")
    OVERALL_FAILED=1
    return
  fi

  echo "-- review approval -> publish cascade --"
  local review publish_status publish_detail
  review=$(curl -sS --max-time 60 -X POST "$BASE_URL/videos/$video_id/review" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{"decision":"approved"}')
  echo "$review"
  publish_status=$(echo "$review" | python3 -c "import sys,json;print(json.load(sys.stdin).get('auto_publish_status') or '')" 2>/dev/null)
  publish_detail=$(echo "$review" | python3 -c "import sys,json;print(json.load(sys.stdin).get('auto_publish_detail') or '')" 2>/dev/null)
  case "$publish_status" in
    published) publish="PASS (published: $publish_detail)" ;;
    scheduled) publish="PASS (scheduled/manual: $publish_detail)" ;;
    skipped) publish="FAIL (skipped: $publish_detail)"; OVERALL_FAILED=1 ;;
    failed) publish="FAIL (failed: $publish_detail)"; OVERALL_FAILED=1 ;;
    *) publish="FAIL (unexpected status '$publish_status': $publish_detail)"; OVERALL_FAILED=1 ;;
  esac

  SUMMARY+=("$sv_id | transcription=$transcription | clip_selection=$clip_selection | render=$render | publish=$publish")
}

for sv_id in $(echo "$SOURCE_VIDEO_IDS" | tr ',' ' '); do
  verify_one_video "$sv_id"
done

echo
echo "================= PER-VIDEO SUMMARY ================="
for line in "${SUMMARY[@]}"; do
  echo "$line"
done

if [[ "$OVERALL_FAILED" == "1" ]]; then
  echo
  echo "Overall: at least one video did not complete the full pipeline - see FAIL entries above."
  exit 1
fi
echo
echo "Overall: PASS - every video completed the full pipeline for real."
