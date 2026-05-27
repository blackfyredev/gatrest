#!/usr/bin/env python3
"""
gatrest: terminal uploader for DataNaviGatr ingest.

This is intentionally standard-library only so it can be installed from GitHub
with a small curl command on Ubuntu systems.
"""

import argparse
import base64
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import time
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
import uuid


APP_NAME = "gatrest"
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_BATCH_SIZE = 20
DEFAULT_CERT_PATH = "/.well-known/datanavigatr/datanavigatr.crt"

_ACCESS_TOKEN = None
_ACCESS_TOKEN_EXPIRES_AT = 0


class GatrestError(Exception):
    """Expected user-facing error."""


def default_config():
    return {
        "version": 1,
        "defaults": {
            "server": "",
            "user": "",
            "batch_size": DEFAULT_BATCH_SIZE,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "trust_self_signed": True,
            "verify_hostname": False,
            "cert_path": DEFAULT_CERT_PATH,
        },
        "servers": {},
    }


def load_config():
    if not CONFIG_PATH.exists():
        return default_config()

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise GatrestError("Config file is invalid JSON: %s" % exc)

    base = default_config()
    base.update(config)
    base.setdefault("defaults", {}).update(config.get("defaults", {}))
    base.setdefault("servers", {})
    return base


def save_config(config):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2, sort_keys=True)
        config_file.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def require_server(config, name):
    servers = config.get("servers") or {}
    server = servers.get(name)
    if not server:
        raise GatrestError("Unknown server '%s'. Run 'gatrest show servers'." % name)
    server.setdefault("users", {})
    return server


def require_user(server, name):
    user = (server.get("users") or {}).get(name)
    if not user:
        raise GatrestError("Unknown user '%s' for this server." % name)
    return user


def resolve_server_name(config, explicit_name=None):
    name = explicit_name or config.get("defaults", {}).get("server")
    if not name:
        raise GatrestError("No server selected. Use --server or set a default with 'gatrest use server NAME'.")
    return name


def resolve_user_name(config, server, explicit_name=None):
    name = explicit_name or config.get("defaults", {}).get("user")
    users = server.get("users") or {}
    if not name:
        if len(users) == 1:
            return next(iter(users))
        raise GatrestError("No user selected. Use --user or set a default with 'gatrest use user NAME'.")
    return name


def normalize_host(host, scheme="https"):
    host = (host or "").strip().rstrip("/")
    if not host:
        raise GatrestError("Server host is required.")
    parsed = urllib_parse.urlparse(host)
    if parsed.scheme:
        return host
    return "%s://%s" % (scheme, host)


def join_url(base_url, path):
    parsed = urllib_parse.urlparse(base_url.rstrip("/"))
    return urllib_parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def safe_filename_part(value, default_value):
    safe = []
    for char in str(value or default_value):
        if char.isalnum() or char in ("-", "_", "."):
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or default_value


def cert_cache_path(url):
    parsed = urllib_parse.urlparse(url)
    host_key = safe_filename_part(parsed.netloc, "datanavigatr")
    return os.path.join(tempfile.gettempdir(), "datanavigatr_%s.crt" % host_key)


def cert_download_url(url, cert_path):
    parsed = urllib_parse.urlparse(url)
    return urllib_parse.urlunparse(("http", parsed.netloc, cert_path, "", "", ""))


def get_presented_server_cert(url):
    parsed = urllib_parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or 443
    if not host:
        raise GatrestError("Unable to determine host from %s" % url)

    try:
        cert_pem = ssl.get_server_certificate((host, port), timeout=30)
    except TypeError:
        cert_pem = ssl.get_server_certificate((host, port))
    return cert_pem.encode("ascii")


def refresh_datanav_cert(url, cert_path):
    url_for_cert = cert_download_url(url, cert_path)
    try:
        cert_pem = urllib_request.urlopen(url_for_cert, timeout=30).read()
    except Exception:
        cert_pem = get_presented_server_cert(url)

    if b"BEGIN CERTIFICATE" not in cert_pem:
        raise GatrestError("DataNaviGatr certificate download did not return a PEM certificate.")

    cache_path = cert_cache_path(url)
    with open(cache_path, "wb") as cert_file:
        cert_file.write(cert_pem)
    return cache_path


def datanav_ssl_context(url, trust_self_signed=True, verify_hostname=False, cert_path=DEFAULT_CERT_PATH, refresh=False):
    parsed = urllib_parse.urlparse(url)
    if parsed.scheme != "https" or not trust_self_signed:
        return None

    cached_cert_path = cert_cache_path(url)
    if refresh or not os.path.exists(cached_cert_path):
        cached_cert_path = refresh_datanav_cert(url, cert_path)

    context = ssl.create_default_context(cafile=cached_cert_path)
    context.check_hostname = bool(verify_hostname)
    return context


def is_certificate_error(err):
    reason = getattr(err, "reason", err)
    return (
        isinstance(reason, ssl.CertificateError)
        or "CERTIFICATE_VERIFY_FAILED" in str(err)
        or "certificate verify failed" in str(err).lower()
    )


def urlopen_datanav(request, timeout, server):
    url = request.full_url
    context = datanav_ssl_context(
        url,
        trust_self_signed=server.get("trust_self_signed", True),
        verify_hostname=server.get("verify_hostname", False),
        cert_path=server.get("cert_path", DEFAULT_CERT_PATH),
    )
    try:
        return urllib_request.urlopen(request, timeout=timeout, context=context)
    except urllib_error.URLError as err:
        if not is_certificate_error(err):
            raise

        refreshed_context = datanav_ssl_context(
            url,
            trust_self_signed=server.get("trust_self_signed", True),
            verify_hostname=server.get("verify_hostname", False),
            cert_path=server.get("cert_path", DEFAULT_CERT_PATH),
            refresh=True,
        )
        return urllib_request.urlopen(request, timeout=timeout, context=refreshed_context)


def json_request(url, payload, timeout, server):
    body = json.dumps(payload).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = urlopen_datanav(request, timeout=timeout, server=server)
    response_body = response.read().decode("utf-8")
    return json.loads(response_body)


def decode_token_exp(token):
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        return int(json.loads(decoded).get("exp") or 0)
    except Exception:
        return 0


def get_access_token(server, user, force_refresh=False):
    global _ACCESS_TOKEN
    global _ACCESS_TOKEN_EXPIRES_AT

    if (
        not force_refresh
        and _ACCESS_TOKEN
        and (_ACCESS_TOKEN_EXPIRES_AT == 0 or _ACCESS_TOKEN_EXPIRES_AT - 30 > int(time.time()))
    ):
        return _ACCESS_TOKEN

    identifier = user.get("username") or user.get("identifier") or ""
    password = user.get("password") or ""
    if not identifier or not password:
        raise GatrestError("Username and saved password are required for upload.")

    auth_url = server.get("auth_url") or join_url(server["base_url"], "/api/auth/login")
    timeout = int(server.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    try:
        auth_result = json_request(auth_url, {"identifier": identifier, "password": password}, timeout, server)
    except urllib_error.HTTPError as err:
        response_body = err.read().decode("utf-8", errors="replace")
        raise GatrestError("DataNaviGatr login failed with HTTP %s: %s" % (err.code, response_body))
    except urllib_error.URLError as err:
        raise GatrestError("DataNaviGatr login failed: %s" % err)

    auth_user = auth_result.get("user") or {}
    if "admin" not in (auth_user.get("roles") or []):
        raise GatrestError("DataNaviGatr login succeeded, but this user is not an admin.")

    token = auth_result.get("access_token")
    if not token:
        raise GatrestError("DataNaviGatr login response did not include an access token.")

    _ACCESS_TOKEN = token
    _ACCESS_TOKEN_EXPIRES_AT = decode_token_exp(token)
    return _ACCESS_TOKEN


def build_multipart_payload(fields, file_paths):
    boundary = "----DataNaviGatrBoundary%s" % uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            "--%s\r\n" % boundary,
            'Content-Disposition: form-data; name="%s"\r\n\r\n' % name,
            str(value),
            "\r\n",
        ])

    body = "".join(chunks).encode("utf-8")
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        header = (
            "--%s\r\n"
            'Content-Disposition: form-data; name="files"; filename="%s"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ) % (boundary, filename)
        with open(file_path, "rb") as infile:
            body += header.encode("utf-8")
            body += infile.read()
            body += b"\r\n"

    body += ("--%s--\r\n" % boundary).encode("utf-8")
    return boundary, body


def validate_tag(value, label, length):
    value = (value or "").strip().upper()
    if len(value) != length or not value.isalnum():
        raise GatrestError("%s must be exactly %d alphanumeric characters." % (label, length))
    return value


def collect_json_files(paths):
    files = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            files.extend(sorted(str(item) for item in path.iterdir() if item.is_file() and item.name.lower().endswith(".json")))
        elif path.is_file() and path.name.lower().endswith(".json"):
            files.append(str(path))
        elif path.exists():
            raise GatrestError("Only .json files are accepted: %s" % path)
        else:
            raise GatrestError("Path does not exist: %s" % path)

    deduped = []
    seen = set()
    for file_path in files:
        absolute = str(Path(file_path).resolve())
        if absolute not in seen:
            seen.add(absolute)
            deduped.append(absolute)
    return deduped


def validate_json_file(file_path):
    with open(file_path, "r", encoding="utf-8-sig") as infile:
        json.load(infile)


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def send_files_to_ingest(server, user, file_paths, collector_code, organization_code):
    access_token = get_access_token(server, user)
    timeout = int(server.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    upload_url = server.get("ingest_url") or join_url(server["base_url"], "/api/ingest/upload")
    boundary, body = build_multipart_payload(
        {
            "collector_code": collector_code,
            "organization_code": organization_code,
        },
        file_paths,
    )
    request = urllib_request.Request(
        upload_url,
        data=body,
        headers={
            "Authorization": "Bearer %s" % access_token,
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
            "Content-Length": str(len(body)),
        },
    )

    try:
        response = urlopen_datanav(request, timeout=timeout, server=server)
        response_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as err:
        response_body = err.read().decode("utf-8", errors="replace")
        if err.code != 401:
            raise GatrestError("Ingest upload failed with HTTP %s: %s" % (err.code, response_body))

        access_token = get_access_token(server, user, force_refresh=True)
        request.add_header("Authorization", "Bearer %s" % access_token)
        try:
            response = urlopen_datanav(request, timeout=timeout, server=server)
            response_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as retry_err:
            retry_body = retry_err.read().decode("utf-8", errors="replace")
            raise GatrestError("Ingest upload failed with HTTP %s: %s" % (retry_err.code, retry_body))
        except urllib_error.URLError as retry_err:
            raise GatrestError("Ingest upload failed: %s" % retry_err)
    except urllib_error.URLError as err:
        raise GatrestError("Ingest upload failed: %s" % err)

    try:
        return json.loads(response_body)
    except ValueError:
        return {"raw_response": response_body}


def cmd_server_add(args):
    config = load_config()
    servers = config.setdefault("servers", {})
    if args.name in servers and not args.force:
        raise GatrestError("Server '%s' already exists. Use --force to replace it." % args.name)

    base_url = normalize_host(args.host, scheme=args.scheme)
    server = {
        "base_url": base_url,
        "collector_code": validate_tag(args.collector_code, "Collector Code", 2) if args.collector_code else "",
        "organization_code": validate_tag(args.organization_code, "Organization Code", 5) if args.organization_code else "",
        "timeout_seconds": args.timeout,
        "batch_size": args.batch_size,
        "trust_self_signed": not args.no_trust_self_signed,
        "verify_hostname": args.verify_hostname,
        "cert_path": args.cert_path,
        "users": {},
    }
    server["auth_url"] = join_url(base_url, "/api/auth/login")
    server["ingest_url"] = join_url(base_url, "/api/ingest/upload")
    if server["batch_size"] < 1 or server["batch_size"] > DEFAULT_BATCH_SIZE:
        raise GatrestError("Batch size must be between 1 and %d." % DEFAULT_BATCH_SIZE)
    servers[args.name] = server

    if not config["defaults"].get("server"):
        config["defaults"]["server"] = args.name
    save_config(config)
    print("Added server '%s' -> %s" % (args.name, base_url))


def cmd_server_set(args):
    config = load_config()
    server = require_server(config, args.name)

    if args.host:
        base_url = normalize_host(args.host, scheme=args.scheme)
        server["base_url"] = base_url
        server["auth_url"] = join_url(base_url, "/api/auth/login")
        server["ingest_url"] = join_url(base_url, "/api/ingest/upload")
    if args.collector_code:
        server["collector_code"] = validate_tag(args.collector_code, "Collector Code", 2)
    if args.organization_code:
        server["organization_code"] = validate_tag(args.organization_code, "Organization Code", 5)
    if args.timeout is not None:
        server["timeout_seconds"] = args.timeout
    if args.batch_size is not None:
        if args.batch_size < 1 or args.batch_size > DEFAULT_BATCH_SIZE:
            raise GatrestError("Batch size must be between 1 and %d." % DEFAULT_BATCH_SIZE)
        server["batch_size"] = args.batch_size
    if args.trust_self_signed is not None:
        server["trust_self_signed"] = args.trust_self_signed
    if args.verify_hostname is not None:
        server["verify_hostname"] = args.verify_hostname
    if args.cert_path:
        server["cert_path"] = args.cert_path

    save_config(config)
    print("Updated server '%s'." % args.name)


def cmd_server_remove(args):
    config = load_config()
    if args.name not in config.get("servers", {}):
        raise GatrestError("Unknown server '%s'." % args.name)
    del config["servers"][args.name]
    if config.get("defaults", {}).get("server") == args.name:
        config["defaults"]["server"] = ""
        config["defaults"]["user"] = ""
    save_config(config)
    print("Removed server '%s'." % args.name)


def cmd_user_add(args):
    config = load_config()
    server = require_server(config, args.server)
    users = server.setdefault("users", {})
    if args.name in users and not args.force:
        raise GatrestError("User '%s' already exists for server '%s'. Use --force to replace it." % (args.name, args.server))

    password = args.password
    if args.prompt_password or password is None:
        password = getpass.getpass("Password for %s: " % (args.username or args.name))

    users[args.name] = {
        "username": args.username or args.name,
        "password": password,
    }

    if not config["defaults"].get("server"):
        config["defaults"]["server"] = args.server
    if not config["defaults"].get("user"):
        config["defaults"]["user"] = args.name
    save_config(config)
    print("Added user '%s' to server '%s'." % (args.name, args.server))


def cmd_user_set(args):
    config = load_config()
    server = require_server(config, args.server)
    user = require_user(server, args.name)
    if args.username:
        user["username"] = args.username
    if args.prompt_password:
        user["password"] = getpass.getpass("Password for %s: " % user.get("username", args.name))
    elif args.password is not None:
        user["password"] = args.password
    save_config(config)
    print("Updated user '%s' on server '%s'." % (args.name, args.server))


def cmd_user_remove(args):
    config = load_config()
    server = require_server(config, args.server)
    users = server.setdefault("users", {})
    if args.name not in users:
        raise GatrestError("Unknown user '%s' for server '%s'." % (args.name, args.server))
    del users[args.name]
    if config.get("defaults", {}).get("user") == args.name:
        config["defaults"]["user"] = ""
    save_config(config)
    print("Removed user '%s' from server '%s'." % (args.name, args.server))


def cmd_use(args):
    config = load_config()
    if args.kind == "server":
        require_server(config, args.name)
        config["defaults"]["server"] = args.name
        config["defaults"]["user"] = ""
    elif args.kind == "user":
        server_name = resolve_server_name(config, args.server)
        server = require_server(config, server_name)
        require_user(server, args.name)
        config["defaults"]["server"] = server_name
        config["defaults"]["user"] = args.name
    save_config(config)
    print("Default %s set to '%s'." % (args.kind, args.name))


def print_server_details(name, server, show_passwords=False):
    print("Server: %s" % name)
    print("  base_url: %s" % server.get("base_url", ""))
    print("  auth_url: %s" % server.get("auth_url", ""))
    print("  ingest_url: %s" % server.get("ingest_url", ""))
    print("  collector_code: %s" % server.get("collector_code", ""))
    print("  organization_code: %s" % server.get("organization_code", ""))
    print("  batch_size: %s" % server.get("batch_size", DEFAULT_BATCH_SIZE))
    print("  timeout_seconds: %s" % server.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    print("  trust_self_signed: %s" % server.get("trust_self_signed", True))
    print("  verify_hostname: %s" % server.get("verify_hostname", False))
    print("  cert_path: %s" % server.get("cert_path", DEFAULT_CERT_PATH))
    print("  users:")
    users = server.get("users") or {}
    if not users:
        print("    none")
        return
    for user_name in sorted(users):
        user = users[user_name]
        password = user.get("password", "")
        displayed_password = password if show_passwords else ("*" * min(len(password), 12) if password else "")
        print("    %s:" % user_name)
        print("      username: %s" % user.get("username", ""))
        print("      password: %s" % displayed_password)


def cmd_show(args):
    config = load_config()
    servers = config.get("servers") or {}
    if args.target == "servers":
        if not servers:
            print("No servers configured.")
            return
        default_server = config.get("defaults", {}).get("server")
        for name in sorted(servers):
            marker = " *" if name == default_server else ""
            print("%s%s  %s" % (name, marker, servers[name].get("base_url", "")))
        return

    server = require_server(config, args.target)
    print_server_details(args.target, server, show_passwords=args.show_passwords)


def cmd_upload(args):
    config = load_config()
    server_name = resolve_server_name(config, args.server)
    server = require_server(config, server_name)
    user_name = resolve_user_name(config, server, args.user)
    user = require_user(server, user_name)

    collector_code = validate_tag(args.collector_code or server.get("collector_code"), "Collector Code", 2)
    organization_code = validate_tag(args.organization_code or server.get("organization_code"), "Organization Code", 5)
    batch_size = args.batch_size or int(server.get("batch_size", DEFAULT_BATCH_SIZE))
    if batch_size < 1 or batch_size > DEFAULT_BATCH_SIZE:
        raise GatrestError("Batch size must be between 1 and %d." % DEFAULT_BATCH_SIZE)

    files = collect_json_files(args.paths)
    if not files:
        print("No .json files found.")
        return

    valid_files = []
    skipped = 0
    failed = 0
    for file_path in files:
        try:
            validate_json_file(file_path)
            valid_files.append(file_path)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            skipped += 1
            print("Skipping %s: invalid JSON (%s)" % (os.path.basename(file_path), exc))

    uploaded_files = 0
    for batch in chunked(valid_files, batch_size):
        try:
            result = send_files_to_ingest(server, user, batch, collector_code, organization_code)
            uploaded_files += len(batch)
            print("Uploaded %d file(s): %s" % (len(batch), json.dumps(result, sort_keys=True)))
        except GatrestError as exc:
            failed += len(batch)
            print("Failed batch (%d file(s)): %s" % (len(batch), exc))

    print(
        "Finished at %s. Uploaded: %d. Skipped: %d. Failed: %d."
        % (datetime.now().isoformat(sep=" ", timespec="seconds"), uploaded_files, skipped, failed)
    )
    if failed or uploaded_files == 0:
        raise SystemExit(1)


def cmd_doctor(args):
    config = load_config()
    server_name = resolve_server_name(config, args.server)
    server = require_server(config, server_name)
    user_name = resolve_user_name(config, server, args.user)
    user = require_user(server, user_name)

    print("Checking server '%s'..." % server_name)
    parsed = urllib_parse.urlparse(server["base_url"])
    if parsed.scheme == "https" and server.get("trust_self_signed", True):
        cert_path = refresh_datanav_cert(server["base_url"], server.get("cert_path", DEFAULT_CERT_PATH))
        print("Certificate cached: %s" % cert_path)
    else:
        print("Certificate cache skipped for %s." % parsed.scheme)
    token = get_access_token(server, user, force_refresh=True)
    exp = decode_token_exp(token)
    if exp:
        print("Login ok. Token expires: %s" % datetime.fromtimestamp(exp).isoformat(sep=" ", timespec="seconds"))
    else:
        print("Login ok. Token expiry not available.")


def add_common_server_user_flags(parser):
    parser.add_argument("-s", "--server", help="server profile name")
    parser.add_argument("-u", "--user", help="user profile name")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gatrest",
        description="Upload JSON files to DataNaviGatr ingest over HTTPS.",
        epilog=(
            "Examples:\n"
            "  gatrest server add dngserver1 --host 192.168.1.2 -c A1 -o 1234A\n"
            "  gatrest user add -s dngserver1 ewsmyth --username ewsmyth --password password\n"
            "  gatrest upload -s dngserver1 -u ewsmyth ./exports\n"
            "  gatrest upload -s dngserver1 -u ewsmyth -c Y4 -o 8322Y file.json\n"
            "  gatrest show servers\n"
            "  gatrest show dngserver1 --show-passwords\n"
            "\nConfig: %s" % CONFIG_PATH
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version="gatrest 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    server_parser = subparsers.add_parser("server", help="manage server profiles")
    server_subparsers = server_parser.add_subparsers(dest="server_command", required=True)

    server_add = server_subparsers.add_parser("add", help="add a DataNaviGatr server")
    server_add.add_argument("name", help="local server profile name")
    server_add.add_argument("--host", "-H", required=True, help="server IP, hostname, or full URL")
    server_add.add_argument("--scheme", choices=["https", "http"], default="https", help="scheme to use when --host is not a URL")
    server_add.add_argument("--collector-code", "-c", help="default 2-character collector code")
    server_add.add_argument("--organization-code", "-o", help="default 5-character organization code")
    server_add.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="network timeout in seconds")
    server_add.add_argument("--batch-size", "-b", type=int, default=DEFAULT_BATCH_SIZE, help="files per upload batch, max 20")
    server_add.add_argument("--no-trust-self-signed", action="store_true", help="use system HTTPS trust instead of downloading the DataNaviGatr cert")
    server_add.add_argument("--verify-hostname", action="store_true", help="verify certificate hostname/SAN")
    server_add.add_argument("--cert-path", default=DEFAULT_CERT_PATH, help="HTTP path where DataNaviGatr exposes its cert")
    server_add.add_argument("--force", "-f", action="store_true", help="replace an existing server profile")
    server_add.set_defaults(func=cmd_server_add)

    server_set = server_subparsers.add_parser("set", help="change server settings")
    server_set.add_argument("name", help="server profile name")
    server_set.add_argument("--host", "-H", help="server IP, hostname, or full URL")
    server_set.add_argument("--scheme", choices=["https", "http"], default="https", help="scheme to use when --host is not a URL")
    server_set.add_argument("--collector-code", "-c", help="default 2-character collector code")
    server_set.add_argument("--organization-code", "-o", help="default 5-character organization code")
    server_set.add_argument("--timeout", "-t", type=int, help="network timeout in seconds")
    server_set.add_argument("--batch-size", "-b", type=int, help="files per upload batch, max 20")
    tls_group = server_set.add_mutually_exclusive_group()
    tls_group.add_argument("--trust-self-signed", dest="trust_self_signed", action="store_true", help="download and trust the DataNaviGatr cert")
    tls_group.add_argument("--no-trust-self-signed", dest="trust_self_signed", action="store_false", help="use system HTTPS trust")
    server_set.set_defaults(trust_self_signed=None)
    host_group = server_set.add_mutually_exclusive_group()
    host_group.add_argument("--verify-hostname", dest="verify_hostname", action="store_true", help="verify certificate hostname/SAN")
    host_group.add_argument("--no-verify-hostname", dest="verify_hostname", action="store_false", help="disable hostname verification")
    server_set.set_defaults(verify_hostname=None)
    server_set.add_argument("--cert-path", help="HTTP path where DataNaviGatr exposes its cert")
    server_set.set_defaults(func=cmd_server_set)

    server_remove = server_subparsers.add_parser("remove", aliases=["rm"], help="remove a server profile")
    server_remove.add_argument("name", help="server profile name")
    server_remove.set_defaults(func=cmd_server_remove)

    user_parser = subparsers.add_parser("user", help="manage saved users")
    user_subparsers = user_parser.add_subparsers(dest="user_command", required=True)

    user_add = user_subparsers.add_parser("add", help="add a user to a server")
    user_add.add_argument("name", help="local user profile name")
    user_add.add_argument("-s", "--server", required=True, help="server profile name")
    user_add.add_argument("--username", "-n", help="DataNaviGatr username or email")
    user_add_password = user_add.add_mutually_exclusive_group()
    user_add_password.add_argument("--password", "-p", help="DataNaviGatr password; visible in shell history")
    user_add_password.add_argument("--prompt-password", action="store_true", help="prompt for password without printing it")
    user_add.add_argument("--force", "-f", action="store_true", help="replace an existing user profile")
    user_add.set_defaults(func=cmd_user_add)

    user_set = user_subparsers.add_parser("set", help="change a saved user")
    user_set.add_argument("name", help="local user profile name")
    user_set.add_argument("-s", "--server", required=True, help="server profile name")
    user_set.add_argument("--username", "-n", help="DataNaviGatr username or email")
    user_set_password = user_set.add_mutually_exclusive_group()
    user_set_password.add_argument("--password", "-p", help="DataNaviGatr password; visible in shell history")
    user_set_password.add_argument("--prompt-password", action="store_true", help="prompt for password without printing it")
    user_set.set_defaults(func=cmd_user_set)

    user_remove = user_subparsers.add_parser("remove", aliases=["rm"], help="remove a saved user")
    user_remove.add_argument("name", help="local user profile name")
    user_remove.add_argument("-s", "--server", required=True, help="server profile name")
    user_remove.set_defaults(func=cmd_user_remove)

    use_parser = subparsers.add_parser("use", help="set default server or user")
    use_parser.add_argument("kind", choices=["server", "user"], help="default type to set")
    use_parser.add_argument("name", help="server or user profile name")
    use_parser.add_argument("-s", "--server", help="server profile name when setting default user")
    use_parser.set_defaults(func=cmd_use)

    show_parser = subparsers.add_parser("show", help="show configured servers or one server")
    show_parser.add_argument("target", help="'servers' or a server profile name")
    show_parser.add_argument("--show-passwords", "-P", action="store_true", help="print saved passwords in clear text")
    show_parser.set_defaults(func=cmd_show)

    upload_parser = subparsers.add_parser("upload", help="upload JSON files")
    add_common_server_user_flags(upload_parser)
    upload_parser.add_argument("-c", "--collector-code", help="2-character collector code for this upload")
    upload_parser.add_argument("-o", "--organization-code", help="5-character organization code for this upload")
    upload_parser.add_argument("-b", "--batch-size", type=int, help="files per upload batch, max 20")
    upload_parser.add_argument("paths", nargs="+", help="JSON files or directories containing JSON files")
    upload_parser.set_defaults(func=cmd_upload)

    doctor_parser = subparsers.add_parser("doctor", help="test certificate download and login")
    add_common_server_user_flags(doctor_parser)
    doctor_parser.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except GatrestError as exc:
        print("gatrest: error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ngatrest: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
