#Requires -Version 7.0
<#
.SYNOPSIS
  Own the Weights – serverless SFT on Microsoft Foundry (coach subscription only, pay-per-token, NO PTU).

.DESCRIPTION
  ./finetune.ps1 preflight      # az login, account/region, data files, base-model id candidates
  ./finetune.ps1 upload         # POST {endpoint}/files (purpose=fine-tune) for train.jsonl + validation.jsonl
  ./finetune.ps1 create         # POST {endpoint}/fine_tuning/jobs (trainingType=GlobalStandard)
  ./finetune.ps1 wait           # poll the job until succeeded/failed
  ./finetune.ps1 deploy         # ARM PUT .../accounts/<acct>/deployments/<name> (sku GlobalStandard)
  ./finetune.ps1 test           # one chat completion through required APIM
  ./finetune.ps1 status         # show state + job + deployment
  ./finetune.ps1 delete         # DELETE the deployment -> stops the hourly hosting fee
  ./finetune.ps1 cleanup-files  # optional: delete the uploaded files
  ./finetune.ps1 all            # preflight, upload, create, wait, deploy, test

  Same environment variables and state file (data/.finetune-state) as finetune.sh:
  Required: AZ_SUBSCRIPTION_ID, AZ_RESOURCE_GROUP, FOUNDRY_ACCOUNT.
  Optional: FOUNDRY_ENDPOINT, FOUNDRY_API_KEY, BASE_MODEL (Ministral-3B, VALIDATE id), TRAINING_TYPE (GlobalStandard),
            SUFFIX, SEED, N_EPOCHS, DEPLOYMENT_NAME (bytecart-ft), DEPLOY_SKU (GlobalStandard), DEPLOY_CAPACITY (50, VALIDATE),
            FT_MODEL_FORMAT (auto -> az list-models, fallback OpenAI; VALIDATE), FT_MODEL_VERSION (1), FT_MODEL,
            ARM_API_VERSION (2024-10-01), DATA_DIR, POLL_SECONDS (60).
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('preflight', 'upload', 'create', 'wait', 'deploy', 'test', 'status', 'delete', 'cleanup-files', 'all', 'help')]
    [string]$Command = 'help'
)
$ErrorActionPreference = 'Stop'

function Get-EnvOr([string]$Name, [string]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrEmpty($v)) { return $Default } else { return $v }
}

$DataDir = Get-EnvOr 'DATA_DIR' (Join-Path $PSScriptRoot 'data')
$StateFile = Join-Path $DataDir '.finetune-state'
$BaseModel = Get-EnvOr 'BASE_MODEL' 'Ministral-3B'
$TrainingType = Get-EnvOr 'TRAINING_TYPE' 'GlobalStandard'
$Suffix = Get-EnvOr 'SUFFIX' 'bytecart'
$Seed = [int](Get-EnvOr 'SEED' '42')
$NEpochs = Get-EnvOr 'N_EPOCHS' ''
$DeploymentName = Get-EnvOr 'DEPLOYMENT_NAME' 'bytecart-ft'
$DeploySku = Get-EnvOr 'DEPLOY_SKU' 'GlobalStandard'
$DeployCapacity = [int](Get-EnvOr 'DEPLOY_CAPACITY' '50')
$FtModelFormat = Get-EnvOr 'FT_MODEL_FORMAT' 'auto'
$FtModelVersion = Get-EnvOr 'FT_MODEL_VERSION' '1'
$ArmApiVersion = Get-EnvOr 'ARM_API_VERSION' '2024-10-01'
$PollSeconds = [int](Get-EnvOr 'POLL_SECONDS' '60')

function Fail([string]$Message) { Write-Host "❌ $Message" -ForegroundColor Red; exit 1 }
function Info([string]$Message) { Write-Host "▶ $Message" }

function Initialize-Context {
    foreach ($n in 'AZ_SUBSCRIPTION_ID', 'AZ_RESOURCE_GROUP', 'FOUNDRY_ACCOUNT') {
        if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($n))) { Fail "set $n" }
    }
    if ($BaseModel -notmatch '^[A-Za-z0-9][A-Za-z0-9._:/-]*$') {
        Fail "invalid BASE_MODEL '$BaseModel' – use the exact model id from preflight (remove commas, spaces and quotes)"
    }
    $script:Sub = $env:AZ_SUBSCRIPTION_ID; $script:Rg = $env:AZ_RESOURCE_GROUP; $script:Account = $env:FOUNDRY_ACCOUNT
    # VALIDATE: new-Foundry domain; the classic https://<acct>.openai.azure.com/openai/v1 works for the same account.
    $script:Endpoint = (Get-EnvOr 'FOUNDRY_ENDPOINT' "https://$($script:Account).services.ai.azure.com/openai/v1").TrimEnd('/')
    $script:ArmDeploymentUrl = "https://management.azure.com/subscriptions/$($script:Sub)/resourceGroups/$($script:Rg)" +
        "/providers/Microsoft.CognitiveServices/accounts/$($script:Account)/deployments/$($DeploymentName)?api-version=$ArmApiVersion"
    if (-not $script:Headers) {
        if ($env:FOUNDRY_API_KEY) {
            $script:Headers = @{ 'api-key' = $env:FOUNDRY_API_KEY }
        } else {
            $token = az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv
            if ($LASTEXITCODE -ne 0 -or -not $token) { Fail 'could not get an Entra ID token (az login?) – or set FOUNDRY_API_KEY' }
            $script:Headers = @{ Authorization = "Bearer $token" }
        }
    }
}

function Get-State([string]$Key) {
    if (-not (Test-Path $StateFile)) { return '' }
    $line = Get-Content $StateFile | Where-Object { $_ -like "$Key=*" } | Select-Object -Last 1
    if ($line) { return $line.Substring($Key.Length + 1) } else { return '' }
}

function Set-State([string]$Key, [string]$Value) {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $lines = @()
    if (Test-Path $StateFile) { $lines = @(Get-Content $StateFile | Where-Object { $_ -notlike "$Key=*" }) }
    $lines += "$Key=$Value"
    Set-Content -Path $StateFile -Value $lines -Encoding utf8NoBOM
}

function Invoke-Api([string]$Method, [string]$Path, $Body = $null, $Form = $null) {
    $params = @{ Method = $Method; Uri = "$($script:Endpoint)$Path"; Headers = $script:Headers }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 20 -Compress); $params.ContentType = 'application/json' }
    if ($null -ne $Form) { $params.Form = $Form }
    return Invoke-RestMethod @params
}

function Invoke-Preflight {
    Initialize-Context
    foreach ($tool in 'az') { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool not found" } }
    az account set --subscription $script:Sub | Out-Null
    $acct = az cognitiveservices account show -g $script:Rg -n $script:Account --query '[kind, location, properties.customSubDomainName]' -o tsv
    Info "Account: $($acct -join ' ')"
    Write-Host '   (Global training needs a supported project region, e.g. swedencentral – see the overview''s region table.)'
    foreach ($f in 'train.jsonl', 'validation.jsonl') {
        $p = Join-Path $DataDir $f
        if (-not (Test-Path $p)) { Fail "$p missing – run: python generate_dataset.py --out `"$DataDir`"" }
        Info "$($f): $((Get-Content $p | Measure-Object -Line).Lines) examples"
    }
    Info "Endpoint: $($script:Endpoint)   auth: $(if ($env:FOUNDRY_API_KEY) { 'api-key' } else { 'Entra ID token' })"
    Info 'Fine-tunable base models matching ministral|gpt-oss|qwen3|llama-3.3 (VALIDATE: capability field names):'
    $models = (Invoke-Api GET '/models').data | Where-Object { $_.id -match '(?i)ministral|gpt-oss|qwen3|llama-3\.3' }
    if (-not $models) { Write-Host '   (none listed – check the model id in the Foundry portal: Fine-tuning > + Fine-tune model)' }
    foreach ($m in $models) { Write-Host "   $($m.id)   fine_tune=$($m.capabilities.fine_tune)" }
    Write-Host "   BASE_MODEL=$BaseModel  TRAINING_TYPE=$TrainingType  DEPLOY_SKU=$DeploySku (pay-per-token + hourly hosting, no PTU)"
}

function Invoke-Upload {
    Initialize-Context
    foreach ($kind in 'train', 'validation') {
        $p = Join-Path $DataDir "$kind.jsonl"
        if (-not (Test-Path $p)) { Fail "$p missing" }
        Info "Uploading $p"
        $resp = Invoke-Api POST '/files' -Form @{ purpose = 'fine-tune'; file = Get-Item $p }
        Set-State "$($kind)_file_id" $resp.id
        $status = $resp.status
        for ($i = 0; $i -lt 60; $i++) {   # VALIDATE status values
            $status = (Invoke-Api GET "/files/$($resp.id)").status
            if (-not $status -or $status -eq 'processed') { break }
            if ($status -eq 'error') { Fail "file $($resp.id) failed validation" }
            Start-Sleep -Seconds 5
        }
        Write-Host "   $kind file id: $($resp.id) (status: $status)"
    }
}

function Invoke-Create {
    Initialize-Context
    $train = Get-State 'train_file_id'; $val = Get-State 'validation_file_id'
    if (-not $train) { Fail 'no training file id – run upload first' }
    $body = [ordered]@{ model = $BaseModel; training_file = $train; suffix = $Suffix; seed = $Seed; trainingType = $TrainingType }
    if ($val) { $body.validation_file = $val }
    if ($NEpochs) { $body.method = @{ type = 'supervised'; supervised = @{ hyperparameters = @{ n_epochs = [int]$NEpochs } } } }
    Info "Creating fine-tuning job: $($body | ConvertTo-Json -Depth 10 -Compress)"
    $resp = Invoke-Api POST '/fine_tuning/jobs' -Body $body
    Set-State 'job_id' $resp.id
    Write-Host "   job id: $($resp.id)  status: $($resp.status)"
}

function Invoke-Wait {
    Initialize-Context
    $job = Get-State 'job_id'
    if (-not $job) { Fail 'no job id – run create first' }
    while ($true) {
        $resp = Invoke-Api GET "/fine_tuning/jobs/$job"
        $lastEvent = (Invoke-Api GET "/fine_tuning/jobs/$job/events?limit=1").data | Select-Object -First 1
        Write-Host "$((Get-Date).ToUniversalTime().ToString('HH:mm:ssZ')) status=$($resp.status)  $($lastEvent.message)"
        switch ($resp.status) {
            'succeeded' {
                Set-State 'fine_tuned_model' $resp.fine_tuned_model
                Write-Host "✅ fine-tuned model: $($resp.fine_tuned_model)   trained_tokens: $($resp.trained_tokens)"
                return
            }
            { $_ -in 'failed', 'cancelled' } { $resp | ConvertTo-Json -Depth 10 | Write-Host; Fail "job $($resp.status)" }
        }
        Start-Sleep -Seconds $PollSeconds
    }
}

function Invoke-Deploy {
    Initialize-Context
    $model = Get-EnvOr 'FT_MODEL' (Get-State 'fine_tuned_model')
    if (-not $model) { Fail 'no fine-tuned model – run wait first (or set FT_MODEL)' }
    $format = $FtModelFormat
    if ($format -eq 'auto') {
        # VALIDATE: assumes fine-tuned models are listed by the account's models API with their deployment format.
        $format = az cognitiveservices account list-models -g $script:Rg -n $script:Account --query "[?name=='$model'].format | [0]" -o tsv 2>$null
        if (-not $format) { $format = 'OpenAI' }
    }
    $body = @{ sku = @{ name = $DeploySku; capacity = $DeployCapacity }; properties = @{ model = @{ format = $format; name = $model; version = $FtModelVersion } } }
    $bodyFile = Join-Path $DataDir '.deploy-body.json'
    $body | ConvertTo-Json -Depth 10 | Set-Content -Path $bodyFile -Encoding utf8NoBOM
    Info "PUT deployment $($DeploymentName): $(Get-Content $bodyFile -Raw)"
    Write-Host "   ⏱️  From now on the deployment bills an HOURLY hosting fee until you run: ./finetune.ps1 delete"
    az rest --method put --url $script:ArmDeploymentUrl --body "@$bodyFile" --headers 'Content-Type=application/json' | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'ARM PUT failed' }
    Remove-Item $bodyFile -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 120; $i++) {
        $state = az cognitiveservices account deployment show -g $script:Rg -n $script:Account --deployment-name $DeploymentName --query properties.provisioningState -o tsv 2>$null
        Write-Host "$((Get-Date).ToUniversalTime().ToString('HH:mm:ssZ')) provisioningState=$state"
        if ($state -eq 'Succeeded') { break }
        if ($state -eq 'Failed') { Fail 'deployment failed – check the portal (Models page) and FT_MODEL_FORMAT/DEPLOY_CAPACITY' }
        Start-Sleep -Seconds 30
    }
    Set-State 'deployment_name' $DeploymentName
    Write-Host "✅ Deployed. Next: python register_custom_model.py --deployment $DeploymentName --resource $($script:Account) --write-pricing"
}

function Invoke-Test {
    $python = Get-EnvOr 'PYTHON' 'python'
    $gatewayJson = & $python -c 'import json,sys; sys.path.insert(0,sys.argv[1]); from kit_common import find_root,read_json,gateway_credentials; root=find_root(); print(json.dumps(gateway_credentials(root,read_json(root/"shared"/"config"/"models.json"))))' $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { Fail 'required gateway configuration could not be loaded' }
    $gateway = $gatewayJson | ConvertFrom-Json
    $first = Get-Content (Join-Path $DataDir 'validation.jsonl') -TotalCount 1 -Encoding utf8 | ConvertFrom-Json
    $msgs = @($first.messages | Where-Object { $_.role -ne 'assistant' } | ForEach-Object { @{ role = $_.role; content = $_.content } })
    $body = @{ model = $DeploymentName; messages = $msgs; max_tokens = 300; temperature = 0.2 } | ConvertTo-Json -Depth 20 -Compress
    $resp = Invoke-RestMethod -Method POST -Uri "$($gateway[0])chat/completions" -Headers @{ 'api-key' = $gateway[1] } -ContentType 'application/json' -Body $body
    Write-Host "Question: $(($msgs[-1].content -split 'Question: ')[-1])"
    Write-Host "Answer:   $($resp.choices[0].message.content)"
    Write-Host "Usage:    $($resp.usage | ConvertTo-Json -Compress)"
}

function Invoke-Status {
    Initialize-Context
    if (Test-Path $StateFile) { Get-Content $StateFile } else { Write-Host "(no state file at $StateFile)" }
    $job = Get-State 'job_id'
    if ($job) { Write-Host "job: $((Invoke-Api GET "/fine_tuning/jobs/$job").status)" }
    az cognitiveservices account deployment show -g $script:Rg -n $script:Account --deployment-name $DeploymentName `
        --query '{name:name, state:properties.provisioningState, model:properties.model.name, sku:sku.name}' -o table 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "deployment $($DeploymentName): not found (no hosting fee)" }
}

function Invoke-Delete {
    Initialize-Context
    Info "Deleting deployment $DeploymentName (stops the hourly hosting fee; the fine-tuned model itself is kept)"
    az cognitiveservices account deployment delete -g $script:Rg -n $script:Account --deployment-name $DeploymentName
    if ($LASTEXITCODE -ne 0) { Fail 'delete failed' }
    Write-Host '✅ Deleted. Also run: python register_custom_model.py --remove --write-pricing'
}

function Invoke-CleanupFiles {
    Initialize-Context
    foreach ($k in 'train_file_id', 'validation_file_id') {
        $id = Get-State $k
        if ($id) { Invoke-Api DELETE "/files/$id" | Out-Null; Write-Host "deleted $id" }
    }
}

switch ($Command) {
    'preflight' { Invoke-Preflight }
    'upload' { Invoke-Upload }
    'create' { Invoke-Create }
    'wait' { Invoke-Wait }
    'deploy' { Invoke-Deploy }
    'test' { Invoke-Test }
    'status' { Invoke-Status }
    'delete' { Invoke-Delete }
    'cleanup-files' { Invoke-CleanupFiles }
    'all' { Invoke-Preflight; Invoke-Upload; Invoke-Create; Invoke-Wait; Invoke-Deploy; Invoke-Test }
    default { Get-Help $PSCommandPath -Detailed; exit 1 }
}
