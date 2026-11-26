<#
.SYNOPSIS
  Token Wars - model availability, quota and deployment-type check (Prerequisites).

.DESCRIPTION
  Builds the configuration you are about to deploy - the original values in infra/variables.tf plus the overrides you
  uncommented in infra/terraform.tfvars (tfvars wins) - and checks every model deployment against the regional model
  catalog and your subscription quota. Read-only: it never creates resources or edits files.
    1. Azure CLI installed + logged in, active subscription
    2. Effective model configuration, showing which values come from terraform.tfvars: premium, balanced, economy,
       decision, judge, embedding and open (location); premium/balanced/economy again in secondary_location when
       deploy_secondary_region = true; fireworks_models in fireworks_location when deploy_fireworks = true
    3. Per region and deployment:
         - model name (case-sensitive), format and version are in the regional catalog
         - deployment types offered for that version: GlobalStandard (Global), DataZoneStandard (Data Zone),
           Standard (Regional); PTU / Batch types are listed but never used by the kit
         - retirement dates, maximum capacity per deployment
         - free quota (limit - used) per quota pool; deployments sharing a pool are added up. "used" includes every
           deployment that already exists in the subscription and region (yours too after terraform apply).
    4. Optional (-ScanRegions): other regions where every model of a failing Foundry resource fits
  Ends with the fix written both ways: a) the override for terraform.tfvars and b) the original value to change in
  variables.tf (file:line). You decide where to change it, then re-run the script until it reports 0 blockers.
  Fireworks feature registration and roles are checked separately by check-fireworks-prereqs.ps1 (Challenge 2.4).
  Bash equivalent (same checks and output, no PowerShell needed): check-model-prereqs.sh.

.PARAMETER TfvarsFile
  Overrides file (default: infra/terraform.tfvars; only the variables.tf values are checked if it is missing).

.PARAMETER Location
  Check the primary Foundry resource in this region instead of the configured location.

.PARAMETER Subscription
  Subscription ID or name to check (default: the active az account).

.PARAMETER ScanRegions
  Also search other regions for a location where every model of a failing resource fits (2 az calls per region).

.PARAMETER Regions
  Candidate regions for -ScanRegions (comma-separated or array). Defaults: EU + US regions for the primary and
  secondary resources, US regions for Fireworks.

.EXAMPLE
  ./scripts/check-model-prereqs.ps1
  ./scripts/check-model-prereqs.ps1 -Location francecentral
  ./scripts/check-model-prereqs.ps1 -ScanRegions
  ./scripts/check-model-prereqs.ps1 -ScanRegions -Regions swedencentral,francecentral,eastus2
#>
[CmdletBinding()]
param(
  [string] $TfvarsFile = "",
  [string] $Location = "",
  [string] $Subscription = "",
  [switch] $ScanRegions,
  [string[]] $Regions = @()
)

$ErrorActionPreference = "Continue"
$InfraDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Inv = [System.Globalization.CultureInfo]::InvariantCulture

$PayPerToken = @("GlobalStandard", "DataZoneStandard", "Standard")
$SkuLabels = @{
  GlobalStandard             = "Global, pay-per-token"
  DataZoneStandard           = "Data Zone, pay-per-token"
  Standard                   = "Regional, pay-per-token"
  GlobalProvisionedManaged   = "Global PTU - not used"
  DataZoneProvisionedManaged = "Data Zone PTU - not used"
  ProvisionedManaged         = "Regional PTU - not used"
  GlobalBatch                = "Global Batch - not used"
  DataZoneBatch              = "Data Zone Batch - not used"
  DeveloperTier              = "Developer - not used"
}
$DefaultScanRegions = @("swedencentral", "francecentral", "germanywestcentral", "spaincentral", "italynorth",
  "polandcentral", "norwayeast", "switzerlandnorth", "uksouth", "westeurope", "northeurope",
  "eastus", "eastus2", "westus3", "southcentralus", "centralus")
$DefaultFireworksScanRegions = @("eastus", "eastus2", "centralus", "northcentralus", "westus", "westus3")
$EuRegions = @("swedencentral", "francecentral", "francesouth", "germanywestcentral", "germanynorth", "spaincentral",
  "italynorth", "polandcentral", "norwayeast", "norwaywest", "switzerlandnorth", "switzerlandwest", "westeurope",
  "northeurope", "swedensouth", "belgiumcentral", "austriaeast", "denmarkeast")

$script:Blockers = 0
$script:Warnings = 0
$script:NextSteps = New-Object System.Collections.Generic.List[string]
function Ok([string] $m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn([string] $m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:Warnings++ }
function Fail([string] $m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:Blockers++ }
function Info([string] $m) { Write-Host "         $m" }
function Next([string] $m) { if (-not $script:NextSteps.Contains($m)) { $script:NextSteps.Add($m) } }

# Runs the Azure CLI and returns { Ok; Data (array); Error } (named AzJson: PowerShell names are case-insensitive).
function AzJson {
  $raw = & az @args -o json 2>&1
  $code = $LASTEXITCODE
  $errText = (@($raw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }) | ForEach-Object { $_.ToString() }) -join " "
  $outText = (@($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })) -join "`n"
  if ($code -ne 0) { return [pscustomobject]@{ Ok = $false; Data = @(); Error = $errText.Trim() } }
  $data = @()
  if ($outText.Trim()) { $data = @(($outText | ConvertFrom-Json) | ForEach-Object { $_ }) }
  return [pscustomobject]@{ Ok = $true; Data = $data; Error = "" }
}

function Normalize-Location([string] $l) { return (($l -replace "\s", "").ToLowerInvariant()) }

function Format-Num($v) {
  if ($null -eq $v) { return "?" }
  $d = [double]$v
  if ([math]::Floor($d) -eq $d) { return ([int64]$d).ToString($Inv) }
  return $d.ToString($Inv)
}

# -----------------------------------------------------------------------------------------------
# Minimal HCL reader: enough for variables.tf (variable blocks, object/optional types, defaults) and terraform.tfvars
# (attributes with strings, numbers, bools, null, nested objects, lists). Expressions are skipped, not evaluated.
# Objects become ordered dictionaries; key "__raw" keeps the source text of every attribute value (used to print
# merged tfvars suggestions that preserve your existing overrides).
# -----------------------------------------------------------------------------------------------
function Read-HclTokens([string] $Text) {
  $list = New-Object System.Collections.Generic.List[object]
  $n = $Text.Length; $i = 0
  $ops2 = @("==", "!=", ">=", "<=", "&&", "||", "=>")
  while ($i -lt $n) {
    $c = $Text[$i]
    if ([char]::IsWhiteSpace($c)) { $i++; continue }
    $two = if ($i + 1 -lt $n) { $Text.Substring($i, 2) } else { [string]$c }
    if ($c -eq "#" -or $two -eq "//") { while ($i -lt $n -and $Text[$i] -ne "`n") { $i++ }; continue }
    if ($two -eq "/*") { $e = $Text.IndexOf("*/", $i + 2); if ($e -lt 0) { $i = $n } else { $i = $e + 2 }; continue }
    $s = $i
    if ($c -eq '"') {
      $sb = New-Object System.Text.StringBuilder
      $i++
      while ($i -lt $n -and $Text[$i] -ne '"') {
        $ch = $Text[$i]
        if ($ch -eq "\" -and $i + 1 -lt $n) { [void]$sb.Append($Text[$i + 1]); $i += 2; continue }
        if ($ch -eq "$" -and $i + 1 -lt $n -and $Text[$i + 1] -eq "{") {
          $depth = 0
          do {
            $cc = $Text[$i]
            if ($cc -eq "{") { $depth++ } elseif ($cc -eq "}") { $depth-- }
            [void]$sb.Append($cc); $i++
          } while ($i -lt $n -and $depth -gt 0)
          continue
        }
        [void]$sb.Append($ch); $i++
      }
      $i++
      $list.Add([pscustomobject]@{ T = "str"; V = $sb.ToString(); S = $s; E = $i }); continue
    }
    if ($two -eq "<<") {
      $m = [regex]::Match($Text.Substring($i), "^<<-?([A-Za-z_][A-Za-z0-9_]*)\r?\n")
      if ($m.Success) {
        $bodyStart = $i + $m.Length
        $em = [regex]::new("(?m)^\s*" + [regex]::Escape($m.Groups[1].Value) + "\s*$").Match($Text, $bodyStart)
        if ($em.Success) { $val = $Text.Substring($bodyStart, $em.Index - $bodyStart); $i = $em.Index + $em.Length }
        else { $val = $Text.Substring($bodyStart); $i = $n }
        $list.Add([pscustomobject]@{ T = "str"; V = $val; S = $s; E = $i }); continue
      }
    }
    if ([char]::IsLetter($c) -or $c -eq "_") {
      while ($i -lt $n -and ([char]::IsLetterOrDigit($Text[$i]) -or "_-.".Contains([string]$Text[$i]))) { $i++ }
      $list.Add([pscustomobject]@{ T = "id"; V = $Text.Substring($s, $i - $s); S = $s; E = $i }); continue
    }
    if ([char]::IsDigit($c)) {
      while ($i -lt $n -and ([char]::IsDigit($Text[$i]) -or ".eE".Contains([string]$Text[$i]))) { $i++ }
      $list.Add([pscustomobject]@{ T = "num"; V = $Text.Substring($s, $i - $s); S = $s; E = $i }); continue
    }
    if ($ops2 -contains $two) { $i += 2; $list.Add([pscustomobject]@{ T = "p"; V = $two; S = $s; E = $i }); continue }
    $i++
    $list.Add([pscustomobject]@{ T = "p"; V = [string]$c; S = $s; E = $i })
  }
  return , $list
}

function Get-Tok([int] $Offset = 0) {
  $k = $script:P + $Offset
  if ($k -lt $script:Tk.Count) { return $script:Tk[$k] }
  return $null
}
function Test-Tok($Tok, [string] $Type, [string[]] $Values) {
  return ($null -ne $Tok -and $Tok.T -eq $Type -and (-not $Values -or $Values -contains $Tok.V))
}

function Get-LineNo([int] $Offset) {
  $lo = 0; $hi = $script:LineStarts.Count - 1
  while ($lo -lt $hi) { $mid = [int][math]::Ceiling(($lo + $hi) / 2); if ($script:LineStarts[$mid] -le $Offset) { $lo = $mid } else { $hi = $mid - 1 } }
  return ($lo + 1)
}

function Read-HclBody([bool] $Braced) {
  $obj = [ordered]@{}
  $raw = [ordered]@{}
  $line = [ordered]@{}
  while ($script:P -lt $script:Tk.Count) {
    $t = Get-Tok
    if (Test-Tok $t "p" "}") { $script:P++; if ($Braced) { break } else { continue } }
    if (Test-Tok $t "p" ",") { $script:P++; continue }
    if ($t.T -eq "id" -or $t.T -eq "str" -or $t.T -eq "num") {
      if (Test-Tok (Get-Tok 1) "p" @("=", ":")) {
        $script:P += 2
        $first = Get-Tok
        $val = Read-HclExpr
        $obj[$t.V] = $val
        $line[$t.V] = Get-LineNo $t.S
        if ($first) { $last = $script:Tk[$script:P - 1]; $raw[$t.V] = $script:Src.Substring($first.S, $last.E - $first.S) }
        continue
      }
      # Block: identifier, optional labels, then "{" (variable "x" { ... }, validation { ... })
      $j = 1; $labels = @($t.V)
      while ((Get-Tok $j) -and ((Get-Tok $j).T -eq "str" -or (Get-Tok $j).T -eq "id")) { $labels += (Get-Tok $j).V; $j++ }
      if (Test-Tok (Get-Tok $j) "p" "{") {
        $script:P += $j + 1
        $key = $labels -join "."
        $line[$key] = Get-LineNo $t.S
        $obj[$key] = Read-HclBody $true
        continue
      }
    }
    $script:P++
  }
  $obj["__raw"] = $raw
  $obj["__line"] = $line
  return $obj
}

function Read-HclSeq([string] $Close) {
  $items = New-Object System.Collections.Generic.List[object]
  while ($script:P -lt $script:Tk.Count) {
    $t = Get-Tok
    if (Test-Tok $t "p" $Close) { $script:P++; break }
    if (Test-Tok $t "p" ",") { $script:P++; continue }
    $before = $script:P
    $items.Add((Read-HclExpr))
    if ($script:P -eq $before) { $script:P++ }
  }
  return , ($items.ToArray())
}

function Read-HclPrimary {
  $t = Get-Tok
  if (-not $t) { return $null }
  $script:P++
  if ($t.T -eq "str") { return $t.V }
  if ($t.T -eq "num") { $d = 0.0; if ([double]::TryParse($t.V, [System.Globalization.NumberStyles]::Float, $Inv, [ref]$d)) { return $d }; return $null }
  if ($t.T -eq "id") {
    if ($t.V -ceq "true") { return $true }
    if ($t.V -ceq "false") { return $false }
    if ($t.V -ceq "null") { return $null }
    if (Test-Tok (Get-Tok) "p" "(") { $script:P++; return [pscustomobject]@{ Call = $t.V; Args = (Read-HclSeq ")") } }
    return [pscustomobject]@{ Ref = $t.V }
  }
  if ($t.V -eq "{") { return (Read-HclBody $true) }
  if ($t.V -eq "[") { return [pscustomobject]@{ List = (Read-HclSeq "]") } }
  if ($t.V -eq "(") { $inner = Read-HclExpr; if (Test-Tok (Get-Tok) "p" ")") { $script:P++ }; return $inner }
  if ($t.V -eq "-") { $inner = Read-HclPrimary; if ($inner -is [double]) { return (-$inner) }; return $null }
  if ($t.V -eq "!") { [void](Read-HclPrimary); return $null }
  return $null
}

function Read-HclExpr {
  $v = Read-HclPrimary
  $ternary = 0
  while ($script:P -lt $script:Tk.Count) {
    $t = Get-Tok
    if ($t.T -ne "p") { break }
    if ($t.V -eq "[") { $script:P++; [void](Read-HclSeq "]"); $v = $null; continue }
    if ($t.V -eq "?") { $ternary++ }
    elseif ($t.V -eq ":") { if ($ternary -le 0) { break }; $ternary-- }
    elseif (@("}", ",", ")", "]", "=", "{", "(") -contains $t.V) { break }
    $script:P++
    [void](Read-HclPrimary)
    $v = $null
  }
  return $v
}

function Read-HclFile([string] $Path) {
  $script:Src = [System.IO.File]::ReadAllText($Path)
  $script:LineStarts = New-Object System.Collections.Generic.List[int]
  $script:LineStarts.Add(0)
  for ($k = 0; $k -lt $script:Src.Length; $k++) { if ($script:Src[$k] -eq "`n") { $script:LineStarts.Add($k + 1) } }
  $script:Tk = Read-HclTokens $script:Src
  $script:P = 0
  return (Read-HclBody $false)
}

function Test-Dict($v) { return ($v -is [System.Collections.IDictionary]) }

# object({ a = optional(string, "x"), b = string }) -> ordered { a = "x"; b = $null }
function Get-ObjectTypeDefaults($TypeExpr) {
  $res = [ordered]@{}
  if ($TypeExpr -and $TypeExpr.Call -eq "object" -and $TypeExpr.Args.Count -gt 0 -and (Test-Dict $TypeExpr.Args[0])) {
    $body = $TypeExpr.Args[0]
    foreach ($k in @($body.Keys)) {
      if ($k -like "__*") { continue }
      $a = $body[$k]
      if ($a -and -not (Test-Dict $a) -and $a.Call -eq "optional" -and $a.Args.Count -ge 2) { $res[$k] = $a.Args[1] } else { $res[$k] = $null }
    }
  }
  return $res
}

# Terraform semantics: the tfvars object replaces the default object; missing optional attributes get their defaults.
function Resolve-ObjectValue($Defaults, $Value) {
  $res = [ordered]@{}
  foreach ($k in @($Defaults.Keys)) { $res[$k] = $Defaults[$k] }
  if (Test-Dict $Value) { foreach ($k in @($Value.Keys)) { if ($k -notlike "__*") { $res[$k] = $Value[$k] } } }
  return $res
}

function Get-VarDecl([string] $Name) { return $script:VarsTf["variable.$Name"] }
function Test-TfvarsSet([string] $Name) { return $script:Tfvars.Contains($Name) }
function Get-VarValue([string] $Name) {
  if (Test-TfvarsSet $Name) { return $script:Tfvars[$Name] }
  $decl = Get-VarDecl $Name
  if ($decl) { return $decl["default"] }
  return $null
}
function Get-ObjectVar([string] $Name) {
  $decl = Get-VarDecl $Name
  if (-not $decl) { return $null }
  return (Resolve-ObjectValue (Get-ObjectTypeDefaults $decl["type"]) (Get-VarValue $Name))
}

# Where the effective value comes from: "tfvars" (override) or "variables.tf" (default / optional() default).
function Get-AttrOrigin([string] $Name, [string] $Attr) {
  if ((Test-TfvarsSet $Name) -and (Test-Dict $script:Tfvars[$Name]) -and $script:Tfvars[$Name].Contains($Attr)) { return "tfvars" }
  return "variables.tf"
}
function Get-ScalarOrigin([string] $Name) { if (Test-TfvarsSet $Name) { return "tfvars" }; return "variables.tf" }

# variables.tf line of an object attribute (its optional(...) declaration) or of a variable's default.
function Get-AttrLine([string] $Name, [string] $Attr) {
  $decl = Get-VarDecl $Name
  if (-not $decl) { return $null }
  $type = $decl["type"]
  if ($type -and $type.Call -eq "map" -and $type.Args.Count -gt 0) { $type = $type.Args[0] }
  if ($type -and $type.Call -eq "object" -and (Test-Dict $type.Args[0])) { return $type.Args[0]["__line"][$Attr] }
  return $null
}
function Get-AttrType([string] $Name, [string] $Attr) {
  $decl = Get-VarDecl $Name
  $type = $decl["type"]
  if ($type -and $type.Call -eq "map" -and $type.Args.Count -gt 0) { $type = $type.Args[0] }
  if ($type -and $type.Call -eq "object" -and (Test-Dict $type.Args[0])) {
    $a = $type.Args[0][$Attr]
    if ($a -and $a.Call -eq "optional" -and $a.Args.Count -gt 0 -and $a.Args[0].Ref) { return $a.Args[0].Ref }
  }
  return "string"
}
function Get-DefaultLine([string] $Name) {
  $decl = Get-VarDecl $Name
  if ($decl -and $decl["__line"].Contains("default")) { return $decl["__line"]["default"] }
  return $script:VarsTf["__line"]["variable.$Name"]
}

# -----------------------------------------------------------------------------------------------
# Regional catalog + quota (cached per region)
# -----------------------------------------------------------------------------------------------
$script:Catalogs = @{}

function Get-RegionCatalog([string] $Loc) {
  if ($script:Catalogs.ContainsKey($Loc)) { return $script:Catalogs[$Loc] }
  $q = "[].{format:model.format, name:model.name, version:model.version, isDefault:model.isDefaultVersion, " +
       "lifecycle:model.lifecycleStatus, retire:model.deprecation.inference, " +
       "skus:model.skus[].{name:name, usage:usageName, max:capacity.maximum, retire:deprecationDate}}"
  $ml = AzJson cognitiveservices model list --location $Loc --query $q
  $ul = AzJson cognitiveservices usage list --location $Loc --query "[].{name:name.value, current:currentValue, limit:limit}"

  # The catalog lists each model once per account kind (OpenAI, AIServices): merge by format + name + version.
  $entries = New-Object "System.Collections.Generic.Dictionary[string,object]" ([System.StringComparer]::Ordinal)
  foreach ($m in $ml.Data) {
    if (-not $m.name) { continue }
    $key = "$($m.format)|$($m.name)|$($m.version)"
    if (-not $entries.ContainsKey($key)) {
      $entries[$key] = [pscustomobject]@{ Format = [string]$m.format; Name = [string]$m.name; Version = [string]$m.version
        IsDefault = [bool]$m.isDefault; Lifecycle = [string]$m.lifecycle; Retire = [string]$m.retire; Skus = [ordered]@{} }
    }
    $e = $entries[$key]
    if ($m.isDefault) { $e.IsDefault = $true }
    foreach ($s in @($m.skus)) {
      if (-not $s -or -not $s.name) { continue }
      if (-not $e.Skus.Contains([string]$s.name) -or (-not $e.Skus[[string]$s.name].Usage -and $s.usage)) {
        $e.Skus[[string]$s.name] = [pscustomobject]@{ Name = [string]$s.name; Usage = [string]$s.usage; Max = $s.max; Retire = [string]$s.retire }
      }
    }
  }
  $usage = @{}
  foreach ($u in $ul.Data) { if ($u.name) { $usage[[string]$u.name] = $u } }

  $cat = [pscustomobject]@{ Location = $Loc; Ok = $ml.Ok; Error = $ml.Error; UsageOk = $ul.Ok; UsageError = $ul.Error
    Models = @($entries.Values); Usage = $usage }
  $script:Catalogs[$Loc] = $cat
  return $cat
}

function Get-FreeQuota($Cat, [string] $UsageName) {
  if (-not $UsageName -or -not $Cat.Usage.ContainsKey($UsageName)) { return $null }
  $u = $Cat.Usage[$UsageName]
  return ([double]$u.limit - [double]$u.current)
}

function Get-RetireDate([string] $s) {
  if (-not $s) { return $null }
  $d = [datetime]::MinValue
  if ([datetime]::TryParse($s, $Inv, [System.Globalization.DateTimeStyles]::AssumeUniversal, [ref]$d)) { return $d }
  return $null
}

function Format-SkuList($Entry, [string] $Configured) {
  $parts = foreach ($k in @($Entry.Skus.Keys)) {
    $label = if ($SkuLabels.ContainsKey($k)) { $SkuLabels[$k] } else { "not used" }
    $mark = if ($k -eq $Configured) { "*" } else { "" }
    "$k$mark ($label)"
  }
  return ($parts -join ", ")
}

# -----------------------------------------------------------------------------------------------
# Evaluation of one group of deployments (one Foundry resource) in one region. Pure: returns results, prints nothing.
# Result.Fix = tfvars attribute changes that make the deployment work in this region;
# Result.NeedsRegion = no in-place fix (model / pay-per-token type / quota not available in the region).
# -----------------------------------------------------------------------------------------------
function Add-Line($R, [string] $Level, [string] $Msg) {
  $R.Lines.Add([pscustomobject]@{ Level = $Level; Msg = $Msg })
  if ($Level -eq "fail") { $R.Status = "fail" } elseif ($Level -eq "warn" -and $R.Status -eq "ok") { $R.Status = "warn" }
}
function Set-Fix($R, [string] $Attr, $Value, [string] $Why) { $R.Fix[$Attr] = $Value; $R.Why.Add("$($R.Dep.Label): $Why") }

function Resolve-Deployment($D, $Cat) {
  $r = [pscustomobject]@{ Dep = $D; Lines = (New-Object System.Collections.Generic.List[object]); Status = "ok"
    Fix = [ordered]@{}; Why = (New-Object System.Collections.Generic.List[string]); NeedsRegion = $false
    Entry = $null; SkuMissing = $false; Usage = $null }
  $loc = $Cat.Location
  if (-not $Cat.Ok) {
    Add-Line $r "fail" "cannot read the model catalog of ${loc}: $($Cat.Error)"
    $r.NeedsRegion = $true
    return $r
  }
  $byName = @($Cat.Models | Where-Object { $_.Name -ceq $D.Name })
  if ($byName.Count -eq 0) {
    $ci = @($Cat.Models | Where-Object { $_.Name -ieq $D.Name }) | Select-Object -First 1
    if ($ci) {
      Add-Line $r "fail" "'$($D.Name)' not found; the catalog name is case-sensitive: '$($ci.Name)'"
      Set-Fix $r "name" $ci.Name "exact catalog name"
      $byName = @($Cat.Models | Where-Object { $_.Name -ceq $ci.Name })
    } else {
      Add-Line $r "fail" "'$($D.Name)' is not offered in $loc"
      $prefix = (($D.Name -split "[-.]")[0])
      $similar = @($Cat.Models | Where-Object { $_.Name -like "$prefix*" } | ForEach-Object { $_.Name } | Sort-Object -Unique | Select-Object -First 10)
      if ($similar.Count -gt 0) { Add-Line $r "info" ("similar models in ${loc}: " + ($similar -join ", ")) }
      $r.NeedsRegion = $true
      return $r
    }
  }
  $fmt = @($byName | Where-Object { $_.Format -eq $D.Format })
  if ($fmt.Count -eq 0) {
    $f = $byName[0].Format
    Add-Line $r "fail" "format '$($D.Format)' does not match the catalog ('$f')"
    Set-Fix $r "format" $f "catalog format"
    $fmt = @($byName | Where-Object { $_.Format -eq $f })
  }
  $sorted = @($fmt | Sort-Object Version)
  $default = @($sorted | Where-Object { $_.IsDefault }) + @($sorted[-1]) | Select-Object -First 1
  if ($D.Version) {
    $e = @($sorted | Where-Object { $_.Version -eq $D.Version }) | Select-Object -First 1
    if (-not $e) {
      Add-Line $r "fail" ("version $($D.Version) is not offered (available: " + (($sorted | ForEach-Object { $_.Version }) -join ", ") + "; default $($default.Version))")
      Set-Fix $r "version" $default.Version "version offered in the region"
      $e = $default
    }
  } else {
    $e = $default
    Add-Line $r "info" "version not pinned -> Azure deploys the default version $($e.Version)"
  }
  $r.Entry = $e
  $life = if ($e.Lifecycle) { ", $($e.Lifecycle)" } else { "" }
  Add-Line $r "ok" "in catalog: $($e.Name) $($e.Version) ($($e.Format)$life)"
  Add-Line $r "info" ("deployment types: " + (Format-SkuList $e $D.Sku))

  $now = (Get-Date).ToUniversalTime()
  $rd = Get-RetireDate $e.Retire
  if ($rd -and $rd -lt $now) { Add-Line $r "fail" "model version retired on $($rd.ToString('yyyy-MM-dd'))" }
  elseif ($rd -and $rd -lt $now.AddDays(45)) { Add-Line $r "warn" "model version retires on $($rd.ToString('yyyy-MM-dd'))" }

  if (-not $e.Skus.Contains($D.Sku)) {
    Add-Line $r "fail" "deployment type $($D.Sku) is not offered for this model version in $loc"
    $r.SkuMissing = $true
    return $r
  }
  $s = $e.Skus[$D.Sku]
  $r.Usage = $s.Usage
  $sd = Get-RetireDate $s.Retire
  if ($sd -and $sd -lt $now) { Add-Line $r "fail" "deployment type $($D.Sku) retired on $($sd.ToString('yyyy-MM-dd'))" }
  elseif ($sd -and $sd -lt $now.AddDays(45)) { Add-Line $r "warn" "deployment type $($D.Sku) retires on $($sd.ToString('yyyy-MM-dd'))" }
  if ($null -ne $s.Max -and [double]$s.Max -gt 0 -and $D.Capacity -gt [double]$s.Max) {
    Add-Line $r "fail" "capacity $(Format-Num $D.Capacity) is above the maximum $(Format-Num $s.Max) per $($D.Sku) deployment"
    Set-Fix $r "capacity" ([double]$s.Max) "maximum capacity per deployment"
  }
  return $r
}

function Test-Deployments([object[]] $Deps, [string] $Loc) {
  $cat = Get-RegionCatalog $Loc
  $results = @(foreach ($d in $Deps) { Resolve-Deployment $d $cat })

  # Requested capacity per quota pool (deployments of the same model + type in a region share one pool).
  $demand = @{}
  foreach ($r in $results) {
    if (-not $r.Usage) { continue }
    $cap = if ($r.Fix.Contains("capacity")) { [double]$r.Fix["capacity"] } else { [double]$r.Dep.Capacity }
    $demand[$r.Usage] = $(if ($demand.ContainsKey($r.Usage)) { $demand[$r.Usage] } else { 0.0 }) + $cap
  }

  foreach ($r in $results) {
    $d = $r.Dep
    $cap = if ($r.Fix.Contains("capacity")) { [double]$r.Fix["capacity"] } else { [double]$d.Capacity }
    $alts = @()
    if ($r.Entry) {
      $alts = @(foreach ($sku in $d.Allowed) {
        if ($sku -eq $d.Sku -or -not $r.Entry.Skus.Contains($sku)) { continue }
        [pscustomobject]@{ Sku = $sku; Free = (Get-FreeQuota $cat $r.Entry.Skus[$sku].Usage) }
      })
    }
    if ($r.SkuMissing) {
      $fit = @($alts | Where-Object { $null -ne $_.Free -and $_.Free -ge $cap }) | Select-Object -First 1
      $any = @($alts | Where-Object { $null -ne $_.Free -and $_.Free -ge 1 } | Sort-Object Free -Descending) | Select-Object -First 1
      $unknown = @($alts | Where-Object { $null -eq $_.Free }) | Select-Object -First 1
      if ($fit) {
        Add-Line $r "info" "-> $($fit.Sku) is offered with $(Format-Num $fit.Free) free quota"
        Set-Fix $r "sku" $fit.Sku "$($d.Sku) not offered, $($fit.Sku) has quota"
      } elseif ($any) {
        Add-Line $r "info" "-> $($any.Sku) is offered but only $(Format-Num $any.Free) quota is free"
        Set-Fix $r "sku" $any.Sku "$($d.Sku) not offered"
        Set-Fix $r "capacity" ([math]::Floor($any.Free)) "free $($any.Sku) quota"
      } elseif ($unknown) {
        Add-Line $r "warn" "-> $($unknown.Sku) is offered; its quota is not reported (check Foundry portal > Quota)"
        Set-Fix $r "sku" $unknown.Sku "$($d.Sku) not offered"
      } else {
        Add-Line $r "info" ("allowed deployment types for this deployment: " + ($d.Allowed -join ", ") + " - none has quota here")
        $r.NeedsRegion = $true
      }
      continue
    }
    if (-not $r.Usage) {
      if ($r.Entry) { Add-Line $r "warn" "quota pool not reported for $($d.Sku); check Foundry portal > Quota" }
      continue
    }
    $free = Get-FreeQuota $cat $r.Usage
    if ($null -eq $free) {
      $why = if (-not $cat.UsageOk) { " ($($cat.UsageError))" } else { "" }
      Add-Line $r "warn" "quota pool $($r.Usage) not listed by az cognitiveservices usage list$why; check Foundry portal > Quota"
      continue
    }
    $u = $cat.Usage[$r.Usage]
    $total = [double]$demand[$r.Usage]
    $poolTxt = if ($total -ne $cap) { " (pool total $(Format-Num $total))" } else { "" }
    $msg = "quota $($r.Usage): limit $(Format-Num $u.limit), used $(Format-Num $u.current) -> free $(Format-Num $free); needs $(Format-Num $cap)$poolTxt"
    if ($free -ge $total) { Add-Line $r "ok" $msg; continue }
    Add-Line $r "fail" $msg
    $alt = @($alts | Where-Object { $null -ne $_.Free -and $_.Free -ge $cap } | Sort-Object Free -Descending) | Select-Object -First 1
    $share = [math]::Floor($free * $cap / $total)
    if ($alt) {
      Add-Line $r "info" "option: sku = `"$($alt.Sku)`" ($(Format-Num $alt.Free) free, keeps capacity $(Format-Num $cap))"
      Set-Fix $r "sku" $alt.Sku "not enough $($d.Sku) quota, $($alt.Sku) has $(Format-Num $alt.Free) free"
    }
    if ($share -ge 1) {
      Add-Line $r "info" "option: capacity = $(Format-Num $share) (fits the free $($d.Sku) quota)"
      if (-not $alt) { Set-Fix $r "capacity" ([double]$share) "free $($d.Sku) quota ($(Format-Num $free))" }
    }
    if (-not $alt -and $share -lt 1) { $r.NeedsRegion = $true }
    Add-Line $r "info" "option: request more quota (Foundry portal > Management center > Quota)"
  }
  return $results
}

# -----------------------------------------------------------------------------------------------
# Output helpers: results, region scan, tfvars suggestions
# -----------------------------------------------------------------------------------------------
function Format-Hcl($v) {
  if ($null -eq $v) { return "null" }
  if ($v -is [bool]) { if ($v) { return "true" } else { return "false" } }
  if ($v -is [double] -or $v -is [int] -or $v -is [int64]) { return (Format-Num $v) }
  return '"' + (([string]$v) -replace '\\', '\\' -replace '"', '\"') + '"'
}
function Format-HclKey([string] $k) { if ($k -match '^[A-Za-z_][A-Za-z0-9_-]*$') { return $k }; return '"' + $k + '"' }

# Re-indents a multi-line raw value (nested object you already wrote) under the given indentation.
function Format-RawValue([string] $Raw, [string] $Indent) {
  $lines = $Raw -split "`r?`n"
  if ($lines.Count -le 1) { return $Raw }
  $rest = @($lines[1..($lines.Count - 1)])
  $min = ($rest | Where-Object { $_.Trim() } | ForEach-Object { $_.Length - $_.TrimStart().Length } | Measure-Object -Minimum).Minimum
  if ($null -eq $min) { $min = 0 }
  $out = @($lines[0]) + @($rest | ForEach-Object { if ($_.Length -ge $min) { "$Indent$($_.Substring($min))" } else { "$Indent$($_.TrimStart())" } })
  return ($out -join "`n")
}

# Merges attribute changes into the attributes you already set (kept verbatim) and prints one assignment.
function Format-ObjectAssign([string] $Name, $Raw, $Changes, [string] $Indent) {
  $pairs = [ordered]@{}
  if (Test-Dict $Raw) { foreach ($k in @($Raw.Keys)) { $pairs[$k] = $Raw[$k] } }
  foreach ($k in @($Changes.Keys)) { $pairs[$k] = Format-Hcl $Changes[$k] }
  $multi = @($pairs.Values | Where-Object { $_ -match "`n" }).Count -gt 0
  if (-not $multi) {
    $body = (@($pairs.Keys) | ForEach-Object { "$(Format-HclKey $_) = $($pairs[$_])" }) -join ", "
    return @("$Indent$(Format-HclKey $Name) = { $body }")
  }
  $lines = @("$Indent$(Format-HclKey $Name) = {")
  foreach ($k in @($pairs.Keys)) { $lines += "$Indent  $(Format-HclKey $k) = $(Format-RawValue $pairs[$k] "$Indent  ")" }
  return ($lines + "$Indent}")
}

function New-ChangeSet { return [pscustomobject]@{ Scalars = [ordered]@{}; Objects = [ordered]@{}; Fw = [ordered]@{}; Why = (New-Object System.Collections.Generic.List[string]) } }

# A group can be fixed in place when no deployment needs another region. The secondary resource reuses the primary
# model objects: catalog fixes (name, format, version) apply to them, capacity goes to secondary_capacity, but a
# different SKU would also change the primary deployment -> treated as "needs another secondary_location".
function Test-NeedsRegion([string] $Group, $R) {
  return ($R.NeedsRegion -or ($Group -eq "secondary" -and $R.Fix.Contains("sku")))
}
function Test-InPlace([string] $Group, $Results) {
  foreach ($r in $Results) { if (Test-NeedsRegion $Group $r) { return $false } }
  return $true
}

function Add-Fixes($Cs, [string] $Group, $Results) {
  foreach ($r in $Results) {
    if ($r.Fix.Count -eq 0 -or (Test-NeedsRegion $Group $r)) { continue }
    $d = $r.Dep
    foreach ($w in $r.Why) { if (-not $Cs.Why.Contains($w)) { $Cs.Why.Add($w) } }
    foreach ($attr in @($r.Fix.Keys)) {
      $val = $r.Fix[$attr]
      if ($Group -eq "secondary" -and $attr -eq "capacity") {
        $prev = if ($Cs.Scalars.Contains("secondary_capacity")) { [double]$Cs.Scalars["secondary_capacity"] } else { [double]::MaxValue }
        $Cs.Scalars["secondary_capacity"] = [math]::Min($prev, [double]$val)
      } elseif ($Group -eq "fireworks") {
        if ($attr -eq "format") { $Cs.Scalars["fireworks_model_format"] = $val; continue }
        if (-not $Cs.Fw.Contains($d.FwKey)) { $Cs.Fw[$d.FwKey] = [ordered]@{} }
        $key = if ($attr -eq "name") { "model" } else { $attr }
        $Cs.Fw[$d.FwKey][$key] = $val
      } else {
        if (-not $Cs.Objects.Contains($d.Var)) { $Cs.Objects[$d.Var] = [ordered]@{} }
        $Cs.Objects[$d.Var][$attr] = $val
      }
    }
  }
}

function Write-FireworksMap([string] $Name, $Cs, [string] $Indent) {
  # A map variable is replaced as a whole: print every entry (current attributes + the changes).
  $src = Get-VarValue "fireworks_models"
  Write-Host "$Indent$Name = {" -ForegroundColor Cyan
  foreach ($k in @($src.Keys)) {
    if ($k -like "__*") { continue }
    $changes = if ($Cs.Fw.Contains($k)) { $Cs.Fw[$k] } else { [ordered]@{} }
    $raw = if (Test-Dict $src[$k]) { $src[$k]["__raw"] } else { $null }
    Format-ObjectAssign $k $raw $changes "$Indent  " | ForEach-Object { Write-Host $_ -ForegroundColor Cyan }
  }
  Write-Host "$Indent}" -ForegroundColor Cyan
}

# Prints the same fix twice: as a terraform.tfvars override and as a change of the original value in variables.tf.
# A variables.tf change has no effect for values that terraform.tfvars overrides: those are flagged instead.
function Write-ChangeSet($Cs, [string] $Indent) {
  if (($Cs.Scalars.Count + $Cs.Objects.Count + $Cs.Fw.Count) -eq 0) { return }
  foreach ($w in $Cs.Why) { Write-Host "$Indent# $w" -ForegroundColor DarkGray }
  $sub = "$Indent  "
  Write-Host "${Indent}a) override in terraform.tfvars (takes precedence over variables.tf):"
  foreach ($k in @($Cs.Scalars.Keys)) { Write-Host "$sub$k = $(Format-Hcl $Cs.Scalars[$k])" -ForegroundColor Cyan }
  foreach ($var in @($Cs.Objects.Keys)) {
    $raw = $null
    if ((Test-TfvarsSet $var) -and (Test-Dict $script:Tfvars[$var])) { $raw = $script:Tfvars[$var]["__raw"] }
    Format-ObjectAssign $var $raw $Cs.Objects[$var] $sub | ForEach-Object { Write-Host $_ -ForegroundColor Cyan }
  }
  if ($Cs.Fw.Count -gt 0) { Write-FireworksMap "fireworks_models" $Cs $sub }

  Write-Host "${Indent}b) or change the original value in variables.tf:"
  foreach ($k in @($Cs.Scalars.Keys)) {
    if ((Get-ScalarOrigin $k) -eq "tfvars") { Write-Host "$sub# $k is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)" -ForegroundColor Yellow; continue }
    Write-Host ("{0}variables.tf:{1,-5} variable `"{2}`" {{ default = {3} }}" -f $sub, (Get-DefaultLine $k), $k, (Format-Hcl $Cs.Scalars[$k])) -ForegroundColor Cyan
  }
  foreach ($var in @($Cs.Objects.Keys)) {
    foreach ($attr in @($Cs.Objects[$var].Keys)) {
      if ((Get-AttrOrigin $var $attr) -eq "tfvars") { Write-Host "$sub# $var.$attr is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)" -ForegroundColor Yellow; continue }
      Write-Host ("{0}variables.tf:{1,-5} variable `"{2}`": {3} = optional({4}, {5})" -f $sub, (Get-AttrLine $var $attr), $var, $attr, (Get-AttrType $var $attr), (Format-Hcl $Cs.Objects[$var][$attr])) -ForegroundColor Cyan
    }
  }
  if ($Cs.Fw.Count -gt 0) {
    if ((Get-ScalarOrigin "fireworks_models") -eq "tfvars") { Write-Host "$sub# fireworks_models is overridden in terraform.tfvars: change it there (a variables.tf change has no effect)" -ForegroundColor Yellow }
    else {
      Write-Host ("{0}variables.tf:{1,-5} variable `"fireworks_models`": replace its default with" -f $sub, (Get-DefaultLine "fireworks_models")) -ForegroundColor Cyan
      Write-FireworksMap "default" $Cs "$sub  "
    }
  }
}

function Write-Results($Results) {
  foreach ($r in $Results) {
    $d = $r.Dep
    $ver = if ($d.Version) { $d.Version } else { "(default version)" }
    Write-Host ("  - {0}: {1} {2} / {3} / {4} / capacity {5}   [{6}]" -f $d.Label, $d.Name, $ver, $d.Format, $d.Sku, (Format-Num $d.Capacity), $d.Source) -ForegroundColor White
    foreach ($l in $r.Lines) {
      switch ($l.Level) { "ok" { Ok $l.Msg } "warn" { Warn $l.Msg } "fail" { Fail $l.Msg } default { Info $l.Msg } }
    }
    if ($d.Note) { Info $d.Note }
  }
}

function Format-FixSummary($Results) {
  $parts = foreach ($r in $Results) {
    if ($r.Fix.Count -eq 0) { continue }
    $attrs = (@($r.Fix.Keys) | ForEach-Object { $v = $r.Fix[$_]; if ($v -is [double]) { "$_=$(Format-Num $v)" } else { "$_=$v" } }) -join " "
    "$($r.Dep.Label) $attrs"
  }
  return ($parts -join "; ")
}

function Invoke-RegionScan([string] $Group, $Deps, [string] $Current) {
  $cands = if ($Regions.Count -gt 0) { $Regions } elseif ($Group -eq "fireworks") { $DefaultFireworksScanRegions } else { $DefaultScanRegions }
  $cands = @($cands | ForEach-Object { $_ -split "," } | ForEach-Object { Normalize-Location $_ } | Where-Object { $_ -and $_ -ne $Current } | Select-Object -Unique)
  $rows = @()
  foreach ($loc in $cands) {
    $res = @(Test-Deployments $Deps $loc)
    $cat = $script:Catalogs[$loc]
    if (-not $cat.Ok) { Info ("{0,-20} catalog not readable" -f $loc); continue }
    $missing = @($res | Where-Object { Test-NeedsRegion $Group $_ } | ForEach-Object { $_.Dep.Name } | Select-Object -Unique)
    $changed = @($res | Where-Object { $_.Fix.Count -gt 0 }).Count
    $unknown = @($res | Where-Object { $_.Status -eq "warn" }).Count
    if ($missing.Count -gt 0) { Info ("{0,-20} missing: {1}" -f $loc, ($missing -join ", ")) }
    elseif ($changed -eq 0) { Write-Host ("  [ OK ] {0,-20} fits as configured{1}" -f $loc, $(if ($unknown) { " ($unknown quota value(s) not reported)" } else { "" })) -ForegroundColor Green }
    else { Write-Host ("  [ OK ] {0,-20} fits with changes: {1}" -f $loc, (Format-FixSummary $res)) -ForegroundColor Green }
    if ($missing.Count -eq 0) { $rows += [pscustomobject]@{ Location = $loc; Changes = $changed; Unknown = $unknown; Results = $res } }
  }
  return (@($rows | Sort-Object Changes, Unknown) | Select-Object -First 1)
}

function New-Dep([string] $Var, [string] $Label, [string] $Group, [string] $Loc, $M, [string[]] $Allowed, [string] $Note = "") {
  return [pscustomobject]@{ Var = $Var; Label = $Label; Group = $Group; Location = $Loc; Name = [string]$M["name"]
    Format = [string]$M["format"]; Version = $(if ($null -ne $M["version"]) { [string]$M["version"] } else { $null })
    Sku = [string]$M["sku"]; Capacity = [double]$M["capacity"]; Allowed = $Allowed; FwKey = ""; Source = $Var; Note = $Note; Overrides = @() }
}

# ===============================================================================================
Write-Host "Token Wars - model availability, quota and deployment type check`n"

# 1. Azure CLI + login ------------------------------------------------------------------------
Write-Host "1) Azure CLI"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  Fail "Azure CLI (az) not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
  exit 1
}
if ($Subscription) {
  & az account set --subscription $Subscription 2>$null
  if ($LASTEXITCODE -ne 0) { Fail "Cannot select subscription $Subscription"; exit 1 }
}
$acct = AzJson account show --query "{id:id, name:name}"
if (-not $acct.Ok -or -not $acct.Data[0].id) { Fail "Not logged in. Run: az login"; exit 1 }
$token = AzJson account get-access-token --query expiresOn
if (-not $token.Ok) { Fail "The Azure CLI session expired or is invalid. Run: az login   ($($token.Error))"; exit 1 }
Ok "Subscription: $($acct.Data[0].name) ($($acct.Data[0].id))"
Info "Quota is per subscription + region + model + deployment type: run this with the subscription you deploy to."

# 2. Terraform configuration --------------------------------------------------------------------
Write-Host "2) Terraform model configuration"
$varsPath = Join-Path $InfraDir "variables.tf"
if (-not (Test-Path $varsPath)) { Fail "variables.tf not found in $InfraDir"; exit 1 }
$script:VarsTf = Read-HclFile $varsPath
Ok "Original values: $varsPath"
if (-not $TfvarsFile) { $TfvarsFile = Join-Path $InfraDir "terraform.tfvars" }
if (Test-Path $TfvarsFile) {
  $script:Tfvars = Read-HclFile $TfvarsFile
  $active = @($script:Tfvars.Keys | Where-Object { $_ -notlike "__*" -and $script:VarsTf.Contains("variable.$_") -and
    ($_ -match "_model$|^location$|^deploy_|^secondary_|^fireworks_") })
  Ok ("Overrides: $TfvarsFile" + $(if ($active.Count -gt 0) { " (active: " + ($active -join ", ") + ")" } else { " (no model or region overrides active)" }))
} else {
  $script:Tfvars = [ordered]@{ "__raw" = [ordered]@{}; "__line" = [ordered]@{} }
  Info "No overrides: $TfvarsFile not found (cp terraform.tfvars.example terraform.tfvars)."
}

function Set-Origins($D, [string] $Var, [string[]] $Attrs) {
  $D.Overrides = @($Attrs | Where-Object { (Get-AttrOrigin $Var $_) -eq "tfvars" })
  if ($D.Overrides.Count -gt 0) { $D.Source = "$($D.Source); terraform.tfvars overrides: " + ($D.Overrides -join ", ") }
  else { $D.Source = "$($D.Source); variables.tf" }
}

$primaryLoc = Normalize-Location $(if ($Location) { $Location } else { [string](Get-VarValue "location") })
$modelAttrs = @("name", "version", "format", "sku", "capacity")
$groups = [ordered]@{}
$chat = @("GlobalStandard", "DataZoneStandard", "Standard")
$primary = @(
  New-Dep "premium_model" "premium" "primary" $primaryLoc (Get-ObjectVar "premium_model") $chat
  New-Dep "balanced_model" "balanced" "primary" $primaryLoc (Get-ObjectVar "balanced_model") $chat
  New-Dep "economy_model" "economy" "primary" $primaryLoc (Get-ObjectVar "economy_model") $chat
  New-Dep "decision_model" "decision" "primary" $primaryLoc (Get-ObjectVar "decision_model") @("GlobalStandard", "DataZoneStandard") "Decision quota units are model-specific (not TPM); pricing_key follows the SKU automatically."
  New-Dep "judge_model" "judge" "primary" $primaryLoc (Get-ObjectVar "judge_model") $chat
  New-Dep "embedding_model" "embedding" "primary" $primaryLoc (Get-ObjectVar "embedding_model") $chat
)
if ([bool](Get-VarValue "deploy_open_model")) {
  $primary += New-Dep "open_model" "open" "primary" $primaryLoc (Get-ObjectVar "open_model") $chat
}
foreach ($d in $primary) { Set-Origins $d $d.Var $modelAttrs }
$groups["primary"] = [pscustomobject]@{ Label = "primary Foundry resource"; LocVar = "location"; Location = $primaryLoc; Deps = $primary }

if ([bool](Get-VarValue "deploy_secondary_region")) {
  $secLoc = Normalize-Location ([string](Get-VarValue "secondary_location"))
  $secCap = [double](Get-VarValue "secondary_capacity")
  $secondary = @(foreach ($p in $primary[0..2]) {
    $c = $p.PSObject.Copy(); $c.Group = "secondary"; $c.Location = $secLoc; $c.Capacity = $secCap
    $c.Label = "$($p.Label) (secondary)"; $c.Source = "$($p.Var) + secondary_capacity"
    Set-Origins $c $p.Var @("name", "version", "format", "sku")
    if ((Get-ScalarOrigin "secondary_capacity") -eq "tfvars") { $c.Source += " (secondary_capacity from terraform.tfvars)" }
    $c
  })
  $groups["secondary"] = [pscustomobject]@{ Label = "secondary Foundry resource (failover)"; LocVar = "secondary_location"; Location = $secLoc; Deps = $secondary }
}

if ([bool](Get-VarValue "deploy_fireworks")) {
  $fwLoc = Normalize-Location ([string](Get-VarValue "fireworks_location"))
  $fwFormat = [string](Get-VarValue "fireworks_model_format")
  $fwDecl = Get-VarDecl "fireworks_models"
  $fwDefaults = Get-ObjectTypeDefaults $(if ($fwDecl["type"].Call -eq "map") { $fwDecl["type"].Args[0] } else { $null })
  $fwValue = Get-VarValue "fireworks_models"
  $fwOrigin = if ((Get-ScalarOrigin "fireworks_models") -eq "tfvars") { "terraform.tfvars" } else { "variables.tf" }
  $fwDeps = @(foreach ($k in @($fwValue.Keys)) {
    if ($k -like "__*") { continue }
    $m = Resolve-ObjectValue $fwDefaults $fwValue[$k]
    $m["name"] = $m["model"]; $m["format"] = $fwFormat
    $d = New-Dep "fireworks_models" $k "fireworks" $fwLoc $m @("GlobalStandard", "DataZoneStandard")
    $d.FwKey = $k; $d.Source = "fireworks_models.$k; $fwOrigin"; $d
  })
  $groups["fireworks"] = [pscustomobject]@{ Label = "Fireworks Foundry resource (Challenge 2.4)"; LocVar = "fireworks_location"; Location = $fwLoc; Deps = $fwDeps }
}

foreach ($g in $groups.Values) {
  $locOrigin = if ($Location -and $g.LocVar -eq "location") { "-Location" } elseif ((Get-ScalarOrigin $g.LocVar) -eq "tfvars") { "terraform.tfvars" } else { "variables.tf" }
  Info ("{0}: {1} = {2} ({3})" -f $g.Label, $g.LocVar, $g.Location, $locOrigin)
  foreach ($d in $g.Deps) {
    $ver = if ($d.Version) { $d.Version } else { "(default version)" }
    Info ("  {0,-22} {1} {2} / {3} / {4} / capacity {5}   [{6}]" -f $d.Label, $d.Name, $ver, $d.Format, $d.Sku, (Format-Num $d.Capacity), ($d.Source -replace "^[^;]*; ", ""))
  }
}
$depsFlags = @("deploy_open_model", "deploy_secondary_region", "deploy_fireworks") | ForEach-Object { "$_ = $(([string][bool](Get-VarValue $_)).ToLower()) ($(if ((Get-ScalarOrigin $_) -eq 'tfvars') { 'terraform.tfvars' } else { 'variables.tf' }))" }
Info ("Switches: " + ($depsFlags -join ", "))
Info "Quota 'used' includes every deployment that already exists in the subscription and region"
Info "(your own too if you already ran terraform apply)."

# 3+. One section per Foundry resource -----------------------------------------------------------
$step = 3
$evaluated = [ordered]@{}
foreach ($name in @($groups.Keys)) {
  $g = $groups[$name]
  Write-Host "$step) Region $($g.Location) - $($g.Label)"
  $step++
  $cat = Get-RegionCatalog $g.Location
  if (-not $cat.Ok) {
    Fail "cannot read the model catalog of $($g.Location): $($cat.Error)"
    if ($cat.Error -match "MissingSubscriptionRegistration|not registered") { Next "az provider register --namespace Microsoft.CognitiveServices   (then re-run this script)" }
    elseif ($cat.Error -match "NoRegisteredProviderFound|InvalidLocation|location") { Next "Check the region name '$($g.Location)' ($($g.LocVar)): az account list-locations -o table" }
    continue
  }
  if (-not $cat.UsageOk) { Warn "cannot read quota usage in $($g.Location): $($cat.UsageError)" }
  $res = @(Test-Deployments $g.Deps $g.Location)
  Write-Results $res
  $evaluated[$name] = $res
  if ($name -eq "fireworks" -and @($res | Where-Object { $_.NeedsRegion -and -not $_.Entry }).Count -gt 0) {
    Next "Fireworks models are only listed once Fireworks.EnableDeploy is registered: run scripts/check-fireworks-prereqs.ps1 (or .sh) -Register"
  }
}

# Region scan ---------------------------------------------------------------------------------------
$moves = [ordered]@{}
$failing = @($evaluated.Keys | Where-Object { @($evaluated[$_] | Where-Object { $_.Status -eq "fail" }).Count -gt 0 })
if ($failing.Count -gt 0) {
  if ($ScanRegions) {
    foreach ($name in $failing) {
      $g = $groups[$name]
      Write-Host "$step) Region scan for the $($g.Label) (all of: $((($g.Deps | ForEach-Object { $_.Name }) | Select-Object -Unique) -join ', '))"
      $step++
      $best = Invoke-RegionScan $name $g.Deps $g.Location
      if ($best) { $moves[$name] = $best } else { Info "no candidate region fits every model; try -Regions with other regions or change the models." }
    }
  } else {
    Next "Re-run with -ScanRegions (bash: --scan-regions) to find a region where every model of a failing resource fits."
  }
}

# Summary + tfvars suggestions ------------------------------------------------------------------------
Write-Host ""
Write-Host "Summary: $($script:Blockers) blocker(s), $($script:Warnings) warning(s)."
$inPlace = New-ChangeSet
$blocked = @()
foreach ($name in @($evaluated.Keys)) {
  Add-Fixes $inPlace $name $evaluated[$name]
  if (-not (Test-InPlace $name $evaluated[$name])) {
    $who = @($evaluated[$name] | Where-Object { Test-NeedsRegion $name $_ } | ForEach-Object { "$($_.Dep.Label) ($($_.Dep.Name))" })
    $blocked += [pscustomobject]@{ Name = $name; Who = ($who -join ", ") }
  }
}
$hasInPlace = ($inPlace.Scalars.Count + $inPlace.Objects.Count + $inPlace.Fw.Count) -gt 0
if ($hasInPlace -or $blocked.Count -gt 0 -or $moves.Count -gt 0) {
  Write-Host ""
  Write-Host "Suggested changes - each fix as a) terraform.tfvars override or b) original value in variables.tf:"
  if (-not (Test-Path $TfvarsFile)) { Info "(for a) create $TfvarsFile first: cp terraform.tfvars.example terraform.tfvars)" }
  if ($Location -and (Normalize-Location ([string](Get-VarValue "location"))) -ne $primaryLoc) {
    Info "(checked with -Location ${primaryLoc}: to use it, set location = `"$primaryLoc`" in terraform.tfvars or variables.tf)"
  }
  $letter = [int][char]"A"
  if ($hasInPlace -or $blocked.Count -gt 0) {
    Write-Host ("  Option {0} - keep the current regions:" -f [char]$letter)
    $letter++
    Write-ChangeSet $inPlace "    "
    if ($groups.Contains("secondary") -and @($inPlace.Objects.Keys | Where-Object { $_ -in @("premium_model", "balanced_model", "economy_model") -and $inPlace.Objects[$_].Contains("sku") }).Count -gt 0) {
      Write-Host "    # premium/balanced/economy sku changes also apply to the secondary region: re-run this script after editing." -ForegroundColor DarkGray
    }
    foreach ($b in $blocked) {
      $g = $groups[$b.Name]
      Write-Host "    # still blocked in $($g.Location): $($b.Who)" -ForegroundColor Yellow
      Write-Host "    #   -> pick another model (see 'similar models' above) or change $($g.LocVar)" -ForegroundColor Yellow
    }
  }
  foreach ($name in @($moves.Keys)) {
    $mv = $moves[$name]
    $g = $groups[$name]
    Write-Host ("  Option {0} - move the {1} to {2}:" -f [char]$letter, $g.Label, $mv.Location)
    $letter++
    $cs = New-ChangeSet
    $cs.Scalars[$g.LocVar] = $mv.Location
    Add-Fixes $cs $name $mv.Results
    Write-ChangeSet $cs "    "
    if ($name -eq "primary") {
      Write-Host "    # 'location' moves the whole stack (Foundry, APIM, monitoring): terraform apply recreates it." -ForegroundColor DarkGray
      $decision = @($mv.Results | Where-Object { $_.Dep.Var -eq "decision_model" }) | Select-Object -First 1
      $decSku = if ($decision -and $decision.Fix.Contains("sku")) { $decision.Fix["sku"] } elseif ($decision) { $decision.Dep.Sku } else { "" }
      if ($decSku -eq "DataZoneStandard" -and $EuRegions -notcontains $mv.Location) {
        Write-Host "    # Decision on DataZoneStandard outside the EU: pricing.json ships the EU data-zone price - confirm pricing_key with your coach." -ForegroundColor DarkGray
      }
    }
  }
  $chatVars = @("premium_model", "balanced_model", "economy_model", "judge_model")
  if ($inPlace.Scalars.Contains("secondary_capacity") -or @($inPlace.Objects.Keys | Where-Object { $_ -in $chatVars -and $inPlace.Objects[$_].Contains("capacity") }).Count -gt 0) {
    Info "Lower chat capacity? Also lower `"concurrency`" in strategy.json to avoid 429s during the baseline run."
  }
  Info "Apply each fix either as a) terraform.tfvars override or b) in variables.tf, re-run this script until it"
  Info "reports 0 blockers, then: terraform init && terraform apply"
}
if ($script:NextSteps.Count -gt 0) {
  Write-Host ""
  Write-Host "Next steps:"
  $script:NextSteps | ForEach-Object { Write-Host "  - $_" }
}
if ($script:Blockers -eq 0) {
  Write-Host ""
  Write-Host "All configured models are available with enough quota. Next (from infra/): terraform init && terraform apply"
  exit 0
}
exit 1
