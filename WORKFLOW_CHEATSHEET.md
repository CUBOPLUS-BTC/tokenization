# Personal Workflow Guide: Sync & Branching

This guide summarizes the commands used to keep your fork updated and manage branches for new issues.

## 1. Syncing your fork with the official repository

Run these commands whenever you want to bring the latest changes from the main project to your local environment and your GitHub fork (`origin`).

```powershell
# Fetch latest changes from the main repo
git fetch upstream

# Update your local main
git checkout main
git pull upstream main

# Sync your GitHub fork's main
git push origin main
```

## 2. Starting a new Issue

Always start new work from the updated `main` branch to keep a clean history.

```powershell
# Ensure you are on updated main
git checkout main

# Create a new branch for the issue
git checkout -b feat/your-issue-name
```

## 3. Updating an active feature branch

If `main` has new changes and you want them in your current working branch:

```powershell
# Go to your branch
git checkout feat/your-active-branch

# Rebase your changes on top of the new main
git rebase main

# If you already pushed this branch to your fork, update it
git push origin feat/your-active-branch --force-with-lease
```

## 4. Useful verification commands

```powershell
# See your remotes
git remote -v

# Check current branch and status
git status

# See branch history in a compact way
git log --oneline -n 10
```
