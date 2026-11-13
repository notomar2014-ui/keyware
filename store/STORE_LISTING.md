# Keyware: Microsoft Store listing (ready to paste)

Everything below goes into Partner Center after the parent's account exists and the name is reserved.
The package version follows `engine/__init__.py` (1.4.1, stored as 1.4.1.0). The Partner Center
identity used for the 1.3.9.0 upload is in `store/partner-identity.json`. This file is listing copy, not an upload.
Nothing here names the developer. Keep it that way (see "Things that would reveal the developer" at the end).

Limits checked on 31 Oct 2026 in Microsoft's docs:
[Store listing fields](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/add-and-edit-store-listing-info),
[screenshots and images](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/screenshots-and-images),
[properties](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/create-app-submission),
[submission options](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/manage-submission-options).

---

## 1. App name (reserve one of these)

"Keyware" is not on the Microsoft Store as far as a web search shows, but the word is already used by other
companies (a "Keyware" shopping app on Google Play from India, Keyware SA / Keyware GmbH payment companies in
Switzerland with an iOS app, Keyware Solutions Inc. in Japan, keyware.app in Italy). Reserving it can still work;
the Store only blocks exact names already reserved. If it's taken, or you want less trademark risk, try these in order:

1. **Keyware** (first choice, matches the app and website)
2. **Keyware Hotkeys**
3. **Keyware: Key Binder & Macros**
4. **Keyware Key Remapper**
5. **Keyware for Windows**

Whatever name is reserved must be passed as `-DisplayName` to the build (the manifest's DisplayName has to match a
reserved name).

## 2. Short description (under 270 characters; 219 used)

> Make your keys your own. Bind keys, combos, holds and leader keys to websites, apps, text, media and macros, or
> just describe what you want in plain words and review the binding before saving. Free, open source, no ads.

## 3. Description (plain text, up to 10,000 characters)

> Keyware turns any key on your keyboard into a shortcut that does what you want.
>
> Press a combo to open a website, hold a key to type a phrase, tap F9 to launch an app, or set a leader key and
> follow it with a letter. Keyware runs quietly in the system tray and only acts on keys you bind. Every other key
> goes straight to Windows as normal.
>
> DESCRIBE IT, DON'T CONFIGURE IT
> Type something like "F9 opens YouTube and F10 types my email" and Keyware drafts the bindings for you. You see
> exactly what it will do and nothing is saved until you press Save. Prefer doing it by hand? Manual mode has every
> option.
>
> WHAT A BINDING CAN DO
> - Open a website or an app
> - Type text you use often
> - Send another key combo
> - Control media and play a song or video by name
> - Auto-click with a set speed and a safety stop
> - Run a short macro of several steps
>
> TRIGGERS
> - Chords (modifier keys plus one key)
> - Taps, double-taps and timed holds
> - Leader keys (press the leader, then a key)
>
> PROFILES
> Global, Gaming, Browser and Code profiles switch on by themselves when the app you chose is in front, so the same
> key can do different things in different apps.
>
> SAFE BY DESIGN
> - Unbound keys are never blocked
> - Ctrl + Alt + Shift + Esc stops Keyware instantly
> - Ctrl + Alt + F12 pauses and resumes
> - Dry run mode shows what would fire without doing it
> - No administrator rights needed
>
> PRIVATE
> No accounts, no ads, no analytics, no tracking. The key engine works offline and never records what you type.
> Your bindings stay on your PC. Only Text mode (when you press Generate) and the optional feedback form go online;
> the privacy statement lists exactly what is sent. You can also use your own free Gemini or Groq key.
>
> FREE AND OPEN SOURCE
> Keyware is free under the MIT license. The full source code is public.
>
> Keyware can start with Windows so your shortcuts are ready right away. You can turn this off in Keyware's Settings
> or in Windows Settings > Apps > Startup.

(About 1,900 characters.)

## 4. Product features (up to 20, 200 characters each)

1. Bind keys, chords, taps, double-taps, holds and leader keys
2. Open websites and apps, type text, send key combos, control media
3. Describe a binding in plain words and review it before saving
4. Per-app profiles that switch automatically
5. Auto-clicker with speed control and a safety stop
6. Multi-step macros
7. Instant stop with Ctrl + Alt + Shift + Esc, pause with Ctrl + Alt + F12
8. Dry run mode to test bindings safely
9. Runs in the system tray, optional start with Windows
10. No admin rights, no ads, no accounts, no tracking
11. Free and open source (MIT)

## 5. Search terms (up to 7, 40 characters each, 21 words total; 12 words used)

1. hotkeys
2. key remapper
3. keyboard shortcuts
4. macro
5. auto clicker
6. key binding
7. AutoHotkey alternative

(Only use "AutoHotkey alternative" if you're comfortable naming another product. If not, use `text expander`.)

## 6. Other listing fields

| Field | Value |
| --- | --- |
| Category | Utilities & tools |
| Pricing | Free, all markets |
| Privacy policy URL | https://keyware-app.vercel.app/privacy (live, updated 31 Oct 2026) |
| Website | https://keyware-app.vercel.app |
| Support contact info | **Decision needed.** Suggested: https://keyware-app.vercel.app/guide (live). `/support` returns 404 today. A dedicated support email works too, but Microsoft may show it publicly. |
| Copyright and trademark info | © 2026 Keyware contributors. Windows is a trademark of Microsoft. |
| Additional license terms | Leave empty, or link the MIT license: https://github.com/…/blob/main/LICENSE (this link shows the GitHub username, see the end of this file) |
| Developed by | Keyware (no personal name) |
| System requirements | Minimum: Windows 10 version 2004 (build 19041) or later, x64; keyboard; internet only for Text mode and feedback |
| App declarations | Leave "This app has been tested to meet accessibility guidelines" unchecked unless it is tested. Leave the others at their defaults. |

## 7. Age rating questionnaire (IARC)

Pick the category **"Utility, Productivity, Communication or Other"** (not Game). Answers:

| Question (wording varies) | Answer | Why |
| --- | --- | --- |
| Violence, fear, sexual content, nudity, crude humour, bad language | No | None in the app |
| Drugs, alcohol, tobacco, gambling or simulated gambling | No | None |
| Users can talk to or share content with other users | No | Feedback goes only to the developer; nothing is shown to other users |
| Shares the user's current physical location with other users | No | No location use |
| Digital purchases | No | No in-app purchases; donation buttons are hidden |
| Unrestricted internet access (e.g. a web browser or search engine) | No | Open URL hands a link to the user's own browser; the app is not a browser |
| Collects personal info | The feedback form can take an email address the user types in. If there's a separate question about sharing it with third parties: No |

Expected result: the lowest rating in every region (for example 3+ / Everyone).

## 8. Screenshots (1920 x 1080 PNG; Store minimum 1366 x 768, up to 10)

In `screenshots/` (made by `make_screenshots.py` from the v1.3.4 captures, with captions on the image):

| File | Caption to paste (200 characters max) |
| --- | --- |
| 01_text_mode.png | Describe a binding in plain words. Keyware drafts it, you review it and save. |
| 02_bindings.png | Bind chords, timed holds and leader keys to websites, apps, text and more. |
| 03_profiles.png | Profiles switch on by themselves in the apps you choose. |
| 04_settings.png | Dry run, audio ticks, a pause hotkey and an optional start with Windows. |
| 05_feedback.png | No accounts, no ads, no tracking. Send ideas and bug reports from Settings. |

Notes:
- 03_profiles.png came from a capture that still showed the "Support Keyware" button. The button is painted
  out so it matches the current build (donation buttons hidden).
- Older v1.3.1 to v1.3.3 captures were not used: their log shows a Windows user folder path containing a name.
- Best option before submitting: take fresh 1920 x 1080 captures of the actual Store build on Windows.
- Store logos for Partner Center (optional but recommended, the Store prefers them over the package icon):
  `StoreListingImages/StoreLogo_300.png` (1:1 app tile icon 300 x 300), `StoreLogo_1080.png` and `StoreLogo_2160.png`
  (1:1 box art).

## 9. Submission options: restricted capability justification (runFullTrust)

Paste into **Submission options > Restricted capabilities**:

> Keyware is a desktop (Win32) app packaged as a FullTrustApplication. The executable is the PyInstaller
> build. runFullTrust is required because the app cannot do its job inside an AppContainer sandbox. While the
> user has Keyware switched on, it installs a low-level keyboard hook (WH_KEYBOARD_LL) and a low-level mouse
> hook (WH_MOUSE_LL) to see the keys and mouse buttons the user has bound. It sends input with SendInput only
> for actions the user configured, such as typing text, key combinations, and auto-click. It reads the
> foreground window's process name so a per-app profile can apply. It also shows a notification-area icon.
> The hooks are started when Keyware is switched on and stopped when it is switched off. Keystrokes are not
> logged, stored, or uploaded. The app does not need administrator rights.

## 10. Notes for certification

Paste into **Submission options > Notes for certification**:

> Keyware is a free, open-source (MIT) key binding and macro app.
>
> Keyboard hook: Keyware uses a global low-level keyboard hook only while it is ON (green dot next to the name). It
> acts only on keys the user has bound; all other keys pass through to Windows. Keystrokes are never logged, stored or
> sent anywhere; the on-screen log only names which binding fired. Ctrl + Alt + Shift + Esc removes the hook at once,
> and Ctrl + Alt + F12 pauses it.
>
> Startup: the package declares a windows.startupTask (TaskId KeywareStartup) that launches Keyware with
> --background so it sits in the system tray with no window. Users can turn it off in Keyware Settings > "Start
> Keyware with Windows" or in Windows Settings > Apps > Startup. When the user disables it in Windows, Keyware
> respects that and does not re-enable it. The packaged build does not write the HKCU Run key.
>
> How to test:
> 1. Launch Keyware. The sample "Global" profile has Ctrl + Win + Shift + R, which opens youtube.com in the default
>    browser.
> 2. Settings (gear icon) > "Dry run" shows bindings firing in the log without performing them.
> 3. Text mode: choose Text in the Quick binder, type "F2 opens YouTube", press Generate. This uses a free AI service
>    with a daily limit; no account or key is needed.
> 4. Closing the window keeps Keyware in the tray; right-click the tray icon > Quit closes it fully.
>
> No account, sign-in, purchase or test credentials are needed. Privacy policy:
> https://keyware-app.vercel.app/privacy

## 11. Things that would reveal the developer (avoid in the listing)

- **The GitHub link.** The repository lives at `github.com/notomar2014-ui/keyware`. The username has a first name
  and what looks like a year in it. Don't use GitHub links for Support, Website or License. Use the website pages
  instead, or move the repo to a neutral organization first. **Decision needed.**
- **Publisher display name.** Partner Center shows it as the publisher on the Store page. Use something neutral
  like "Keyware" (or "Keyware Apps" if "Keyware" isn't accepted), not the parent's or child's name.
- **Support email.** Microsoft may show it publicly. Use a dedicated address that has no name in it.
- **Screenshots.** Already checked: no user folder paths or names in the five provided.
