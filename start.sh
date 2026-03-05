#!/bin/bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}"
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║         Binayah Properties - News Automation             ║"
    echo "║                 Startup Script                            ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed!"
        echo ""
        echo "Install Docker:"
        echo "  curl -fsSL https://get.docker.com -o get-docker.sh"
        echo "  sudo sh get-docker.sh"
        echo "  sudo usermod -aG docker \$USER"
        echo "  # Log out and back in"
        exit 1
    fi
    print_success "Docker is installed"
}

check_docker_compose() {
    if ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not installed!"
        echo ""
        echo "Install Docker Compose:"
        echo "  sudo apt-get update"
        echo "  sudo apt-get install docker-compose-plugin"
        exit 1
    fi
    print_success "Docker Compose is installed"
}

check_env_file() {
    if [ ! -f .env ]; then
        print_error ".env file not found!"
        echo ""
        echo "Create .env file:"
        echo "  cp .env.example .env"
        echo "  nano .env  # Fill in your credentials"
        echo ""
        print_warning "Required credentials:"
        echo "  - MONGODB_URI"
        echo "  - OPENAI_API_KEY"
        echo "  - STABILITY_API_KEY"
        exit 1
    fi
    print_success ".env file exists"
}


check_env_vars() {
    set -a
    source .env
    set +a
    
    local missing_vars=()
    
    if [ -z "$MONGODB_URI" ]; then
        missing_vars+=("MONGODB_URI")
    fi
    
    if [ -z "$OPENAI_API_KEY" ]; then
        missing_vars+=("OPENAI_API_KEY")
    fi
    
    if [ -z "$STABILITY_API_KEY" ]; then
        missing_vars+=("STABILITY_API_KEY")
    fi
    
    if [ ${#missing_vars[@]} -ne 0 ]; then
        print_error "Missing required environment variables:"
        for var in "${missing_vars[@]}"; do
            echo "  - $var"
        done
        echo ""
        echo "Edit .env file and add these values"
        exit 1
    fi
    
    print_success "All required environment variables are set"
}

preflight_checks() {
    print_info "Running pre-flight checks..."
    echo ""
    check_docker
    check_docker_compose
    check_env_file
    check_env_vars
    echo ""
    print_success "All checks passed!"
    echo ""
}

start_services() {
    print_info "Starting services..."
    echo ""
    
    docker compose up -d --build
    
    echo ""
    print_success "Services started!"
    echo ""
    
    print_info "Waiting for services to be ready..."
    sleep 5
    
    docker compose ps
    
    echo ""
    print_success "All services are running!"
    echo ""
    
    print_info "Access the application:"
    echo ""
    echo "  🌐 Frontend:     http://localhost:5173"
    echo "  🔧 API:          http://localhost:8000"
    echo "  📚 API Docs:     http://localhost:8000/docs"
    echo "  🔍 Redis:        redis://localhost:6379"
    echo ""
    
    print_info "Useful commands:"
    echo ""
    echo "  ./start.sh logs              # View logs"
    echo "  ./start.sh stop              # Stop services"
    echo "  ./start.sh restart           # Restart services"
    echo "  docker compose logs -f api   # Follow API logs"
    echo ""
}

show_logs() {
    print_info "Showing logs (Ctrl+C to exit)..."
    echo ""
    docker compose logs -f
}

stop_services() {
    print_info "Stopping services..."
    echo ""
    docker compose down
    echo ""
    print_success "Services stopped!"
}

restart_services() {
    print_info "Restarting services..."
    echo ""
    docker compose restart
    echo ""
    print_success "Services restarted!"
}

# Clean everything (remove containers and volumes)
clean_all() {
    print_warning "This will remove all containers, volumes, and data!"
    echo ""
    read -p "Are you sure? (yes/no): " confirmation
    
    if [ "$confirmation" != "yes" ]; then
        print_info "Cancelled"
        exit 0
    fi
    
    print_info "Cleaning up..."
    echo ""
    
    # Stop and remove containers, networks, volumes
    docker compose down -v
    
    # Remove images
    docker compose down --rmi all
    
    # Prune system
    docker system prune -f
    
    echo ""
    print_success "Cleanup complete!"
}


print_header

# Parse command
case "${1:-start}" in
    start)
        preflight_checks
        start_services
        ;;
    
    logs)
        show_logs
        ;;
    
    stop)
        stop_services
        ;;
    
    restart)
        restart_services
        ;;
    
    clean)
        clean_all
        ;;
    
    *)
        print_error "Unknown command: $1"
        echo ""
        echo "Usage:"
        echo "  ./start.sh          - Start all services"
        echo "  ./start.sh logs     - Show logs"
        echo "  ./start.sh stop     - Stop all services"
        echo "  ./start.sh restart  - Restart all services"
        echo "  ./start.sh clean    - Remove all containers and volumes"
        exit 1
        ;;
esac
