# ──────────────────────────────────────────────────────────────────────
# CDW 365 Baseline – PowerShell Runner
# Hosts the PowerShell-only M365 workloads using NATIVE module cmdlets
# (Exchange Online, Teams, SharePoint/PnP) plus REST (Defender for Cloud Apps).
#
# Apply engine = native cmdlets, NOT Microsoft365DSC.
# M365DSC is deliberately excluded: its Update-M365DSCDependencies installer
# refuses to install its dependency tree on PowerShell Core / Linux. If a future
# workload genuinely needs DSC, run THAT piece on a separate Windows/ACI runner —
# do not add M365DSC back to this Linux image.
#
# Expansion = add a native module here + drop a new file in src/Workloads/.
# ──────────────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/powershell:7.4-ubuntu-22.04

LABEL maintainer="CDW Modern Workplace"
LABEL description="Granular PowerShell apply runner for CDW 365 Baseline (native cmdlets, Linux/Core)"

SHELL ["pwsh", "-NoLogo", "-NonInteractive", "-Command"]

# --- Module set (all support PowerShell Core) ---------------------------
# Pinned for reproducible builds. Add new workload modules here as coverage
# expands. Verify versions still exist on the gallery before a fresh build.
RUN $ErrorActionPreference = 'Stop'; \
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; \
    Install-Module ExchangeOnlineManagement -RequiredVersion 3.7.2 -Scope AllUsers -Force; \
    Install-Module PnP.PowerShell           -RequiredVersion 2.12.0 -Scope AllUsers -Force; \
    Install-Module MicrosoftTeams           -RequiredVersion 6.6.0  -Scope AllUsers -Force

# --- Runner code --------------------------------------------------------
WORKDIR /runner
COPY src/ /runner/src/

# The job runs once and exits. Payload arrives via the RUNNER_PAYLOAD env var
# (base64-encoded JSON) set by the triggering Function at job-start time.
ENTRYPOINT ["pwsh", "-NoLogo", "-NonInteractive", "-File", "/runner/src/Invoke-Runner.ps1"]
