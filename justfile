# Run with `just <recipe>`. Install just: https://github.com/casey/just
#
# First time setup:
#   just setup
# Then in two separate terminals:
#   just backend
#   just frontend

set shell := ["bash", "-cu"]

# Create the conda env from environment.yml
setup-backend:
    cd backend && conda env create -f environment.yml

# Install frontend deps
setup-frontend:
    cd frontend && npm install

# One-shot full setup
setup: setup-backend setup-frontend
    @echo "Setup done. Run 'just backend' and 'just frontend' in separate terminals."

# Run FastAPI dev server
backend:
    cd backend && conda run -n landplan uvicorn app.main:app --reload --host 127.0.0.1 --port 8765

# Run Vite dev server
frontend:
    cd frontend && npm run dev
