# Two-step sign-in (authenticator app) — guide for officers

## Why

A password can be guessed, shared by mistake or stolen. Two-step sign-in adds a second proof that only you have: a 6-digit
code from an app on your phone. Someone who learns your password still cannot open your account. Case data is sensitive, so
some roles **must** use it.

* **Required** for Admin, Supervisor and Investigator on a real installation (your administrator can change this per role).
* **Optional** for everyone else, and in demonstration mode. You can turn it on for yourself any time.

It works **without internet or SMS**. The app on your phone calculates the code from the time.

## Set it up (about two minutes)

1. Install an authenticator app on your phone: Google Authenticator, Microsoft Authenticator or Authy.
2. Sign in as usual. If your role requires it, the screen asks you to set up. Otherwise open **Account** (top right) and choose
   **Turn on two-step sign-in**, then type your password.
3. In the app choose *Add account* and **scan the square** on the screen. Cannot scan? Choose *Enter a setup key* and type the key shown.
4. Type the 6-digit code the app shows to confirm.
5. **Save your 10 recovery codes.** They are shown only once. Copy or download them and keep them somewhere safe, **not only on
   the same phone**. Tick the box to finish.

## Every sign-in after that

Type your account ID and password, then the current 6-digit code. Codes change every 30 seconds and each code works only once:
if the sign-in says the code is not valid, wait for the next code. If it keeps failing, check that your phone's clock is
set to automatic time.

After several wrong codes the account is locked for a while. This is on purpose.

## Lost, broken or replaced phone

* **You have a recovery code:** type it in the code box instead of the 6 digits. Each recovery code works **once**. Then make new
  codes under Account.
* **No recovery codes:** ask an administrator to **reset your two-step sign-in** (Admin Portal -> Users -> Reset two-step).
  You are signed out and set it up again on the new phone at your next sign-in. Resets are recorded in the audit log.
* You cannot reset it yourself without a recovery code. That protects you from someone who only has your password.

## Good habits

* Never tell anyone your codes or recovery codes, including administrators. No one from the system will ask for them.
* Make new recovery codes when only a few are left (Account shows how many remain).
* Sensitive actions (changing your password or username, and for admins creating users or changing roles) need a session where
  you used your code. If you are told to sign in again with your code, do that.

## For administrators

* Admin Portal -> **Two-step sign-in**: switch a role on to require it. People in that role set it up at their next sign-in. The Demo role can never be forced.
* Environment: `MFA_REQUIRED_ROLES=admin,supervisor,investigator` (empty = nobody). Without it: nobody in demo mode; admin, supervisor and investigator otherwise. A choice made in the Admin Portal wins.
* Lost phone: **Reset two-step** on the user. If the last admin is locked out, run on the server `python scripts/set_password.py NAME --mfa-only`.
