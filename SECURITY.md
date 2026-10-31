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
safety checks, the DPAPI key store, Text-mode parsing) and the website API in `site/api`.

Things we especially want to hear about:

- a way for a binding, macro or AI reply to run a program, script or command without
  the one-time confirmation;
- a key that gets stuck or swallowed, or a way to defeat the panic chord;
- an API key, install ID or feedback text leaking into logs, errors or responses.

Download integrity: every CI build attaches `Keyware.exe.sha256` next to the exe; compare it
with `Get-FileHash Keyware.exe -Algorithm SHA256`.
