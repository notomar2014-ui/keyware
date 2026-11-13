# Security policy

## Supported versions

Only the latest release (shown on https://keyware-app.vercel.app) gets security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting instead: open the **Security** tab of this repository
and choose **Report a vulnerability**. Include the Keyware version, Windows version,
steps to reproduce and the impact you expect.

You should get a first reply within a week. Once a fix ships we will credit you in the
release notes unless you prefer otherwise.

## Scope

In scope: the app in this repository (keyboard hook, actions, macro and command
safety checks, optional scripts, the DPAPI key store, Text-mode parsing) and the website API in `site/api`.

Things we especially want to hear about:

- a way for a binding, macro, Keyware script, or AI reply to run a program, script, or command without
  the one-time confirmation;
- a key that gets stuck or swallowed, or a way to defeat the panic chord;
- an API key, install ID or feedback text leaking into logs, errors or responses.

Download integrity: every CI build attaches a `.sha256` file next to the exe
(`Keyware.exe` for x64, `Keyware-arm64.exe` for ARM64). Compare it with
`Get-FileHash .\Keyware.exe -Algorithm SHA256` or
`Get-FileHash .\Keyware-arm64.exe -Algorithm SHA256`. The website shows both
hashes for the current version. The two hashes are not interchangeable.
