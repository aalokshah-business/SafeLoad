# Installing SafeLoad On Windows

## Before You Start

SafeLoad supports Windows 10/11 x64. The standard installer needs PowerShell, access to GitHub, and permission to write to your own Windows profile. It does not require Python, Git, Microsoft Office, or administrator rights. Windows and office application-control rules still determine whether the installed executable may run.

The app works locally. Scanning (tabs 1 and 2) never modifies the files being checked; the MakeItSafe tab (tab 3) rewrites files in place only when you click MakeItSafe Folder and confirm. Installation downloads application files; folder scans and saved keywords are not uploaded.

## One-Command Installation

Open PowerShell and run:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/aalokshah-business/SafeLoad/main/install.ps1)))
```

The command runs [install.ps1](install.ps1), which:

1. Downloads the latest [Windows release ZIP and SHA-256 checksum](https://github.com/aalokshah-business/SafeLoad/releases/latest).
2. Verifies the checksum and rejects unsafe archive paths before extracting.
3. Installs a new version under `%LOCALAPPDATA%\Programs\SafeLoad\versions`.
4. Creates **SafeLoad** shortcuts on the Desktop and in the Start Menu, with the application icon.
5. Attempts to open SafeLoad.

You can [review the script](https://github.com/aalokshah-business/SafeLoad/blob/main/install.ps1) before running it. It does not disable App Control, change execution policy, install certificates, or clear downloaded-file zone metadata. A SHA-256 checksum checks the downloaded package against the published hash; it is not a publisher signature or Windows policy approval.

## Shortcuts And Taskbar Icon

Open **SafeLoad** from its Desktop or Start Menu shortcut. Its custom shield/search icon also appears on the taskbar while the app runs.

To keep the icon on the taskbar after closing the app, right-click the running icon and select **Pin To Taskbar**. Windows requires a user action for permanent pinning; the installer does not force it. See [Microsoft's taskbar guidance](https://learn.microsoft.com/en-us/windows/apps/develop/windows-integration/pin-to-taskbar).

## First Scan

1. In **Directory**, choose the source folder.
2. In **Keywords**, enter words or phrases separated by commas. Spaces around commas are ignored; spaces inside phrases are retained. Enable **Case-Sensitive Matching** if needed.
3. Select **Scan Folder**.

SafeLoad checks file names and supported file contents. Results always appear in this order:

- **Failed**: a configured keyword matched. These files appear first. Select a file to see each keyword by name, its location and match count.
- **Not Scanned**: the file could not be checked; select it to see the specific reason, such as unsupported format or denied file access.
- **OK**: no configured keyword matched the inspected text.

Resolve matches manually and scan again. **Safe** is a keyword-check result within the documented [format coverage](README.md#inspection-coverage), not a general security certification or permission to upload.

## Saved Keywords

Keywords and the case-sensitivity setting are saved when scanning or closing the app. They are restored when SafeLoad opens again.

Settings are stored separately from the installed version at:

```text
%LOCALAPPDATA%\SafeLoad\settings.json
```

The file contains your configured keywords in local JSON. The settings file is not embedded in exported reports or uploaded by SafeLoad. Scan reports include the names of keywords that matched, their locations, and reasons for files that were not scanned. Normal updates retain it. Remove this settings file only if you want to reset the saved keywords.

## Updating And Uninstalling

Run the same installation command to install the latest release. Each installation uses a new version directory and updates the Desktop/Start Menu shortcuts. Previous versions remain on disk; a running app is not overwritten. Close the previous window and open the updated shortcut. An already pinned taskbar shortcut may still point to an older version; pin the updated app again if necessary.

To uninstall, close SafeLoad, remove `%LOCALAPPDATA%\Programs\SafeLoad`, and delete its Desktop/Start Menu shortcuts. Remove `%LOCALAPPDATA%\SafeLoad\settings.json` separately if you also want to erase saved keywords. Unpin its taskbar icon through Windows.

## Offline Installation

On an approved connected machine, download these files from the [same release](https://github.com/aalokshah-business/SafeLoad/releases/latest):

- `SafeLoad-Windows-x64.zip`
- `SafeLoad-Windows-x64.zip.sha256`

Transfer them using your organization's approved process. Extract `SafeLoad\install.ps1` from that ZIP, place it next to the two downloaded files, and run:

```powershell
& .\install.ps1 -ArchivePath .\SafeLoad-Windows-x64.zip -ChecksumPath .\SafeLoad-Windows-x64.zip.sha256
```

If your organization does not permit local scripts, ask IT to deploy the reviewed package. Offline installation does not change application-control requirements. A portable alternative is to verify the published checksum, extract the **entire** ZIP, and open `SafeLoad\SafeLoad.exe` where policy permits it.

## Installation Finishes But Windows Blocks Launch

The following message can occur after the files and shortcuts have already been installed:

```text
An Application Control policy has blocked this file.
```

The current public executable is unsigned. The observed SafeLoad 1.0.3 installation created both shortcuts successfully, then Windows refused to start the executable. Read-only diagnostics found no embedded Authenticode signature in the installed PE file and a relevant Code Integrity block event naming the `VerifiedAndReputableDesktop` policy. These observations identify this machine's block; they do not establish every machine's trust rules or prove that signing alone will satisfy an office policy.

Ask your IT administrator to review:

- The release version, package checksum, and blocked file path shown by the installer or shortcut.
- The relevant events under **Applications And Services Logs > Microsoft > Windows > CodeIntegrity > Operational**. Events such as 3077 can identify the blocking policy; related signature events provide more detail.
- Whether the executable and bundled native libraries meet the organization's signing and deployment requirements.
- An approved publisher, file-hash, catalog, or managed-deployment authorization for the reviewed package, according to the organization's rules. File hashes change between releases.

Do not turn off Windows security or change trust settings to follow this guide. IT can approve a reviewed release or arrange an appropriately signed build. SafeLoad's source, dependency versions and build instructions are available in the repository for that review.

Microsoft references: [App Control troubleshooting](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/appcontrol-debugging-and-troubleshooting), [Smart App Control](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview).

## Comparison With AskAi's Windows Implementation

The comparison below is based on AskAi commit `bbae260e66212103f2f566c87309d201ad0d55bf` inspected locally:

- [AskAi's Windows desktop builder](https://github.com/aalokshah-business/AskAi/blob/bbae260e66212103f2f566c87309d201ad0d55bf/lib/desktop.js) compiles `standalone/desktop.cs` locally with the Windows .NET Framework compiler, creates a source-hash-named executable under `desktop-bin`, and creates shortcuts to it.
- [Its Windows UI](https://github.com/aalokshah-business/AskAi/blob/bbae260e66212103f2f566c87309d201ad0d55bf/standalone/desktop.cs) is a .NET Windows Forms app. It starts an installed Node.js runtime for its desktop bridge and commands. Windows does not use the Linux Python UI path.
- [Its bootstrap](https://github.com/aalokshah-business/AskAi/blob/bbae260e66212103f2f566c87309d201ad0d55bf/get.ps1) retrieves source. It also contains downloaded-file metadata removal and a process-specific PowerShell execution-policy override. SafeLoad does not adopt those operations as a remedy for an App Control block.
- SafeLoad downloads a self-contained, prebuilt PyInstaller executable with its Python runtime and native document libraries. The destination does not compile the application or install Python/Node.js.

Local compilation, trusted runtime availability, file origin, reputation, signing, and organization-specific rules can produce different outcomes. AskAi running on a machine does not establish that another executable is permitted there. The exact allow decision for AskAi requires that machine's policy evidence. AskAi's builder also explicitly reports application-control and permission failures rather than guaranteeing it will run everywhere.

Use this comparison when IT reviews the deployment format. Any future source-based or locally compiled SafeLoad deployment should be an approved deployment choice; this guide does not provide a route around an active block.
