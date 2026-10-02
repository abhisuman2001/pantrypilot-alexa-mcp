#!/usr/bin/env pwsh
# deploy.ps1  –  Build, push to Amazon ECR, and deploy to AWS App Runner
# Usage: .\deploy.ps1
# Prerequisites: Docker Desktop running, AWS CLI configured, IAM permissions for
#   ECR (push), AppRunner (create/update service), and IAM (pass role).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Config (edit these if needed) ──────────────────────────────────────────
$REGION       = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$ACCOUNT      = (aws sts get-caller-identity --query Account --output text)
$REPO_NAME    = "pantrypilot"
$IMAGE_TAG    = "latest"
$SERVICE_NAME = "pantrypilot-mcp"
$PORT         = 8000

$ECR_REGISTRY = "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
$IMAGE_URI    = "${ECR_REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"

Write-Host "`n=== PantryPilot Deploy ===" -ForegroundColor Cyan
Write-Host "Account : $ACCOUNT"
Write-Host "Region  : $REGION"
Write-Host "Image   : $IMAGE_URI"

# ── Step 1: Create ECR repo (safe if it already exists) ────────────────────
Write-Host "`n[1/5] Ensuring ECR repository exists..." -ForegroundColor Yellow
aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION 2>$null
if ($LASTEXITCODE -ne 0) {
    aws ecr create-repository --repository-name $REPO_NAME --region $REGION | Out-Null
    Write-Host "  Created repository: $REPO_NAME"
} else {
    Write-Host "  Repository already exists."
}

# ── Step 2: Docker login to ECR ────────────────────────────────────────────
Write-Host "`n[2/5] Logging in to ECR..." -ForegroundColor Yellow
aws ecr get-login-password --region $REGION |
    docker login --username AWS --password-stdin $ECR_REGISTRY

# ── Step 3: Build image ────────────────────────────────────────────────────
Write-Host "`n[3/5] Building Docker image..." -ForegroundColor Yellow
docker build -t "${REPO_NAME}:${IMAGE_TAG}" .
docker tag "${REPO_NAME}:${IMAGE_TAG}" $IMAGE_URI

# ── Step 4: Push image ─────────────────────────────────────────────────────
Write-Host "`n[4/5] Pushing image to ECR..." -ForegroundColor Yellow
docker push $IMAGE_URI

# ── Step 5: Create or update App Runner service ────────────────────────────
Write-Host "`n[5/5] Deploying to AWS App Runner..." -ForegroundColor Yellow

# Check if service already exists
$existing = aws apprunner list-services --region $REGION --query "ServiceSummaryList[?ServiceName=='$SERVICE_NAME'].ServiceArn" --output text 2>$null

# Build source-configuration as a PS object then serialize — avoids all
# inline JSON escaping issues in Windows PowerShell 5.1.
$srcCfg = @{
    ImageRepository = @{
        ImageIdentifier     = $IMAGE_URI
        ImageRepositoryType = "ECR"
        ImageConfiguration  = @{
            Port                        = "$PORT"
            RuntimeEnvironmentVariables = @{
                PORT       = "$PORT"
                AWS_REGION = $REGION
                PANTRY_DB  = "/data/pantry.db"
            }
        }
    }
    AutoDeploymentsEnabled = $false
}

$tmpSrc = [System.IO.Path]::GetTempFileName() + ".json"

if ($existing -and $existing -ne "None") {
    Write-Host "  Updating existing service: $SERVICE_NAME"

    @{ ServiceArn = $existing; SourceConfiguration = $srcCfg } |
        ConvertTo-Json -Depth 10 -Compress |
        Set-Content -Encoding utf8 $tmpSrc

    aws apprunner update-service `
        --cli-input-json "file://$tmpSrc" `
        --region $REGION | Out-Null

    Write-Host "`n=== Update triggered! ===" -ForegroundColor Green
    Write-Host "Service will be live in ~2 min at https://kykh233phz.us-east-1.awsapprunner.com"
    Write-Host "MCP endpoint: https://kykh233phz.us-east-1.awsapprunner.com/mcp"
} else {
    Write-Host "  Creating new App Runner service: $SERVICE_NAME"

    # Create ECR access role for App Runner (idempotent)
    $ROLE_NAME = "AppRunnerECRAccessRole"
    $ROLE_ARN  = ""
    try {
        $ROLE_ARN = (aws iam get-role --role-name $ROLE_NAME --query Role.Arn --output text 2>$null)
    } catch {}

    if (-not $ROLE_ARN) {
        $TRUST = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
        $ROLE_ARN = (aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document $TRUST --query Role.Arn --output text)
        aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess" | Out-Null
        Start-Sleep -Seconds 10   # IAM propagation
        Write-Host "  Created IAM role: $ROLE_ARN"
    }

    $srcCfg.AuthenticationConfiguration = @{ AccessRoleArn = $ROLE_ARN }

    @{
        ServiceName           = $SERVICE_NAME
        SourceConfiguration   = $srcCfg
        InstanceConfiguration = @{ Cpu = "0.25 vCPU"; Memory = "0.5 GB" }
    } | ConvertTo-Json -Depth 10 -Compress | Set-Content -Encoding utf8 $tmpSrc

    $svc = aws apprunner create-service `
        --cli-input-json "file://$tmpSrc" `
        --region $REGION | ConvertFrom-Json

    $SERVICE_URL = $svc.Service.ServiceUrl
    Write-Host "`n=== Deploy triggered! ===" -ForegroundColor Green
    Write-Host "Service URL (available in ~2 min): https://$SERVICE_URL"
    Write-Host "MCP endpoint: https://$SERVICE_URL/mcp"
    Write-Host "`nIMPORTANT: Add your AWS credentials and BEDROCK_MODEL_ID as"
    Write-Host "App Runner environment variables in the AWS console:"
    Write-Host "  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BEDROCK_MODEL_ID, MCP_API_KEY"
}

Remove-Item -ErrorAction SilentlyContinue $tmpSrc
Write-Host "`nDone. Check status at: https://$REGION.console.aws.amazon.com/apprunner" -ForegroundColor Cyan
