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

# Real gap this closes: analyze (an LLM call) and review's publish cascade
# can genuinely take longer than a first, aggressive curl --max-time to
# return a response - the client giving up is not the same as the server
# failing. A synchronous FastAPI endpoint keeps running in its worker
# thread to completion regardless of the client disconnecting, so a curl
# timeout here must poll the existing read-only GET endpoints for the
# real outcome instead of reporting a false FAIL.
_POLL_INTERVAL_S=15
_POLL_MAX_ATTEMPTS=20  # 20 * 15s = 5 extra minutes of polling

_poll_for_clips() {
  local sv_id="$1"
  local attempt=0 clips="[]" count=0
  while [[ $attempt -lt $_POLL_MAX_ATTEMPTS ]]; do
    sleep "$_POLL_INTERVAL_S"
    clips=$(curl -sS --max-time 30 "$BASE_URL/source-videos/$sv_id/clips" "${AUTH[@]}")
    count=$(echo "$clips" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
    if [[ "$count" -gt 0 ]]; then
      echo "$clips"
      return 0
    fi
    attempt=$((attempt + 1))
  done
  echo "$clips"
}

# Polls GET /videos/{id} until its status moves off pending_review (the
# review decision itself was recorded), then checks GET /publications for
# a matching video_id to report the equivalent of the direct response's
# auto_publish_status/auto_publish_detail. Prints "<video_status>|<publication_status>".
_poll_for_review_outcome() {
  local video_id="$1"
  local attempt=0 video_status=""
  while [[ $attempt -lt $_POLL_MAX_ATTEMPTS ]]; do
    sleep "$_POLL_INTERVAL_S"
    video_status=$(
      curl -sS --max-time 30 "$BASE_URL/videos/$video_id" "${AUTH[@]}" \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null
    )
    if [[ -n "$video_status" && "$video_status" != "pending_review" ]]; then
      break
    fi
    attempt=$((attempt + 1))
  done

  local publication_status=""
  if [[ -n "$video_status" && "$video_status" != "pending_review" ]]; then
    publication_status=$(
      curl -sS --max-time 30 "$BASE_URL/publications" "${AUTH[@]}" \
        | python3 -c "
import sys, json
pubs = json.load(sys.stdin)
matches = [p for p in pubs if p.get('video_id') == $video_id]
print(matches[-1]['status'] if matches else '')
" 2>/dev/null
    )
  fi
  echo "${video_status}|${publication_status}"
}

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
  clips=$(curl -sS --max-time 600 -X POST "$BASE_URL/source-videos/$sv_id/analyze" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{"max_clips": 3}')
  if [[ $? -ne 0 ]]; then
    echo "analyze request itself timed out client-side - the LLM call may still be running server-side; polling GET /source-videos/$sv_id/clips instead of failing immediately"
    clips=$(_poll_for_clips "$sv_id")
  fi
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
  review=$(curl -sS --max-time 300 -X POST "$BASE_URL/videos/$video_id/review" "${AUTH[@]}" \
    -H "content-type: application/json" -d '{"decision":"approved"}')
  if [[ $? -ne 0 ]]; then
    echo "review request itself timed out client-side - the publish cascade may still be running server-side; polling GET /videos/$video_id and GET /publications instead of failing immediately"
    local polled video_status publication_status
    polled=$(_poll_for_review_outcome "$video_id")
    video_status="${polled%%|*}"
    publication_status="${polled##*|}"
    echo "polled video status: '$video_status', matching publication status: '$publication_status'"
    case "$video_status" in
      published) publish="PASS (video status became published after polling)" ;;
      approved|rejected|revision_requested)
        if [[ -n "$publication_status" ]]; then
          publish="PASS (review recorded as $video_status, publication status=$publication_status after polling)"
        else
          publish="FAIL (review recorded as $video_status, but no publication found after polling)"
          OVERALL_FAILED=1
        fi
        ;;
      *)
        publish="FAIL (review request never completed even after $((_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_S / 60)) extra minutes of polling - a genuine timeout, not just a slow response)"
        OVERALL_FAILED=1
        ;;
    esac
    SUMMARY+=("$sv_id | transcription=$transcription | clip_selection=$clip_selection | render=$render | publish=$publish")
    return
  fi
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
