#!/usr/bin/env bash
# Verifies the single mandated journey (long video -> transcribe -> AI
# selects clips -> real ffmpeg cut/hook/captions -> review -> publish)
# against a REAL, already-deployed instance (Render or otherwise). Run
# this from a machine that can actually reach that instance - this
# sandbox's own network policy cannot (onrender.com/render.com are
# blocked here; see docs/PILOT_ENVIRONMENT_STATUS.md).
#
# Fails fast and loud on the exact stage that's still a placeholder,
# rather than a generic non-zero exit - every check prints PASS/FAIL and
# why, and a final summary table lists every stage's outcome.
#
# Usage:
#   BASE_URL=https://your-app.onrender.com \
#   AUTH_CLIENT_ID=pilot-operator \
#   AUTH_CLIENT_SECRET=<real value from Render dashboard> \
#   SOURCE_VIDEO_PATH=/path/to/a/real/long-form-video.mp4 \
#   bash scripts/verify_production_pipeline.sh
set -uo pipefail

BASE_URL="${BASE_URL:-}"
AUTH_CLIENT_ID="${AUTH_CLIENT_ID:-pilot-operator}"
AUTH_CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"
SOURCE_VIDEO_PATH="${SOURCE_VIDEO_PATH:-}"

declare -a RESULTS=()
FAILED=0

pass() { RESULTS+=("PASS - $1"); echo "PASS: $1"; }
fail() { RESULTS+=("FAIL - $1"); echo "FAIL: $1" >&2; FAILED=1; }
info() { echo "....  $1"; }

if [[ -z "$BASE_URL" || -z "$AUTH_CLIENT_SECRET" || -z "$SOURCE_VIDEO_PATH" ]]; then
  echo "Set BASE_URL, AUTH_CLIENT_SECRET, and SOURCE_VIDEO_PATH (env vars) before running this script." >&2
  exit 2
fi
if [[ ! -f "$SOURCE_VIDEO_PATH" ]]; then
  echo "SOURCE_VIDEO_PATH ($SOURCE_VIDEO_PATH) does not exist." >&2
  exit 2
fi

echo "== 0) /health - resolved pipeline config (no auth, no video needed) =="
HEALTH=$(curl -sS --max-time 20 "$BASE_URL/health")
echo "$HEALTH"
echo "$HEALTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get('pipeline', {})
print('resolved transcription_provider:', p.get('transcription_provider'))
print('resolved clip_renderer_backend:', p.get('clip_renderer_backend'))
print('resolved llm_provider:', p.get('llm_provider'))
print('media_backup_enabled:', p.get('media_backup_enabled'))
print('media_backup_publicly_hostable:', p.get('media_backup_publicly_hostable'))
print('publishing_enabled:', p.get('publishing_enabled'))
sys.exit(0 if p else 1)
" || { fail "GET /health did not return a 'pipeline' block - is this build's code up to date?"; }

TRANSCRIPTION_PROVIDER=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pipeline',{}).get('transcription_provider'))")
CLIP_RENDERER=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pipeline',{}).get('clip_renderer_backend'))")
LLM_PROVIDER=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pipeline',{}).get('llm_provider'))")
PUBLIC_HOSTABLE=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pipeline',{}).get('media_backup_publicly_hostable'))")

[[ "$TRANSCRIPTION_PROVIDER" == "groq" ]] && pass "TRANSCRIPTION_PROVIDER resolved to groq (not null)" || fail "TRANSCRIPTION_PROVIDER resolved to '$TRANSCRIPTION_PROVIDER' -> real transcription will NOT run (check GROQ_API_KEY is set with a real value in Render's dashboard)"
[[ "$CLIP_RENDERER" == "ffmpeg" ]] && pass "CLIP_RENDERER_BACKEND resolved to ffmpeg (not null)" || fail "CLIP_RENDERER_BACKEND resolved to '$CLIP_RENDERER' -> clips will be JSON manifests, not real video"
[[ "$LLM_PROVIDER" != "fake" ]] && pass "LLM provider resolved to '$LLM_PROVIDER' (not the fake default)" || fail "LLM provider resolved to 'fake' -> analyze() will produce fabricated, not real, clip selections"

if [[ "$FAILED" == "1" ]]; then
  echo
  echo "Stopping here: the config alone already proves the pipeline cannot produce a real result. Fix the above in Render's Environment tab, redeploy, and re-run this script before continuing." >&2
  printf '%s\n' "${RESULTS[@]}"
  exit 1
fi

echo
echo "== auth =="
TOKEN=$(curl -sS --max-time 20 -X POST "$BASE_URL/auth/token" \
  -H "content-type: application/json" \
  -d "{\"client_id\":\"$AUTH_CLIENT_ID\",\"client_secret\":\"$AUTH_CLIENT_SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
  fail "could not obtain an auth token - check AUTH_CLIENT_ID/AUTH_CLIENT_SECRET"
  printf '%s\n' "${RESULTS[@]}"
  exit 1
fi
pass "obtained a real auth token"
AUTH=(-H "Authorization: Bearer $TOKEN")

echo
echo "== 1) upload a real long-form video =="
UPLOAD=$(curl -sS --max-time 120 -X POST "$BASE_URL/source-videos" "${AUTH[@]}" \
  -F "title=Production verification upload" \
  -F "file=@${SOURCE_VIDEO_PATH};type=video/mp4")
echo "$UPLOAD"
SV_ID=$(echo "$UPLOAD" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[[ -n "$SV_ID" ]] && pass "uploaded source video (id=$SV_ID)" || { fail "upload failed: $UPLOAD"; printf '%s\n' "${RESULTS[@]}"; exit 1; }

echo
echo "== 2) transcribe - must use Groq, non-empty transcript_text =="
TRANSCRIBE=$(curl -sS --max-time 300 -X POST "$BASE_URL/source-videos/$SV_ID/transcribe" "${AUTH[@]}" \
  -H "content-type: application/json" -d '{}')
echo "$TRANSCRIBE"
TR_STATUS=$(echo "$TRANSCRIBE" | python3 -c "import sys,json;print(json.load(sys.stdin).get('transcription_status',''))" 2>/dev/null)
TR_TEXT=$(echo "$TRANSCRIBE" | python3 -c "import sys,json;print(json.load(sys.stdin).get('transcript_text') or '')" 2>/dev/null)
if [[ "$TR_STATUS" == "completed" && -n "$TR_TEXT" ]]; then
  pass "real, non-empty transcript produced ($(echo -n "$TR_TEXT" | wc -c) chars)"
else
  fail "transcription_status='$TR_STATUS', transcript_text empty -> still on a placeholder/failed transcription"
fi

echo
echo "== 3) analyze - must produce real, hooked clips =="
CLIPS=$(curl -sS --max-time 120 -X POST "$BASE_URL/source-videos/$SV_ID/analyze" "${AUTH[@]}" \
  -H "content-type: application/json" -d '{"max_clips": 3}')
echo "$CLIPS"
CLIP_COUNT=$(echo "$CLIPS" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [[ "$CLIP_COUNT" -gt 0 ]]; then
  pass "$CLIP_COUNT real clip(s) suggested"
else
  fail "analyze produced zero clips -> LLM call failed or returned nothing usable"
  printf '%s\n' "${RESULTS[@]}"
  exit 1
fi
FIRST_CLIP_ID=$(echo "$CLIPS" | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")

echo
echo "== 4/5/6/7) render clip $FIRST_CLIP_ID - real cut, hook, captions, 9:16 output =="
VIDEO=$(curl -sS --max-time 180 -X POST "$BASE_URL/clips/$FIRST_CLIP_ID/render" "${AUTH[@]}" \
  -H "content-type: application/json" -d '{}')
echo "$VIDEO"
VIDEO_ID=$(echo "$VIDEO" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
QC_STATUS=$(echo "$VIDEO" | python3 -c "import sys,json;print(json.load(sys.stdin).get('qc_status',''))" 2>/dev/null)
ASSET_URL=$(echo "$VIDEO" | python3 -c "import sys,json;print(json.load(sys.stdin).get('asset_url') or '')" 2>/dev/null)
if [[ -n "$VIDEO_ID" && "$QC_STATUS" == "passed" && "$ASSET_URL" == *.mp4 ]]; then
  pass "real .mp4 rendered, QC passed (asset_url=$ASSET_URL)"
else
  fail "render did not produce a real, QC-passed .mp4 (qc_status='$QC_STATUS', asset_url='$ASSET_URL')"
  printf '%s\n' "${RESULTS[@]}"
  exit 1
fi

echo
echo "== 8) confirm the output is a real, playable file =="
if [[ "$ASSET_URL" == http* ]]; then
  curl -sS --max-time 60 "$ASSET_URL" -o /tmp/prod_verify_clip.mp4
else
  curl -sS --max-time 60 "$BASE_URL/videos/$VIDEO_ID/file" "${AUTH[@]}" -o /tmp/prod_verify_clip.mp4
fi
FILE_TYPE=$(file -b /tmp/prod_verify_clip.mp4 2>/dev/null || echo "")
info "file: $FILE_TYPE"
if echo "$FILE_TYPE" | grep -qiE "MP4|ISO Media"; then
  pass "downloaded asset is a real MP4 container"
else
  fail "downloaded asset does not look like a real MP4 (got: $FILE_TYPE)"
fi
if command -v ffprobe >/dev/null 2>&1; then
  STREAMS=$(ffprobe -v error -show_entries stream=codec_type,width,height -of default=noprint_wrappers=1 /tmp/prod_verify_clip.mp4 2>&1)
  echo "$STREAMS"
  if echo "$STREAMS" | grep -q "codec_type=video" && echo "$STREAMS" | grep -q "codec_type=audio"; then
    pass "ffprobe confirms both a real video and audio stream"
  else
    fail "ffprobe did not find both a video and an audio stream"
  fi
else
  info "ffprobe not installed locally - open /tmp/prod_verify_clip.mp4 in a player to confirm it's watchable"
fi

echo
echo "== 9/10) review approval -> publish cascade =="
REVIEW=$(curl -sS --max-time 60 -X POST "$BASE_URL/videos/$VIDEO_ID/review" "${AUTH[@]}" \
  -H "content-type: application/json" -d '{"decision":"approved"}')
echo "$REVIEW"
PUBLISH_STATUS=$(echo "$REVIEW" | python3 -c "import sys,json;print(json.load(sys.stdin).get('auto_publish_status') or '')" 2>/dev/null)
PUBLISH_DETAIL=$(echo "$REVIEW" | python3 -c "import sys,json;print(json.load(sys.stdin).get('auto_publish_detail') or '')" 2>/dev/null)
case "$PUBLISH_STATUS" in
  published)
    pass "auto-publish reached a real provider and PUBLISHED for real: $PUBLISH_DETAIL" ;;
  scheduled)
    pass "auto-publish cascade ran with zero manual calls (provider: see detail) - a human/ManualPublishingProvider still posts it: $PUBLISH_DETAIL"
    info "if a real OwnedAccount + access token + INSTAGRAM_APP_ID/APP_SECRET are all configured and this still says 'scheduled' via ManualPublishingProvider rather than reaching the real Instagram provider, check those three are actually set in Render's dashboard." ;;
  skipped)
    fail "auto-publish was skipped: $PUBLISH_DETAIL" ;;
  failed)
    fail "auto-publish failed: $PUBLISH_DETAIL" ;;
  *)
    fail "unexpected auto_publish_status: '$PUBLISH_STATUS' ($PUBLISH_DETAIL)" ;;
esac

echo
echo "================= SUMMARY ================="
printf '%s\n' "${RESULTS[@]}"
if [[ "$FAILED" == "1" ]]; then
  echo
  echo "Overall: FAIL - see the FAIL lines above for exactly which stage to fix."
  exit 1
fi
echo
echo "Overall: PASS - the full pipeline produced a real clip end-to-end on this deployment."
