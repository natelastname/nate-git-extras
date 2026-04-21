#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Config:
    forgejo_container: str
    forgejo_user_uid: str
    forgejo_user_gid: str
    forgejo_url: str
    forgejo_ssh_host_alias: str
    forgejo_ssh_hostname: str
    forgejo_ssh_port: str
    agent_username: str
    agent_email: str
    agent_fullname: str
    token_name: str
    token_scopes: str
    git_remote_name: str
    git_main_branch: str
    initial_commit_message: str
    profile_name: str
    project_profile_rel_path: str
    config_root: Path
    ssh_key_path: Path
    secrets_env_path: Path
    repo_private: bool

    @staticmethod
    def load() -> "Config":
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"

        agent_username = env("AGENT_USERNAME", "agent")
        config_root = Path(
            env(
                "CONFIG_ROOT",
                str(config_base / "goose_template" / "forgejo" / agent_username),
            )
        )

        ssh_key_path = Path(
            env("SSH_KEY_PATH", str(config_root / "ssh" / "id_ed25519"))
        )
        secrets_env_path = Path(
            env("SECRETS_ENV_PATH", str(config_root / "agent.env"))
        )

        return Config(
            forgejo_container=env("FORGEJO_CONTAINER", "forgejo"),
            forgejo_user_uid=env("FORGEJO_USER_UID", "1000"),
            forgejo_user_gid=env("FORGEJO_USER_GID", "1000"),
            forgejo_url=env("FORGEJO_URL", "http://localhost:3000"),
            forgejo_ssh_host_alias=env("FORGEJO_SSH_HOST_ALIAS", "forgejo-local"),
            forgejo_ssh_hostname=env("FORGEJO_SSH_HOSTNAME", "localhost"),
            forgejo_ssh_port=env("FORGEJO_SSH_PORT", "2222"),
            agent_username=agent_username,
            agent_email=env("AGENT_EMAIL", "agent@local.dev"),
            agent_fullname=env("AGENT_FULLNAME", "Local Agent"),
            token_name=env("TOKEN_NAME", "local-agent"),
            token_scopes=env("TOKEN_SCOPES", "write:repository,write:user"),
            git_remote_name=env("GIT_REMOTE_NAME", "forgejo"),
            git_main_branch=env("GIT_MAIN_BRANCH", "main"),
            initial_commit_message=env("INITIAL_COMMIT_MESSAGE", "Initial commit"),
            profile_name=env("PROFILE_NAME", "local-forgejo-agent"),
            project_profile_rel_path=env(
                "PROJECT_PROFILE_REL_PATH", ".goose/forgejo.yaml"
            ),
            config_root=config_root,
            ssh_key_path=ssh_key_path,
            secrets_env_path=secrets_env_path,
            repo_private=env("REPO_PRIVATE", "true").lower() in {"1", "true", "yes"},
        )


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def need_cmd(command: str) -> None:
    if shutil.which(command) is None:
        die(f"Missing required command: {command}")


def run(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=capture_output,
        text=text,
        cwd=cwd,
    )


def container_exec(
    cfg: Config,
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "docker",
            "exec",
            "-u",
            f"{cfg.forgejo_user_uid}:{cfg.forgejo_user_gid}",
            cfg.forgejo_container,
            *args,
        ],
        check=check,
        capture_output=capture_output,
    )


def get_repo_root() -> Path | None:
    try:
        result = run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    return Path(result.stdout.strip())


def resolve_workspace_root() -> Path:
    repo_root = get_repo_root()
    if repo_root is not None:
        return repo_root
    return Path.cwd().resolve()


def git_dir_exists(root: Path) -> bool:
    return (root / ".git").exists()


def check_http(cfg: Config) -> bool:
    result = run(
        ["curl", "-fsS", cfg.forgejo_url],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def ensure_dirs(cfg: Config) -> None:
    cfg.config_root.mkdir(parents=True, exist_ok=True)
    cfg.ssh_key_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.config_root, 0o700)
    os.chmod(cfg.ssh_key_path.parent, 0o700)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def forgejo_user_list(cfg: Config) -> str:
    try:
        result = container_exec(cfg, ["forgejo", "admin", "user", "list"])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        stdout = exc.stdout.strip() if exc.stdout else ""
        detail = stderr or stdout or "unknown error"
        die(f"Failed to list Forgejo users: {detail}")

    return result.stdout


def forgejo_user_exists(cfg: Config) -> bool:
    output = forgejo_user_list(cfg)
    pattern = re.compile(
        rf"(^|\s){re.escape(cfg.agent_username)}(\s|$)",
        re.MULTILINE,
    )
    return bool(pattern.search(output))


def ensure_agent_user(cfg: Config) -> None:
    if forgejo_user_exists(cfg):
        print(f"Agent user already exists: {cfg.agent_username}")
        return

    print(f"Creating Forgejo agent user: {cfg.agent_username}")
    result = container_exec(
        cfg,
        [
            "forgejo",
            "admin",
            "user",
            "create",
            "--username",
            cfg.agent_username,
            "--email",
            cfg.agent_email,
            "--fullname",
            cfg.agent_fullname,
            "--random-password",
            "--must-change-password=false",
        ],
        check=False,
    )

    if result.returncode != 0:
        if forgejo_user_exists(cfg):
            print(f"Agent user already exists after create attempt: {cfg.agent_username}")
            return

        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or "unknown error"
        die(f"Failed to create agent user: {detail}")

    combined = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if combined:
        print(combined)


def ensure_token(cfg: Config, force_rotate: bool) -> None:
    current = parse_env_file(cfg.secrets_env_path)
    if current.get("FORGEJO_AGENT_TOKEN") and not force_rotate:
        print(f"Token already present at {cfg.secrets_env_path}")
        return

    print(f"Generating Forgejo access token for {cfg.agent_username}")
    result = container_exec(
        cfg,
        [
            "forgejo",
            "admin",
            "user",
            "generate-access-token",
            "--username",
            cfg.agent_username,
            "--token-name",
            cfg.token_name,
            "--scopes",
            cfg.token_scopes,
            "--raw",
        ],
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or "unknown error"
        die(f"Failed to generate token: {detail}")

    token = result.stdout.strip()
    if not token:
        die("Forgejo returned an empty token")

    values = {
        "FORGEJO_URL": cfg.forgejo_url,
        "FORGEJO_AGENT_USERNAME": cfg.agent_username,
        "FORGEJO_AGENT_TOKEN": token,
        "FORGEJO_SSH_KEY_PATH": str(cfg.ssh_key_path),
    }
    write_env_file(cfg.secrets_env_path, values)
    print(f"Wrote token to {cfg.secrets_env_path}")


def ensure_ssh_key(cfg: Config) -> None:
    pub_path = Path(f"{cfg.ssh_key_path}.pub")
    if cfg.ssh_key_path.exists() and pub_path.exists():
        print(f"SSH key already exists: {cfg.ssh_key_path}")
        os.chmod(cfg.ssh_key_path, 0o600)
        os.chmod(pub_path, 0o644)
        return

    print(f"Generating SSH keypair at {cfg.ssh_key_path}")
    run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(cfg.ssh_key_path),
            "-C",
            f"{cfg.agent_username}@{os.uname().nodename}-forgejo",
        ]
    )
    os.chmod(cfg.ssh_key_path, 0o600)
    os.chmod(pub_path, 0o644)


def ensure_ssh_config_alias(cfg: Config) -> None:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)

    ssh_config = ssh_dir / "config"
    if not ssh_config.exists():
        ssh_config.touch()
    os.chmod(ssh_config, 0o600)

    text = ssh_config.read_text()
    host_pattern = re.compile(
        rf"(?m)^[ \t]*Host[ \t]+{re.escape(cfg.forgejo_ssh_host_alias)}(?:[ \t]+.*)?$"
    )
    if host_pattern.search(text):
        print(f"SSH host alias already present: {cfg.forgejo_ssh_host_alias}")
        return

    block = f"""

Host {cfg.forgejo_ssh_host_alias}
    HostName {cfg.forgejo_ssh_hostname}
    Port {cfg.forgejo_ssh_port}
    User git
    IdentityFile {cfg.ssh_key_path}
    IdentitiesOnly yes
"""
    ssh_config.write_text(text.rstrip() + block + "\n")
    print(f"Added SSH host alias to {ssh_config}")


def upload_pubkey_via_api(cfg: Config) -> None:
    values = parse_env_file(cfg.secrets_env_path)
    token = values.get("FORGEJO_AGENT_TOKEN")
    if not token:
        die(f"Missing FORGEJO_AGENT_TOKEN in {cfg.secrets_env_path}")

    pub_path = Path(f"{cfg.ssh_key_path}.pub")
    if not pub_path.exists():
        die(f"Public key not found: {pub_path}")

    pubkey = pub_path.read_text().strip()
    if not pubkey:
        die("Public key file is empty")

    title = f"{cfg.agent_username}@{os.uname().nodename}-{cfg.ssh_key_path.parent.name}"

    list_result = run(
        [
            "curl",
            "-fsS",
            "-H",
            f"Authorization: token {token}",
            f"{cfg.forgejo_url}/api/v1/user/keys",
        ],
        check=False,
        capture_output=True,
    )

    if list_result.returncode == 0 and pubkey in list_result.stdout:
        print("Public key already registered in Forgejo")
        return

    print("Uploading public key to Forgejo")
    payload = json.dumps({"title": title, "key": pubkey})
    create_result = run(
        [
            "curl",
            "-fsS",
            "-H",
            "Content-Type: application/json",
            "-H",
            f"Authorization: token {token}",
            "-X",
            "POST",
            "-d",
            payload,
            f"{cfg.forgejo_url}/api/v1/user/keys",
        ],
        check=False,
        capture_output=True,
    )

    if create_result.returncode != 0:
        detail = (create_result.stderr or create_result.stdout or "").strip()
        die(f"Failed to upload SSH public key: {detail}")

    print("Uploaded SSH public key")


def configure_tea_login(cfg: Config) -> None:
    if shutil.which("tea") is None:
        print("tea not installed; skipping tea login setup")
        return

    values = parse_env_file(cfg.secrets_env_path)
    token = values.get("FORGEJO_AGENT_TOKEN")
    if not token:
        die(f"Missing FORGEJO_AGENT_TOKEN in {cfg.secrets_env_path}")

    login_ls = run(
        ["tea", "login", "ls"],
        check=False,
        capture_output=True,
    )
    combined = (login_ls.stdout or "") + "\n" + (login_ls.stderr or "")
    if cfg.forgejo_url in combined or cfg.forgejo_ssh_host_alias in combined:
        print(f"tea login for {cfg.forgejo_url} already exists")
        return

    print("Configuring tea login")
    result = run(
        [
            "tea",
            "login",
            "add",
            "--name",
            cfg.forgejo_ssh_host_alias,
            "--url",
            cfg.forgejo_url,
            "--token",
            token,
        ],
        check=False,
        capture_output=True,
    )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        die(f"Failed to configure tea login: {detail}")

    print("Configured tea login")


def write_project_profile(cfg: Config, repo_root: Path) -> None:
    profile_path = repo_root / cfg.project_profile_rel_path
    profile_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""profile_name: {cfg.profile_name}
forgejo_url: {cfg.forgejo_url}
forgejo_user: {cfg.agent_username}
git_remote_name: {cfg.git_remote_name}
git_main_branch: {cfg.git_main_branch}
ssh_host_alias: {cfg.forgejo_ssh_host_alias}
ssh_hostname: {cfg.forgejo_ssh_hostname}
ssh_port: {cfg.forgejo_ssh_port}
token_env_file: {cfg.secrets_env_path}
ssh_key_file: {cfg.ssh_key_path}
tea_login_name: {cfg.forgejo_ssh_host_alias}
"""
    profile_path.write_text(content)
    print(f"Wrote project profile: {profile_path}")


def require_repo_root() -> Path:
    repo_root = get_repo_root()
    if repo_root is None:
        die("Not inside a git repository")
    return repo_root


def get_repo_name(repo_root: Path) -> str:
    return repo_root.name


def has_commits(repo_root: Path | None = None) -> bool:
    result = run(
        ["git", "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    return result.returncode == 0


def get_current_branch(repo_root: Path | None = None) -> str | None:
    result = run(
        ["git", "branch", "--show-current"],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    branch = result.stdout.strip()
    if not branch:
        return None
    return branch


def git_config_get(key: str, repo_root: Path) -> str | None:
    result = run(
        ["git", "config", "--get", key],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value:
        return None
    return value


def ensure_git_identity(cfg: Config, repo_root: Path) -> None:
    current_name = git_config_get("user.name", repo_root)
    current_email = git_config_get("user.email", repo_root)

    if current_name is None:
        run(["git", "config", "user.name", cfg.agent_fullname], cwd=repo_root)
        print(f"Set local git user.name: {cfg.agent_fullname}")
    else:
        print(f"Local git user.name already set: {current_name}")

    if current_email is None:
        run(["git", "config", "user.email", cfg.agent_email], cwd=repo_root)
        print(f"Set local git user.email: {cfg.agent_email}")
    else:
        print(f"Local git user.email already set: {current_email}")


def ensure_git_repo_initialized(cfg: Config) -> Path:
    repo_root = resolve_workspace_root()

    if get_repo_root() is not None:
        print(f"Git repo already initialized: {repo_root}")
        return repo_root

    if git_dir_exists(repo_root):
        result = run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            cwd=repo_root,
        )
        if result.returncode == 0:
            resolved_root = Path(result.stdout.strip())
            print(f"Git repo already initialized: {resolved_root}")
            return resolved_root

    print(f"Initializing git repo in {repo_root}")
    run(["git", "init", "-b", cfg.git_main_branch, str(repo_root)])
    return repo_root


def ensure_main_branch(cfg: Config, repo_root: Path) -> None:
    current_branch = get_current_branch(repo_root)

    if current_branch == cfg.git_main_branch:
        print(f"Current branch already {cfg.git_main_branch}")
        return

    if has_commits(repo_root):
        run(["git", "branch", "-M", cfg.git_main_branch], cwd=repo_root)
        print(f"Renamed current branch to {cfg.git_main_branch}")
        return

    run(["git", "symbolic-ref", "HEAD", f"refs/heads/{cfg.git_main_branch}"], cwd=repo_root)
    print(f"Set initial branch to {cfg.git_main_branch}")


def ensure_initial_commit(cfg: Config, repo_root: Path) -> None:
    if has_commits(repo_root):
        print("Git repo already has commits")
        return

    ensure_git_identity(cfg, repo_root)
    print("Creating empty initial commit")
    run(
        ["git", "commit", "--allow-empty", "-m", cfg.initial_commit_message],
        cwd=repo_root,
    )


def get_git_remote_url(remote_name: str, repo_root: Path | None = None) -> str | None:
    result = run(
        ["git", "remote", "get-url", remote_name],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def expected_remote_url(cfg: Config, repo_name: str) -> str:
    return f"git@{cfg.forgejo_ssh_host_alias}:{cfg.agent_username}/{repo_name}.git"


def ensure_git_remote(cfg: Config, repo_root: Path, repo_name: str) -> None:
    remote_name = cfg.git_remote_name
    expected = expected_remote_url(cfg, repo_name)
    current = get_git_remote_url(remote_name, repo_root)

    if current is None:
        print(f"Adding git remote {remote_name}: {expected}")
        run(["git", "remote", "add", remote_name, expected], cwd=repo_root)
        return

    if current != expected:
        print(f"Updating git remote {remote_name}: {current} -> {expected}")
        run(["git", "remote", "set-url", remote_name, expected], cwd=repo_root)
        return

    print(f"Git remote {remote_name} already correct: {expected}")


def git_can_ls_remote(remote_name: str, repo_root: Path | None = None) -> bool:
    result = run(
        ["git", "ls-remote", remote_name],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    return result.returncode == 0


def token_from_env_file(cfg: Config) -> str:
    values = parse_env_file(cfg.secrets_env_path)
    token = values.get("FORGEJO_AGENT_TOKEN")
    if not token:
        die(f"Missing FORGEJO_AGENT_TOKEN in {cfg.secrets_env_path}")
    return token


def forgejo_api_json(
    cfg: Config,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    token = token_from_env_file(cfg)

    args = [
        "curl",
        "-fsS",
        "-H",
        f"Authorization: token {token}",
        "-H",
        "Content-Type: application/json",
        "-X",
        method,
    ]
    if payload is not None:
        args.extend(["-d", json.dumps(payload)])
    args.append(f"{cfg.forgejo_url}{path}")

    return run(args, check=False, capture_output=True)


def forgejo_repo_exists(cfg: Config, repo_name: str) -> bool:
    result = forgejo_api_json(
        cfg,
        "GET",
        f"/api/v1/repos/{cfg.agent_username}/{repo_name}",
    )
    return result.returncode == 0


def ensure_forgejo_repo_exists(cfg: Config, repo_name: str) -> None:
    if forgejo_repo_exists(cfg, repo_name):
        print(f"Forgejo repo already exists: {cfg.agent_username}/{repo_name}")
        return

    print(f"Creating Forgejo repo: {cfg.agent_username}/{repo_name}")
    result = forgejo_api_json(
        cfg,
        "POST",
        "/api/v1/user/repos",
        payload={
            "name": repo_name,
            "private": cfg.repo_private,
            "auto_init": False,
            "default_branch": cfg.git_main_branch,
        },
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        die(f"Failed to create Forgejo repo: {detail}")

    print(f"Created Forgejo repo: {cfg.agent_username}/{repo_name}")


def remote_branch_exists(remote_name: str, branch_name: str, repo_root: Path | None = None) -> bool:
    result = run(
        ["git", "ls-remote", "--heads", remote_name, branch_name],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def ensure_branch_pushed(remote_name: str, branch_name: str, repo_root: Path) -> None:
    if remote_branch_exists(remote_name, branch_name, repo_root):
        print(f"Remote branch already exists: {remote_name}/{branch_name}")
    else:
        print(f"Pushing branch to remote: {branch_name}")
        result = run(
            ["git", "push", "-u", remote_name, branch_name],
            check=False,
            capture_output=True,
            cwd=repo_root,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            die(f"Failed to push branch {branch_name} to {remote_name}: {detail}")
        print(f"Pushed branch to remote: {remote_name}/{branch_name}")

    ensure_tracking_branch(remote_name, branch_name, repo_root)


def get_upstream_branch(repo_root: Path) -> str | None:
    result = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        check=False,
        capture_output=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch:
        return None
    return branch


def ensure_tracking_branch(remote_name: str, branch_name: str, repo_root: Path) -> None:
    expected = f"{remote_name}/{branch_name}"
    current = get_upstream_branch(repo_root)
    if current == expected:
        print(f"Upstream tracking already set: {expected}")
        return

    run(
        ["git", "branch", "--set-upstream-to", expected, branch_name],
        cwd=repo_root,
    )
    print(f"Set upstream tracking: {branch_name} -> {expected}")


def delete_forgejo_user(cfg: Config) -> None:
    print(f"Checking whether Forgejo user exists: {cfg.agent_username}")
    if not forgejo_user_exists(cfg):
        print(f"Forgejo user does not exist: {cfg.agent_username}")
        return

    print(f"Deleting Forgejo user: {cfg.agent_username}")
    result = container_exec(
        cfg,
        [
            "forgejo",
            "admin",
            "user",
            "delete",
            "--username",
            cfg.agent_username,
            "--purge",
        ],
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or "unknown error"
        die(f"Failed to delete Forgejo user: {detail}")

    combined = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    if combined:
        print(combined)

    print(f"Deleted Forgejo user: {cfg.agent_username}")


def remove_local_credentials(cfg: Config) -> None:
    if cfg.config_root.exists():
        print(f"Removing local credentials: {cfg.config_root}")
        shutil.rmtree(cfg.config_root)
    else:
        print(f"Local credentials directory not present: {cfg.config_root}")


def remove_tea_login(cfg: Config) -> None:
    if shutil.which("tea") is None:
        print("tea not installed; skipping tea login removal")
        return

    result = run(
        ["tea", "login", "ls"],
        check=False,
        capture_output=True,
    )
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if cfg.forgejo_ssh_host_alias not in combined and cfg.forgejo_url not in combined:
        print(f"tea login not present: {cfg.forgejo_ssh_host_alias}")
        return

    print(f"Attempting to remove tea login: {cfg.forgejo_ssh_host_alias}")

    candidate_commands = [
        ["tea", "login", "remove", cfg.forgejo_ssh_host_alias],
        ["tea", "login", "delete", cfg.forgejo_ssh_host_alias],
        ["tea", "login", "rm", cfg.forgejo_ssh_host_alias],
    ]

    for cmd in candidate_commands:
        result = run(cmd, check=False, capture_output=True)
        if result.returncode == 0:
            print(f"Removed tea login using: {' '.join(cmd[:3])}")
            return

        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        if "No help topic" in detail or "unknown command" in detail.lower():
            continue

    print(
        "Could not remove tea login automatically; "
        "your tea version does not support the expected subcommand. "
        "Continuing reset without failing."
    )


def remove_ssh_alias(cfg: Config) -> None:
    ssh_config = Path.home() / ".ssh" / "config"
    if not ssh_config.exists():
        print(f"SSH config not present: {ssh_config}")
        return

    text = ssh_config.read_text()
    pattern = re.compile(
        rf"(?ms)^[ \t]*Host[ \t]+{re.escape(cfg.forgejo_ssh_host_alias)}(?:[ \t]+.*)?\n.*?(?=^[ \t]*Host[ \t]+\S|\Z)"
    )
    new_text, count = pattern.subn("", text)

    if count:
        ssh_config.write_text(new_text.rstrip() + "\n")
        print(f"Removed SSH host alias: {cfg.forgejo_ssh_host_alias}")
    else:
        print(f"SSH host alias not present: {cfg.forgejo_ssh_host_alias}")


def remove_project_profile(cfg: Config) -> None:
    repo_root = get_repo_root()
    if repo_root is None:
        print("Not in a git repository; skipping project profile removal")
        return

    profile_path = repo_root / cfg.project_profile_rel_path
    if profile_path.exists():
        print(f"Removing project profile: {profile_path}")
        profile_path.unlink()
    else:
        print(f"Project profile not present: {profile_path}")


def ssh_alias_points_to_expected(cfg: Config) -> bool:
    ssh_config = Path.home() / ".ssh" / "config"
    if not ssh_config.exists():
        return False

    text = ssh_config.read_text()
    pattern = re.compile(
        rf"(?ms)^[ \t]*Host[ \t]+{re.escape(cfg.forgejo_ssh_host_alias)}(?:[ \t]+.*)?\n(.*?)(?=^[ \t]*Host[ \t]+\S|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return False

    block = match.group(1)
    return (
        f"HostName {cfg.forgejo_ssh_hostname}" in block
        and f"Port {cfg.forgejo_ssh_port}" in block
        and f"IdentityFile {cfg.ssh_key_path}" in block
        and "User git" in block
    )


def doctor(cfg: Config) -> int:
    repo_root = get_repo_root()
    workspace_root = resolve_workspace_root()
    env_values = parse_env_file(cfg.secrets_env_path)
    pub_path = Path(f"{cfg.ssh_key_path}.pub")
    repo_name = get_repo_name(repo_root) if repo_root is not None else workspace_root.name
    expected_remote = expected_remote_url(cfg, repo_name)
    current_branch = get_current_branch(repo_root) if repo_root is not None else None
    current_remote = get_git_remote_url(cfg.git_remote_name, repo_root) if repo_root is not None else None
    upstream = get_upstream_branch(repo_root) if repo_root is not None else None
    remote_reachable = (
        git_can_ls_remote(cfg.git_remote_name, repo_root)
        if repo_root is not None and current_remote is not None
        else False
    )
    remote_main_exists = (
        remote_branch_exists(cfg.git_remote_name, cfg.git_main_branch, repo_root)
        if repo_root is not None and current_remote is not None
        else False
    )

    checks: list[tuple[str, bool, str]] = [
        ("forgejo_http", check_http(cfg), cfg.forgejo_url),
        ("docker", shutil.which("docker") is not None, "docker in PATH"),
        ("git", shutil.which("git") is not None, "git in PATH"),
        ("tea", shutil.which("tea") is not None, "tea in PATH"),
        ("ssh-keygen", shutil.which("ssh-keygen") is not None, "ssh-keygen in PATH"),
        ("agent_user", forgejo_user_exists(cfg), cfg.agent_username),
        ("token_env_file", cfg.secrets_env_path.exists(), str(cfg.secrets_env_path)),
        (
            "token_present",
            bool(env_values.get("FORGEJO_AGENT_TOKEN")),
            "FORGEJO_AGENT_TOKEN set",
        ),
        ("ssh_private_key", cfg.ssh_key_path.exists(), str(cfg.ssh_key_path)),
        ("ssh_public_key", pub_path.exists(), str(pub_path)),
        (
            "ssh_alias",
            ssh_alias_points_to_expected(cfg),
            cfg.forgejo_ssh_host_alias,
        ),
        (
            "git_repo",
            repo_root is not None,
            str(repo_root) if repo_root else str(workspace_root),
        ),
        (
            "git_main_branch",
            current_branch == cfg.git_main_branch,
            current_branch or "no branch",
        ),
        (
            "git_has_commits",
            repo_root is not None and has_commits(repo_root),
            repo_name,
        ),
        (
            "forgejo_repo",
            repo_root is not None and forgejo_repo_exists(cfg, repo_name),
            f"{cfg.agent_username}/{repo_name}",
        ),
        (
            "git_remote",
            current_remote == expected_remote,
            current_remote or f"missing ({expected_remote})",
        ),
        (
            "git_remote_reachable",
            remote_reachable,
            cfg.git_remote_name,
        ),
        (
            "remote_main_branch",
            remote_main_exists,
            f"{cfg.git_remote_name}/{cfg.git_main_branch}",
        ),
        (
            "git_upstream",
            upstream == f"{cfg.git_remote_name}/{cfg.git_main_branch}",
            upstream or "missing",
        ),
        (
            "project_profile",
            repo_root is not None
            and (repo_root / cfg.project_profile_rel_path).exists(),
            str(repo_root / cfg.project_profile_rel_path) if repo_root else "no git repo",
        ),
    ]

    print("Forgejo agent doctor")
    print("--------------------")
    failed = 0
    for name, ok, detail in checks:
        status = "OK" if ok else "MISSING"
        print(f"{status:8} {name:20} {detail}")
        if not ok:
            failed += 1

    return 0 if failed == 0 else 1


def ensure_repo_tracking_ready(cfg: Config) -> Path:
    repo_root = ensure_git_repo_initialized(cfg)
    repo_name = get_repo_name(repo_root)

    ensure_main_branch(cfg, repo_root)
    ensure_initial_commit(cfg, repo_root)
    write_project_profile(cfg, repo_root)

    if not check_http(cfg):
        die(f"Forgejo is not reachable at {cfg.forgejo_url}")

    if not cfg.secrets_env_path.exists():
        die(
            f"Missing token env file: {cfg.secrets_env_path}. "
            "Bootstrap did not complete correctly."
        )

    if not cfg.ssh_key_path.exists():
        die(
            f"Missing SSH private key: {cfg.ssh_key_path}. "
            "Bootstrap did not complete correctly."
        )

    if not ssh_alias_points_to_expected(cfg):
        die(
            f"SSH alias {cfg.forgejo_ssh_host_alias} is missing or incorrect. "
            "Bootstrap did not complete correctly."
        )

    ensure_forgejo_repo_exists(cfg, repo_name)
    ensure_git_remote(cfg, repo_root, repo_name)

    if not git_can_ls_remote(cfg.git_remote_name, repo_root):
        die(
            f"Cannot contact git remote {cfg.git_remote_name}. "
            "Check SSH auth and Forgejo remote configuration."
        )

    ensure_branch_pushed(cfg.git_remote_name, cfg.git_main_branch, repo_root)
    return repo_root


def bootstrap(cfg: Config, force_rotate: bool) -> int:
    need_cmd("docker")
    need_cmd("curl")
    need_cmd("git")
    need_cmd("ssh-keygen")

    if not check_http(cfg):
        die(f"Forgejo is not reachable at {cfg.forgejo_url}")

    ensure_dirs(cfg)
    ensure_agent_user(cfg)
    ensure_token(cfg, force_rotate=force_rotate)
    ensure_ssh_key(cfg)
    ensure_ssh_config_alias(cfg)
    upload_pubkey_via_api(cfg)
    configure_tea_login(cfg)

    repo_root = ensure_repo_tracking_ready(cfg)
    repo_name = get_repo_name(repo_root)

    print(
        f"""
Bootstrap complete.

Artifacts:
  Token env: {cfg.secrets_env_path}
  SSH key:   {cfg.ssh_key_path}
  SSH pub:   {cfg.ssh_key_path}.pub

Repo readiness:
  Repo root:      {repo_root}
  Repo name:      {repo_name}
  Local branch:   {cfg.git_main_branch}
  Forgejo repo:   {cfg.agent_username}/{repo_name}
  Git remote:     {cfg.git_remote_name}
  Remote URL:     {expected_remote_url(cfg, repo_name)}
  Upstream:       {cfg.git_remote_name}/{cfg.git_main_branch}

Recommended checks:
  ssh -T {cfg.forgejo_ssh_host_alias}
  git remote -v
  git branch -vv
  tea login ls
  tea whoami
""".strip()
    )
    return 0


def reset(
    cfg: Config,
    *,
    remove_ssh_alias_flag: bool,
    remove_project_profile_flag: bool,
) -> int:
    need_cmd("docker")
    need_cmd("git")

    delete_forgejo_user(cfg)
    remove_local_credentials(cfg)
    remove_tea_login(cfg)

    if remove_ssh_alias_flag:
        remove_ssh_alias(cfg)
    else:
        print("Skipping SSH alias removal")

    if remove_project_profile_flag:
        remove_project_profile(cfg)
    else:
        print("Skipping project profile removal")

    print(
        f"""
Reset complete.

Removed:
  - Forgejo user: {cfg.agent_username}
  - Local credentials: {cfg.config_root}
  - tea login: {cfg.forgejo_ssh_host_alias}
  - SSH alias: {cfg.forgejo_ssh_host_alias} {'(if present)' if remove_ssh_alias_flag else '(kept)'}
  - Project profile: {cfg.project_profile_rel_path} {'(if present)' if remove_project_profile_flag else '(kept)'}

Next step:
  ./scripts/forgejo_agent.py bootstrap
""".strip()
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage a local Forgejo agent identity for this project."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Create or repair the local Forgejo agent identity and connect this repo to Forgejo.",
    )
    bootstrap_parser.add_argument(
        "--force-rotate-token",
        action="store_true",
        help="Generate a fresh Forgejo token even if one already exists locally.",
    )

    reset_parser = subparsers.add_parser(
        "reset",
        help="Delete the local Forgejo agent identity and local config.",
    )
    reset_parser.add_argument(
        "--keep-ssh-alias",
        action="store_true",
        help="Do not remove the SSH host alias from ~/.ssh/config.",
    )
    reset_parser.add_argument(
        "--keep-project-profile",
        action="store_true",
        help="Do not remove the project-local .goose/forgejo.yaml file.",
    )

    subparsers.add_parser(
        "doctor",
        help="Inspect current local Forgejo agent and repo-tracking state.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = Config.load()

    if args.command == "bootstrap":
        return bootstrap(cfg, force_rotate=args.force_rotate_token)
    if args.command == "reset":
        return reset(
            cfg,
            remove_ssh_alias_flag=not args.keep_ssh_alias,
            remove_project_profile_flag=not args.keep_project_profile,
        )
    if args.command == "doctor":
        return doctor(cfg)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
