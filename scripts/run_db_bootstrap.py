from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PROFILE_CONFIG = {
    "local": {
        "env_file": REPO_ROOT / "infra" / ".env.local",
        "network": "tokenization-local_platform",
    },
    "regtest": {
        "env_file": REPO_ROOT / "infra" / ".env.regtest",
        "network": "tokenization-regtest_platform",
    },
    "public-beta": {
        "env_file": REPO_ROOT / "infra" / ".env.beta",
        "network": "tokenization-public-beta_beta",
    },
    "testnet4": {
        "env_file": REPO_ROOT / "infra" / ".env.testnet4.example",
        "network": "tokenization-testnet4_testnet4",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run database migrations and optional seeders in a standalone Docker container."
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_CONFIG),
        default="local",
        help="Environment profile to use for env-file and Docker network resolution.",
    )
    parser.add_argument(
        "--env-file",
        help="Override the env file path used by the bootstrap container.",
    )
    parser.add_argument(
        "--network",
        help="Override the Docker network used to reach service hostnames such as postgres.",
    )
    parser.add_argument(
        "--image",
        default="python:3.11-slim",
        help="Docker image used to execute the bootstrap script.",
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Run only Alembic migrations.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Run only idempotent seeders.",
    )
    args = parser.parse_args()
    if args.migrate_only and args.seed_only:
        parser.error("--migrate-only and --seed-only cannot be used together.")
    return args


def main() -> int:
    args = parse_args()
    profile = PROFILE_CONFIG[args.profile]
    env_file = Path(args.env_file).resolve() if args.env_file else profile["env_file"]
    network = args.network or profile["network"]

    if not env_file.exists():
        raise SystemExit(
            f"Env file not found: {env_file}. Create it first or pass --env-file."
        )

    bootstrap_args: list[str] = []
    if args.migrate_only:
        bootstrap_args.append("--migrate-only")
    if args.seed_only:
        bootstrap_args.append("--seed-only")

    bootstrap_cmd = "python scripts/db_bootstrap.py"
    if bootstrap_args:
        bootstrap_cmd = f"{bootstrap_cmd} {' '.join(bootstrap_args)}"

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--env-file",
        str(env_file),
        "-v",
        f"{REPO_ROOT}:/app",
        "-w",
        "/app",
        args.image,
        "sh",
        "-lc",
        (
            "pip install --no-cache-dir "
            "-r scripts/requirements-migrations.txt "
            "-r services/auth/requirements.txt "
            f"&& {bootstrap_cmd}"
        ),
    ]

    print(f"Running db bootstrap with profile={args.profile}, network={network}")
    print(" ".join(docker_cmd))
    completed = subprocess.run(docker_cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
