#!/bin/bash
set -e

APP_PATH="dist/mac-arm64/PhantomCineAnalyzer.app"
DMG_PATH="dist/PhantomCineAnalyzer-1.1.17-mac-arm64.dmg"
ZIP_NAME="PhantomCineAnalyzer.zip"
VOL_NAME="PhantomCineAnalyzer"

# ✅ Check environment variables
echo "🔍 Checking environment variables..."
if [[ -z "$APPLE_ID" || -z "$APPLE_TEAM_ID" || -z "$APPLE_APP_SPECIFIC_PASSWORD" || -z "$CSC_NAME" ]]; then
  echo "❌ Missing required environment variables. Please source your env script first."
  exit 1
fi
echo "✅ Env OK: APPLE_ID=$APPLE_ID | TEAM_ID=$APPLE_TEAM_ID | CSC_NAME=$CSC_NAME"

# ✅ Step 1: Sign the APP (if not already signed)
echo
echo "🔏 Signing APP..."
codesign --deep --force --verify --verbose \
  --sign "$CSC_NAME" \
  --options runtime \
  "$APP_PATH"
echo "✅ APP signed."

# ✅ Step 2: Zip the APP for notarization
echo
echo "📦 Creating ZIP for APP notarization..."
ditto -c -k --keepParent "$APP_PATH" "$ZIP_NAME"
echo "✅ ZIP created: $ZIP_NAME"

# ✅ Step 3: Submit APP for notarization
echo
echo "📝 Submitting APP for notarization..."
xcrun notarytool submit "$ZIP_NAME" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --wait
echo "✅ APP notarized successfully."

# ✅ Step 4: Staple APP
echo
echo "📌 Stapling APP..."
if xcrun stapler staple "$APP_PATH"; then
  echo "✅ APP stapled."
else
  echo "❌ APP stapling failed! Will still proceed with DMG."
fi

# ✅ Step 5: Create DMG
echo
echo "📦 Creating DMG..."
hdiutil create -volname "$VOL_NAME" \
  -srcfolder "$APP_PATH" \
  -ov -format UDZO "$DMG_PATH"
echo "✅ DMG created: $DMG_PATH"

# ✅ Step 6: Sign DMG
echo
echo "🔏 Signing DMG..."
codesign --sign "$CSC_NAME" --timestamp --options runtime "$DMG_PATH"
echo "✅ DMG signed."

# ✅ Step 7: Submit DMG for notarization
echo
echo "📝 Submitting DMG for notarization..."
xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --wait
echo "✅ DMG notarized successfully."

# ✅ Step 8: Staple DMG
echo
echo "📌 Stapling DMG..."
if xcrun stapler staple "$DMG_PATH"; then
  echo "✅ DMG stapled."
else
  echo "❌ DMG stapling failed! Running diagnostics..."
  
  # Diagnostics
  echo
  echo "🔍 Checking codesign status..."
  codesign --verify --deep --strict --verbose=2 "$DMG_PATH" || echo "❌ Signature invalid"

  echo
  echo "🔍 Checking Gatekeeper assessment..."
  spctl --assess --type open --verbose "$DMG_PATH" || echo "❌ Gatekeeper rejected"

  echo
  echo "🔍 Checking notarization ticket..."
  xcrun stapler validate "$DMG_PATH" || echo "❌ Stapler cannot validate ticket"
fi

# ✅ Step 9: Verify APP and DMG
echo
echo "🔍 Verifying APP..."
spctl --assess --type execute --verbose "$APP_PATH"
echo "✅ APP verification done."

echo
echo "🔍 Verifying DMG..."
spctl --assess --type open --verbose "$DMG_PATH"
echo "✅ DMG verification done."