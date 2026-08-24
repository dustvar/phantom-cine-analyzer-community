#!/bin/bash
set -eu

: "${APPLE_ID:?Set APPLE_ID in your secure shell or CI secret store}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?Set APPLE_APP_SPECIFIC_PASSWORD securely}"
: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID securely}"
: "${CSC_NAME:?Set CSC_NAME securely}"
: "${CSC_LINK:?Set CSC_LINK to a signing certificate outside this repository}"
: "${CSC_KEY_PASSWORD:?Set CSC_KEY_PASSWORD securely}"

echo "macOS signing variables are present. Ready to build."
