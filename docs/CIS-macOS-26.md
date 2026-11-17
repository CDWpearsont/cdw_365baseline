# CIS Apple macOS 26 Tahoe Intune Benchmark v1.0.0: coverage

How the library's `MacOS - CDW Baseline - SC - CIS L1/L2 - ...` policies map to the benchmark. All recommendations are for supervised (ADE-enrolled) Macs. 93 of 100 recommendations are applied.

| # | Level | Setting | Policy |
|---|---|---|---|
| 2.1.1.1 | L1 | Disable Guest Account: True | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.2.1 | L2 | Restrict Store Software Update Only: True | L2 Software Update - App Store Updates Only |
| 2.3.1.1 | L1 | User Enters Missing Info: True | L1 Disk Encryption - FileVault |
| 2.3.1.2 | L1 | Enable: On | L1 Disk Encryption - FileVault |
| 2.3.1.3 | L1 | Defer Force At User Login Max Bypass Attempts: 0 | L1 Disk Encryption - FileVault |
| 2.3.1.4 | L1 | Show Recovery Key: Disabled | L1 Disk Encryption - FileVault |
| 2.3.1.5 | L1 | Force Enable In Setup Assistant: True | L1 Disk Encryption - FileVault |
| 2.3.1.6 | L1 | Defer: Enabled | L1 Disk Encryption - FileVault |
| 2.3.2.1 | L1 | Prevent FileVault From Being Disabled: True | L1 Disk Encryption - FileVault |
| 2.4.1.1 | L1 | Show Full Name: True | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.4.2.1 | L1 | Disable Login Items Suppression: True | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.5.1.1 | L2 | Activation Lock Allowed While Supervised: per organisation | Not applied: Organisation-specific (set Activation Lock to the customer's policy) |
| 2.6.1.1 | L2 | Auto Activation: False | L2 Software Update - App Store Updates Only |
| 2.6.2.1 | L1 | Enable Stealth Mode: True | L1 Firewall - Firewall |
| 2.6.2.2 | L1 | Enable Firewall: True | L1 Firewall - Firewall |
| 2.7.1 | L1 | Removal Password: absent | Not applied: Compliant when absent; no removal password is set |
| 2.8.1 | L1 | Allow Bluetooth Sharing Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.2 | L1 | Allow Password Sharing: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.3 | L1 | Allow File Sharing Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.4 | L1 | Allow Erase Content And Settings: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.5 | L1 | Allow Air Play Incoming Requests: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.6 | L1 | Allow Media Sharing Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.7 | L1 | Allow Cloud Photo Library: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.8 | L1 | Allow Cloud Document Sync: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.9 | L1 | Allow Activity Continuation: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.10 | L1 | Allow Cloud Bookmarks: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.11 | L1 | Allow Cloud Freeform: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.12 | L1 | Allow Cloud Calendar: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.13 | L1 | Allow ARD Remote Management Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.14 | L2 | Allow Game Center: False | L2 Restrictions - Game Center, Content Caching and AutoFill |
| 2.8.15 | L1 | Allow Auto Unlock: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.16 | L1 | Allow Startup Disk Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.17 | L1 | Allow UI Configuration Profile Installation: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.18 | L1 | Allow Device Name Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.19 | L1 | Allow Internet Sharing Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.20 | L2 | Allow Content Caching: False | L2 Restrictions - Game Center, Content Caching and AutoFill |
| 2.8.21 | L1 | Allow Find My Friends: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.22 | L1 | Allow AirDrop: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.23 | L2 | Allow Password Auto Fill: False | L2 Restrictions - Game Center, Content Caching and AutoFill |
| 2.8.24 | L1 | Allow Cloud Notes: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.25 | L1 | Allow Multiplayer Gaming: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.26 | L1 | Allow Local User Creation: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.27 | L1 | Allow Apple Personalized Advertising: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.28 | L1 | Allow Printer Sharing Modification: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.29 | L2 | Allow Fingerprint For Unlock: per organisation | Not applied: Organisation-specific (Touch ID for unlock) |
| 2.8.30 | L1 | Allow Diagnostic Submission: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.31 | L1 | Allow Cloud Reminders: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.32 | L1 | Allow Password Proximity Requests: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.33 | L1 | Allow iPhone Mirroring: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.34 | L1 | Allow Cloud Mail: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.35 | L1 | Allow Universal Control: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.36 | L1 | Allow Cloud Address Book: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.37 | L1 | Allow Cloud Desktop And Documents: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.38 | L1 | Allow iTunes File Sharing: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.8.39 | L1 | Allow Cloud Keychain Sync: False | L1 Restrictions - Sharing, iCloud and Continuity |
| 2.9.1.1 | L1 | Maximum Passcode Age In Days: 365 or less, but not 0 | L1 Passcode - Local Account Password |
| 2.9.1.2 | L1 | Maximum Grace Period: 0 | L1 Passcode - Local Account Password |
| 2.9.1.3 | L1 | Automatic Device Lock: 15 or less, but not 0 | L1 Passcode - Local Account Password |
| 2.9.1.4 | L2 | Minimum Complex Characters: 1 or more | L2 Passcode - Complex Password |
| 2.9.1.5 | L1 | Passcode Reuse Limit: 24 or more | L1 Passcode - Local Account Password |
| 2.9.1.6 | L1 | Require Passcode on Device: True | L1 Passcode - Local Account Password |
| 2.9.1.7 | L1 | Regex: defined | Not applied: Organisation-specific word list for the passcode regex |
| 2.9.1.8 | L1 | Maximum Number of Failed Attempts: 5 or less, but not 0 | L1 Passcode - Local Account Password |
| 2.9.1.9 | L2 | Require Complex Passcode: True | L2 Passcode - Complex Password |
| 2.9.1.10 | L1 | Failed Attempts Reset In Minutes: 15 or more | L1 Passcode - Local Account Password |
| 2.9.1.11 | L1 | Require Alphanumeric Passcode: Enabled | L1 Passcode - Local Account Password |
| 2.9.1.12 | L1 | Minimum Passcode Length: 15 or more | L1 Passcode - Local Account Password |
| 2.9.1.13 | L1 | Change At Next Auth: Enabled | Not applied: Would force every user to change their password at once |
| 2.10.1.1 | L1 | Enforce Latest Software Update Version: True | L1 Software Update - Automatic and Enforced Updates |
| 2.10.2.1 | L1 | Download: AlwaysOn | L1 Software Update - Automatic and Enforced Updates |
| 2.10.2.2 | L1 | Install OS Updates: Always On | L1 Software Update - Automatic and Enforced Updates |
| 2.10.2.3 | L1 | Install Security Update: AlwaysOn | L1 Software Update - Automatic and Enforced Updates |
| 2.10.3.1 | L1 | Enable: True | L1 Software Update - Automatic and Enforced Updates |
| 2.10.3.2 | L1 | Enable Rollback: False | L1 Software Update - Automatic and Enforced Updates |
| 2.10.4 | L1 | Allow Standard User OS Updates: Allowed | L1 Software Update - Automatic and Enforced Updates |
| 2.11.1.1 | L1 | Display Sleep Timer: 10 or less, but not 0 | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.1.2 | L1 | System Sleep Timer: 15 or less, but not 0 | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.1.3 | L1 | Wake on LAN: False | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.1.4 | L1 | Wake On Modem Ring: False | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.1.5 | L1 | Destroy FV Key On Standby: True | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.2.1 | L1 | Ask For Password: True and Ask For Password Delay is absent or set to 0 | L1 System Configuration - Power, Screen Saver and Login Window |
| 2.11.3.1 | L1 | Allow User Overrides: False | Not applied: Already off by default; a second system extension payload can clash with the Defender for Endpoint profile |
| 2.12.1.1 | L1 | Enable XProtect Malware Upload: Disabled | L1 Gatekeeper - App Store and Identified Developers |
| 2.12.1.2 | L1 | Gatekeeper: App Store and identified developers | L1 Gatekeeper - App Store and Identified Developers |
| 2.12.1.3 | L2 | Gatekeeper: App Store only | L2 Gatekeeper - App Store Only |
| 2.12.2.1 | L1 | Disable Override: True | L1 Gatekeeper - App Store and Identified Developers |
| 2.13.1 | L1 | Configuration profiles not removable by the user | Not applied: Set on the ADE enrolment profile (profile not removable), not in a policy |
| 2.14.1.1 | L1 | Allow Sign In: Blocked | L1 Apple Intelligence and Siri |
| 2.14.1.2 | L1 | Enabled: Disabled | L1 Apple Intelligence and Siri |
| 2.14.2.1.1.1 | L1 | Allow Smart Replies: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.1.1.2 | L1 | Allow Summary: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.1.2.1 | L1 | Allow Transcription: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.1.2.2 | L1 | Allow Transcription Summary: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.1.3.1 | L1 | Allow Summary: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.2 | L1 | Allow Writing Tools: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.3 | L1 | Allow Image Playground: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.4 | L1 | Allow Apple Intelligence Report: Blocked | L1 Apple Intelligence and Siri |
| 2.14.2.5 | L1 | Force On Device Only Dictation: Enabled | L1 Apple Intelligence and Siri |
| 2.14.2.6 | L1 | Allow Genmoji: Blocked | L1 Apple Intelligence and Siri |
| 2.14.3.1 | L1 | Enabled: Disabled | L1 Apple Intelligence and Siri |

Notes:
- FileVault also sets the Intune escrow location and 6-monthly key rotation, which Intune needs to manage the recovery key.
- Software update enforcement uses a 7-day deadline at 12:00 local time; change it per customer.
- The screen saver module is set to Flurry because the payload needs a module name.

