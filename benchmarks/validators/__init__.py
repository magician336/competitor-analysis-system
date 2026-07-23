"""Safe, shell-free benchmark validator runner package."""

# Keep package import side-effect free so ``python -m ...run_task`` does not
# pre-import its own target module (which otherwise triggers a runpy warning).
