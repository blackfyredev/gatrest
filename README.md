# gatrest

`gatrest` is a terminal-only Ubuntu uploader for the DataNaviGatr ingest API.
It stores one or more DataNaviGatr servers, one or more users per server, and
uploads `.json` files over HTTPS.

The tool is standard-library Python only. It is designed to install cleanly
from GitHub and run as `/usr/local/bin/gatrest`.

## Install

Install from the public GitHub repo:

```bash
curl -fsSL https://raw.githubusercontent.com/blackfyredev/gatrest/main/install.sh | sudo sh
```

Verify the install:

```bash
gatrest --version
gatrest --help
```

The installer downloads `gatrest.py` and installs it here:

```text
/usr/local/bin/gatrest
```

## What It Does

- Uploads only `.json` files.
- Accepts explicit `.json` files or directories containing `.json` files.
- Scans directories non-recursively.
- Validates JSON before upload.
- Uploads files in batches of up to 20.
- Authenticates through `/api/auth/login`.
- Uploads to `/api/ingest/upload`.
- Stores config in `~/.config/gatrest/config.json`.
- Saves passwords locally with config file permissions set to `0600`.
- Supports DataNaviGatr's built-in self-signed HTTPS certificate.

## HTTPS And Self-Signed Certs

DataNaviGatr serves HTTPS through NGINX. When DataNaviGatr uses its built-in
self-signed certificate, `gatrest` downloads the certificate from:

```text
http://<server>/.well-known/datanavigatr/datanavigatr.crt
```

It caches that cert and uses it for HTTPS login and upload requests. Hostname
verification is disabled by default because local deployments commonly use an
IP address that may not match the certificate SAN.

To use normal system certificate trust instead:

```bash
gatrest server set dngserver1 --no-trust-self-signed
```

To require hostname verification:

```bash
gatrest server set dngserver1 --verify-hostname
```

## First Setup

Add a server:

```bash
gatrest server add dngserver1 --host 192.168.1.2 -c A1 -o 1234A
```

That creates a profile named `dngserver1` with:

- `https://192.168.1.2/api/auth/login`
- `https://192.168.1.2/api/ingest/upload`
- collector code `A1`
- organization code `1234A`

Add a user:

```bash
gatrest user add -s dngserver1 ewsmyth --username ewsmyth --password
```

The `--password` flag prompts for the password without printing it in the
terminal.

You can also pass the password directly, but it may be saved in shell history:

```bash
gatrest user add -s dngserver1 ewsmyth --username ewsmyth -P "password"
```

## Upload

Upload one file:

```bash
gatrest upload -s dngserver1 -u ewsmyth /path/to/file.json
```

Upload several files:

```bash
gatrest upload -s dngserver1 -u ewsmyth file1.json file2.json file3.json
```

Upload every `.json` file directly inside a directory:

```bash
gatrest upload -s dngserver1 -u ewsmyth /path/to/json-folder
```

Override collector and organization tags for one upload:

```bash
gatrest upload -s dngserver1 -u ewsmyth -c Y4 -o 8322Y /path/to/file.json
```

Change batch size for one upload:

```bash
gatrest upload -s dngserver1 -u ewsmyth -b 10 /path/to/json-folder
```

Batch size must be between 1 and 20.

## Show Config

List configured servers:

```bash
gatrest show servers
```

Show one server:

```bash
gatrest show dngserver1
```

Show one server with saved passwords visible:

```bash
gatrest show dngserver1 --show-passwords
```

Short form:

```bash
gatrest show dngserver1 -P
```

## Defaults

Set a default server:

```bash
gatrest use server dngserver1
```

Set a default user for the current default server:

```bash
gatrest use user ewsmyth
```

Set a default user for a specific server:

```bash
gatrest use user ewsmyth -s dngserver1
```

After defaults are set, uploads can be shorter:

```bash
gatrest upload /path/to/file.json
```

## Doctor

Use `doctor` to test the HTTPS certificate path and login:

```bash
gatrest doctor -s dngserver1 -u ewsmyth
```

With defaults set:

```bash
gatrest doctor
```

## Manage Servers

Add a server:

```bash
gatrest server add dngserver1 --host 192.168.1.2 -c A1 -o 1234A
```

Use a full URL:

```bash
gatrest server add dngserver1 --host https://datanavigatr.example.local
```

Change collector or organization code:

```bash
gatrest server set dngserver1 -c B2 -o 5678B
```

Change host:

```bash
gatrest server set dngserver1 --host 192.168.1.25
```

Change timeout:

```bash
gatrest server set dngserver1 -t 180
```

Change default batch size:

```bash
gatrest server set dngserver1 -b 20
```

Remove a server:

```bash
gatrest server remove dngserver1
```

Short alias:

```bash
gatrest server rm dngserver1
```

## Manage Users

Add a user:

```bash
gatrest user add -s dngserver1 ewsmyth --username ewsmyth --password
```

Change username:

```bash
gatrest user set -s dngserver1 ewsmyth --username newname
```

Change password:

```bash
gatrest user set -s dngserver1 ewsmyth --password
```

Remove a user:

```bash
gatrest user remove -s dngserver1 ewsmyth
```

Short alias:

```bash
gatrest user rm -s dngserver1 ewsmyth
```

## Help Menu

Top-level help:

```text
usage: gatrest [-h] [-V] {server,user,use,show,upload,doctor} ...

Upload JSON files to DataNaviGatr ingest over HTTPS.

positional arguments:
  {server,user,use,show,upload,doctor}
    server              manage server profiles
    user                manage saved users
    use                 set default server or user
    show                show configured servers or one server
    upload              upload JSON files
    doctor              test certificate download and login

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit

Examples:
  gatrest server add dngserver1 --host 192.168.1.2 -c A1 -o 1234A
  gatrest user add -s dngserver1 ewsmyth --username ewsmyth --password
  gatrest upload -s dngserver1 -u ewsmyth ./exports
  gatrest upload -s dngserver1 -u ewsmyth -c Y4 -o 8322Y file.json
  gatrest show servers
  gatrest show dngserver1 --show-passwords
```

Command-specific help:

```bash
gatrest server --help
gatrest server add --help
gatrest server set --help
gatrest user --help
gatrest user add --help
gatrest upload --help
gatrest show --help
gatrest doctor --help
```

## Config File

Config is stored at:

```text
~/.config/gatrest/config.json
```

The file contains server profiles, saved users, saved passwords, and defaults.
`gatrest` writes it with `0600` permissions.

Example shape:

```json
{
  "version": 1,
  "defaults": {
    "server": "dngserver1",
    "user": "ewsmyth"
  },
  "servers": {
    "dngserver1": {
      "base_url": "https://192.168.1.2",
      "auth_url": "https://192.168.1.2/api/auth/login",
      "ingest_url": "https://192.168.1.2/api/ingest/upload",
      "collector_code": "A1",
      "organization_code": "1234A",
      "batch_size": 20,
      "timeout_seconds": 120,
      "trust_self_signed": true,
      "verify_hostname": false,
      "cert_path": "/.well-known/datanavigatr/datanavigatr.crt",
      "users": {
        "ewsmyth": {
          "username": "ewsmyth",
          "password": "password"
        }
      }
    }
  }
}
```

## Requirements

- Ubuntu
- Python 3
- `curl` or `wget` for installation
- DataNaviGatr user with the `admin` role

The ingest API requires an admin access token.
