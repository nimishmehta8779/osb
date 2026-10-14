#!/usr/bin/env bash
# ==============================================================================
# Open Service Broker (OSB) Edge Platform - Deployment & Lifecycle Orchestrator
# ==============================================================================
# A self-service edge platform where services are configuration, not infrastructure.
# This script manages local development, automated testing, AWS Terraform operations,
# and team API key administration.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="${SCRIPT_DIR}/infra"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.local.yml"

# Defaults (overrideable via environment variables)
AWS_REGION="${AWS_REGION:-ap-south-1}"
OSB_NAME="${OSB_NAME:-osb-dev}"

# Colors
COLOR_RESET="\033[0m"
COLOR_RED="\033[1;31m"
COLOR_GREEN="\033[1;32m"
COLOR_YELLOW="\033[1;33m"
COLOR_BLUE="\033[1;34m"
COLOR_CYAN="\033[1;36m"
COLOR_BOLD="\033[1m"

log_info() {
    printf "${COLOR_BLUE}[INFO]${COLOR_RESET} %s\n" "$1"
}

log_success() {
    printf "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} %s\n" "$1"
}

log_warn() {
    printf "${COLOR_YELLOW}[WARN]${COLOR_RESET} %s\n" "$1"
}

log_error() {
    printf "${COLOR_RED}[ERROR]${COLOR_RESET} %s\n" "$1" >&2
}

banner() {
    printf "${COLOR_CYAN}${COLOR_BOLD}"
    cat << "EOF"
  ___  ____  ____    _____    _             ____  _       _    __                      
 / _ \/ ___|| __ )  | ____|__| | __ _  ___ |  _ \| | __ _| |_ / _| ___  _ __ _ __ ___  
| | | \___ \|  _ \  |  _| / _` |/ _` |/ _ \| |_) | |/ _` | __| |_ / _ \| '__| '_ ` _ \ 
| |_| |___) | |_) | | |__| (_| | (_| |  __/|  __/| | (_| | |_|  _| (_) | |  | | | | | |
 \___/|____/|____/  |_____\__,_|\__, |\___||_|   |_|\__,_|\__|_|  \___/|_|  |_| |_| |_|
                                |___/                                                   
EOF
    printf "${COLOR_RESET}\n"
    printf "  ${COLOR_BOLD}Self-Service Edge Load-Balancing Platform${COLOR_RESET}\n"
    printf "  Services as Configuration, Not Infrastructure\n\n"
}

check_bin() {
    if ! command -v "$1" &> /dev/null; then
        log_error "Required tool '$1' is not installed or not in PATH."
        return 1
    fi
}

check_prereqs() {
    log_info "Verifying prerequisites..."
    local missing=0
    check_bin python3 || missing=1
    check_bin docker || missing=1
    check_bin terraform || missing=1
    check_bin aws || missing=1

    if [[ "$missing" -eq 1 ]]; then
        log_error "Prerequisites check failed. Please install missing tools."
        exit 1
    fi
    log_success "All CLI prerequisites verified (python3, docker, terraform, aws)."
}

verify_aws_auth() {
    log_info "Verifying AWS credentials in region: ${AWS_REGION}..."
    if ! identity_json=$(aws sts get-caller-identity --output json 2>/dev/null); then
        log_error "AWS credentials are invalid or expired. Run 'aws configure' or check your AWS_PROFILE."
        exit 1
    fi

    local arn
    arn=$(echo "$identity_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('Arn', 'unknown'))")
    log_success "Authenticated to AWS as: ${arn}"

    # Warn if deploying with root account
    if [[ "$arn" == *":root" ]]; then
        log_warn "SECURITY ALERT: You are using the AWS Account ROOT identity!"
        log_warn "It is strongly recommended to use a scoped deployment IAM user or role."
    fi
}

run_tests() {
    banner
    log_info "Running test suites..."
    
    # 1. LKG Agent unit tests
    log_info "--> Running LKG Agent test suite (envoy/lkg-agent)..."
    (
        cd "${SCRIPT_DIR}/envoy/lkg-agent"
        python3 -m unittest test_agent
    )
    log_success "LKG Agent test suite passed (20 tests)."

    # 2. Check if moto/fastapi are installed for services tests
    if python3 -c "import moto, fastapi, boto3" 2>/dev/null; then
        log_info "--> Running Services test suite (broker, worker, common, sovereign)..."
        (
            cd "${SCRIPT_DIR}"
            PYTHONPATH=. python3 -m unittest \
                services.common.tests.test_store \
                services.broker.tests.test_api \
                services.worker.tests.test_saga \
                services.worker.tests.test_worker \
                services.sovereign.tests.test_templates
        )
        log_success "Services test suite passed (52 tests)."
    else
        log_warn "Python test dependencies ('moto', 'fastapi') not found in local environment."
        log_warn "Skipping service unit tests. Run inside a virtual environment with 'pip install -r services/broker/requirements.txt moto' or run CI."
    fi
}

local_up() {
    banner
    check_bin docker
    log_info "Starting full OSB local stack via Docker Compose..."
    docker compose -f "${COMPOSE_FILE}" up --build -d
    log_success "Stack started successfully!"
    printf "\n"
    log_info "Local Services Overview:"
    printf "  - Envoy Edge Ingress : http://localhost:10000 (HTTP) | https://localhost:10443 (TLS)\n"
    printf "  - Envoy Admin UI     : http://localhost:9901\n"
    printf "  - Service Broker API : http://localhost:8000\n"
    printf "  - Sovereign xDS Server: http://localhost:8080\n"
    printf "  - Pre-seeded API Key : 'local-dev-only' (Team: 'local-dev-team')\n\n"
    printf "Try onboarding a service locally:\n"
    printf "  curl -X PUT http://localhost:8000/v2/service_instances/demo-1 \\\n"
    printf "    -H 'X-Api-Key: local-dev-only' \\\n"
    printf "    -H 'Content-Type: application/json' \\\n"
    printf "    -d '{\"service_id\":\"web\",\"parameters\":{\"hostname\":\"app1.osb.internal\",\"backend_host\":\"backend-demo\",\"backend_port\":80}}'\n\n"
}

local_down() {
    banner
    check_bin docker
    log_info "Tearing down OSB local stack..."
    docker compose -f "${COMPOSE_FILE}" down -v
    log_success "Local containers and test volumes removed."
}

tf_init() {
    banner
    check_prereqs
    verify_aws_auth
    log_info "Initializing Terraform in ${INFRA_DIR}..."
    (
        cd "${INFRA_DIR}"
        terraform init
    )
    log_success "Terraform initialized."
}

tf_plan() {
    banner
    check_prereqs
    verify_aws_auth
    log_info "Running Terraform plan for ${OSB_NAME} in ${AWS_REGION}..."
    (
        cd "${INFRA_DIR}"
        terraform plan \
            -var="region=${AWS_REGION}" \
            -var="name=${OSB_NAME}"
    )
}

tf_apply() {
    banner
    check_prereqs
    verify_aws_auth
    log_info "Preparing to apply Terraform for ${OSB_NAME} in ${AWS_REGION}..."
    printf "${COLOR_YELLOW}This will provision live AWS resources (NLB, EC2, DynamoDB, SQS, CloudWatch).${COLOR_RESET}\n"
    read -r -p "Are you sure you want to proceed? [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY])
            log_info "Applying infrastructure changes..."
            (
                cd "${INFRA_DIR}"
                terraform apply \
                    -var="region=${AWS_REGION}" \
                    -var="name=${OSB_NAME}" \
                    -auto-approve
            )
            log_success "Terraform apply completed successfully!"
            ;;
        *)
            log_warn "Apply canceled by user."
            exit 0
            ;;
    esac
}

tf_destroy() {
    banner
    check_prereqs
    verify_aws_auth
    log_warn "WARNING: This will DESTROY all deployed AWS infrastructure for ${OSB_NAME} in ${AWS_REGION}!"
    read -r -p "Type 'destroy' to confirm destruction: " response
    if [[ "$response" == "destroy" ]]; then
        (
            cd "${INFRA_DIR}"
            terraform destroy \
                -var="region=${AWS_REGION}" \
                -var="name=${OSB_NAME}" \
                -auto-approve
        )
        log_success "All resources destroyed."
    else
        log_info "Destruction canceled."
    fi
}

seed_api_key() {
    banner
    verify_aws_auth
    local team_id="${1:-}"
    local raw_key="${2:-}"

    if [[ -z "$team_id" || -z "$raw_key" ]]; then
        log_error "Usage: ./deploy.sh seed-key <team_id> <raw_api_key>"
        exit 1
    fi

    log_info "Hashing API key and registering team '${team_id}' in DynamoDB..."
    local hashed_key
    hashed_key=$(python3 -c "import hashlib; print(hashlib.sha256('${raw_key}'.encode('utf-8')).hexdigest())")
    local table_name="${OSB_NAME}-api-keys"

    aws dynamodb put-item \
        --region "${AWS_REGION}" \
        --table-name "${table_name}" \
        --item "{\"key_hash\": {\"S\": \"${hashed_key}\"}, \"team_id\": {\"S\": \"${team_id}\"}}"

    log_success "Key seeded successfully in table '${table_name}'!"
    printf "  Team ID   : %s\n" "${team_id}"
    printf "  Key Hash  : %s\n" "${hashed_key}"
    printf "  Auth Header: X-Api-Key: %s\n\n" "${raw_key}"
}

print_status() {
    banner
    verify_aws_auth
    log_info "Fetching AWS resource status for '${OSB_NAME}' in '${AWS_REGION}'..."
    printf "\n${COLOR_BOLD}=== EC2 Instances ===${COLOR_RESET}\n"
    aws ec2 describe-instances \
        --region "${AWS_REGION}" \
        --filters "Name=tag:project,Values=osb" "Name=tag:environment,Values=dev" "Name=instance-state-name,Values=running,pending,stopped" \
        --query 'Reservations[*].Instances[*].[Tags[?Key==`Name`].Value | [0], InstanceId, State.Name, PrivateIpAddress]' \
        --output table || true

    printf "\n${COLOR_BOLD}=== NLB Ingress ===${COLOR_RESET}\n"
    aws elbv2 describe-load-balancers \
        --region "${AWS_REGION}" \
        --names "${OSB_NAME}-nlb" \
        --query 'LoadBalancers[*].[LoadBalancerName, DNSName, State.Code]' \
        --output table 2>/dev/null || log_info "No active NLB found."

    printf "\n${COLOR_BOLD}=== SQS Queues ===${COLOR_RESET}\n"
    aws sqs list-queues \
        --region "${AWS_REGION}" \
        --queue-name-prefix "${OSB_NAME}" \
        --output table 2>/dev/null || log_info "No queues found."
}

show_help() {
    banner
    cat << EOF
Usage: ./deploy.sh <command> [arguments]

Commands:
  local-up             Launch complete local stack with Docker Compose (Envoy, Sovereign, Broker, Worker)
  local-down           Stop and clean up local Docker Compose stack
  test                 Run local unit tests (LKG agent and Services)
  init                 Initialize Terraform backend and providers (infra/)
  plan                 Run Terraform plan against AWS
  apply                Deploy the complete platform to AWS
  destroy              Tear down and destroy all AWS resources
  seed-key <team> <key> Register a new team API key in DynamoDB
  status               Check the live status of deployed AWS or local containers
  check                Verify local CLI tool dependencies (python, docker, terraform, aws)
  help                 Display this help manual

Environment Overrides:
  AWS_REGION           AWS Region for deployment (default: ap-south-1)
  OSB_NAME             Resource prefix name (default: osb-dev)

Examples:
  ./deploy.sh local-up
  ./deploy.sh test
  ./deploy.sh plan
  ./deploy.sh apply
  ./deploy.sh seed-key finance-team my-secret-api-key-123
  ./deploy.sh status
  ./deploy.sh destroy

EOF
}

# Main Command Dispatcher
COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
    local-up)
        local_up
        ;;
    local-down)
        local_down
        ;;
    test)
        run_tests
        ;;
    init)
        tf_init
        ;;
    plan)
        tf_plan
        ;;
    apply)
        tf_apply
        ;;
    destroy)
        tf_destroy
        ;;
    seed-key)
        seed_api_key "$@"
        ;;
    status)
        print_status
        ;;
    check)
        check_prereqs
        verify_aws_auth
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: ${COMMAND}"
        show_help
        exit 1
        ;;
esac
