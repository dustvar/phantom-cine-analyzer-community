# Phantom Cine Analyzer Additional Features launcher

This directory contains the Electron launcher. The Track & Measure module is
maintained separately in `../app-source/modules/trackmeasure` and is copied into
the packaged application during the build.

## Local development

1. Install the Node dependencies with `npm install`.
2. Create the PCA Conda environment using the scripts in `scripts/`.
3. Run the launcher using the Electron command configured by your development
   environment.

The launcher locates Miniforge/Miniconda/Anaconda on macOS, Windows, and Linux,
activates the `PCA` environment, and launches the selected Python module.

## Signing

Signing and notarization credentials must be supplied through environment
variables and must never be committed:

```sh
export APPLE_ID="developer@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="<app-specific-password>"
export APPLE_TEAM_ID="<team-id>"
export CSC_NAME="Developer ID Application: Example (<team-id>)"
export CSC_LINK="/secure/path/to/signing-certificate.p12"
export CSC_KEY_PASSWORD="<certificate-password>"
```

`scripts/set-mac-env.sh` validates that these variables are already present.
Certificate files are intentionally excluded from this repository.

See the root [README](../README.md) and [developer handoff](../docs/DEVELOPER_HANDOFF.md)
for the repository layout and feature-specific change maps.
