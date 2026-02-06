# Updating Dist-git Packages

Packages are automatically updated from Fedora dist-git. Each update creates a separate MR.

## Testing Locally

```bash
# Dry-run (no MRs)
./ci/dist_git_update_multi_mr.sh --clone --max=5

# Create test MRs
export CHORE_MR_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
./ci/dist_git_update_multi_mr.sh --clone --max=3 --create-mrs
```

## Environment Variables

- `CHORE_MR_GITLAB_TOKEN` - GitLab token with `write_repository` scope (required for `--create-mrs`)
- `GITLAB_REMOTE_URL` - Target repo (default: upstream)

## Flags

- `--clone` - Clone to /tmp (safe testing)
- `--max=N` - Limit to N packages
- `--create-mrs` - Actually create MRs (requires token)
