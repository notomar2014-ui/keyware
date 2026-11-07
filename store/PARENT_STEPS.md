# Putting Keyware on the Microsoft Store: steps for the parent

You (the parent) will own the developer account. It's free. You won't upload anything yet: you create the
account, reserve the app's name, and copy four values back to Omar. Omar's side builds the package with them.

The 1.3.8 packages that can be built before those values exist are labelled `NOT-STORE-READY`.
Their publisher is a Windows test marker, not a Partner Center publisher. Do not upload them.
Nothing in this repository is the real Package/Identity/Publisher.

Microsoft's own pages for this (checked 7 Nov 2026):
- Sign-up: https://learn.microsoft.com/en-us/windows/apps/publish/partner-center/open-a-developer-account
- Free registration for individuals: https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer
  and Microsoft's announcement: https://blogs.windows.com/windowsdeveloper/2025/09/10/free-developer-registration-for-individual-developers-on-microsoft-store/
- Developer agreement you'll accept: https://learn.microsoft.com/en-us/legal/windows/agreements/app-developer-agreement
- What to upload: https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements
- Where the identity values are shown: https://learn.microsoft.com/en-us/windows/apps/publish/view-app-identity-details

## Before you start

- **Cost: free.** Individual developer accounts have no fee (the old $19 fee is waived). This only applies if you
  start from **https://storedeveloper.microsoft.com**. Other sign-up links can still lead to the old paid flow. If
  you're asked for a card payment, stop and go back to that link.
- **Use your own details, not your child's.** The account holder accepts a legal agreement, has to verify a real
  identity, and can't share the password. Microsoft doesn't publish a minimum age, but the agreement assumes an
  adult who can accept it. So the account should be yours.
- **What you need:**
  - A personal Microsoft account (Outlook/Hotmail/Live) that you control. Make a new one if you like.
  - A government photo ID (passport, national ID card or driving licence).
  - A phone or webcam for the selfie check.
- **Individual, not company.** Choose "Individual developer". A company account costs money and needs business
  verification.
- **Your name stays private on the Store page, but other fields don't.** The Store shows the **publisher display
  name** you pick (step 3). Choose something neutral like **Keyware**, not your own name. The contact email may be
  shown publicly too, so use one with no name in it if you can.

## Step 1: Create the account (about 15 minutes, then a wait)

1. Go to **https://storedeveloper.microsoft.com** and choose **Get started for free**.
2. Choose **Individual developer**.
3. Sign in with your personal Microsoft account.
4. Verify your identity: photograph your ID, then take a selfie when asked.
5. Fill in the developer profile. Where it asks for a **publisher display name**, enter `Keyware` (or `Keyware Apps`
   if that's taken).
6. Accept the App Developer Agreement.
7. You land in **Partner Center** (partner.microsoft.com). If verification is still running, wait for the
   confirmation email before step 2.

Need help? Email Microsoft Store support at storesupport@service.microsoft.com.

## Step 2: Reserve the app name (2 minutes)

Microsoft's page: https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/reserve-your-apps-name

1. In Partner Center open **Apps and games** and choose **+ New product > MSIX or PWA app**.
2. Type **Keyware** and press **Check availability**.
3. If it's taken, try these in order: `Keyware Hotkeys`, `Keyware: Key Binder & Macros`, `Keyware Key Remapper`,
   `Keyware for Windows`.
4. Press **Reserve product name**. Write down the exact name you got.

A reserved name is held for **3 months**. If nothing is submitted in that time, the name can be lost, so reserve
it when Omar is nearly ready.

Don't click "Start your submission" yet. If you do by accident, just leave it. Nothing goes public until someone
presses Submit.

## Step 3: Copy four values back to Omar

Microsoft's page: https://learn.microsoft.com/en-us/windows/apps/publish/view-app-identity-details

1. Open the app you reserved. In the left menu expand **Product management** and choose **Product identity**.
2. Copy these three lines exactly, with no extra spaces:

| Partner Center label | Looks like |
| --- | --- |
| Package/Identity/Name | `12345YourName.Keyware` |
| Package/Identity/Publisher | `CN=1A2B3C4D-…` (a long code that starts with `CN=`) |
| Package/Properties/PublisherDisplayName | `Keyware` |

3. Also send the **reserved app name** from step 2. That is the fourth value. It has to match the package's
   display name exactly.

These values aren't passwords, so it's fine to send them in a message. **Never send your Microsoft account password
or a sign-in code.**

## What Omar does next (no Partner Center access needed)

1. Builds the Store package with those values: run the "Microsoft Store MSIX" GitHub Actions workflow and fill in
   all four fields. The file to upload from that run is `Keyware-<version>.msixupload` (a zip of the x64 and
   ARM64 bundle). Partner Center also accepts the `.msixbundle`, or the two architecture `.msix` files. Leave
   the package unsigned; Microsoft re-signs MSIX uploads. Do not upload a file whose name starts with
   `NOT-STORE-READY`.
2. Installs the test build on a PC and checks that it starts, the hotkeys work, and "Start with Windows" can be
   switched off.
3. Gets the listing text, screenshots, age-rating answers and certification notes ready (all in
   `STORE_LISTING.md`).

## Step 4 (later, together): Submit

When Omar has the package and listing ready, sign in to Partner Center together and choose **Start your
submission**. Then:

1. **Pricing and availability:** Free.
2. **Properties:** category Utilities & tools; privacy policy URL https://keyware-app.vercel.app/privacy.
3. **Age ratings:** use the answers in `STORE_LISTING.md`.
4. **Packages:** upload `Keyware.msix`.
5. **Store listings:** paste the text and add the screenshots.
6. **Submission options:** paste the runFullTrust justification and certification notes.
7. Press **Submit to the Store**.

Microsoft usually reviews in a few business days. Because the app uses a keyboard hook and runs at startup,
certification may ask questions. Replies go to the account email.
