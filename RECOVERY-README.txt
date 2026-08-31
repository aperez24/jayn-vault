JAYN Vault - Browser Recovery Snapshot
======================================

Source:
  Chrome HAR capture with cache disabled.

This package contains the first-party browser-delivered assets recovered from
the deployed JAYN Vault site. It intentionally excludes Cloudflare challenge
scripts and unrelated third-party/browser-extension requests.

Recovered:
  - index.html
  - production CSS bundle
  - production JavaScript bundles
  - JAYN emblem image
  - favicon

Important:
  This is a production/deployed frontend snapshot, not the original clean
  React/TypeScript source repository. Server-only source, secrets, build-time
  configuration, and original component filenames may not be recoverable from
  the browser bundle alone.

The manifest.json file records each recovered asset, response type, byte size,
and SHA-256 checksum.
