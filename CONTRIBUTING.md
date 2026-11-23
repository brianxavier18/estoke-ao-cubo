# Contributing to EstokeAoCubo

Thank you for your interest in contributing to **EstokeAoCubo**! This project aims to provide a free, offline-first ERP & POS for small businesses, shops, restaurants, and independent artisans.

## How to Contribute

1. Fork the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Set up a local virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   pip install -r requirements.txt pytest
   ```
3. Make focused changes and run the test suite before opening a Pull Request:
   ```bash
   pytest
   ```
4. Follow clear commit messages (Conventional Commits style: `feat:`, `fix:`, `docs:`, `test:`, `chore:`).

## Reporting Bugs & Ideas

Please open a GitHub Issue describing:
- Your OS and Python version
- Steps to reproduce (for bugs) or the business workflow it solves (for features)
- Relevant error logs (never paste `.env` tokens or real customer data)
