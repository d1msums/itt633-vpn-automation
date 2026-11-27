# ITT633: IPsec VPN Automation

Python configuration generation and SSH deployment for an HQ-to-branch IPsec VPN in GNS3. The lab uses two Cisco CSR1000v routers, an ISP transit router and a separate management network.

## Current status

The template follows the group's supplied manual router configurations. Nine local software tests passed during development. This upload does not include evidence establishing live automated deployment, completed fault tests or timing savings. Verification integration and independent network evidence must be added and tested separately.

## Included files

- `inventory.yaml`: addresses, interfaces and SSH username.
- `tunnel.j2`: IKEv2, IPsec, Tunnel0 and remote LAN route template.
- `deploy.py`: input validation, offline preview, SSH prechecks, sequential deployment and JSON records.
- `preview.py`: offline preview entry point.
- `test_deploy.py`: local tests, including simulated connection and partial-deployment failures.
- `requirements.txt`: Python dependencies.
- `README_SYAHMI.md`: detailed Windows lab handoff instructions.

## Lab prerequisites

Prepare interface addressing, ISP routing, host gateways and SSH manually. Keep a clean baseline for repeatable tests. Run Python on a computer/container that can SSH to both management addresses; the Docker AUTO node is appropriate when Windows cannot route to the isolated management network.

- R1: management `10.0.0.1/24`, LAN `192.168.10.1/24`, WAN `203.0.113.1/30`.
- R2: management `10.0.0.2/24`, LAN `192.168.20.1/24`, WAN `198.51.100.1/30`.
- Tunnel0: R1 `172.16.0.1/30`, R2 `172.16.0.2/30`.
- Gi1: LAN; Gi2: WAN; Gi3: management.

The current implementation uses one SSH username/password and optional enable password for both routers, and one shared VPN key. The template targets the supplied IOS XE 16.8 configuration; other versions need testing.

## Windows setup

Open PowerShell in this folder with Python installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v test_deploy.py
.\.venv\Scripts\python.exe deploy.py
```

## Linux / Docker AUTO setup

Transfer the source files into AUTO. Python 3, pip and venv support must be available. Run from the project directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest -v test_deploy.py
.venv/bin/python deploy.py
```

Create the environment on the execution machine; do not copy a Windows `.venv` into Linux. If AUTO already has an approved Python environment with these dependencies, use its interpreter instead.

## Deployment

Previewing makes no SSH connection. Before deploying, review both generated configurations, confirm SSH and privileged access to both routers and restore the agreed clean baseline.

Windows:

```powershell
.\.venv\Scripts\python.exe deploy.py --deploy --lab-ready
```

Linux / AUTO:

```bash
.venv/bin/python deploy.py --deploy --lab-ready
```

Enter the VPN key, SSH password and optional enable password privately at the prompts. The supported VPN key is a single token of 8-127 characters using letters, digits or `!@#$%^&*_.+=:-`. No actual key is included in this repository.

## Results and recovery

Run records are created under `runs/` and excluded from Git. Command submission is recorded as `commands_sent_verification_pending`; it is not proof of VPN success. Independently verify IKEv2/IPsec state, routes, bidirectional host communication, counter changes and WAN ESP traffic.

The script does not automatically roll back or save startup configuration. A configuration-stage failure may leave partial changes. Inspect both routers and restore the clean baseline before a fresh trial. `elapsed_seconds` includes password-entry time; use an agreed timing protocol for manual-versus-automated comparisons.

## Team responsibilities

- Sofea: inventory, templates, deployment and integration.
- Syahmi: GNS3 lab, manual VPN reference and baseline recovery.
- Aisyah: verification and fault detection.
- Haziq: independent tests, timing records and report evidence.

## Evidence and sharing

Add only actual, labelled and sanitised results. Do not commit credentials, private router configuration exports, SSH keys, Cisco images, VM disks or Python environments. GNS3 exports may contain credentials and require review before sharing. Keep large lab files separately.

## References

- [Netmiko documentation](https://ktbyers.github.io/netmiko/docs/netmiko/)
- [GNS3 documentation](https://docs.gns3.com/)
- [Cisco IOS XE 16.8 FlexVPN and IKEv2 guide](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/sec_conn_ike2vpn/configuration/xe-16-8/sec-flex-vpn-xe-16-8-book.pdf)

AI assistance was used during development. Group members must review, understand and validate the implementation and report actual outcomes.
