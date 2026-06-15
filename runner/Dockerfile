# ──────────────────────────────────────────────────────────────────────
# CDW 365 Baseline – PowerShell Runner
# Single image that hosts every PowerShell-only M365 workload.
# Expansion = add a module here + drop a new file in src/Workloads/.
# ──────────────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/powershell:7.4-ubuntu-22.04

LABEL maintainer="CDW Modern Workplace"
LABEL description="Granular PowerShell apply runner for CDW 365 Baseline"

SHELL ["pwsh", "-NoLogo", "-NonInteractive", "-Command"]

# --- Module set ---------------------------------------------------------
# Pin versions so image builds are reproducible. Add new workload modules
# here as coverage expands (Teams, PnP, Purview, etc.).
RUN $ErrorActionPreference = 'Stop'; \
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; \
    Install-Module ExchangeOnlineManagement -RequiredVersion 3.7.2 -Scope AllUsers -Force; \
    Install-Module PnP.PowerShell           -RequiredVersion 2.12.0 -Scope AllUsers -Force; \
    Install-Module MicrosoftTeams           -RequiredVersion 6.6.0  -Scope AllUsers -Force

# Microsoft365DSC pulls a large, version-locked dependency tree.
# Install the module, then let it resolve its own exact dependency set.
# Trim to the resources you actually use to keep the image lean.
RUN $ErrorActionPreference = 'Stop'; \
    Install-Module Microsoft365DSC -Scope AllUsers -Force; \
    Import-Module Microsoft365DSC; \
    Update-M365DSCDependencies

# --- Runner code --------------------------------------------------------
WORKDIR /runner
COPY src/ /runner/src/

# The job runs once and exits. Payload arrives via the RUNNER_PAYLOAD env var
# (base64-encoded JSON) set by the triggering Function at job-start time.
ENTRYPOINT ["pwsh", "-NoLogo", "-NonInteractive", "-File", "/runner/src/Invoke-Runner.ps1"]
